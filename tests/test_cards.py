"""Card tiles, the special forms (evolutions, heroes) and the engine's status bits, headless.

The special forms arrive in the engine's own layout (RoyaleSim state_json, 2026-09-27: "evo"
rows [card_id, plays, next_evolved], "abilities" rows [available, spent, cost]): these tests
feed the viewer those rows and check it draws them, and -- the half that matters today -- that a
source which does not carry them draws exactly as before. "Not reported" is None or -1, and
``-1 & bit`` is the bit, so a raw mask would dress every unit of an older engine as under
ground, invisible, evolved and a hero at once.

Every colour a test expects is read from the theme, never from the function under test, so a
function that paints two kinds alike cannot agree with itself into a pass.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import msgspec
import pygame
import pytest

from royaleviser import capture
from royaleviser.app import App
from royaleviser.model import (
    CARD_BUILDING,
    CARD_SPELL,
    CARD_TROOP,
    KIND_BUILDING,
    KIND_PRINCESS_TOWER,
    KIND_TROOP,
    STATUS_EVOLVED,
    STATUS_HERO,
    STATUS_HIDDEN,
    STATUS_INVISIBLE,
    STATUS_UNDERGROUND,
    CardFace,
    Frame,
    Names,
    Player,
    Unit,
    decode_frame,
    encode_frame,
    hand_evolved,
    live_card_kind,
    problems,
    status_bits,
)
from royaleviser.render import (
    FORM_RING_GAP,
    Renderer,
    Transport,
    ViewState,
    monogram,
    shade,
    spell_style,
    status_words,
)
from royaleviser.sources import frame_from_state, special_fields
from royaleviser.theme import DEFAULT
from synthetic import ListSource

UPT = 18000  # engine subtiles per tile
ART = 1.1  # the art panel is the face colour lightened by this (render._draw_card)
T = DEFAULT


def player(**kw: object) -> Player:
    base = dict(
        team=0,
        elixir_milli=10000,
        elixir_known=True,
        hand=["Knight", "Fireball", "Cannon", "MinionHorde"],
        hand_known=True,
        next_card="Zap",
        cycle=[],
        deck=[],
        deck_known=False,
        crowns=0,
        tower_hp=[4824, 3052, 3052],
        tower_max_hp=[4824, 3052, 3052],
        king_active=False,
    )
    base.update(kw)
    return Player(**base)  # type: ignore[arg-type]


def unit(
    uid: int = 1,
    flags: object = "absent",
    kind: int = KIND_TROOP,
    x: int = 9 * UPT,
    y: int = 12 * UPT,
    **kw: object,
) -> Unit:
    extra: dict[str, object] = {"tower_slot": -1}
    if flags != "absent":
        extra["status_flags"] = flags
    fields: dict[str, object] = dict(
        uid=uid, team=0, kind=kind, name="Knight", x=x, y=y, hp=600, max_hp=1000,
        radius=9000, flying=False, deploy_ticks=0, stun_ticks=0, target=None, path=[],
        direction=None, state=None, extra=extra,
    )  # fmt: skip
    if kind != KIND_TROOP:
        half = 3 * UPT // 2
        fields.update(name="Tesla", footprint=(x - half, y - half, x + half, y + half))
    fields.update(kw)
    return Unit(**fields)  # type: ignore[arg-type]


def frame(units: list[Unit], players: list[Player] | None = None) -> Frame:
    return Frame(
        tick=200, tick_ms=50, units_per_tile=UPT,
        players=players or [player(team=0), player(team=1)],
        units=units, spells=[], overtime=False, game_over=False, winner=-1, crowns=[0, 0],
    )  # fmt: skip


@dataclass
class EngineCard:  # the shape of royalegym.protocol.CardInfo that Names.from_cards reads
    card_id: int
    name: str
    elixir: int
    count: int = 1
    flying: bool = False
    card_kind: str | None = None


NAMES = Names.from_cards(
    [
        EngineCard(0, "Knight", 3, 1, False, "TROOP"),
        EngineCard(1, "Fireball", 4, 0, False, "SPELL"),
        EngineCard(2, "Cannon", 3, 1, False, "BUILDING"),
        EngineCard(3, "MinionHorde", 5, 6, True, "TROOP"),
        EngineCard(4, "Zap", 2, 0, False, "SPELL"),
        EngineCard(5, "Mystery", 4, 1, False, None),
        EngineCard(6, "Skeletons", 1, 3, False, "TROOP"),
        EngineCard(7, "SkeletonArmy", 3, 15, False, "TROOP"),
    ]
)


def named(r: Renderer, names: Names = NAMES) -> Renderer:
    r.cost_of = names.cost_of_name
    r.face_of = names.face_of
    return r


@pytest.fixture()
def renderer() -> Renderer:
    return named(Renderer(scale=24))


def px(surf: pygame.Surface, x: int, y: int) -> tuple[int, int, int]:
    return tuple(surf.get_at((x, y)))[:3]  # type: ignore[return-value]


# --------------------------------------------------------------------- names and faces


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("HogRider", "HR"),
        ("Knight", "Kn"),
        ("Elixir Collector", "EC"),
        ("MergeMaiden_Mounted", "MM"),
        ("ElixirGolem1", "EG"),
        ("PEKKA", "Pe"),
        ("#26000099", "?"),
        ("", "?"),
    ],
)
def test_the_monogram_is_two_letters_from_the_name(name: str, want: str) -> None:
    assert monogram(name) == want


def test_an_engine_table_says_what_each_card_is() -> None:
    assert NAMES.face_of("Knight") == CardFace("Knight", 3, CARD_TROOP, 1, False)
    assert NAMES.face_of("MinionHorde") == CardFace("MinionHorde", 5, CARD_TROOP, 6, True)
    assert NAMES.face_of("Cannon").kind == CARD_BUILDING  # type: ignore[union-attr]
    # A spell summons nothing: count 0 reads as "not said", not as a card of zero units.
    assert NAMES.face_of("Fireball") == CardFace("Fireball", 4, CARD_SPELL, None, False)
    # An engine without the card_kind column: no kind, never one guessed from placement.
    assert NAMES.face_of("Mystery").kind is None  # type: ignore[union-attr]
    assert NAMES.face_of("Nobody") is None


def test_a_live_table_takes_the_kind_from_the_id_class_and_nothing_else() -> None:
    assert (live_card_kind(26000000), live_card_kind(27000000), live_card_kind(28000000)) == (
        CARD_TROOP,
        CARD_BUILDING,
        CARD_SPELL,
    )
    assert live_card_kind(203000014) is None and live_card_kind(-1) is None
    names = Names.live()
    knight = names.face_of("Knight")
    assert knight is not None and knight.kind == CARD_TROOP and knight.count is None
    # A card table without the metadata (a hand-built Names) still gives the cost it has.
    assert Names([(7, "Golem", 8)]).face_of("Golem") == CardFace("Golem", 8)


# The cards whose live id class and engine kind differ, each for a reason, so a tile of one of
# them looks different on a stream (live table) and a trace (engine table). Anything NOT here
# that starts to differ is a new disagreement and fails below.
KNOWN_KIND_DIFFERENCES = {
    # The live table lists the Furnace with the buildings; the engine's kind is the row the
    # card puts on the board, which its data makes a troop.
    "FirespiritHut": (CARD_BUILDING, CARD_TROOP),
    # An event card listed in the spells' id class that summons a flying troop.
    "MergeMaiden": (CARD_SPELL, CARD_TROOP),
}


NOT_A_PASS = "SKIPPED, NOT PASSED"


def engine_cards() -> list:
    """The engine's card table, or a loud skip for each of the three ways there is none: no
    royalegym, no built extension, or a build STALE against the data on disk (another repo's
    drift, not a defect here). The same three states as test_sources.engine_or_skip, which
    this module cannot import: that module skips whole where royalegym is absent."""
    rust_engine = pytest.importorskip("royalegym.rust_engine", reason=f"{NOT_A_PASS}: no royalegym")
    if not rust_engine.core_available():
        pytest.skip(f"{NOT_A_PASS}: the royalesim extension is not built")
    from royalegym.protocol import default_calibration

    stale = rust_engine.stale_build_differences(default_calibration())
    if stale:
        pytest.skip(f"{NOT_A_PASS}: the engine is built but STALE ({len(stale)} differences)")
    return rust_engine.RustEngine().cards()


def test_the_live_and_engine_tables_agree_on_every_card_but_the_known_ones() -> None:
    cards = engine_cards()
    engine, live = Names.from_cards(cards), Names.live()
    diff = {}
    for name in (c.name for c in cards):
        lf, ef = live.face_of(name), engine.face_of(name)
        if lf is not None and ef is not None and lf.kind != ef.kind:
            diff[name] = (lf.kind, ef.kind)
    assert diff == KNOWN_KIND_DIFFERENCES


# --------------------------------------------------------------------- status bits


def test_the_status_bit_numbers_are_the_contract() -> None:
    bits = (STATUS_UNDERGROUND, STATUS_INVISIBLE, STATUS_HIDDEN, STATUS_EVOLVED, STATUS_HERO)
    assert bits == (1, 2, 4, 8, 16)
    protocol = pytest.importorskip(
        "royalegym.protocol", reason=f"{NOT_A_PASS}: no royalegym to compare the numbers with"
    )
    assert bits[:3] == (
        protocol.STATUS_UNDERGROUND,
        protocol.STATUS_INVISIBLE,
        protocol.STATUS_HIDDEN,
    )


@pytest.mark.parametrize("value", ["absent", None, -1, -7, True, "8"])
def test_status_bits_not_reported_is_none(value: object) -> None:
    assert status_bits(unit(flags=value)) is None
    assert status_words(unit(flags=value)) == "not reported"


def test_status_bits_and_their_words() -> None:
    assert status_bits(unit(flags=0)) == 0 and status_words(unit(flags=0)) == "none"
    assert status_words(unit(flags=STATUS_UNDERGROUND | STATUS_HERO)) == "underground, hero"
    assert status_words(unit(flags=STATUS_EVOLVED | 64)) == "evolved, bits 64"


# --------------------------------------------------------------------- the wire


def test_a_player_without_the_special_rows_decodes_with_none_of_them() -> None:
    old = msgspec.to_builtins(player())
    for key in ("evo", "abilities"):
        old.pop(key)
    p = msgspec.convert(old, Player)
    assert (p.evo, p.abilities, hand_evolved(p)) == ([], [], [-1, -1, -1, -1])


def test_the_special_rows_ride_the_frame_codec() -> None:
    p = player(evo=[("Knight", 2, 1), ("Fireball", 1, 0)], abilities=[("Cannon", 1, 0, 1)])
    back = decode_frame(encode_frame(frame([], [p, player(team=1)])))
    assert back.players[0].evo == [("Knight", 2, 1), ("Fireball", 1, 0)]
    assert back.players[0].abilities == [("Cannon", 1, 0, 1)]
    assert back.players[1].abilities == []


def test_a_hand_slot_is_evolved_when_its_cards_row_says_the_next_play_is() -> None:
    p = player(evo=[("Knight", 2, 1), ("Fireball", 1, 0), ("Golem", 0, 0)])
    assert hand_evolved(p) == [1, 0, -1, -1]  # Knight, Fireball, Cannon, MinionHorde


def test_the_contract_check_reads_the_special_rows() -> None:
    good = player(evo=[("Knight", 2, 1)], abilities=[("", 0, 1, 1)])
    assert problems(frame([], [good, player(team=1)])) == []
    bad = player(evo=[("Knight", -1, 2)], abilities=[("Cannon", 2, 0, 1), ("", 0, 0, -3)])
    found = problems(frame([], [bad, player(team=1)]))
    assert len(found) == 3, found


def test_special_fields_turn_the_engine_rows_into_names() -> None:
    @dataclass
    class State:
        evo: list[list[int]]
        abilities: list[list[int]]

    got = special_fields(State([[0, 2, 1]], [[1, 0, 2]]), NAMES.name_of)
    # The engine's ability row names no card, so the viewer's name column is "".
    assert got == {"evo": [["Knight", 2, 1]], "abilities": [["", 1, 0, 2]]}
    # A PlayerState from before the special forms has none of the attributes: nothing added.
    assert special_fields(object(), NAMES.name_of) == {}
    # The engine sends both keys on every player, empty without forms: still nothing added.
    assert special_fields(State([], []), NAMES.name_of) == {}


def special_state() -> tuple[object, Names]:
    """A MockEngine BattleState whose players carry the engine's rows, each team different."""
    pytest.importorskip("royalegym", reason=f"{NOT_A_PASS}: no royalegym, so no engine state")
    from royalegym.mock_engine import MockEngine
    from royalegym.protocol import MatchSetup, PlayerState

    class Special(PlayerState, frozen=True):  # the fields the engine adds to a player
        evo: list[list[int]] = msgspec.field(default_factory=list)
        abilities: list[list[int]] = msgspec.field(default_factory=list)

    eng = MockEngine()
    cards = eng.cards()
    ids = [c.card_id for c in cards][:8]
    eng.reset(1, MatchSetup(decks=[ids, ids], shuffle=0, start_tick=0))
    st = eng.state()
    hand0 = st.players[0].hand[0]
    rows = (
        dict(evo=[[hand0, 2, 1]], abilities=[]),
        dict(evo=[], abilities=[[1, 0, 2]]),
    )
    players = [
        Special(**msgspec.structs.asdict(p), **rows[i])  # type: ignore[arg-type]
        for i, p in enumerate(st.players)
    ]
    return msgspec.structs.replace(st, players=players), Names.from_cards(cards)


def test_frame_from_state_carries_each_players_rows() -> None:
    state, names = special_state()
    f = frame_from_state(state, names, UPT)
    first = names.name_of(state.players[0].evo[0][0])
    assert f.players[0].evo == [(first, 2, 1)] and f.players[0].abilities == []
    assert hand_evolved(f.players[0])[0] == 1  # the hand's first card is the evolved one
    assert f.players[1].evo == [] and f.players[1].abilities == [("", 1, 0, 2)]


# --------------------------------------------------------------------- the tiles

BOX = pygame.Rect(100, 100, T.card_w, T.card_h)
FOOT_H = 30  # the name band's height (render._draw_card)
ART_BOTTOM = 4 + (T.card_h - FOOT_H - 7)  # the art panel's bottom, from the tile's top


def draw_tile(r: Renderer, name: str, **kw: object) -> pygame.Surface:
    r.surface.fill((0, 0, 0))
    r._draw_card(BOX, name, kw.pop("elixir", 10000), **kw)  # type: ignore[arg-type]
    return r.surface.subsurface(BOX).copy()


def tile_px(r: Renderer, name: str, dx: int, dy: int, **kw: object) -> tuple[int, int, int]:
    return px(draw_tile(r, name, **kw), dx, dy)


def test_a_tile_is_coloured_by_what_the_card_is(renderer: Renderer) -> None:
    # (6, 40): inside the art panel, left of the monogram, clear of every badge.
    got = {name: tile_px(renderer, name, 6, 40) for name in ("Knight", "Cannon", "Zap")}
    assert got == {
        "Knight": shade(T.card_troop, ART),
        "Cannon": shade(T.card_building, ART),
        "Zap": shade(T.card_spell, ART),
    }
    assert len(set(got.values())) == 3
    # No kind from the source: the plain tile, not a colour picked for it.
    assert tile_px(renderer, "Mystery", 6, 40) == shade(T.card_bg, ART)


def test_the_kind_is_also_a_glyph_not_only_a_colour(renderer: Renderer) -> None:
    # The glyph's centre, 8 px in from the art panel's bottom-right corner, is ink for every
    # kind (a sword's crossing, a tower's wall, a spark's middle) and bare art for no kind.
    at = (4 + (T.card_w - 8) - 8, ART_BOTTOM - 8)
    for name, face in (
        ("Knight", T.card_troop),
        ("Cannon", T.card_building),
        ("Zap", T.card_spell),
    ):
        assert tile_px(renderer, name, *at) == shade(face, 0.42), name
    assert tile_px(renderer, "Mystery", *at) == shade(T.card_bg, ART)


def test_the_missing_elixir_is_veiled_from_the_top(renderer: Renderer) -> None:
    lit = tile_px(renderer, "Knight", 6, 40)
    # 1.5 of 3 elixir: the top half is veiled, the bottom half of the art is not.
    assert tile_px(renderer, "Knight", 6, 40, elixir=1500) != lit
    assert tile_px(renderer, "Knight", 6, 60, elixir=1500) == tile_px(renderer, "Knight", 6, 60)
    # Enough elixir, or a source that does not know it: no veil anywhere.
    assert tile_px(renderer, "Knight", 6, 40, elixir=3000) == lit
    assert tile_px(renderer, "Knight", 6, 40, elixir=None) == lit


def test_a_free_card_is_never_veiled(renderer: Renderer) -> None:
    free = named(Renderer(scale=24), Names([(0, "Free", 0)]))
    assert tile_px(free, "Free", 6, 40, elixir=-500) == tile_px(free, "Free", 6, 40)


def test_the_border_says_evolved_hero_or_neither(renderer: Renderer) -> None:
    assert tile_px(renderer, "Knight", 0, 50) == T.card_border
    assert tile_px(renderer, "Knight", 0, 50, evolved=1) == T.evo
    assert tile_px(renderer, "Knight", 0, 50, hero=True) == T.hero
    # 0: the card HAS an evolution that is not charged -- its pips show that, not its frame.
    assert tile_px(renderer, "Knight", 0, 50, evolved=0) == T.card_border


@pytest.mark.parametrize("name", ["Skeletons", "SkeletonArmy"])
def test_the_evo_tag_and_the_count_do_not_cover_each_other(renderer: Renderer, name: str) -> None:
    # Evo Skeletons summons 3 and Evo Skeleton Army 15; the wide "x15" pill reaches the tag.
    def evo_px(s: pygame.Surface) -> int:
        return sum(px(s, x, y) == T.evo for x in range(10, T.card_w - 3) for y in range(3, 16))

    def footer_px(s: pygame.Surface) -> int:
        return sum(
            px(s, x, y) == T.card_footer for x in range(3, T.card_w - 3) for y in range(3, 45)
        )

    with_count = draw_tile(renderer, name, evolved=1)
    no_tag = draw_tile(renderer, name)
    alone = draw_tile(named(Renderer(scale=24), Names([(0, name, 1)])), name, evolved=1)
    assert evo_px(with_count) == evo_px(alone) > 0  # the tag is whole
    assert footer_px(with_count) == footer_px(no_tag) > 0  # and so is the pill


def test_the_pips_count_the_plays(renderer: Renderer) -> None:
    # Pips 9 px apart, centred under the monogram at the art's foot (x 40 is the centre).
    y = ART_BOTTOM - 3
    art = shade(T.card_troop, ART)
    assert [tile_px(renderer, "Knight", x, y, evo=1) for x in (36, 40, 45)] == [art, T.evo, art]
    assert [tile_px(renderer, "Knight", x, y, evo=2) for x in (36, 40, 45)] == [T.evo, art, T.evo]
    # An evolution with nothing counted: one hollow pip, not a promise of a total.
    assert tile_px(renderer, "Knight", 40, y, evo=0) == art
    ring = {tile_px(renderer, "Knight", 40 + d, y, evo=0) for d in range(-3, 4)}
    assert shade(T.evo, 0.5) in ring and T.evo not in ring
    assert tile_px(renderer, "Knight", 43, y) == art  # and no evolution, no pip


def hand_band(r: Renderer, p: Player, elixir_row: bool = True) -> bytes:
    """The bottom player's hand, and its elixir row above it unless told not to."""
    r.draw(frame([], [p, player(team=1)]), ViewState(), Transport())
    _x, y, w, h = r.layout.bottom_hand
    top = r.layout.bottom_elixir[1] if elixir_row else y
    return pygame.image.tobytes(r.surface.subsurface((0, top, w, y + h - top)), "RGB")


def test_a_player_without_special_rows_draws_no_special_marks(renderer: Renderer) -> None:
    plain = hand_band(renderer, player())
    # An evolution of a card not in the hand changes nothing drawn there, byte for byte.
    assert hand_band(renderer, player(evo=[("Golem", 1, 0)]), False) == hand_band(
        renderer, player(), False
    )
    x, y = renderer.layout.bottom_hand[:2]
    step = T.card_w + T.card_gap
    renderer.draw(frame([], [player(), player(team=1)]), ViewState(), Transport())
    edges = [px(renderer.surface, x + i * step, y + 50) for i in range(4)]
    assert edges == [T.card_border] * 4
    assert plain == hand_band(renderer, player(evo=[], abilities=[]))
    # Unknown elixir: nothing is veiled, whatever the number in the field says.
    unknown = player(elixir_known=False, elixir_milli=0)
    assert hand_band(renderer, unknown, False) == hand_band(renderer, player(), False)
    assert hand_band(renderer, player(elixir_milli=0), False) != hand_band(
        renderer, player(), False
    )


def test_a_hand_draws_evolved_hero_and_pips_from_the_player_rows(renderer: Renderer) -> None:
    p = player(evo=[("Knight", 2, 1), ("Fireball", 1, 0)], abilities=[("Cannon", 1, 0, 1)])
    renderer.draw(frame([], [p, player(team=1)]), ViewState(), Transport())
    x, y = renderer.layout.bottom_hand[:2]
    step = T.card_w + T.card_gap
    edges = [px(renderer.surface, x + i * step, y + 50) for i in range(4)]
    assert edges == [T.evo, T.card_border, T.hero, T.card_border]
    # The Fireball's row: one play counted, one pip, from Player.evo by name.
    assert px(renderer.surface, x + step + 40, y + ART_BOTTOM - 3) == T.evo
    # An ability row that names no card crowns nothing (the engine's rows name none).
    renderer.draw(
        frame([], [player(abilities=[("", 1, 0, 1)]), player(team=1)]), ViewState(), Transport()
    )
    assert [px(renderer.surface, x + i * step, y + 50) for i in range(4)] == [T.card_border] * 4


def test_the_next_card_is_a_small_tile_of_its_kind(renderer: Renderer) -> None:
    for name, face in (("Zap", T.card_spell), ("Cannon", T.card_building), ("Mystery", T.card_bg)):
        renderer.draw(frame([], [player(next_card=name), player(team=1)]), ViewState(), Transport())
        ex, ey, _, _ = renderer.layout.bottom_elixir
        assert px(renderer.surface, ex + 200 + 8 + 2, ey + 4) == face, name


def button_px(r: Renderer, row: tuple[str, int, int, int], dx: int = 0, dy: int = 9) -> tuple:
    r.draw(frame([], [player(abilities=[row]), player(team=1)]), ViewState(), Transport())
    ex, ey, _, eh = r.layout.bottom_elixir
    # After the 200 px bar, the 8 px gap, the 24 px next card and 5 px: a 13 px button.
    return px(r.surface, ex + 200 + 8 + 24 + 5 + 13 + dx, ey + eh // 2 + dy)


def test_an_ability_button_shows_what_the_engine_says(renderer: Renderer) -> None:
    idle = T.ability_idle
    assert button_px(renderer, ("", 1, 0, 1)) == T.hero  # available
    assert button_px(renderer, ("", 0, 0, 1)) == idle  # neither: no hero of it standing
    assert button_px(renderer, ("", 0, 1, 1)) == shade(idle, 0.6)  # spent
    # Spent is crossed out: the slash runs through the centre.
    assert button_px(renderer, ("", 0, 1, 1), dx=0, dy=0) == T.ui_dim
    assert button_px(renderer, ("", 0, 0, 1), dx=0, dy=0) == idle
    # The press's elixir is the dot at the lower right (sampled beside its digit).
    assert button_px(renderer, ("", 1, 0, 1), dx=16, dy=10) == T.elixir


# --------------------------------------------------------------------- the board


def board(r: Renderer, units: list[Unit]) -> pygame.Surface:
    r.draw(frame(units), ViewState(show_targets=False, show_paths=False), Transport())
    return r.surface.copy()


def unit_px(r: Renderer, u: Unit, dx: int = 0, dy: int = 0) -> tuple[int, int, int]:
    surf = board(r, [u])
    x, y = r.to_px(u.x, u.y, UPT, 0)
    return px(surf, x + dx, y + dy)


@pytest.mark.parametrize("kind", [KIND_TROOP, KIND_BUILDING, KIND_PRINCESS_TOWER])
@pytest.mark.parametrize("value", ["absent", None, -1])
def test_a_source_that_reports_no_bits_draws_exactly_as_zero(
    renderer: Renderer, value: object, kind: int
) -> None:
    base = pygame.image.tobytes(board(renderer, [unit(flags=0, kind=kind)]), "RGB")
    assert pygame.image.tobytes(board(renderer, [unit(flags=value, kind=kind)]), "RGB") == base


def blend(top: tuple[int, int, int], alpha: int, under: tuple[int, int, int]) -> tuple:
    return tuple(
        round(a * alpha / 255 + b * (255 - alpha) / 255) for a, b in zip(top, under, strict=True)
    )


def near(a: tuple, b: tuple, tol: int = 2) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_under_ground_invisible_and_hidden_each_draw_their_own_way(renderer: Renderer) -> None:
    grass = px(board(renderer, []), *renderer.to_px(9 * UPT, 12 * UPT, UPT, 0))
    team = T.team_color(0)
    solid = unit_px(renderer, unit(flags=0))
    under = unit_px(renderer, unit(flags=STATUS_UNDERGROUND))
    unseen = unit_px(renderer, unit(flags=STATUS_INVISIBLE))
    assert solid == team
    assert len({solid, under, unseen, grass}) == 4, (solid, under, unseen, grass)
    # Turned earth over the grass (no body), and the team's colour faded over it.
    assert near(under, blend(T.burrow, 170, grass)), (under, grass)
    assert near(unseen, blend(team, T.unseen_alpha, grass)), (unseen, grass)
    # A hidden building's body fades the same way; a plain one is solid.
    tesla = unit(kind=KIND_BUILDING, radius=9000)
    assert unit_px(renderer, tesla) == team
    assert unit_px(renderer, unit(kind=KIND_BUILDING, radius=9000, flags=STATUS_HIDDEN)) != team


def ring_colours(r: Renderer, u: Unit, lo: int, hi: int) -> set[tuple[int, int, int]]:
    """The colours on the unit's right, from lo to hi px past its body: where a ring is."""
    rad = r.unit_radius_px(u, UPT)
    surf = board(r, [u])
    x, y = r.to_px(u.x, u.y, UPT, 0)
    return {px(surf, x + rad + d, y) for d in range(lo, hi + 1)}


def test_evolved_and_hero_rings_sit_outside_the_status_rings(renderer: Renderer) -> None:
    g = FORM_RING_GAP
    assert T.evo in ring_colours(renderer, unit(flags=STATUS_EVOLVED), g - 2, g)
    assert T.hero not in ring_colours(renderer, unit(flags=STATUS_EVOLVED), 1, g + 6)
    assert T.hero in ring_colours(renderer, unit(flags=STATUS_HERO), g - 2, g)
    assert T.evo not in ring_colours(renderer, unit(flags=STATUS_HERO), 1, g + 6)
    both = unit(flags=STATUS_EVOLVED | STATUS_HERO)
    assert T.evo in ring_colours(renderer, both, g - 2, g)
    assert T.hero in ring_colours(renderer, both, g + 2, g + 4)
    assert not {T.evo, T.hero} & ring_colours(renderer, unit(flags=0), 1, g + 6)
    # A Freeze draws a white rim at body + 1..2 inside an ink ring; with an evolution ring on
    # top of it, both must still show: the rim is not painted over and the ring is not hidden.
    frozen = unit(status=[("Freeze", 2000)], stun_ticks=40)
    both_on = unit(flags=STATUS_EVOLVED, status=[("Freeze", 2000)], stun_ticks=40)
    assert ring_colours(renderer, both_on, 1, 5) == ring_colours(renderer, frozen, 1, 5)
    assert T.evo in ring_colours(renderer, both_on, 1, 14)


def test_a_heros_crown_does_not_cover_its_status_pips(renderer: Renderer) -> None:
    stunned = unit(flags=STATUS_HERO, stun_ticks=20)
    h = renderer.unit_radius_px(stunned, UPT)
    pip_dy = -h - 3 - T.hp_bar_h - 3 - 3  # render._draw_status: the pip row's centre
    assert unit_px(renderer, stunned, 0, pip_dy) == T.status_stun
    # And the crown is drawn, above the pips.
    column = {unit_px(renderer, stunned, 0, pip_dy - d) for d in range(6, 20)}
    assert T.hero in column


@pytest.mark.parametrize("flying", [False, True])
def test_a_pinned_units_ring_clears_its_form_ring(renderer: Renderer, flying: bool) -> None:
    # A flyer's form ring once sat at exactly the pin ring's radius and width.
    u = unit(flags=STATUS_HERO, flying=flying)
    renderer.draw(frame([u]), ViewState(selected_uid=u.uid, show_targets=False), Transport())
    x, y = renderer.to_px(u.x, u.y, UPT, 0)
    rad = renderer.unit_radius_px(u, UPT)
    g = FORM_RING_GAP

    def right(lo: int, hi: int) -> set[tuple[int, int, int]]:
        return {px(renderer.surface, x + rad + d, y) for d in range(lo, hi + 1)}

    assert T.hero in right(g - 2, g) and T.hover not in right(g - 2, g)
    assert T.hover in right(g + 2, g + 8)  # and the pin ring is drawn, outside it


def test_an_unknown_pulsing_spell_is_an_area_not_a_ball() -> None:
    # The Goblin Curse has no style of its own and pulses (motion 4); it drew as a ball.
    assert spell_style("GoblinCurse", 4) == ("spell", "area")
    assert spell_style("SomeShot", 0) == ("spell", "ball")


# --------------------------------------------------------------------- the wiring


def test_a_capture_and_the_window_install_the_card_table() -> None:
    src = ListSource([frame([])], "cards")
    src.names = NAMES  # type: ignore[attr-defined]
    r = capture._renderer_for(src, 24, T)
    assert r.face_of("Knight") == NAMES.face_of("Knight")
    a = App([src], ViewState())
    assert a.renderer.face_of("Cannon") == NAMES.face_of("Cannon")
