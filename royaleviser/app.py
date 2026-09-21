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


def compare_text(main: Frame, other: Frame) -> str:
    """'N entities, M differ' for two frames of the same tick."""
    return f"{len(other.units)} entities, {differing(signature(main), signature(other))} differ"


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
    """

    def __init__(self, restart_on_drop: bool = False) -> None:
        self.restart_on_drop = restart_on_drop
        self._sig: list[OrderedDict[int, tuple[Signature, int]]] = [OrderedDict(), OrderedDict()]
        self._last_tick: list[int | None] = [None, None]
        self.results: dict[int, tuple[int, int, int]] = {}  # tick -> (n main, n other, differ)
        self.ticks = 0
        self.differ = 0

    def reset(self) -> None:
        self._sig = [OrderedDict(), OrderedDict()]
        self._last_tick = [None, None]
        self.results.clear()
        self.ticks = self.differ = 0

    def note(self, which: int, frame: Frame) -> None:
        """Frame ``frame`` was shown by source ``which`` (0 main, 1 compare)."""
        last = self._last_tick[which]
        if self.restart_on_drop and last is not None and frame.tick < last - NEW_BATTLE_DROP:
            self.reset()
        self._last_tick[which] = frame.tick
        buf = self._sig[which]
        if frame.tick in buf or frame.tick in self.results:
            return
        buf[frame.tick] = (signature(frame), len(frame.units))
        while len(buf) > COMPARE_WINDOW:
            buf.popitem(last=False)
        if frame.tick in self._sig[1 - which]:
            (sa, na), (sb, nb) = self._sig[0][frame.tick], self._sig[1][frame.tick]
            d = differing(sa, sb)
            self.results[frame.tick] = (na, nb, d)
            self.ticks += 1
            self.differ += d > 0

    def text(self, tick: int | None) -> str:
        """Two lines: the comparison at ``tick`` (or why there is none) and the totals."""
        if tick is None:
            first = "no frame yet"
        elif tick in self.results:
            na, nb, d = self.results[tick]
            n = f"{na} entities" if na == nb else f"{na} vs {nb} entities"
            first = f"tick {tick}: {n}, {d} differ"
        else:
            first = f"tick {tick}: not seen by both"
        return f"{first}\n{self.ticks} ticks compared, {self.differ} differ"


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


def shot_path(source: Any, frame: Frame | None, explicit: str | None) -> Path:
    """--shot's path, else 'royaleviser-<source>-t<tick>.png' next to the source file (or cwd)."""
    if explicit:
        return Path(explicit)
    tick = frame.tick if frame is not None else 0
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(getattr(source, "name", "viser")))
    where = getattr(source, "path", None)
    folder = Path(where).parent if where else Path.cwd()
    return folder / f"royaleviser-{name}-t{tick}.png"


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
    ) -> None:
        if not sources:
            raise ValueError("run needs at least one source")
        self.source = sources[0]
        self.compare = sources[1] if len(sources) > 1 else None
        self.agreement = Compare(restart_on_drop=self.source.live)
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
        """The main source's current frame and the compare source's at the same tick."""
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
        sources, view, scale=scale, speed=speed, theme=theme, title=title, follow_local=follow_local
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
            if app.dirty or not app.draw_times:
                app.draw(time.perf_counter())
            app.renderer.save(shot)
            print(f"royaleviser: saved {shot}")
        print(app.stats())
        for s in sources:
            s.close()
        pygame.quit()
    return code
