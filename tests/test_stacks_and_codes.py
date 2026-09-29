"""Stacked troops and the small tiles' codes, headless.

A stack is two troops of one team and one layer drawn so close that the one under cannot be
seen: the Ram Rider's rider stands where its Ram stood a tick before. The small tiles (the next
card, an ability button) show two letters, and two cards of one deck can share them.
"""

from __future__ import annotations

import os
import sys
from itertools import combinations
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame

from royaleviser.model import KIND_BUILDING, Names
from royaleviser.render import (
    STACK_TILES_X100,
    Renderer,
    Transport,
    ViewState,
    monogram,
    riders,
    stacks,
    tile_code,
    tile_codes,
)
from royaleviser.theme import DEFAULT
from test_cards import UPT, engine_cards, frame, player, unit

QUARTER = STACK_TILES_X100 * UPT // 100  # the stack reach, raw units


def ram(uid: int, dx: int = 0, **kw: object) -> object:
    return unit(uid=uid, **{"name": "RamRider", "radius": 10800, "x": 9 * UPT + dx, **kw})


# --------------------------------------------------------------------- which units stack


def test_a_stack_is_one_team_one_layer_closer_than_a_quarter_tile() -> None:
    assert stacks([ram(1), ram(2)], UPT) == {1: 0, 2: 2}  # the later one is on top
    assert stacks([ram(1), ram(2, QUARTER - 1)], UPT) == {1: 0, 2: 2}
    assert stacks([ram(1), ram(2, QUARTER)], UPT) == {}  # a quarter tile apart: two units
    assert stacks([ram(1), ram(2, team=1)], UPT) == {}  # not teammates
    assert stacks([ram(1), ram(2, flying=True)], UPT) == {}  # a flyer is drawn apart
    tesla = unit(uid=3, kind=KIND_BUILDING)
    assert stacks([tesla, ram(1)], UPT) == {}  # a building is not a troop
    # Chained: each within reach of the next, the ends not; the last in the frame is on top.
    chain = [ram(1), ram(2, QUARTER - 1), ram(3, 2 * QUARTER - 2)]
    assert stacks(chain, UPT) == {1: 0, 2: 0, 3: 3}


def board_bytes(r: Renderer, units: list) -> bytes:
    r.draw(frame(units), ViewState(show_targets=False, show_paths=False), Transport())
    x, y, w, h = r.layout.arena
    return pygame.image.tobytes(r.surface.subsurface((x, y, w, h)), "RGB")


def test_a_stack_draws_as_its_top_unit_labelled_with_the_count() -> None:
    r = Renderer(scale=24)
    # Two Ram Riders on one point draw exactly as one unit named "RamRider x2": the one under
    # is covered pixel for pixel, and its label is not printed over the top one's.
    two = board_bytes(r, [ram(1), ram(2)])
    assert two == board_bytes(r, [ram(2, name="RamRider x2")])
    # A quarter tile apart they are two units, each with its own name and no count.
    apart = [ram(1), ram(2, QUARTER)]
    assert board_bytes(r, apart) != board_bytes(r, [ram(1), ram(2, QUARTER, name="RamRider x2")])


def test_no_label_scale_draws_no_count() -> None:
    r = Renderer(scale=12)  # below 16 px a tile, units carry no names, and so no count
    assert board_bytes(r, [ram(1), ram(2)]) == board_bytes(r, [ram(2)])


# --------------------------------------------------------------------- riders


def rider(uid: int, mount: object, **kw: object) -> object:
    return ram(uid, extra={"tower_slot": -1, "mount": mount}, **kw)


def test_a_rider_is_known_only_by_what_the_source_says() -> None:
    assert riders([ram(1), rider(2, 1)]) == {2: 1}
    assert riders([ram(1), ram(2)]) == {}  # no mount said: nothing inferred from names
    assert riders([rider(2, 1)]) == {}  # its mount is not in the frame
    assert riders([ram(1), rider(2, True)]) == {}  # a bool is not a uid
    tesla = unit(uid=3, kind=KIND_BUILDING)
    assert riders([tesla, rider(2, 3)]) == {}  # only a troop is ridden


def test_a_rider_is_drawn_on_its_mount_not_counted_in_a_stack() -> None:
    assert stacks([ram(1), rider(2, 1)], UPT) == {}
    r = Renderer(scale=24)
    seated = board_bytes(r, [ram(1), rider(2, 1)])
    # The frame's order does not matter: the rider is drawn last, on top.
    assert board_bytes(r, [rider(2, 1), ram(1)]) == seated
    # Its seat shows: the board is not the mount alone, and not a stack labelled x2.
    assert seated != board_bytes(r, [ram(1)])
    assert seated != board_bytes(r, [ram(2, name="RamRider x2")])
    # The seat: the team's colour at its centre, a white rim, on the mount's upper half.
    m = ram(1)
    x, y = r.to_px(m.x, m.y, UPT, 0)
    mr = r.unit_radius_px(m, UPT)
    sr, sy = max(3, mr * 11 // 20), y - mr * 2 // 5
    r.draw(frame([ram(1), rider(2, 1)]), ViewState(show_targets=False), Transport())
    assert tuple(r.surface.get_at((x, sy)))[:3] == DEFAULT.team_color(0)
    assert DEFAULT.rider_rim in {tuple(r.surface.get_at((x + sr - d, sy)))[:3] for d in (0, 1)}


# --------------------------------------------------------------------- the small tiles' codes


def test_a_code_is_the_monogram_unless_the_deck_shares_it() -> None:
    deck = ["Witch", "Wizard", "Knight", "Minions", "Miner", "Giant", "GoblinGang", "GoblinGiant"]
    got = tile_codes(deck)
    assert got == {
        "Witch": "Wt", "Wizard": "Wz", "Knight": "Kn", "Minions": "Mi", "Miner": "Me",
        "Giant": "Gi", "GoblinGang": "Ga", "GoblinGiant": "Gn",  # "Gi" is the Giant's
    }  # fmt: skip
    # Two groups whose first differing letters give the same codes (Sk / Su twice).
    four = ["SkeletonArmy", "SuperArcher", "SkeletonKing", "SuperKnight"]
    assert len(set(tile_codes(four).values())) == 4
    # A deck in which no two monograms match shows every monogram, as before.
    plain = ["Knight", "Fireball", "Cannon", "MinionHorde", "Zap", "Golem", "HogRider", "Log"]
    assert tile_codes(plain) == {n: monogram(n) for n in plain}
    assert tile_code("Witch", ("Witch", "Knight")) == "Wi" == monogram("Witch")
    assert tile_code("Witch") == "Wi"  # nothing to tell it apart from
    rr = ("RoyalRecruits", "RoyalRecruits_Chess")
    assert (tile_code(rr[0], rr), tile_code(rr[1], rr)) == ("RR", "Rc")
    # Monograms that differ only in case are told apart too (Skeletons "Sk", SuperKnight "SK").
    sk = tile_codes(("Skeletons", "SuperKnight"))
    assert sk["Skeletons"].upper() != sk["SuperKnight"].upper()


def mini_tile(r: Renderer, p: object) -> bytes:
    r.draw(frame([], [p, player(team=1)]), ViewState(), Transport())
    ex, ey, _, eh = r.layout.bottom_elixir
    return pygame.image.tobytes(r.surface.subsurface((ex + 208, ey + 2, 24, eh - 4)), "RGB")


def test_the_next_tile_tells_apart_two_cards_of_one_deck_that_share_a_monogram() -> None:
    # Witch and Wizard: one monogram, both 5-elixir troops, so today the tiles are identical.
    names = Names([(0, "Witch", 5), (1, "Wizard", 5), (2, "Knight", 3)])
    r = Renderer(scale=24)
    r.cost_of, r.face_of = names.cost_of_name, names.face_of
    deck = ["Witch", "Wizard", "Knight", "Knight", "Knight", "Knight", "Knight", "Knight"]
    hand = ["Knight"] * 4

    def tile(nxt: str, known: bool) -> bytes:
        return mini_tile(
            r, player(hand=hand, next_card=nxt, deck=deck if known else [], deck_known=known)
        )

    assert tile("Witch", True) != tile("Wizard", True)
    # Without the deck the tile cannot know Wizard is a rival: the monogram, as before.
    assert tile("Witch", False) == tile("Wizard", False)


def test_every_code_the_catalogue_can_need_fits_the_tile_and_is_unique() -> None:
    """Every pair of engine cards whose monograms match (case aside), in one deck with each
    third card of the catalogue and with each other such pair: every code differs, case aside,
    and no code that replaces a monogram is wider in the tiny font than the widest monogram
    the 24 px tile already shows."""
    names = [c.name for c in engine_cards()]
    font = Renderer(scale=24).fonts["tiny"]
    rivals = [
        (a, b) for a, b in combinations(names, 2) if monogram(a).upper() == monogram(b).upper()
    ]
    assert rivals  # the catalogue has cards whose monograms match, or this tests nothing
    widest = 0
    decks = [{a, b, c} for a, b in rivals for c in names]
    decks += [{*p, *q} for p in rivals for q in rivals]  # two shared monograms in one deck
    for deck in decks:
        codes = tile_codes(deck)
        assert len({code.upper() for code in codes.values()}) == len(deck), (deck, codes)
        changed = [code for n, code in codes.items() if code != monogram(n)]
        widest = max(widest, *(font.size(code)[0] for code in changed or ["?"]))
    # No wider than a monogram the tile already shows: WitchMother's "WM" is 23 px.
    assert 0 < widest <= max(font.size(monogram(n))[0] for n in names) <= 24, widest
