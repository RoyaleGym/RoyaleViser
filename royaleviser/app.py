"""The window, the loop, the input and the timeline around ``render.Renderer``.

    from royaleviser.app import run
    run(source)                                           # one source, the layout's window
    run([capture, other], ViewState(seat=1), geometry=(0, 0, 604, 0), seconds=8, shot="s.png")

``run`` takes Source objects (``model.Source``: sources.py builds them from the command
line, tests use a list-backed one) -- the first is the main source, an optional second is
the compare source ghosted onto the board at the main frame's tick. Replays are paced by
``Frame.tick_ms`` times the playback speed; live sources are polled every iteration and
drawn when a new frame arrives. The window is redrawn only when the frame or the view
changed, at most 60 times a second; otherwise the loop sleeps. On exit it prints the draw
statistics ("royaleviser: N draws, mean X ms, max Y ms") and closes the sources.

KEYS is the one list (the help footer, ``python -m royaleviser --help`` and the README
quote it). --seconds N closes the window by itself and --shot PATH saves the last drawn
window as PNG for unattended runs; with SDL_VIDEODRIVER=dummy no window opens at all.

Windows: the process is made per-monitor DPI aware before pygame starts so --geometry is
physical pixels (what a caller placing the window measures), and the position is
handed to SDL through SDL_VIDEO_WINDOW_POS. The window's SIZE is always the layout's
(dashboard + arena + inspector); a geometry's WxH only picks the largest tile scale that
fits when no --scale was given.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import dataclasses
import os
import re
import sys
import time
from collections import Counter, OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .model import (
    LIVE_UNITS_PER_TILE,
    NO_WINNER,
    TICK_MS,
    TOWER_SLOTS,
    UNKNOWN_CARD,
    UNKNOWN_HP,
    Frame,
    Names,
    Player,
    Source,
)
from .render import Renderer, Transport, ViewState
from .theme import DEFAULT, Theme, layout

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

# (key, action): the help footer, --help and README are generated from this list.
KEYS: list[tuple[str, str]] = [
    ("space", "play / pause (replays)"),
    ("left / right", "step a frame (shift: 20)"),
    ("home / end", "first / last frame"),
    ("+ / -", "speed x2 / x0.5 (also ] [)"),
    ("wheel", "step frames"),
    ("f", "flip the seat"),
    ("p", "unit paths"),
    ("t", "target lines"),
    ("g", "tile grid"),
    ("d", "debug numbers"),
    ("b", "building footprints"),
    ("c", "compare ghost"),
    ("h", "this help"),
    ("s / F12", "save a PNG"),
    ("click", "pin a unit / seek the timeline"),
    ("escape / q", "quit"),
]
SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
FPS_CAP = 60
IDLE_WAIT_MS = 4
MIN_SCALE = 8
COMPACT_BELOW = 16  # a fitted scale under this (unit labels vanish): drop the inspector column
LIVE_REFRESH_S = 0.5  # a live window redraws its status line this often without a new frame
COMPARE_WINDOW = 60  # ticks buffered per source for the same-tick agreement (3 s at 20 Hz)
NEW_BATTLE_DROP = 100  # the tick falling back by more than this: a new battle, totals restart


def dpi_aware() -> None:
    """Per-monitor DPI awareness (Windows) so window geometry is physical pixels."""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)


def frame_extras() -> tuple[int, int]:
    """Estimated (width, height) a Windows top-level frame adds around the client area."""
    if sys.platform != "win32":
        return 0, 0
    metrics = ctypes.windll.user32.GetSystemMetrics
    border = metrics(32) + metrics(92)  # SM_CXSIZEFRAME + SM_CXPADDEDBORDER
    return 2 * border, metrics(4) + 2 * (metrics(33) + metrics(92))  # SM_CYCAPTION + frame


def place_window(x: int, y: int) -> tuple[int, int]:
    """Move the pygame window so its OUTER rectangle starts at (x, y); returns the measured
    frame extras. Only Windows has the frame; elsewhere SDL_VIDEO_WINDOW_POS already applied."""
    if sys.platform != "win32":
        return 0, 0
    hwnd = pygame.display.get_wm_info().get("window")
    if not hwnd:
        return 0, 0
    user32 = ctypes.windll.user32
    outer, client = ctypes.wintypes.RECT(), ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    user32.GetClientRect(hwnd, ctypes.byref(client))
    dw = (outer.right - outer.left) - client.right
    dh = (outer.bottom - outer.top) - client.bottom
    user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)  # NOSIZE NOZORDER NOACTIVATE
    return dw, dh


def fit_scale(theme: Theme, tiles: tuple[int, int], w: int, h: int) -> int:
    """The largest tile scale whose layout fits in w x h, at least MIN_SCALE."""
    scale = MIN_SCALE
    while True:
        lay = layout(theme, scale + 1, tiles)
        if lay.window[0] > w or lay.window[1] > h:
            return scale
        scale += 1


def fits(theme: Theme, tiles: tuple[int, int], scale: int, w: int, h: int) -> bool:
    win = layout(theme, scale, tiles).window
    return win[0] <= w and win[1] <= h


def fit_layout(theme: Theme, tiles: tuple[int, int], w: int, h: int) -> tuple[Theme, int]:
    """The theme and scale for a w x h window: the full layout when it fits at COMPACT_BELOW
    px/tile or more, else the compact one (``inspector_w`` 0) when that fits better.
    A 712 px slot takes the compact layout at 20."""
    full = fit_scale(theme, tiles, w, h)
    if full >= COMPACT_BELOW and fits(theme, tiles, full, w, h):
        return theme, full
    compact = dataclasses.replace(theme, inspector_w=0)
    small = fit_scale(compact, tiles, w, h)
    if small > full or not fits(theme, tiles, full, w, h):
        return compact, small
    return theme, full


Signature = Counter[tuple[int, str, int, int, int]]


def signature(frame: Frame) -> Signature:
    """The multiset two sources must agree on at one tick: (team, name, x, y, hp) per unit.

    Positions are brought to millitiles so a trace (18000 per tile) and a capture (1000) are
    comparable; uids are per source (each client numbers its entities on its own) and are
    left out, as are the towers' max hp and the timers a capture does not carry.
    """
    upt = frame.units_per_tile
    return Counter(
        (u.team, u.name, u.x * 1000 // upt, u.y * 1000 // upt, u.hp) for u in frame.units
    )


def differing(a: Signature, b: Signature) -> int:
    """Units of one multiset without a twin in the other (the larger of the two sides)."""
    return max(sum((a - b).values()), sum((b - a).values()))


#: One unit as the comparison sees it: (team, name, x, y, hp), positions in millitiles.
Row = tuple[int, str, int, int, int]


def rows(frame: Frame) -> list[Row]:
    """Every unit as a comparison row, in millitiles (``signature`` as a list)."""
    upt = frame.units_per_tile
    return [(u.team, u.name, u.x * 1000 // upt, u.y * 1000 // upt, u.hp) for u in frame.units]


def keyed_rows(frame: Frame) -> dict[Any, Row]:
    """Every unit as a comparison row, under its uid."""
    upt = frame.units_per_tile
    return {
        u.uid: (u.team, u.name, u.x * 1000 // upt, u.y * 1000 // upt, u.hp) for u in frame.units
    }


def differing_by_uid(a: dict[Any, Row], b: dict[Any, Row], tolerance: int) -> tuple[int, int]:
    """``differing_within`` for two sides that key the same units the same way.

    WHERE THE UIDS MEAN THE SAME THING, PAIRING BY THEM IS THE ONLY HONEST COMPARISON. Pairing
    by name and distance is satisfied by the defect it is supposed to show: two units of one
    card that swap places pair with each other's positions and the tick reads as agreeing. The
    two sides of a parity trace both key by the recording's entity key, so this is available
    there; two recordings of one battle number their entities separately, so it is not, and
    ``Compare`` only turns it on where the caller says the keys are shared.

    A uid one side does not have counts as differing, whatever the tolerance -- the same rule
    as an unpaired unit in ``differing_within``, and here it cannot be papered over by a
    same-named neighbour.
    """
    differ = hp_differ = 0
    for key in a.keys() | b.keys():
        left, right = a.get(key), b.get(key)
        if left is None or right is None:
            differ += 1
            continue
        d2 = (left[2] - right[2]) ** 2 + (left[3] - right[3]) ** 2
        if d2 > tolerance * tolerance:
            differ += 1
        elif left[4] != right[4]:
            hp_differ += 1
    return differ, hp_differ


def differing_within(a: list[Row], b: list[Row], tolerance: int) -> tuple[int, int]:
    """(units that differ, pairs that agree on position but not on hp), within ``tolerance``.

    EXACT EQUALITY IS THE WRONG QUESTION for two different simulations of one battle. Two
    clients of one friendly run the same lockstep simulation, so their recordings agree to
    the unit; an engine replaying a recorded battle does not, and never will. Every unit is
    then "differing" and the count says nothing about whether the engine is close or lost.

    So the units of one team and name on one side are paired with those on the other, and a
    pair counts as agreeing when the two are within ``tolerance`` millitiles. As many pairs
    are made as CAN be made at once (``cheapest_pairing``) rather than greedily nearest-first,
    which over-reports: two units that could both be accounted for get called a disagreement
    because a closer pair was taken first. What is left unpaired on either side is a unit one
    side has and the other does not, which is the failure the position tolerance must never
    hide, so it counts as differing whatever the tolerance is.

    HP is not part of the DIFFER count: a pair that stands in the same place with different hp
    is a different finding from a unit in the wrong place, and the second return value keeps
    it visible instead of folding the two into one number that cannot be read back.

    HP IS part of the pairing, as the tie-break, and has to be. Where several units of one
    card can pair with each other -- stacked Skeletons, which is how they spawn -- the number
    of pairs is the same whichever way round they go, so an algorithm that only counts pairs
    picks arbitrarily and the hp count comes out of that arbitrary choice. Two attempts got a
    wrong number that way. Pairs that agree on hp are preferred among the pairings that make
    the most pairs, which changes no differ count and makes the hp count mean something.
    """
    differ = hp_differ = 0
    keys = {(t, n) for t, n, *_ in a} | {(t, n) for t, n, *_ in b}
    for key in sorted(keys):
        left = sorted(r for r in a if (r[0], r[1]) == key)
        right = sorted(r for r in b if (r[0], r[1]) == key)
        if left == right:
            continue
        # WHICH units are paired with which decides the hp count, so it cannot be left to
        # whatever a cardinality-only matching happens to return. Two attempts at this were
        # wrong in the same way: a maximum matching is maximum in CARDINALITY, and among the
        # matchings of that size it is free to pair each unit with the wrong twin.
        # Preferring an hp-agreeing partner in the candidate order is not enough either,
        # because the augmenting step re-routes what it has already matched -- it holds only
        # where the distance already broke the tie, which is to say everywhere except the
        # stacked case the problem is about. Skeletons spawn stacked.
        #
        # So the pairing is chosen rather than accepted: the cost of a pair is NO_PAIR when
        # the two are further apart than the tolerance, 1 when they agree on position and not
        # on hp, and 0 when they agree on both. NO_PAIR is larger than every hp cost put
        # together, so the cheapest assignment makes as many pairs as CAN be made, and among
        # those makes as many of them agree on hp as can agree.
        # The bound this construction stands on: at most min(L, R) pairs exist, so the whole
        # hp bill can never exceed min(L, R), and one refused pair costs L*R+1, which is more.
        # Cardinality therefore dominates strictly and the optimiser can never buy an hp
        # agreement by giving up a pair. Change the cost scale and that has to be rechecked;
        # a test pins it.
        no_pair = len(left) * len(right) + 1
        cost = [
            [
                no_pair
                if (x0 - x1) ** 2 + (y0 - y1) ** 2 > tolerance * tolerance
                else int(hp0 != hp1)
                for _, _, x1, y1, hp1 in right
            ]
            for _, _, x0, y0, hp0 in left
        ]
        paired = [(i, j) for i, j in cheapest_pairing(cost) if cost[i][j] < no_pair]
        differ += max(len(left), len(right)) - len(paired)
        hp_differ += sum(cost[i][j] for i, j in paired)
    return differ, hp_differ


def cheapest_pairing(cost: list[list[int]]) -> list[tuple[int, int]]:
    """The assignment of rows to columns with the least total cost, as (row, column) pairs.

    The Hungarian algorithm in its shortest-augmenting-path form, on a rectangular matrix
    with at least as many columns as rows (the caller's matrix is transposed here when it is
    not). Every row is assigned; it is the CALLER that decides which assignments mean
    anything, by giving an impossible pair a cost larger than every possible one added
    together and then throwing those pairs away.

    Why an assignment and not a maximum matching, which is what this used to be. A maximum
    matching answers "how many pairs can be made", and two matchings of that size can pair
    the same units differently. Anything read off WHICH units were paired -- here, how many
    pairs disagree on hp -- then depends on an arbitrary choice, and the two attempts before
    this one both got a wrong number that way. A cheapest assignment makes the choice on the
    caller's terms instead, and one number comes out of one algorithm.

    It is O(n^2 m), which is nothing at these sizes: the rows are the units of one card on
    one side of one tick, a handful, fifteen for a Skeleton Army. ``tests/test_render.py``
    checks it against brute force over every permutation for small random matrices, because
    an optimiser that is subtly wrong is worse than a greedy one that is obviously wrong.
    """
    if not cost or not cost[0]:
        return []
    if len(cost) > len(cost[0]):
        turned = [list(col) for col in zip(*cost, strict=True)]
        return [(i, j) for j, i in cheapest_pairing(turned)]
    n, m = len(cost), len(cost[0])
    big = sum(max(row) for row in cost) + 1
    # 1-based potentials and the column -> row assignment, as the algorithm is usually given.
    u, v = [0] * (n + 1), [0] * (m + 1)
    at_col = [0] * (m + 1)  # at_col[j] is the row assigned to column j, 0 for none
    came_from = [0] * (m + 1)
    for row in range(1, n + 1):
        at_col[0] = row
        col = 0
        least = [big] * (m + 1)
        done = [False] * (m + 1)
        while True:
            done[col] = True
            here, step, best = at_col[col], big, -1
            for j in range(1, m + 1):
                if done[j]:
                    continue
                through = cost[here - 1][j - 1] - u[here] - v[j]
                if through < least[j]:
                    least[j], came_from[j] = through, col
                if least[j] < step:
                    step, best = least[j], j
            for j in range(m + 1):
                if done[j]:
                    u[at_col[j]] += step
                    v[j] -= step
                else:
                    least[j] -= step
            col = best
            if at_col[col] == 0:
                break
        while col:
            previous = came_from[col]
            at_col[col] = at_col[previous]
            col = previous
    return [(at_col[j] - 1, j - 1) for j in range(1, m + 1) if at_col[j]]


def compare_text(main: Frame, other: Frame, tolerance: int = 0) -> str:
    """'N entities, M differ' for two frames of the same tick.

    With no tolerance this is exact agreement, the question two recordings of one battle
    answer. With one it is "how far apart are they", the question an engine and a recording
    answer, and the hp disagreements are counted beside the positions rather than inside them.
    """
    if tolerance <= 0:
        return f"{len(other.units)} entities, {differing(signature(main), signature(other))} differ"
    differ, hp_differ = differing_within(rows(main), rows(other), tolerance)
    hp = f", {hp_differ} hp" if hp_differ else ""
    return f"{len(other.units)} entities, {differ} differ{hp} (within {tiles_text(tolerance)})"


def tiles_text(millitiles: int) -> str:
    """A tolerance as tiles, for the line a reader has to judge every number above it by.

    Two places where two places are enough, and the millitiles themselves where they are not.
    ``--tolerance`` takes any integer, and rounding 4 to "0.00 tiles" would tell a reader, in
    this viewer's own documented vocabulary for exact agreement, that nothing was allowed to
    move -- while units four millitiles apart were being counted as agreeing.
    """
    if millitiles % 10 == 0:
        return f"{millitiles / 1000:.2f} tiles"
    return f"{millitiles} millitile" + ("" if millitiles == 1 else "s")


class Compare:
    """Same-tick agreement between the main source and the compare source, with totals.

    Every frame either source shows is noted under its tick (a buffer of COMPARE_WINDOW ticks
    per source: live sources are at most a few frames apart; a replay seeks its compare
    source to the exact tick). A tick both sources have noted is compared once: the two
    clients of one friendly run the same lockstep simulation, so the multisets must be equal
    (measured 2026-09-20 on the 12:07 captures: identical on 2401 of 2407 common ticks, the
    rest tap ticks where the two captures disagree for one frame). ``text(tick)`` is the footer's
    two lines:
    the result at ``tick`` and the running totals. With ``restart_on_drop`` (live sources)
    a tick falling back by NEW_BATTLE_DROP restarts everything: a new battle; a replay
    scrubbed backwards keeps its totals and never compares a tick twice.

    ``tolerance`` in millitiles turns the question from "are these the same battle" into "how
    far apart are they", which is the only readable answer when one side is an ENGINE replaying
    a battle the other side RECORDED. Then the units of one team and name on one side
    are matched against the other's, as many pairs at once as the tolerance allows rather than
    greedily nearest-first, and a pair that agrees on position but not on hp is counted
    separately (``differing_within``). Where the caller says both sides number their units the
    same way, which is ``--parity`` and nothing else, the pairing is by uid at EVERY tolerance
    including 0 (``differing_by_uid``). A unit one side has and the other does not is
    never within any tolerance. 0, the default, is exact agreement.
    """

    def __init__(
        self, restart_on_drop: bool = False, tolerance: int = 0, pair_by_uid: bool = False
    ) -> None:
        self.restart_on_drop = restart_on_drop
        self.tolerance = max(0, tolerance)
        # Only where the caller KNOWS both sides number their units the same way; see
        # ``differing_by_uid``. Two recordings of one battle do not.
        self.pair_by_uid = pair_by_uid
        self._sig: list[OrderedDict[int, tuple[list[Row], int]]] = [OrderedDict(), OrderedDict()]
        self._keyed: list[OrderedDict[int, dict[Any, Row]]] = [OrderedDict(), OrderedDict()]
        self._last_tick: list[int | None] = [None, None]
        # tick -> (n main, n other, differ, hp differ)
        self.results: dict[int, tuple[int, int, int, int]] = {}
        self.ticks = 0
        self.differ = 0
        self.hp_differ = 0

    def reset(self) -> None:
        self._sig = [OrderedDict(), OrderedDict()]
        self._keyed = [OrderedDict(), OrderedDict()]
        self._last_tick = [None, None]
        self.results.clear()
        self.ticks = self.differ = self.hp_differ = 0

    def note(self, which: int, frame: Frame) -> None:
        """Frame ``frame`` was shown by source ``which`` (0 main, 1 compare)."""
        last = self._last_tick[which]
        if self.restart_on_drop and last is not None and frame.tick < last - NEW_BATTLE_DROP:
            self.reset()
        self._last_tick[which] = frame.tick
        buf = self._sig[which]
        if frame.tick in buf or frame.tick in self.results:
            return
        buf[frame.tick] = (rows(frame), len(frame.units))
        if self.pair_by_uid:
            self._keyed[which][frame.tick] = keyed_rows(frame)
            while len(self._keyed[which]) > COMPARE_WINDOW:
                self._keyed[which].popitem(last=False)
        while len(buf) > COMPARE_WINDOW:
            buf.popitem(last=False)
        if frame.tick in self._sig[1 - which]:
            (sa, na), (sb, nb) = self._sig[0][frame.tick], self._sig[1][frame.tick]
            # A tolerance of 0 is the STRICTEST setting, not the absence of one, so it must
            # not be the one setting that falls back to pairing by name and position. It used
            # to, which brought the swap that pairing cannot see back at exactly the setting a
            # reader reaches for when they want strictness, and folded the hp disagreements
            # into the single differ count along the way.
            if self.pair_by_uid:
                d, hp = differing_by_uid(
                    self._keyed[0][frame.tick], self._keyed[1][frame.tick], self.tolerance
                )
            elif self.tolerance:
                d, hp = differing_within(sa, sb, self.tolerance)
            else:
                d, hp = differing(Counter(sa), Counter(sb)), 0
            self.results[frame.tick] = (na, nb, d, hp)
            self.ticks += 1
            self.differ += d > 0
            self.hp_differ += hp > 0

    def text(self, tick: int | None) -> str:
        """Two lines: the comparison at ``tick`` (or why there is none) and the totals."""
        if tick is None:
            first = "no frame yet"
        elif tick in self.results:
            na, nb, d, hp = self.results[tick]
            n = f"{na} entities" if na == nb else f"{na} vs {nb} entities"
            first = f"tick {tick}: {n}, {d} differ" + (f", {hp} hp" if hp else "")
        else:
            first = f"tick {tick}: not seen by both"
        # The tolerance is on the totals line, not the tick line: every number above it was
        # judged by it, and a reader who reads only "0 differ" must not have to guess within
        # what. The clause is at the end, which makes it what an ellipsis eats first, so the
        # line has to stay inside the column it is drawn in (287 px; the long form plus the
        # clause measured 295). A width test pins both lines.
        #
        # Which is also why the subject changes to a colon once there is a clause. Every
        # number on THIS line counts ticks and every number on the line above counts units,
        # and the two lines used to share their trailing words: "2 ticks, 0 differ, 1 hp"
        # under "tick 100: 3 entities, 0 differ, 3 hp", with nothing saying the 1 and the 3
        # count different things. "2407 ticks: 3 differ, 2 hp" reads as what it is.
        if not self.tolerance:
            return f"{first}\n{self.ticks} ticks compared, {self.differ} differ"
        totals = f"{self.ticks} ticks: {self.differ} differ"
        if self.hp_differ:
            totals += f", {self.hp_differ} hp"
        return f"{first}\n{totals} (within {tiles_text(self.tolerance)})"


def empty_frame(units_per_tile: int) -> Frame:
    """What a window shows before its source has a frame: the board and the panels with
    nothing known, so a source that has not produced a frame yet looks like the viewer,
    not a blank window."""
    players = [
        Player(
            team=team,
            elixir_milli=0,
            elixir_known=False,
            hand=[UNKNOWN_CARD] * 4,
            hand_known=False,
            next_card=None,
            cycle=[],
            deck=[],
            deck_known=False,
            crowns=0,
            tower_hp=[UNKNOWN_HP] * TOWER_SLOTS,
            tower_max_hp=[UNKNOWN_HP] * TOWER_SLOTS,
            king_active=None,
        )
        for team in (0, 1)
    ]
    return Frame(
        tick=0,
        tick_ms=TICK_MS,
        units_per_tile=units_per_tile or LIVE_UNITS_PER_TILE,
        players=players,
        units=[],
        spells=[],
        overtime=False,
        game_over=False,
        winner=NO_WINNER,
        crowns=[0, 0],
    )


def empty_shot_reason(app: App) -> str:
    """Why a requested ``--shot`` would be a picture of nothing, or "" when it would not.

    A shot of a live source that never received a frame is the worst kind of output: it is a
    real screenshot of the real window, showing an empty board under a full set of panels, and
    it looks exactly like a photograph of a run that has died. The training session nearly
    sent one to the owner as the picture of a new feature.

    The most likely cause is the least obvious one. A publisher keeps ONE peer, the address of
    the last heartbeat it read, so a second viewer opened on a stream that is already being
    watched gets no frames at all -- while its LEARNER panel fills normally, because the status
    goes out on a different port from a different sender. That combination is diagnostic: a
    status arriving with no frames means something IS running and is not sending here.

    A replay always has a frame, so this is a live-source concern only.
    """
    if not app.source.live or app.frame is not None:
        return ""
    where = app.source.name
    if app.transport.learning is not None:
        return (
            f"no frame ever arrived from {where}, but a learner's status did. Something is "
            "running and is not sending frames here: a publisher keeps one peer, so another "
            "viewer attached to that stream is holding it. Close the other window, or shoot "
            "it with S"
        )
    return f"no frame ever arrived from {where}; nothing was published while this window was open"


def shot_path(source: Any, frame: Frame | None, explicit: str | None) -> Path:
    """--shot's path, else 'royaleviser-<source>-t<tick>.png' next to the source file (or cwd).

    A live source has no file, so its shot lands in the working directory. Before its first
    frame there is no tick either, and "t0" would name a frame nobody saw, so it says so.
    """
    if explicit:
        return Path(explicit)
    tick = f"t{frame.tick}" if frame is not None else "no-frame"
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(getattr(source, "name", "viser")))
    where = getattr(source, "path", None)
    folder = Path(where).parent if where else Path.cwd()
    return folder / f"royaleviser-{name}-{tick}.png"


def source_names(source: Any) -> Names | None:
    """The source's card table for the cost badges, else the live table, else nothing."""
    names = getattr(source, "names", None)
    if isinstance(names, Names):
        return names
    try:
        return Names.live()
    except (OSError, ValueError):
        return None


class App:
    """One window over a main source and an optional compare source."""

    def __init__(
        self,
        sources: Sequence[Source],
        view: ViewState,
        *,
        scale: int | None = None,
        speed: float = 1.0,
        theme: Theme = DEFAULT,
        title: str = "RoyaleViser",
        follow_local: bool = False,
        tolerance: int = 0,
        pair_by_uid: bool = False,
    ) -> None:
        if not sources:
            raise ValueError("run needs at least one source")
        self.source = sources[0]
        self.compare = sources[1] if len(sources) > 1 else None
        self.agreement = Compare(
            restart_on_drop=self.source.live, tolerance=tolerance, pair_by_uid=pair_by_uid
        )
        self.follow_local = follow_local  # --seat local: seat the source's local side once known
        self.view = view
        self.view.compare_name = self.compare.name if self.compare is not None else ""
        self.transport = Transport(
            source_name=self.source.name,
            live=self.source.live,
            playing=not self.source.live,
            speed=min(SPEEDS, key=lambda s: abs(s - speed)),
            length=self.source.length,
        )
        self.renderer = Renderer(scale=scale, theme=theme, help_lines=KEYS)
        arena = getattr(self.source, "arena", None)
        if arena is not None:
            self.renderer.set_arena(arena, arena.subtile)
        names = source_names(self.source)
        if names is not None:
            self.renderer.cost_of = names.cost_of_name
        self.title = title
        self.frame: Frame | None = None
        self.dirty = True
        self.running = True
        self.due_ms = 0.0
        self.dragging = False
        self.last_key: tuple[Any, ...] = ()
        self.last_other: Frame | None = None
        self.draw_times: list[float] = []
        self.drawn_at: list[float] = []
        self.last_draw_at = 0.0
        self.shot_request: str | None = None

    # ------------------------------------------------------------------ sources

    def pull(self) -> None:
        """The main source's current frame and the compare source's at the same tick.

        A source that hears from a learner (``StreamSource``) also carries a ``learning``
        status; it is read with ``getattr`` so every other source simply has none, and the
        panel keeps saying no learner is attached. A new status is a new object, so the
        window redraws when one arrives even though the board has not moved.
        """
        f = self.source.frame()
        key = (id(f), f.tick if f else None, f.meta.get("seq") if f else None)
        changed = key != self.last_key
        if changed:
            self.last_key = key
            self.frame = f
            self.dirty = True
            if f is not None:
                if self.follow_local:
                    side = getattr(self.source, "local_side", None)
                    if side is not None:
                        self.view.seat = side
                        self.follow_local = False
                if self.view.hover_uid is not None and f.unit(self.view.hover_uid) is None:
                    self.view.hover_uid = None
        learning = getattr(self.source, "learning", None)
        if learning is not self.transport.learning:
            self.transport.learning = learning
            self.dirty = True
        peer = getattr(self.source, "learning_peer", None)
        named = f"{peer[0]}:{peer[1]}" if peer else ""
        if named != self.transport.learning_peer:
            self.transport.learning_peer = named
            self.dirty = True
        if self.compare is not None and (changed or self.compare.live):
            self.pull_compare(f, changed)

    def pull_compare(self, f: Frame | None, changed: bool) -> None:
        """A replay compare source is seeked to the main frame's tick; a live one is polled
        every loop, so its frames feed the agreement even while the main frame stands."""
        assert self.compare is not None
        if f is not None and changed:
            self.agreement.note(0, f)
            if not self.compare.live:
                at = getattr(self.compare, "index_at_tick", None)
                if at is not None:
                    self.compare.seek(at(f.tick))
        other = self.compare.frame()
        if other is not None and other is not self.last_other:
            self.agreement.note(1, other)
            self.last_other = other
            self.view.compare_frame = other
            self.dirty = True
        text = self.agreement.text(f.tick if f is not None else None)
        if text != self.view.compare_text:
            self.view.compare_text = text
            self.dirty = True

    def advance(self, dt_ms: float) -> None:
        """Replay pacing: one frame per tick_ms / speed of wall time, catching up in steps."""
        tr = self.transport
        if self.source.live or not tr.playing or self.frame is None:
            return
        self.due_ms += dt_ms * tr.speed
        while self.due_ms >= self.frame.tick_ms:
            before = self.source.index
            self.source.step(1)
            if self.source.index == before:
                tr.playing = False
                tr.at_end = True
                self.due_ms = 0.0
                self.dirty = True
                break
            old_tick = self.frame.tick
            self.pull()
            assert self.frame is not None
            self.due_ms -= max(1, self.frame.tick - old_tick) * self.frame.tick_ms
        tr.index = self.source.index

    def seek(self, index: int) -> None:
        self.source.seek(index)
        self.transport.at_end = False
        self.due_ms = 0.0
        self.pull()
        self.transport.index = self.source.index
        self.dirty = True

    # ------------------------------------------------------------------ input

    def handle(self, event: pygame.event.Event) -> None:
        tr, view = self.transport, self.view
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.KEYDOWN:
            self.key(event.key, event.mod)
        elif event.type == pygame.MOUSEMOTION:
            if self.dragging and tr.length:
                i = self.renderer.timeline_index_at(
                    event.pos[0], self.renderer.layout.timeline[1], tr.length
                )
                if i is not None:
                    self.seek(i)
            elif self.frame is not None:
                uid = self.renderer.unit_at(event.pos[0], event.pos[1], self.frame, view)
                if uid != view.hover_uid:
                    view.hover_uid = uid
                    self.dirty = True
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            i = self.renderer.timeline_index_at(*event.pos, tr.length or 0) if tr.length else None
            if i is not None:
                self.dragging = True
                self.seek(i)
            elif self.frame is not None:
                uid = self.renderer.unit_at(event.pos[0], event.pos[1], self.frame, view)
                view.selected_uid = None if uid == view.selected_uid else uid
                self.dirty = True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        elif event.type == pygame.MOUSEWHEEL and not self.source.live:
            self.step(-event.y)

    def key(self, key: int, mod: int) -> None:
        tr, view = self.transport, self.view
        big = 20 if mod & pygame.KMOD_SHIFT else 1
        if key in (pygame.K_ESCAPE, pygame.K_q):
            self.running = False
        elif key == pygame.K_SPACE and not self.source.live:
            if tr.at_end:
                self.seek(0)
            tr.playing = not tr.playing
            self.due_ms = 0.0
        elif key == pygame.K_LEFT:
            self.step(-big)
        elif key == pygame.K_RIGHT:
            self.step(big)
        elif key == pygame.K_HOME:
            self.seek(0)
        elif key == pygame.K_END and tr.length:
            self.seek(tr.length - 1)
        elif key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS, pygame.K_RIGHTBRACKET):
            tr.speed = SPEEDS[min(len(SPEEDS) - 1, SPEEDS.index(tr.speed) + 1)]
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_LEFTBRACKET):
            tr.speed = SPEEDS[max(0, SPEEDS.index(tr.speed) - 1)]
        elif key == pygame.K_f:
            view.seat = 1 - view.seat
        elif key == pygame.K_p:
            view.show_paths = not view.show_paths
        elif key == pygame.K_t:
            view.show_targets = not view.show_targets
        elif key == pygame.K_g:
            view.show_grid = not view.show_grid
        elif key == pygame.K_d:
            view.show_debug = not view.show_debug
        elif key == pygame.K_b:
            view.show_footprints = not view.show_footprints
        elif key == pygame.K_c:
            view.show_compare = not view.show_compare
        elif key == pygame.K_h:
            view.show_help = not view.show_help
        elif key in (pygame.K_s, pygame.K_F12):
            self.shot_request = str(shot_path(self.source, self.frame, None))
        else:
            return
        self.dirty = True

    def step(self, delta: int) -> None:
        if self.source.live or not delta:
            return
        self.transport.playing = False
        before = self.source.index
        self.source.step(delta)
        self.transport.at_end = False
        self.due_ms = 0.0
        if self.source.index != before:
            self.pull()
        self.transport.index = self.source.index
        self.dirty = True

    # ------------------------------------------------------------------ drawing

    def draw(self, now: float) -> None:
        tr = self.transport
        tr.source_status = self.source.status()
        # The age of the status, not of the draw: a real iteration is minutes apart, so a
        # panel standing still is only alarming once you know how long it has stood.
        at = getattr(self.source, "learning_at", None)
        tr.learning_age_s = None if at is None else max(0.0, time.monotonic() - at)
        tr.index = self.source.index
        self.drawn_at = [t for t in self.drawn_at if now - t < 1.0]
        tr.fps = float(len(self.drawn_at))
        t0 = time.perf_counter()
        if self.frame is None:
            self.renderer.draw(empty_frame(self.source.units_per_tile), self.view, tr)
            ax, ay, aw, _ = self.renderer.layout.arena
            self.renderer.blit_text(
                f"{self.source.name}: waiting for the first frame",
                (ax + aw // 2, ay + 3 * self.renderer.layout.scale),
                "small",
                anchor="midtop",
                shadow=True,
            )
        else:
            self.renderer.draw(self.frame, self.view, tr)
        ms = (time.perf_counter() - t0) * 1000
        tr.draw_ms = ms
        self.draw_times.append(ms)
        self.drawn_at.append(now)
        self.last_draw_at = now
        self.dirty = False

    def stats(self) -> str:
        if not self.draw_times:
            return "royaleviser: 0 draws"
        n, mean, worst = (
            len(self.draw_times),
            sum(self.draw_times) / len(self.draw_times),
            max(self.draw_times),
        )
        return f"royaleviser: {n} draws, mean {mean:.2f} ms, max {worst:.2f} ms"


def run(
    sources: Source | Sequence[Source],
    view: ViewState | None = None,
    *,
    geometry: tuple[int, int, int | None, int | None] | None = None,
    seconds: float | None = None,
    shot: str | None = None,
    scale: int | None = None,
    speed: float = 1.0,
    start_tick: int | None = None,
    title: str = "RoyaleViser",
    theme: Theme = DEFAULT,
    follow_local: bool = False,
    tolerance: int = 0,
    pair_by_uid: bool = False,
) -> int:
    """Open the window over ``sources`` and run until quit, --seconds or a closed window.

    ``geometry`` is (w, h, x, y) as ``__main__.parse_geometry`` returns it: the window's
    OUTER rectangle in physical pixels (what a caller placing the window measures); x, y
    place the window and w, h pick the largest tile scale whose layout fits when ``scale``
    is None (``fit_layout``); the window then fills w x h, the layout at its top left and
    the UI background elsewhere, so it covers its slot. ``shot`` is written at exit (and
    S / F12 write one during the run).
    ``follow_local`` seats the main source's local side at the bottom as soon as the source
    knows it (--seat local; a live source learns it when a battle starts). Returns the
    exit code.
    """
    if isinstance(sources, Source):
        sources = [sources]
    view = view or ViewState()
    tiles = (18, 32)
    fitted = False
    dw, dh = frame_extras()

    def window_size() -> tuple[int, int]:
        """The layout's size, grown to the geometry's client area when one was given."""
        lw, lh = layout(theme, scale or theme.tile_px, tiles).window
        if geometry is None:
            return lw, lh
        return max(lw, geometry[0] - dw), max(lh, geometry[1] - dh)

    if geometry is not None:
        w, h, x, y = geometry
        if scale is None:
            (theme, scale), fitted = fit_layout(theme, tiles, w - dw, h - dh), True
        if x is not None and y is not None:
            os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"
    dpi_aware()
    pygame.init()
    surface = pygame.display.set_mode(window_size())
    if geometry is not None and x is not None and y is not None:
        measured = place_window(x, y)  # the real frame, now that a window exists
        if measured != (0, 0) and measured != (dw, dh):
            dw, dh = measured
            if fitted:
                theme, scale = fit_layout(theme, tiles, w - dw, h - dh)
            surface = pygame.display.set_mode(window_size())
            place_window(x, y)
    app = App(
        sources,
        view,
        scale=scale,
        speed=speed,
        theme=theme,
        title=title,
        follow_local=follow_local,
        tolerance=tolerance,
        pair_by_uid=pair_by_uid,
    )
    app.renderer.surface = surface
    pygame.display.set_caption(title)
    if start_tick is not None and not app.source.live:
        at = getattr(app.source, "index_at_tick", None)
        app.source.seek(at(start_tick) if at is not None else start_tick)
    app.pull()
    app.transport.index = app.source.index
    clock = pygame.time.Clock()
    started = time.perf_counter()
    last = started
    code = 0
    try:
        while app.running:
            now = time.perf_counter()
            dt_ms = (now - last) * 1000
            last = now
            for event in pygame.event.get():
                app.handle(event)
            if app.source.live:
                app.pull()
                if now - app.last_draw_at >= LIVE_REFRESH_S:
                    app.dirty = True  # the status line moves while no frame arrives
            else:
                app.advance(dt_ms)
            if app.shot_request is not None:
                if app.dirty:
                    app.draw(now)
                app.renderer.save(app.shot_request)
                print(f"royaleviser: saved {app.shot_request}")
                app.shot_request = None
            if app.dirty:
                app.draw(now)
                pygame.display.flip()
                clock.tick(FPS_CAP)
            else:
                pygame.time.wait(IDLE_WAIT_MS)
            if seconds is not None and now - started >= seconds:
                app.running = False
    except KeyboardInterrupt:
        code = 130
    finally:
        if shot is not None:
            empty = empty_shot_reason(app)
            if empty:
                print(f"royaleviser: no picture written to {shot}: {empty}", file=sys.stderr)
                code = code or 2
            else:
                if app.dirty or not app.draw_times:
                    app.draw(time.perf_counter())
                app.renderer.save(shot)
                print(f"royaleviser: saved {shot}")
        print(app.stats())
        for s in sources:
            s.close()
        pygame.quit()
    return code
