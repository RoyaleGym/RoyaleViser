"""Drawing one ``model.Frame`` onto a pygame surface with the old renderer's layout.

    dashboard (theme.dashboard_w)  |  arena (tiles * scale)  |  inspector (theme.inspector_w)
    top player's hand + elixir     |  checkerboard grass,    |  hovered / selected unit:
    status + the events list       |  river band, bridges,   |  every field and Unit.extra
    bottom player's elixir + hand  |  tower zones, units,    |  compare panel, help footer
                                   |  spells, paths, timer   |
                                   |  status line, timeline  |

The bottom player is ``ViewState.seat``: the board is drawn rotated 180 degrees when the
seat is team 1 (what that client shows; measured 2026-09-18, RoyaleLive). Positions are the
frame's raw units divided by ``Frame.units_per_tile`` once, in ``to_px``. Colours are per
TEAM (team 0 blue, team 1 red) whatever the seat, like the old renderer. The renderer holds
no game data: a ``Theme``, a ``Layout``, the board geometry (``Board``) and its caches.

    r = Renderer(scale=24)                      # off-screen surface of layout.window
    r.surface = pygame.display.set_mode(r.layout.window)   # or keep the off-screen one
    r.set_arena(arena, arena.subtile)           # a royalegym.protocol.Arena (optional)
    r.draw(frame, view, transport)              # everything, every call
    r.save("shot.png")

PERFORMANCE (measured 2026-09-20 on a laptop, SDL dummy driver, scale 24): the static
board is rendered once per (seat, grid) into a Surface and blitted; text surfaces are
cached by (text, font, colour, shadow) in a bounded OrderedDict; translucent discs and the
card dimmer are cached by size; target lines are at most 16 dashes each. A 100-troop frame
with paths, targets and labels on: 5.1 ms mean on a quiet machine, 3.8-5.8 ms best-of-60
on a busy machine (mean then 9-11 ms from contention); the synthetic battle's
frames 2.7 ms mean; a real capture frame ~5 ms (tests/test_render.py prints the numbers).
Nothing here decides WHEN to draw: app.py redraws only when the frame or the view changed.

Layout note: ``theme.Layout.debug`` (between the two hand blocks) holds the status block
and the events list; ``Layout.hover`` is the inspector; ``Layout.events`` (the inspector's
lower half) holds the compare panel and the help footer.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # the viewer's stdout is the stats line

import pygame

from .model import (
    KIND_BUILDING,
    KIND_KING_TOWER,
    KIND_PRINCESS_TOWER,
    KIND_TROOP,
    UNKNOWN_HP,
    Frame,
    Learning,
    Player,
    Unit,
)
from .theme import DEFAULT, Color, Layout, Rect, Theme, layout

# protocol.SpellMotion values (model.py keeps the same rule: no royalegym import needed)
MOTION_FLIGHT = 0
MOTION_AIRBORNE = 1
MOTION_ROLLING = 2
MOTION_AREA = 3

KIND_NAMES = {
    KIND_TROOP: "troop",
    KIND_BUILDING: "building",
    KIND_KING_TOWER: "king tower",
    KIND_PRINCESS_TOWER: "princess tower",
}
# WHAT A BUILDING IS DRAWN AS
#
# A building and a tower stand on a BOX of whole tiles, and the box is the one thing about
# them a watcher can see: a Cannon covers three tiles by three. When a frame carries that box
# (``Unit.footprint``) the renderer draws it and nothing else decides the size.
#
# Everything below is the fallback for a frame that carries none, which every recording and
# every trace written before the field did. They are GUESSES, and a unit drawn from one is
# marked (``_draw_fallback_marks``) so that a wrong size on the board is visible as a wrong
# size rather than read as the engine's answer. The guess from the collision radius is what
# the viewer showed everywhere before 2026-09-22, and it drew a Cannon about one tile wide.
KING_TILES = 3
PRINCESS_TILES = 2
BUILDING_DEFAULT_TILES_X10 = 14  # a building with radius 0 draws 1.4 tiles wide
AREA_SPELL_TILES_X10 = 15  # ring radius for an AREA spell without a radius in extra
TEXT_CACHE_MAX = 4096


@dataclass(slots=True)
class ViewState:
    """What the viewer is looking at, apart from the frame. Owned by the app, read by draw()."""

    seat: int = 0  # 0 or 1: the team drawn at the bottom of the arena
    show_paths: bool = True  # unit paths (live path_nodes; the engine records none in a trace)
    show_targets: bool = True  # a line from each unit to its target
    show_grid: bool = False  # tile grid lines
    show_debug: bool = False  # raw numbers on the board: hp, state, deploy/stun ticks
    show_footprints: bool = False  # shade each carried footprint and the taps it refuses
    hover_uid: str | int | None = None  # the unit under the mouse, for the inspector
    compare_frame: Frame | None = None  # a second source's frame at the same tick (ghosted)
    selected_uid: str | int | None = None  # a clicked unit: the inspector sticks to it
    show_help: bool = False  # the key list in the inspector's footer
    show_compare: bool = True  # draw compare_frame's units (C toggles)
    compare_name: str = ""  # the compare source's name
    compare_text: str = ""  # "N entities, M differ", computed by the app


# ``Learning`` -- the learner's status -- is defined in model.py with the wire codec that
# carries it (``model.encode_learning``), because it arrives from outside this process the
# way a Frame does. It is imported above, and drawn only here.


#: (heading, ((label, attribute, format), ...)) in the order the panel fills its two columns.
LEARNING_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "learner",
        (
            ("iteration", "iteration", "d"),
            ("policy loss", "policy_loss", ".3f"),
            ("value loss", "value_loss", ".3f"),
            ("entropy", "entropy", ".3f"),
            ("KL", "kl", ".4f"),
            ("clip fraction", "clip_frac", ".1%"),
            ("explained var", "explained_var", ".2f"),
            ("grad norm", "grad_norm", ".2f"),
            ("learning rate", "learning_rate", ".1e"),
        ),
    ),
    (
        "rollout",
        (
            ("env steps/s", "env_steps_per_s", ",.0f"),
            ("engine ticks/s", "engine_ticks_per_s", ",.0f"),
            ("episode ticks", "episode_ticks", ".0f"),
            ("crowns / ep", "crowns_per_episode", ".2f"),
            ("towers / ep", "towers_per_episode", ".2f"),
            ("illegal actions", "illegal_rate", ".1%"),
            ("elixir wasted", "elixir_wasted", ".1f"),
        ),
    ),
    (
        "ladder",
        (
            ("ELO vs pool", "elo", ".0f"),
            ("win rate", "win_rate", ".1%"),
            ("pool size", "pool_size", "d"),
            ("games vs pool", "games_vs_pool", ",d"),
        ),
    ),
)

UNSET = "—"  # what a field with no value shows


def field_text(value: float | int | None, fmt: str) -> str:
    """One fixed row's value, in its own format.

    Two things the plain format gets wrong on a real run. A number that rounds away to
    nothing would print as "0.000" or, worse, "-0.000" -- which reads as a bug and hides
    whether it was a millionth or a thousandth, so a value that is not zero but formats as
    though it were is shown to one significant figure instead (measured on a real PPO
    iteration, 2026-09-22: a policy loss of -3e-05 drew as "-0.000"). And a value that IS
    zero never carries a minus sign, however the float is signed: an honest zero is "0.000".
    """
    if value is None:
        return UNSET
    if value == 0:
        return format(abs(value), fmt)
    text = format(value, fmt)
    if text.strip("0.,-%"):
        return text
    # Keep the unit the format carried: a percent row whose value rounds to nothing must not
    # come back as a bare fraction, or the number silently changes by a hundred.
    return f"{value * 100:.1e}%" if fmt.endswith("%") else f"{value:.1e}"


def extra_text(value: Any) -> str:
    """One of the learner's own ``Learning.extra`` rows, which has no format of its own: an
    integer with thousands, a float to four significant figures, anything else as it stands.
    None is unset here as everywhere, so a learner can carry a row it does not always have."""
    if value is None:
        return UNSET
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,d}"
    if isinstance(value, float):
        return f"{value:.4g}"
    # Never empty: an empty value is how this panel marks a group heading, so a learner's row
    # with nothing in it would draw as one.
    return str(value) or UNSET


def age_text(seconds: float | None) -> str:
    """How long ago the status arrived, short enough for a panel heading.

    A real iteration is MINUTES apart -- 8.9 of them on the laptop profile, measured
    2026-09-22 -- so a panel with no age on it cannot tell a run that is working from one
    that died half an hour ago: both show the same numbers, standing still. "8m" is the
    cadence; "47m" is a learner that is gone.
    """
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}"


def learning_run(ln: Learning | None, peer: str = "") -> str:
    """What the panel writes beside its "learning" heading.

    Whether a learner is attached and what it calls itself are two questions: a status with
    no run name is still a learner, so it gets the em dash of any unset field rather than
    the "nothing here" line standing over its own numbers.

    With nothing attached it NAMES THE PORT it is listening on, because the alarming case
    looks identical to the ordinary one. A learner whose status port was already taken --
    by another training run on the same machine, which is the usual cause -- publishes
    nothing and has no socket to say so on, while its environment may well have got its own
    port and be streaming a battle. The panel then reads "no learner" over a moving board.
    Naming the port turns that from a shrug into something a person can check.
    """
    if ln is not None:
        return ln.run or UNSET
    return f"no learner on {peer}" if peer else "no learner attached"


@dataclass(slots=True)
class Transport:
    """Playback and source facts for the status line and the timeline; owned by the app."""

    source_name: str = ""
    source_status: str = ""
    live: bool = False
    playing: bool = False
    speed: float = 1.0
    index: int = 0
    length: int | None = None
    at_end: bool = False  # a replay that reached its last frame ("end of capture")
    draw_ms: float = 0.0  # last draw, for the status block
    fps: float = 0.0  # wall-clock frames drawn per second (live: frames received)
    learning: Learning | None = None  # None: no learner attached
    learning_peer: str = ""  # where a learner would be heard, named when none is
    learning_age_s: float | None = None  # how long ago the status arrived, None if never


@dataclass(slots=True)
class Board:
    """The static board in HALF-CELLS (2 per tile, arena.json half_tiles_per_tile).

    ``water`` and ``bridge`` are half-cell sets; ``no_deploy`` too (their zones are outlined);
    ``king_zones`` / ``princess_zones`` are (hx0, hy0, hx1, hy1) half-cell rects, end
    exclusive. Native orientation: team 0's back edge is hy 0.
    """

    tiles_x: int
    tiles_y: int
    half: int
    water: set[tuple[int, int]]
    bridge: set[tuple[int, int]]
    no_deploy: set[tuple[int, int]]
    king_zones: list[tuple[int, int, int, int]]
    princess_zones: list[tuple[int, int, int, int]]

    @classmethod
    def from_arena(cls, arena: Any, arena_units_per_tile: int) -> Board:
        """From a royalegym.protocol.Arena (grid bits WATER 32 / NO_DEPLOY 16, centres)."""
        half = int(arena.half)
        hs = arena_units_per_tile // half
        water: set[tuple[int, int]] = set()
        bridge: set[tuple[int, int]] = set()
        no_deploy: set[tuple[int, int]] = set()
        w0, w1 = arena.water_half_rows
        for hy, row in enumerate(arena.grid):
            for hx, bits in enumerate(row):
                if bits & 32:
                    water.add((hx, hy))
                elif w0 <= hy <= w1:
                    bridge.add((hx, hy))  # a river cell that is not water is a bridge
                if bits & 16:
                    no_deploy.add((hx, hy))

        def zone(cx: int, cy: int, tiles: int) -> tuple[int, int, int, int]:
            h = tiles * half // 2
            return (cx // hs - h, cy // hs - h, cx // hs + h, cy // hs + h)

        return cls(
            tiles_x=int(arena.tiles_x),
            tiles_y=int(arena.tiles_y),
            half=half,
            water=water,
            bridge=bridge,
            no_deploy=no_deploy,
            king_zones=[zone(x, y, KING_TILES) for x, y in arena.king_centers],
            princess_zones=[
                zone(x, y, PRINCESS_TILES) for pair in arena.princess_centers for x, y in pair
            ],
        )

    @classmethod
    def builtin(cls) -> Board:
        """The 2026-09 arena.json numbers, for when royalegym is not importable.

        18x32 tiles, water half-rows 30..33, bridges at half-cols 5..8 and 27..30, kings
        3x3 at (9, 3) / (9, 29) tiles, princesses 2x2 at x 3.5 | 14.5, y 6.5 | 25.5.
        No no-deploy outlines (they come from the grid).
        """
        water = {(hx, hy) for hy in range(30, 34) for hx in range(36)}
        bridge = {(hx, hy) for hy in range(30, 34) for hx in (*range(5, 9), *range(27, 31))}
        water -= bridge
        return cls(
            tiles_x=18,
            tiles_y=32,
            half=2,
            water=water,
            bridge=bridge,
            no_deploy=set(),
            king_zones=[(15, 3, 21, 9), (15, 55, 21, 61)],
            princess_zones=[(5, 11, 9, 15), (27, 11, 31, 15), (27, 49, 31, 53), (5, 49, 9, 53)],
        )


def default_board() -> Board:
    """royalegym's default arena (RoyaleSim/data/derived/arena.json) or the built-in copy."""
    try:
        from royalegym.protocol import Arena, default_calibration
    except ImportError:
        return Board.builtin()
    arena = Arena.load(default_calibration())
    return Board.from_arena(arena, arena.subtile)


def split_name(name: str, limit: int) -> list[str]:
    """A card name in lines of at most ``limit`` characters, split at CamelCase / spaces."""
    if len(name) <= limit:
        return [name]
    words: list[str] = []
    for word in name.split():
        start = 0
        for i in range(1, len(word)):
            if word[i].isupper() and not word[i - 1].isupper():
                words.append(word[start:i])
                start = i
        words.append(word[start:])
    lines: list[str] = []
    for w in words:
        if lines and len(lines[-1]) + len(w) <= limit:
            lines[-1] += w
        else:
            lines.append(w[:limit])
    return lines


def footprint_note(frame: Frame) -> str:
    """What the status block says about the sizes on the board, or "" when nothing is guessed.

    Counting the guessed units is the whole of it: "3 of 9" is a fact about THIS frame that a
    reader can act on, where a fixed sentence about which sources carry footprints would be a
    claim about every frame the viewer will ever open, and would go stale the day a source
    starts carrying them.
    """
    solid = [u for u in frame.units if u.kind != KIND_TROOP]
    guessed = sum(1 for u in solid if u.footprint is None)
    if not guessed:
        return ""
    return f"{guessed} of {len(solid)} building sizes guessed"


def footprint_line(unit: Unit, units_per_tile: int) -> str:
    """The inspector's footprint row: the box in tiles, or why there is not one.

    A troop has none and says so; a building without one says the size on the board is the
    viewer's guess, in the same place a reader looks for the number.
    """
    if unit.footprint is None:
        return "-" if unit.kind == KIND_TROOP else "none in this frame (size guessed)"
    x0, y0, x1, y1 = unit.footprint
    w = tiles_x10(x1 - x0, units_per_tile)
    h = tiles_x10(y1 - y0, units_per_tile)
    return f"{w} x {h} tiles at ({tiles_x10(x0, units_per_tile)}, {tiles_x10(y0, units_per_tile)})"


def tiles_x10(v: int, units_per_tile: int) -> str:
    """Raw units as tiles to a tenth, without a float (sources.tiles_text's rule)."""
    tenths = (v * 10 + units_per_tile // 2) // units_per_tile
    return f"{tenths // 10}.{tenths % 10}"


def clock_text(tick: int, tick_ms: int) -> str:
    s = tick * tick_ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def elixir_text(milli: int) -> str:
    return f"{milli // 1000}.{milli % 1000:03d}"


class Renderer:
    """Draws frames at a fixed integer scale; owns the pygame fonts and caches it needs.

    ``surface`` is an off-screen Surface of ``layout.window`` until the app replaces it with
    the display surface. ``set_arena`` gives the board geometry (a royalegym.protocol.Arena
    and the unit its centres are in) so water, bridges and tower zones land on the right
    half-cells whatever the frame's units are; without it the default arena is drawn.
    """

    def __init__(
        self,
        scale: int | None = None,
        tiles: tuple[int, int] = (18, 32),
        theme: Theme = DEFAULT,
        help_lines: Sequence[tuple[str, str]] = (),
    ) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self.theme = theme
        self.layout: Layout = layout(theme, scale or theme.tile_px, tiles)
        self.tiles = tiles
        self.help_lines = list(help_lines)
        self.surface: pygame.Surface = pygame.Surface(self.layout.window)
        self.fonts = {
            "large": pygame.font.Font(theme.font_name, theme.font_large),
            "small": pygame.font.Font(theme.font_name, theme.font_small),
            "tiny": pygame.font.Font(theme.font_name, theme.font_tiny),
            "mono": pygame.font.SysFont(theme.font_mono_name, theme.font_mono),
        }
        self.line_h = {k: f.get_linesize() for k, f in self.fonts.items()}
        self._board: Board | None = None
        self._board_cache: dict[tuple[int, bool], pygame.Surface] = {}
        # OrderedDict, not dict: evicting the oldest entry must stay O(1) (a plain dict's
        # next(iter()) walks every deleted slot at its front once the cache churns).
        self._text_cache: OrderedDict[tuple[str, str, Color, bool], pygame.Surface] = OrderedDict()
        self._disc_cache: dict[tuple[int, tuple[int, int, int, int]], pygame.Surface] = {}
        self._dim_cache: dict[tuple[int, int, int], pygame.Surface] = {}
        # name -> elixir cost for the hand badges and the "unaffordable" dimming; the app
        # installs a source's Names.cost_of_name. None: no badge, no dimming.
        self.cost_of: Callable[[str], int | None] = lambda name: None
        self._cost_cache: dict[str, int | None] = {}

    # ------------------------------------------------------------------ geometry

    def set_arena(self, arena: Any, arena_units_per_tile: int) -> None:
        """The board to draw: royalegym.protocol.Arena and the unit its centres are in."""
        self._board = Board.from_arena(arena, arena_units_per_tile)
        self._board_cache.clear()

    def set_board(self, board: Board) -> None:
        self._board = board
        self._board_cache.clear()

    @property
    def board(self) -> Board:
        if self._board is None:
            self._board = default_board()
        return self._board

    def to_px(self, x: int, y: int, units_per_tile: int, seat: int) -> tuple[int, int]:
        """Raw native/engine units -> window pixels, seat 1 rotated 180 degrees."""
        ax, ay, aw, ah = self.layout.arena
        s = self.layout.scale
        px = x * s // units_per_tile
        py = y * s // units_per_tile
        if seat == 0:
            return ax + px, ay + ah - py
        return ax + aw - px, ay + py

    def px_len(self, raw: int, units_per_tile: int) -> int:
        return raw * self.layout.scale // units_per_tile

    def unit_radius_px(self, unit: Unit, units_per_tile: int) -> int:
        """Half the drawn size: the circle radius or half the square side, in pixels.

        For a building or a tower this is the FALLBACK size, used only when the frame carries
        no footprint (see ``footprint_px``); the numbers it reads are the collision radius and
        the two tower constants, none of which is the box the unit stands on.
        """
        s = self.layout.scale
        if unit.kind == KIND_KING_TOWER:
            return KING_TILES * s // 2
        if unit.kind == KIND_PRINCESS_TOWER:
            return PRINCESS_TILES * s // 2
        if unit.radius > 0:
            r = self.px_len(unit.radius, units_per_tile)
        elif unit.kind == KIND_BUILDING:
            r = BUILDING_DEFAULT_TILES_X10 * s // 20
        else:
            r = self.theme.unit_default_radius_tiles_x100 * s // 100
        return max(r, self.theme.unit_min_px)

    def box_px(self, box: tuple[int, int, int, int], units_per_tile: int, seat: int) -> pygame.Rect:
        """A raw-unit box [x0, y0, x1, y1] as a window rect, whatever the seat.

        The seat turns the board 180 degrees, which swaps which corner is which, so both
        corners are projected and the rect is taken from their extremes rather than from one
        corner and a width. A box of three tiles comes out three tiles wide on either seat.
        """
        x0, y0, x1, y1 = box
        ax, ay = self.to_px(x0, y0, units_per_tile, seat)
        bx, by = self.to_px(x1, y1, units_per_tile, seat)
        left, right = min(ax, bx), max(ax, bx)
        top, bottom = min(ay, by), max(ay, by)
        return pygame.Rect(left, top, right - left, bottom - top)

    def footprint_px(self, unit: Unit, units_per_tile: int, seat: int) -> pygame.Rect | None:
        """The box the frame says ``unit`` stands on, in window pixels, or None if it carries
        none. Nothing here invents a size: a unit without a footprint gets the marked fallback."""
        if unit.footprint is None:
            return None
        return self.box_px(tuple(unit.footprint), units_per_tile, seat)

    def unit_rect_px(self, unit: Unit, units_per_tile: int, seat: int) -> pygame.Rect:
        """Where a non-troop unit is drawn: its carried footprint, else the fallback square."""
        rect = self.footprint_px(unit, units_per_tile, seat)
        if rect is not None:
            return rect
        px, py = self.to_px(unit.x, unit.y, units_per_tile, seat)
        r = self.unit_radius_px(unit, units_per_tile)
        return pygame.Rect(px - r, py - r, 2 * r, 2 * r)

    def unit_at(self, px: int, py: int, frame: Frame, view: ViewState) -> str | int | None:
        """The uid of the unit under the pixel: the shape it is DRAWN as, nearest centre first.

        A troop is its disc. A building or tower is the rect it was drawn as, so a 3x3 Cannon
        is clickable over all nine tiles and a unit standing on the pocket behind a tower is
        not picked up from the tower's far corner.
        """
        best: tuple[int, str | int] | None = None
        upt = frame.units_per_tile
        for u in frame.units:
            ux, uy = self.to_px(u.x, u.y, upt, view.seat)
            d2 = (ux - px) ** 2 + (uy - py) ** 2
            if u.kind == KIND_TROOP:
                r = self.unit_radius_px(u, upt) + 3
                hit = d2 <= r * r
            else:
                hit = self.unit_rect_px(u, upt, view.seat).inflate(6, 6).collidepoint(px, py)
            if hit and (best is None or d2 < best[0]):
                best = (d2, u.uid)
        return best[1] if best is not None else None

    def timeline_index_at(self, px: int, py: int, length: int) -> int | None:
        """Frame index for a click on the scrub bar, None when the pixel is off the bar."""
        x, y, w, h = self.layout.timeline
        if length <= 0 or not (x <= px < x + w and y - 2 <= py < y + h + 2):
            return None
        return max(0, min(length - 1, (px - x) * (length - 1) // max(1, w - 1)))

    def save(self, path: str) -> None:
        """Write the current surface as PNG (--shot)."""
        pygame.image.save(self.surface, path)

    # ------------------------------------------------------------------ caches

    def text(
        self, s: str, font: str = "tiny", color: Color | None = None, shadow: bool = False
    ) -> pygame.Surface:
        """A rendered line, cached by (text, font, colour, shadow); the shadow is baked in."""
        color = color or self.theme.ui_text
        key = (s, font, color, shadow)
        surf = self._text_cache.get(key)
        if surf is None:
            surf = self.fonts[font].render(s, True, color)
            if shadow:
                dark = self.fonts[font].render(s, True, (0, 0, 0))
                w, h = surf.get_size()
                both = pygame.Surface((w + 1, h + 1), pygame.SRCALPHA)
                both.blit(dark, (1, 1))
                both.blit(surf, (0, 0))
                surf = both
            if len(self._text_cache) >= TEXT_CACHE_MAX:
                self._text_cache.popitem(last=False)
            self._text_cache[key] = surf
        return surf

    def blit_text(
        self,
        s: str,
        pos: tuple[int, int],
        font: str = "tiny",
        color: Color | None = None,
        anchor: str = "topleft",
        shadow: bool = False,
    ) -> pygame.Rect:
        surf = self.text(s, font, color, shadow)
        rect = surf.get_rect(**{anchor: pos})
        self.surface.blit(surf, rect)
        return rect

    def disc(self, radius: int, rgba: tuple[int, int, int, int]) -> pygame.Surface:
        key = (radius, rgba)
        surf = self._disc_cache.get(key)
        if surf is None:
            surf = pygame.Surface((2 * radius + 2, 2 * radius + 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, rgba, (radius + 1, radius + 1), radius)
            self._disc_cache[key] = surf
        return surf

    def dimmer(self, w: int, h: int, alpha: int = 110) -> pygame.Surface:
        key = (w, h, alpha)
        surf = self._dim_cache.get(key)
        if surf is None:
            surf = pygame.Surface((w, h), pygame.SRCALPHA)
            surf.fill((0, 0, 0, alpha))
            self._dim_cache[key] = surf
        return surf

    # ------------------------------------------------------------------ the board

    def board_surface(self, seat: int, grid: bool) -> pygame.Surface:
        key = (seat, grid)
        surf = self._board_cache.get(key)
        if surf is None:
            surf = self._render_board(grid)
            if seat == 1:
                surf = pygame.transform.rotate(surf, 180)
            self._board_cache[key] = surf
        return surf

    def _render_board(self, grid: bool) -> pygame.Surface:
        """Native orientation (team 0 at the bottom); rotated once for seat 1."""
        t = self.theme
        b = self.board
        s = self.layout.scale
        _, _, aw, ah = self.layout.arena
        surf = pygame.Surface((aw, ah))
        tiles_x, tiles_y = b.tiles_x, b.tiles_y
        rect = pygame.draw.rect
        for ty in range(tiles_y):
            top = ah - (ty + 1) * s
            for tx in range(tiles_x):
                color = t.grass_light if (tx + ty) % 2 == 0 else t.grass_dark
                rect(surf, color, (tx * s, top, s, s))

        def cell(hx: int, hy: int) -> tuple[int, int, int, int]:
            x0 = hx * s // b.half
            x1 = (hx + 1) * s // b.half
            y1 = ah - hy * s // b.half
            y0 = ah - (hy + 1) * s // b.half
            return (x0, y0, x1 - x0, y1 - y0)

        for hx, hy in b.water:
            rect(surf, t.river, cell(hx, hy))
        for hx, hy in b.bridge:
            rect(surf, t.bridge, cell(hx, hy))
        if grid:
            for tx in range(1, tiles_x):
                pygame.draw.line(surf, t.grid_line, (tx * s, 0), (tx * s, ah - 1))
            for ty in range(1, tiles_y):
                pygame.draw.line(surf, t.grid_line, (0, ty * s), (aw - 1, ty * s))
        # No-deploy zones: the edges of the cell set, so each zone gets one faint outline.
        line = pygame.draw.line
        for hx, hy in b.no_deploy:
            x, y, w, h = cell(hx, hy)
            if (hx - 1, hy) not in b.no_deploy:
                line(surf, t.tower_zone, (x, y), (x, y + h - 1), t.tower_zone_w)
            if (hx + 1, hy) not in b.no_deploy:
                line(surf, t.tower_zone, (x + w - 1, y), (x + w - 1, y + h - 1), t.tower_zone_w)
            if (hx, hy - 1) not in b.no_deploy:
                line(surf, t.tower_zone, (x, y + h - 1), (x + w - 1, y + h - 1), t.tower_zone_w)
            if (hx, hy + 1) not in b.no_deploy:
                line(surf, t.tower_zone, (x, y), (x + w - 1, y), t.tower_zone_w)
        for hx0, hy0, hx1, hy1 in (*b.king_zones, *b.princess_zones):
            x0 = hx0 * s // b.half
            x1 = hx1 * s // b.half
            y0 = ah - hy1 * s // b.half
            y1 = ah - hy0 * s // b.half
            rect(surf, t.tower_zone, (x0, y0, x1 - x0, y1 - y0), t.tower_zone_w)
        return surf

    # ------------------------------------------------------------------ draw

    def draw(self, frame: Frame, view: ViewState, transport: Transport | None = None) -> None:
        """Redraw everything for ``frame`` into ``surface``: board, dashboard, inspector."""
        transport = transport or Transport()
        self.surface.fill(self.theme.ui_bg)
        self.surface.blit(self.board_surface(view.seat, view.show_grid), self.layout.arena[:2])
        self.surface.set_clip(pygame.Rect(self.layout.arena))
        self._draw_units(frame, view)
        # Over the units, not under them: a building is drawn ON its own footprint, so an
        # overlay underneath would be covered by the very thing it is there to describe.
        if view.show_footprints:
            self._draw_footprint_overlay(frame, view)
        self._draw_spells(frame, view)
        if view.compare_frame is not None and view.show_compare:
            self._draw_compare(view.compare_frame, view.seat)
        self.surface.set_clip(None)
        self._draw_arena_hud(frame, view)
        self._draw_dashboard(frame, view, transport)
        self._draw_status_line(transport)
        self._draw_timeline(transport)
        if self.layout.inspector[2]:  # the compact layout has no inspector column
            self._draw_inspector(frame, view)
            self._draw_footer(frame, view)

    # ---- arena

    def _draw_units(self, frame: Frame, view: ViewState) -> None:
        t = self.theme
        surface = self.surface
        upt = frame.units_per_tile
        seat = view.seat
        s = self.layout.scale
        to_px = self.to_px
        pos: dict[str | int, tuple[int, int]] = {}
        radii: dict[str | int, int] = {}
        for u in frame.units:
            pos[u.uid] = to_px(u.x, u.y, upt, seat)
            radii[u.uid] = self.unit_radius_px(u, upt)
        # The hp bar, the labels and the overlays hang off the drawn shape, so a building's
        # half-height comes from the rect it is actually drawn as, not from the fallback radius.
        half: dict[str | int, int] = {}
        half_w: dict[str | int, int] = {}
        for u in frame.units:
            if u.kind == KIND_TROOP:
                half[u.uid] = half_w[u.uid] = radii[u.uid]
            else:
                box = self.unit_rect_px(u, upt, seat)
                half[u.uid], half_w[u.uid] = box.height // 2, box.width // 2
        # Paths and target lines under everything so units stay readable.
        if view.show_paths:
            for u in frame.units:
                if len(u.path) >= 1:
                    pts = [pos[u.uid]] + [to_px(x, y, upt, seat) for x, y in u.path]
                    pygame.draw.lines(surface, t.team_color(u.team), False, pts, 1)
                    pygame.draw.circle(surface, t.team_color(u.team), pts[-1], 3, 1)
        if view.show_targets:
            for u in frame.units:
                if u.target is not None and u.target in pos and u.target != u.uid:
                    dashed_line(surface, t.target_line, pos[u.uid], pos[u.target])
        # Towers and buildings first (troops walk over them), then troops.
        order = sorted(frame.units, key=lambda u: u.kind == KIND_TROOP)
        for u in order:
            px, py = pos[u.uid]
            r = radii[u.uid]
            color = t.team_color(u.team, king=u.kind == KIND_KING_TOWER)
            if u.kind == KIND_TROOP:
                if u.flying:
                    surface.blit(self.disc(r, (0, 0, 0, 90)), (px - r + 2, py - r + 4))
                pygame.draw.circle(surface, color, (px, py), r)
                pygame.draw.circle(surface, t.troop_outline, (px, py), r, 1)
                if u.flying:
                    pygame.draw.circle(surface, (255, 255, 255), (px, py), r + 3, 1)
                if u.direction is not None and (u.direction[0] or u.direction[1]):
                    dx, dy = u.direction
                    if seat == 0:
                        dy = -dy
                    else:
                        dx = -dx
                    end = (px + dx * (r + 4) // 256, py + dy * (r + 4) // 256)
                    pygame.draw.line(surface, t.direction_line, (px, py), end, 2)
            else:
                # The box and the circle are two different real quantities, so they are drawn
                # as two different shapes, and WHICH ONE IS FILLED says which is the thing
                # itself. The circle is the collision radius: what other units and other
                # buildings actually run into, and so the solid body. The box is the ground it
                # stands on, which is a fact about the board rather than about the unit, so it
                # is an outline around that body. They are not the same size and never were: a
                # Cannon's box is 3 tiles and its radius 0.6.
                rect = self.unit_rect_px(u, upt, seat)
                body = self.px_len(u.radius, upt) if u.radius > 0 else 0
                # The team's colour is the OUTERMOST ring, so the drawn extent is the
                # footprint exactly; a dark hairline over the top of it would eat a pixel at
                # each edge and the box would read two pixels narrower than the ground it
                # stands for.
                pygame.draw.rect(surface, color, rect, 2)
                if body > 0:
                    pygame.draw.circle(surface, color, (px, py), body)
                    pygame.draw.circle(surface, t.collision_circle, (px, py), body, 1)
                else:
                    # No radius, so there is no body to draw and an outline alone would be a
                    # building you can see through. Every recording is this case: the box is
                    # filled instead, and the marks below say its size was guessed.
                    pygame.draw.rect(surface, color, rect.inflate(-4, -4))
                    pygame.draw.rect(surface, t.building_outline, rect.inflate(-4, -4), 1)
                if u.footprint is None:
                    self._draw_fallback_marks(rect)
            h, hw = half[u.uid], half_w[u.uid]
            if u.deploy_ticks > 0:
                # Over the whole of what was drawn. It used to be a disc of the fallback
                # radius, so a 3x3 Cannon deploying wore a 30 px veil in the middle of a 72 px
                # building and read as a building that was already up.
                d = max(h, hw)
                surface.blit(self.disc(d + 1, (*t.deploy_overlay, 140)), (px - d - 2, py - d - 2))
            if u.deploy_ticks > 1:
                # A real countdown. A recording carries only the deploying STATE, which
                # sources.py encodes as 1 tick: the overlay without a number.
                self.blit_text(
                    f"{u.deploy_ticks * frame.tick_ms / 1000:.1f}",
                    (px, py),
                    "tiny",
                    (0, 0, 0),
                    "center",
                )
            if u.stun_ticks > 0:
                pygame.draw.circle(surface, t.stun_overlay, (px, py), h + 3, 2)
            if u.uid == view.selected_uid or u.uid == view.hover_uid:
                if u.kind == KIND_TROOP:
                    pygame.draw.circle(surface, t.hover, (px, py), r + 6, 2)
                else:
                    pygame.draw.rect(
                        surface, t.hover, self.unit_rect_px(u, upt, seat).inflate(10, 10), 2
                    )
            if u.max_hp > 0 and 0 <= u.hp < u.max_hp:
                # As wide as the thing it belongs to. It used to be twice the fallback radius,
                # which put a 28 px bar over a 72 px Cannon.
                w = max(16, 2 * hw)
                bar = (px - w // 2, py - h - 3 - t.hp_bar_h, w, t.hp_bar_h)
                pygame.draw.rect(surface, t.hp_bg, bar)
                fill = w * u.hp // u.max_hp
                if fill > 0:
                    pygame.draw.rect(
                        surface, t.hp_color(u.hp, u.max_hp), (bar[0], bar[1], fill, bar[3])
                    )
            if u.kind == KIND_TROOP or u.kind == KIND_BUILDING or view.show_debug:
                label = u.name if s >= 16 else ""
                if label:
                    self.blit_text(
                        label, (px, py + h + 2), "tiny", t.ui_text, "midtop", shadow=True
                    )
            if view.show_debug:
                hp = "?" if u.hp == UNKNOWN_HP else str(u.hp)
                dbg = f"{hp}/{u.max_hp} st{u.state if u.state is not None else '-'}"
                if u.deploy_ticks or u.stun_ticks:
                    dbg += f" d{u.deploy_ticks} k{u.stun_ticks}"
                self.blit_text(
                    dbg,
                    (px, py + h + 2 + self.line_h["tiny"]),
                    "tiny",
                    t.hover,
                    "midtop",
                    shadow=True,
                )

    def _draw_fallback_marks(self, rect: pygame.Rect) -> None:
        """The corner ticks on a building drawn at a GUESSED size (no footprint in the frame).

        A wrong size drawn plainly is indistinguishable from the right one, and the Cannon
        this viewer drew one tile wide sat on the board for days looking like a fact. The
        marks cost four short lines and say "this rectangle is not from the frame" at a glance;
        the status line counts them, so the reason is one look away and not a guess.
        """
        t = self.theme
        n = max(3, min(rect.w, rect.h) // 3)
        line = pygame.draw.line
        for cx, sx in ((rect.left, 1), (rect.right - 1, -1)):
            for cy, sy in ((rect.top, 1), (rect.bottom - 1, -1)):
                line(self.surface, t.footprint_guess, (cx, cy), (cx + sx * n, cy), 2)
                line(self.surface, t.footprint_guess, (cx, cy), (cx, cy + sy * n), 2)

    def _draw_footprint_overlay(self, frame: Frame, view: ViewState) -> None:
        """Over the units: every carried footprint shaded, and the tile taps it refuses.

        Over, not under, because a building is drawn ON its own footprint and an overlay
        underneath would be covered by the very thing it describes (the call site says the
        same, and the two used to disagree).

        A tile is drawn as refused when its CENTRE lies inside a footprint, which is the rule
        the engine applies to the tap point itself. It is not the whole of deploy legality:
        a building also brings its own size to the test, and the viewer does not know which
        card a player is about to play. So this is the part of the answer that the frame
        alone settles, and the status line says which frame it came from. Towers count as
        much as buildings, because their box is what a placement runs into around the bridge.
        """
        t = self.theme
        upt = frame.units_per_tile
        s = self.layout.scale
        boxes = [
            (u, self.box_px(tuple(u.footprint), upt, view.seat))
            for u in frame.units
            if u.footprint is not None
        ]
        for u, rect in boxes:
            shade = pygame.Surface(rect.size, pygame.SRCALPHA)
            shade.fill((*t.team_color(u.team), 70))
            self.surface.blit(shade, rect.topleft)
            pygame.draw.rect(self.surface, t.footprint_edge, rect, 1)
        if not boxes:
            return
        # Tile centres, in the same pixel frame the boxes are in: the arena is a whole number
        # of tiles, so the centre of tile (tx, ty) is half a tile in from its corner.
        ax, ay, aw, ah = self.layout.arena
        # Opaque, not blended: this mark is the overlay's answer about one tile, and a
        # translucent one takes the colour of whatever it lands on, which is a building.
        side = max(2, s // 4)
        for ty in range(ah // s):
            for tx in range(aw // s):
                cx, cy = ax + tx * s + s // 2, ay + ty * s + s // 2
                if any(r.collidepoint(cx, cy) for _, r in boxes):
                    pygame.draw.rect(
                        self.surface,
                        t.footprint_refused,
                        (cx - side // 2, cy - side // 2, side, side),
                    )

    def _draw_spells(self, frame: Frame, view: ViewState) -> None:
        t = self.theme
        surface = self.surface
        upt = frame.units_per_tile
        s = self.layout.scale
        for sp in frame.spells:
            px, py = self.to_px(sp.x, sp.y, upt, view.seat)
            if sp.motion == MOTION_AREA:
                radius = sp.extra.get("radius")
                r = self.px_len(int(radius), upt) if radius else AREA_SPELL_TILES_X10 * s // 10
                pygame.draw.circle(surface, t.spell, (px, py), max(r, 4), 2)
                pygame.draw.circle(surface, t.spell, (px, py), 3)
            else:
                ax, ay = self.to_px(sp.aim_x, sp.aim_y, upt, view.seat)
                dashed_line(surface, t.spell, (px, py), (ax, ay))
                pygame.draw.circle(surface, t.spell, (ax, ay), 5, 1)
                pygame.draw.circle(surface, t.projectile, (px, py), 4)
            self.blit_text(sp.name, (px, py + 6), "tiny", t.spell, "midtop", shadow=True)

    def _draw_compare(self, other: Frame, seat: int) -> None:
        upt = other.units_per_tile
        white = (255, 255, 255)
        for u in other.units:
            if u.kind == KIND_TROOP:
                px, py = self.to_px(u.x, u.y, upt, seat)
                pygame.draw.circle(self.surface, white, (px, py), self.unit_radius_px(u, upt), 1)
            else:
                pygame.draw.rect(self.surface, white, self.unit_rect_px(u, upt, seat), 1)

    def _draw_arena_hud(self, frame: Frame, view: ViewState) -> None:
        """Timer + crowns box top right, OVERTIME / GAME OVER centred (the old HUD)."""
        t = self.theme
        ax, ay, aw, _ = self.layout.arena
        x, y, w, h = self.layout.timer
        pygame.draw.rect(self.surface, (40, 40, 40), (x, y, w, h))
        pygame.draw.rect(self.surface, (80, 80, 80), (x, y, w, h), 1)
        top, bottom = 1 - view.seat, view.seat
        clock = clock_text(frame.tick, frame.tick_ms)
        cy = y + h // 2 - self.line_h["tiny"] // 2 + 1
        self.blit_text(clock, (x + w // 2, cy), "tiny", t.ui_text, "midtop")
        self.blit_text(str(frame.crowns[top]), (x + 5, cy), "tiny", t.team_color(top), "topleft")
        self.blit_text(
            str(frame.crowns[bottom]), (x + w - 5, cy), "tiny", t.team_color(bottom), "topright"
        )
        if frame.overtime and not frame.game_over:
            self.blit_text(
                "OVERTIME", (ax + aw // 2, ay + 6), "small", t.ui_warn, "midtop", shadow=True
            )
        if frame.game_over:
            if frame.winner in (0, 1):
                who = f"{t.team_name[frame.winner]} wins"
                color = t.team_color(frame.winner)
            else:
                who = "draw"
                color = t.ui_text
            cx, cyy = ax + aw // 2, ay + self.layout.arena[3] // 2
            self.blit_text("GAME OVER", (cx, cyy - 4), "large", t.ui_warn, "midbottom", shadow=True)
            self.blit_text(who, (cx, cyy + 4), "small", color, "midtop", shadow=True)

    # ---- dashboard

    def _draw_dashboard(self, frame: Frame, view: ViewState, transport: Transport) -> None:
        lay = self.layout
        top_team, bottom_team = 1 - view.seat, view.seat
        top, bottom = frame.player(top_team), frame.player(bottom_team)
        self._draw_hand(top, lay.top_hand)
        self._draw_elixir_row(top, lay.top_elixir)
        self._draw_elixir_row(bottom, lay.bottom_elixir)
        self._draw_hand(bottom, lay.bottom_hand)
        log_bottom = self._draw_status_block(frame, lay.debug, transport, view)
        self._draw_learning_block(
            lay.debug,
            log_bottom,
            transport.learning,
            transport.learning_peer,
            transport.learning_age_s,
        )

    def card_cost(self, name: str) -> int | None:
        if name not in self._cost_cache:
            self._cost_cache[name] = self.cost_of(name)
        return self._cost_cache[name]

    def _draw_hand(self, p: Player, rect: Rect) -> None:
        """Four 80x100 boxes filling ``rect`` exactly: name, cost badge, dimmed when
        unaffordable, hatched when unknown."""
        t = self.theme
        x, y, _, _ = rect
        cw, ch, gap = t.card_w, t.card_h, t.card_gap
        for i in range(4):
            cx = x + i * (cw + gap)
            box = pygame.Rect(cx, y, cw, ch)
            if not p.hand_known:
                pygame.draw.rect(self.surface, t.card_unknown, box)
                self.surface.set_clip(box)
                for k in range(-ch, cw, 10):
                    pygame.draw.line(self.surface, t.ui_border, (cx + k, y), (cx + k + ch, y + ch))
                self.surface.set_clip(None)
                pygame.draw.rect(self.surface, t.ui_border, box, 1)
                self.blit_text("?", box.center, "large", t.ui_dim, "center")
                continue
            name = p.hand[i] if i < len(p.hand) else "?"
            pygame.draw.rect(self.surface, t.card_bg, box)
            pygame.draw.rect(self.surface, t.card_border, box, 2)
            ly = y + 5
            for line in split_name(name, 10)[:2]:
                self.blit_text(line, (cx + cw // 2, ly), "tiny", t.card_text, "midtop")
                ly += self.line_h["tiny"] - 4
            cost = self.card_cost(name)
            if cost is not None:
                pygame.draw.circle(self.surface, t.elixir, (cx + 14, y + ch - 14), 11)
                self.blit_text(str(cost), (cx + 14, y + ch - 14), "tiny", t.ui_text, "center")
                if p.elixir_known and p.elixir_milli < cost * 1000:
                    self.surface.blit(self.dimmer(cw, ch), box.topleft)

    def _draw_elixir_row(self, p: Player, rect: Rect) -> None:
        t = self.theme
        x, y, _, h = rect
        bar_w, bar_h = 200, h - 8
        bar = (x, y + 4, bar_w, bar_h)
        pygame.draw.rect(self.surface, (50, 50, 50), bar)
        seg = (bar_w - 4 - 9) // 10
        if p.elixir_known:
            full, part = p.elixir_milli // 1000, p.elixir_milli % 1000
            for i in range(min(full, 10)):
                pygame.draw.rect(
                    self.surface, t.elixir, (x + 2 + i * (seg + 1), y + 6, seg, bar_h - 4)
                )
            if full < 10 and part:
                pygame.draw.rect(
                    self.surface,
                    t.elixir,
                    (x + 2 + full * (seg + 1), y + 6, seg * part // 1000, bar_h - 4),
                )
            label = f"{elixir_text(p.elixir_milli)} / 10"
        else:
            label = "elixir: ?"
        pygame.draw.rect(self.surface, t.ui_border, bar, 2)
        self.blit_text(
            label, (x + bar_w // 2, y + h // 2), "tiny", t.ui_text, "center", shadow=True
        )
        rx = x + bar_w + 8
        if not p.hand_known:
            self.blit_text("hand: not", (rx, y + 2), "tiny", t.ui_warn)
            self.blit_text(
                "in this source", (rx, y + 2 + self.line_h["tiny"] - 3), "tiny", t.ui_warn
            )
        else:  # a trace under a shuffle mode may not know the queue's front yet: "?"
            self.blit_text("next", (rx, y + 2), "tiny", t.ui_dim)
            self.blit_text(
                p.next_card or "?", (rx, y + 2 + self.line_h["tiny"] - 3), "tiny", t.ui_text
            )

    def outside_arena(self, frame: Frame) -> list[str]:
        """One line per unit whose CARRIED box runs off the board, newest board geometry.

        This is the shape of the defect the owner found by looking at the window: a building
        standing where its own box does not fit. Whether a placement was legal is the engine's
        answer, and the viewer does not have the rule -- but "this box is not inside the arena"
        needs only the box and the board, both of which are in front of it, so the window can
        say it instead of drawing it and leaving it to be noticed.
        """
        b = self.board
        upt = frame.units_per_tile
        w, h = b.tiles_x * upt, b.tiles_y * upt
        out = []
        for u in frame.units:
            if u.footprint is None:
                continue
            x0, y0, x1, y1 = u.footprint
            if min(x0, x1) < 0 or max(x0, x1) > w or min(y0, y1) < 0 or max(y0, y1) > h:
                out.append(f"{u.name}'s box leaves the arena")
        return out

    def notes(self, frame: Frame, tr: Transport) -> list[str]:
        """The status block's warning lines for this frame: what the board cannot be trusted on.

        Three kinds, and all of them are about the window lying quietly rather than loudly: a
        guessed size looks like a measured one, a box that runs off the board looks like a
        placement the engine allowed, and frames from one run under a status from another look
        like one run.
        """
        out = []
        note = footprint_note(frame)
        if note:
            out.append(note)
        out.extend(self.outside_arena(frame))
        frame_run = str(frame.meta.get("run") or "")
        status_run = tr.learning.run if tr.learning is not None else ""
        if frame_run and status_run and frame_run != status_run:
            out.append(f"frames {frame_run} / learner {status_run}")
        return out

    def _draw_status_block(self, frame: Frame, rect: Rect, tr: Transport, view: ViewState) -> int:
        """Draws the source, the transport lines and the match log; returns its bottom y."""
        t = self.theme
        x, y, w, h = rect
        lh = self.line_h["tiny"]
        mono_h = self.line_h["mono"]
        notes = self.notes(frame, tr)
        # The panel is as tall as its content, not the column: the source, three status lines,
        # any warning line, a rule, and at most ``events_lines`` events. What the column has
        # left stays dark.
        head_h = 4 + self.line_h["small"] + (4 + len(notes)) * lh + 6 + 4 + lh
        n = max(0, min(t.events_lines, (h - head_h) // mono_h))
        if not self.layout.inspector[2] and view.compare_name:
            n = max(0, min(n, (h - head_h - 2 * lh) // mono_h))
        rect = (x, y, w, min(h, head_h + n * mono_h + 4))
        pygame.draw.rect(self.surface, t.ui_panel, rect)
        cy = y + 4
        self.blit_text(
            fit_text(tr.source_name or "-", self.fonts["small"], w - 8), (x + 4, cy), "small"
        )
        cy += self.line_h["small"]
        clock = clock_text(frame.tick, frame.tick_ms)
        self.blit_text(f"tick {frame.tick}   {clock}", (x + 4, cy), "tiny")
        cy += lh
        if frame.game_over:
            who = t.team_name[frame.winner] + " wins" if frame.winner in (0, 1) else "draw"
            self.blit_text(f"GAME OVER  {who}", (x + 4, cy), "tiny", t.ui_warn)
        elif frame.overtime:
            self.blit_text("OVERTIME", (x + 4, cy), "tiny", t.ui_warn)
        elif tr.at_end:
            self.blit_text("end of capture", (x + 4, cy), "tiny", t.ui_warn)
        elif tr.live:
            self.blit_text("live", (x + 4, cy), "tiny", t.ui_dim)
        else:
            state = "playing" if tr.playing else "paused"
            self.blit_text(f"{state}  {tr.speed:g}x", (x + 4, cy), "tiny", t.ui_dim)
        cy += lh
        if tr.source_status:
            self.blit_text(
                fit_text(tr.source_status, self.fonts["tiny"], w - 8), (x + 4, cy), "tiny", t.ui_dim
            )
        cy += lh
        self.blit_text(
            f"draw {tr.draw_ms:.1f} ms   {tr.fps:.0f} fps", (x + 4, cy), "tiny", t.ui_dim
        )
        cy += lh
        for line in notes:
            self.blit_text(
                fit_text(line, self.fonts["tiny"], w - 8), (x + 4, cy), "tiny", t.ui_warn
            )
            cy += lh
        if not self.layout.inspector[2] and view.compare_name:  # compact: no footer for it
            cy = self._draw_compare_lines(view, x, cy, w)
        cy += 6
        pygame.draw.line(self.surface, t.ui_border, (x + 4, cy), (x + w - 4, cy))
        cy += 4
        self.blit_text("events", (x + 4, cy), "tiny", t.ui_dim)
        cy += lh
        for line in frame.events[-n:] if n else []:
            self.blit_text(fit_text(line, self.fonts["mono"], w - 8), (x + 4, cy), "mono")
            cy += mono_h
        return rect[1] + rect[3]

    def learning_blocks(self, ln: Learning | None) -> list[list[tuple[str, str]]]:
        """The panel's rows, one list per group, each starting with its heading (a row with
        an empty value): ``LEARNING_GROUPS`` formatted field by field, then the learner's own
        ``extra`` rows in the order it sent them, if it sent any."""
        blocks = []
        for heading, fields in LEARNING_GROUPS:
            block = [(heading, "")]
            for label, attr, fmt in fields:
                v = getattr(ln, attr, None) if ln is not None else None
                block.append((label, field_text(v, fmt)))
            blocks.append(block)
        extra = getattr(ln, "extra", None) or {}
        if extra:
            blocks.append([("extra", ""), *((str(k), extra_text(v)) for k, v in extra.items())])
        return blocks

    def learning_columns(self, ln: Learning | None, rows: int) -> list[list[tuple[str, str]]]:
        """``learning_blocks`` packed into two columns of at most ``rows`` rows each.

        A group goes whole into the first column with room for all of it, so it is never
        broken while space for it is going spare and a short group still fits after a tall
        one has moved on. A group too tall for either column is split across what is left,
        and rows that fit nowhere are left out -- a heading among them, because a heading
        with no row under it says nothing.
        """
        cols: list[list[tuple[str, str]]] = [[], []]
        for block in self.learning_blocks(ln):
            whole = next((c for c in cols if len(c) + len(block) <= rows), None)
            if whole is not None:
                whole.extend(block)
                continue
            for row in block:
                # A heading needs its own row AND one under it in the SAME column: a heading
                # alone at the foot of a column says nothing, and rows that carried on in the
                # other column would read as belonging to the group above them.
                need = 1 if row[1] else 2
                target = next((c for c in cols if rows - len(c) >= need), None)
                if target is None:
                    return cols
                target.append(row)
        return cols

    def learning_lines(self, ln: Learning | None) -> list[tuple[str, str]]:
        """Every learning row in order, however the panel happens to column them. Each value
        is ``UNSET`` when ``ln`` is None, and so is a field the learner left unset."""
        return [row for col in self.learning_columns(ln, 10**6) for row in col]

    def _draw_learning_block(
        self, rect: Rect, top: int, ln: Learning | None, peer: str = "", age_s: float | None = None
    ) -> None:
        """The rest of the dashboard column, under the match log: the learner's status.

        It closes the column rather than leaving it dark, and it shows the same field list
        whether or not a learner is attached. Fields are filled down the left column and then
        the right; when the column is too short for all of them the last ones are left out,
        and nothing at all is drawn when there is no room for the heading and one row.
        """
        t = self.theme
        x, y, w, h = rect
        top += t.margin
        lh = self.line_h["tiny"]
        head_h = 4 + lh + 4 + 4
        avail = y + h - top
        if avail < head_h + lh:
            return
        pygame.draw.rect(self.surface, t.ui_panel, (x, top, w, avail))
        cy = top + 4
        age = age_text(age_s) if ln is not None else ""
        self.blit_text(f"learning  {age}" if age else "learning", (x + 4, cy), "tiny", t.ui_dim)
        self.blit_text(
            fit_text(learning_run(ln, peer), self.fonts["tiny"], w // 2),
            (x + w - 4, cy),
            "tiny",
            t.ui_text if ln is not None else t.ui_dim,
            "topright",
        )
        cy += lh + 4
        pygame.draw.line(self.surface, t.ui_border, (x + 4, cy), (x + w - 4, cy))
        cy += 4
        rows = max(0, (y + h - cy) // lh)
        col_w = (w - 8) // 2
        for ci, col in enumerate(self.learning_columns(ln, rows)):
            cx = x + 4 + ci * col_w
            for j, (label, value) in enumerate(col):
                ry = cy + j * lh
                if not value:  # a group heading, with a rule running out to the column's edge
                    self.blit_text(label, (cx, ry), "tiny", t.ui_text)
                    pygame.draw.line(
                        self.surface,
                        t.ui_border,
                        (cx + self.fonts["tiny"].size(label)[0] + 6, ry + lh // 2),
                        (cx + col_w - 8, ry + lh // 2),
                    )
                    continue
                shown = fit_text(label, self.fonts["tiny"], col_w - 52)
                self.blit_text(shown, (cx + 6, ry), "tiny", t.ui_dim)
                # The value gets what the label leaves: a learner names its own extras and
                # supplies their values, and a long one would otherwise print over its label.
                room = col_w - 14 - self.fonts["tiny"].size(shown)[0]
                self.blit_text(
                    fit_text(value, self.fonts["tiny"], max(16, room)),
                    (cx + col_w - 6, ry),
                    "tiny",
                    t.ui_text if value != UNSET else t.ui_dim,
                    "topright",
                )

    # ---- under the arena

    def _draw_status_line(self, tr: Transport) -> None:
        t = self.theme
        x, y, w, _ = self.layout.status
        left = tr.source_name
        if tr.live:
            pill = self.blit_text(" LIVE ", (x, y + 2), "tiny", t.ui_text)
            pygame.draw.rect(self.surface, t.live_pill, pill.inflate(2, 2), 0, 3)
            self.surface.blit(self.text(" LIVE ", "tiny", t.ui_text), pill)
            left = f"{tr.fps:.1f} fps   {tr.source_status}"
            self.blit_text(
                fit_text(left, self.fonts["tiny"], w - pill.width - 12),
                (pill.right + 6, y + 2),
                "tiny",
            )
            return
        if tr.length is not None:
            left = f"frame {tr.index + 1} / {tr.length}"
        self.blit_text(left, (x, y + 2), "tiny")
        state = "playing" if tr.playing else "paused"
        self.blit_text(f"{state}  {tr.speed:g}x", (x + w, y + 2), "tiny", t.ui_dim, "topright")

    def _draw_timeline(self, tr: Transport) -> None:
        t = self.theme
        x, y, w, h = self.layout.timeline
        pygame.draw.rect(self.surface, t.ui_panel, (x, y, w, h))
        pygame.draw.rect(self.surface, t.ui_border, (x, y, w, h), 1)
        if tr.live or not tr.length:
            return
        fill = (w - 2) * tr.index // max(1, tr.length - 1)
        pygame.draw.rect(self.surface, t.ui_dim, (x + 1, y + 1, fill, h - 2))
        mx = x + 1 + fill
        pygame.draw.rect(self.surface, t.ui_text, (mx - 1, y - 2, 3, h + 4))

    # ---- inspector

    def _draw_inspector(self, frame: Frame, view: ViewState) -> None:
        t = self.theme
        x, y, w, h = self.layout.hover
        pygame.draw.rect(self.surface, t.ui_panel, (x, y, w, h))
        unit = frame.unit(view.selected_uid) if view.selected_uid is not None else None
        pinned = unit is not None
        if unit is None and view.hover_uid is not None:
            unit = frame.unit(view.hover_uid)
        mono_h = self.line_h["mono"]
        cy = y + 4
        title = "inspector" + ("  (pinned, click to release)" if pinned else "")
        self.blit_text(fit_text(title, self.fonts["tiny"], w - 8), (x + 4, cy), "tiny", t.ui_dim)
        cy += self.line_h["tiny"] + 2
        if view.selected_uid is not None and not pinned:
            gone = f"pinned {view.selected_uid}: not in this frame"
            self.blit_text(
                fit_text(gone, self.fonts["tiny"], w - 8), (x + 4, cy), "tiny", t.ui_warn
            )
            cy += self.line_h["tiny"]
        if unit is None:
            self.blit_text("hover a unit; click to pin", (x + 4, cy), "tiny", t.ui_dim)
            cy += self.line_h["tiny"]
            self.blit_text(
                f"{len(frame.units)} units, {len(frame.spells)} spells",
                (x + 4, cy),
                "tiny",
                t.ui_dim,
            )
            return
        upt = frame.units_per_tile
        lines = [
            f"uid      {unit.uid}",
            f"name     {unit.name}",
            f"team     {unit.team} ({t.team_name[unit.team]})",
            f"kind     {unit.kind} ({KIND_NAMES.get(unit.kind, '?')})",
            f"x, y     {unit.x}, {unit.y}",
            f"tiles    {unit.x / upt:.2f}, {unit.y / upt:.2f}",
            f"hp       {unit.hp} / {unit.max_hp}",
            f"radius   {unit.radius}",
            f"box      {footprint_line(unit, upt)}",
            f"flying   {unit.flying}",
            f"deploy   {unit.deploy_ticks} ticks",
            f"stun     {unit.stun_ticks} ticks",
            f"target   {unit.target}",
            f"path     {len(unit.path)} points" + (f", to {unit.path[-1]}" if unit.path else ""),
            f"dir      {unit.direction}",
            f"state    {unit.state}",
        ]
        if unit.extra:
            lines.append("extra")
            lines += [f"  {k} {v}" for k, v in unit.extra.items()]
        color = t.team_color(unit.team)
        pygame.draw.rect(self.surface, color, (x, y, 4, h))
        for line in lines:
            if cy + mono_h > y + h:
                break
            self.blit_text(fit_text(line, self.fonts["mono"], w - 12), (x + 8, cy), "mono")
            cy += mono_h

    def compare_lines(self, view: ViewState) -> list[str]:
        """The compare panel's lines: the source and the ghost state, then app.Compare.text
        (the result at this tick, the running totals)."""
        ghost = "shown" if view.show_compare else "hidden (C)"
        return [f"compare: {view.compare_name}  {ghost}", *view.compare_text.split("\n")]

    def _draw_compare_lines(self, view: ViewState, x: int, cy: int, w: int) -> int:
        """The compare lines at (x, cy), a differing tick in the warning colour; returns cy."""
        t = self.theme
        for i, line in enumerate(self.compare_lines(view)):
            bad = "differ" in line and not line.endswith(" 0 differ")
            color = t.ui_text if i == 0 else t.ui_warn if bad else t.ui_dim
            self.blit_text(fit_text(line, self.fonts["tiny"], w - 8), (x + 4, cy), "tiny", color)
            cy += self.line_h["tiny"]
        return cy

    def _draw_footer(self, frame: Frame, view: ViewState) -> None:
        """The inspector's lower half: the compare panel and the key help."""
        t = self.theme
        x, y, w, h = self.layout.events
        pygame.draw.rect(self.surface, t.ui_panel, (x, y, w, h))
        cy = y + 4
        lh = self.line_h["tiny"]
        if view.compare_frame is None and not view.compare_name:
            self.blit_text("compare: none (--compare)", (x + 4, cy), "tiny", t.ui_dim)
            cy += lh
        else:
            cy = self._draw_compare_lines(view, x, cy, w)
        cy += 4
        pygame.draw.line(self.surface, t.ui_border, (x + 4, cy), (x + w - 4, cy))
        cy += 6
        if not view.show_help:
            self.blit_text("H  help", (x + 4, cy), "tiny", t.ui_dim)
            return
        key_w = 96
        for key, action in self.help_lines:
            if cy + lh > y + h:
                break
            self.blit_text(fit_text(key, self.fonts["tiny"], key_w - 6), (x + 4, cy), "tiny")
            self.blit_text(
                fit_text(action, self.fonts["tiny"], w - key_w - 8),
                (x + key_w, cy),
                "tiny",
                t.ui_dim,
            )
            cy += lh


# ---------------------------------------------------------------------- helpers


def dashed_line(
    surface: pygame.Surface,
    color: Color,
    a: tuple[int, int],
    b: tuple[int, int],
    max_dashes: int = 16,
) -> None:
    """At most ``max_dashes`` dashes (12 px each when the line is short), 60 % ink."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    dist = math.hypot(dx, dy)
    if dist < 2:
        return
    n = min(max_dashes, int(dist // 12) + 1)
    step_x, step_y = dx / n, dy / n
    ink_x, ink_y = step_x * 0.6, step_y * 0.6
    line = pygame.draw.line
    x, y = a
    for _ in range(n):
        line(surface, color, (x, y), (x + ink_x, y + ink_y), 1)
        x += step_x
        y += step_y


#: C0 and C1 control characters, dropped from every string the panel draws. A learner names
#: its own ``extra`` rows and its own run, and a NUL in a name is a crash in the font layer.
CONTROL_CHARS = dict.fromkeys(list(range(0x00, 0x20)) + list(range(0x7F, 0xA0)))


def fit_text(s: str, font: pygame.font.Font, width: int) -> str:
    """``s`` shortened with an ellipsis so it renders within ``width`` pixels.

    Control characters are dropped first: a learner names its own rows and its own run, and
    a NUL reaching the font layer takes the window down. One edit here covers every string
    the panel draws rather than trusting each call site.
    """
    s = s.translate(CONTROL_CHARS)
    if font.size(s)[0] <= width:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(s[:mid] + "…")[0] <= width:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + "…"
