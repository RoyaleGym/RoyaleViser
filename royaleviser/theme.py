"""Colours, fonts, sizes and the window layout, in one place.

The palette is carried over from the project's earlier Python renderer: the checkerboard
grass, the river, the bridges, blue team 0 / red team 1, the dark UI. The
layout is that renderer's, reduced to numbers: a dashboard column on the left (the top
player's hand, then the elixir rows, then the bottom player's hand), the arena beside it at a
fixed integer pixel-per-tile scale, and -- new -- an inspector column on the right for the
hovered unit, the events list and the status line.

    from royaleviser.theme import DEFAULT, layout
    lay = layout(DEFAULT, scale=24, tiles=(18, 32))   # -> Layout with every rect in pixels

Everything here is an integer; the renderer never computes a size of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int]
Rect = tuple[int, int, int, int]  # x, y, w, h in window pixels


@dataclass(frozen=True, slots=True)
class Theme:
    # Arena (old palette)
    grass_dark: Color = (188, 195, 55)
    grass_light: Color = (217, 215, 47)
    river: Color = (106, 230, 237)
    bridge: Color = (255, 175, 120)
    grid_line: Color = (135, 146, 43)
    tower_zone: Color = (120, 130, 40)  # outline of a crown tower's no-deploy rect
    # UI
    ui_bg: Color = (30, 30, 40)
    ui_panel: Color = (40, 40, 54)
    ui_text: Color = (255, 255, 255)
    ui_dim: Color = (150, 150, 160)
    ui_border: Color = (100, 100, 120)
    ui_warn: Color = (255, 80, 80)  # OVERTIME, game over, "hand: not in this source"
    # Teams: index = team
    team: tuple[Color, Color] = ((71, 204, 218), (224, 73, 41))
    team_king: tuple[Color, Color] = ((51, 184, 198), (204, 53, 21))
    team_name: tuple[str, str] = ("Blue", "Red")
    # Entities
    troop_outline: Color = (0, 0, 0)
    building_outline: Color = (50, 50, 50)
    projectile: Color = (180, 50, 220)
    spell: Color = (255, 100, 255)
    path_line: Color = (255, 255, 255)
    target_line: Color = (255, 220, 60)
    direction_line: Color = (30, 30, 30)
    hover: Color = (255, 255, 0)
    stun_overlay: Color = (255, 255, 100)
    deploy_overlay: Color = (200, 200, 200)
    # Bars
    hp_bg: Color = (60, 60, 60)
    hp_high: Color = (50, 200, 50)
    hp_mid: Color = (220, 200, 50)
    hp_low: Color = (200, 50, 50)
    elixir: Color = (150, 50, 200)
    elixir_bg: Color = (80, 80, 100)
    # Cards (the hand boxes): light on the dark UI like the old renderer's grey cards
    card_bg: Color = (205, 205, 212)
    card_text: Color = (20, 20, 30)
    card_border: Color = (240, 240, 240)
    card_unknown: Color = (70, 70, 84)
    live_pill: Color = (200, 40, 40)
    # Fonts: pygame.font.Font(font_name, size); None is pygame's bundled default. The
    # monospace one goes through pygame.font.SysFont (comma-separated candidates, the
    # bundled font when none is installed) for the inspector and the events list.
    font_name: str | None = None
    font_large: int = 36
    font_small: int = 24
    font_tiny: int = 16
    font_mono_name: str = "consolas,dejavusansmono,couriernew,monospace"
    font_mono: int = 14
    # Sizes (pixels)
    dashboard_w: int = 340  # hand column: 4 cards of 80 + 3 gaps of 5 = 335, plus a 5 px gutter
    inspector_w: int = 300  # 0: no inspector column (the compact layout, app.fit_layout)
    tile_px: int = 24  # default pixels per tile (--scale overrides)
    margin: int = 5
    card_w: int = 80
    card_h: int = 100
    card_gap: int = 5
    elixir_row_h: int = 28
    next_card_w: int = 60
    status_h: int = 22  # one status line under the arena
    timeline_h: int = 18  # scrub bar under the status line (replays only)
    hp_bar_h: int = 4
    unit_min_px: int = 5  # radius floor so a small troop stays visible
    unit_default_radius_tiles_x100: int = 50  # 0.5 tile when a source gives radius 0
    tower_zone_w: int = 1

    def team_color(self, team: int, king: bool = False) -> Color:
        return (self.team_king if king else self.team)[team]

    def hp_color(self, hp: int, max_hp: int) -> Color:
        """Green above 60 %, yellow above 30 %, red below (the old thresholds), integers only."""
        if max_hp <= 0:
            return self.hp_bg
        if hp * 10 > max_hp * 6:
            return self.hp_high
        if hp * 10 > max_hp * 3:
            return self.hp_mid
        return self.hp_low


DEFAULT = Theme()


@dataclass(frozen=True, slots=True)
class Layout:
    """Every rect of the window for one scale and arena size, in pixels (x, y, w, h)."""

    window: tuple[int, int]
    dashboard: Rect
    arena: Rect  # the board itself, tiles * scale
    inspector: Rect
    top_hand: Rect  # the seat-top player's four cards
    top_elixir: Rect
    bottom_elixir: Rect
    bottom_hand: Rect
    debug: Rect  # between the two hand blocks: king activation, pending deploys, paths legend
    timer: Rect  # timer + crowns box, top-right of the arena
    status: Rect  # status line under the arena
    timeline: Rect  # scrub bar under the status line
    events: Rect  # events list, lower part of the inspector
    hover: Rect  # hovered-unit fields, upper part of the inspector
    scale: int


def layout(theme: Theme, scale: int, tiles: tuple[int, int]) -> Layout:
    """Old renderer's arrangement: dashboard | arena | inspector, arena height sets the window.

    With ``theme.inspector_w`` 0 the inspector column is left out (its rects are empty and
    the window ends a margin after the arena): the compact layout for a narrow window, where
    the compare lines move into the dashboard's status block.
    """
    m = theme.margin
    aw, ah = tiles[0] * scale, tiles[1] * scale
    below = theme.status_h + theme.timeline_h + 2 * m
    height = max(ah + below + 2 * m, 4 * theme.card_h + 2 * theme.elixir_row_h + 12 * m)
    arena_x = theme.dashboard_w
    inspector_x = arena_x + aw + m
    width = inspector_x + theme.inspector_w if theme.inspector_w else inspector_x
    hand_h = theme.card_h + 2 * m
    top_hand = (m, m, theme.dashboard_w - 2 * m, hand_h)
    top_elixir = (m, m + hand_h, theme.dashboard_w - 2 * m, theme.elixir_row_h)
    bottom_hand = (m, height - m - hand_h, theme.dashboard_w - 2 * m, hand_h)
    bottom_elixir = (
        m,
        height - m - hand_h - theme.elixir_row_h,
        theme.dashboard_w - 2 * m,
        theme.elixir_row_h,
    )
    debug_y = top_elixir[1] + top_elixir[3] + m
    debug = (m, debug_y, theme.dashboard_w - 2 * m, bottom_elixir[1] - m - debug_y)
    timer_w, timer_h = 5 * scale, 2 * scale
    timer = (arena_x + aw - timer_w - m, m + m, timer_w, timer_h)
    status = (arena_x, m + ah + m, aw, theme.status_h)
    timeline = (arena_x, status[1] + status[3] + m, aw, theme.timeline_h)
    hover_h = (height - 3 * m) // 2
    if theme.inspector_w:
        hover = (inspector_x, m, theme.inspector_w - m, hover_h)
        events = (inspector_x, m + hover_h + m, theme.inspector_w - m, height - 3 * m - hover_h)
    else:
        hover = events = (inspector_x, 0, 0, 0)
    return Layout(
        window=(width, height),
        dashboard=(0, 0, theme.dashboard_w, height),
        arena=(arena_x, m, aw, ah),
        inspector=(inspector_x, 0, theme.inspector_w, height),
        top_hand=top_hand,
        top_elixir=top_elixir,
        bottom_elixir=bottom_elixir,
        bottom_hand=bottom_hand,
        debug=debug,
        timer=timer,
        status=status,
        timeline=timeline,
        events=events,
        hover=hover,
        scale=scale,
    )
