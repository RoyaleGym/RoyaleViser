"""The neighbours a separation step is a function of, RECOMPUTED rather than recorded.

Sim asked for this for the contact law (31.0 % of its missed unit-ticks) on the explicit
understanding that no source records which neighbours the engine's scan actually saw, nor the
push it applied. So this is what the law SHOULD have been looking at. When the engine's
displacement does not match the ring, either the ring is wrong or the scan is, and that
disagreement is the finding. Sim is adding the applied push vector to its trace rows so the
other half stops being inferred.

The rule under test, from the measured contact law: every overlapping neighbour of EITHER side,
buildings and towers included, touching counts (``d2 <= (R1+R2)^2``), and a mover's own radius is
capped at 500 against a static.
"""

from __future__ import annotations

from royaleviser.model import KIND_BUILDING, KIND_TROOP, Frame, Unit
from royaleviser.render import Renderer


def unit(uid: int, x: int, y: int, r: int, kind: int = KIND_TROOP) -> Unit:
    return Unit(
        uid=uid,
        team=0,
        kind=kind,
        name=f"u{uid}",
        x=x,
        y=y,
        hp=100,
        max_hp=100,
        radius=r,
        flying=False,
        deploy_ticks=0,
        stun_ticks=0,
        target=None,
        path=[],
        direction=0,
        state="",
    )


def frame_of(*units: Unit) -> Frame:
    return Frame(
        tick=1,
        tick_ms=50,
        units_per_tile=1000,
        players=[],
        units=list(units),
        spells=[],
        overtime=False,
        game_over=False,
        winner=-1,
        crowns=[0, 0],
        events=[],
    )


def names(r: Renderer, f: Frame, uid: int) -> set[int]:
    return {u.uid for u in r.contact_neighbours(f, uid)}


def test_circles_that_overlap_are_neighbours_and_ones_that_clear_are_not() -> None:
    r = Renderer(scale=16)
    f = frame_of(
        unit(1, 5000, 5000, 400),
        unit(2, 5700, 5000, 400),  # 700 apart, radii sum 800: overlapping
        unit(3, 6500, 5000, 400),  # 1500 apart: clear
    )
    assert names(r, f, 1) == {2}


def test_touching_counts_because_the_engine_counts_it() -> None:
    """``d2 <= (R1+R2)^2``, not ``<``. Exactly touching is the boundary case the rule names,
    and getting it wrong would drop the neighbour at the moment contact begins -- which is
    precisely the tick a contact law is read from."""
    r = Renderer(scale=16)
    f = frame_of(unit(1, 5000, 5000, 400), unit(2, 5800, 5000, 400))  # 800 == 400 + 400
    assert names(r, f, 1) == {2}

    apart = frame_of(unit(1, 5000, 5000, 400), unit(2, 5801, 5000, 400))
    assert names(r, apart, 1) == set(), "one unit past touching is still a neighbour"


def test_a_movers_radius_is_capped_against_a_static(deny: None = None) -> None:
    """The rule that makes a unit pressed against a tower behave as if it were smaller.

    A mover with radius 900 next to a building with radius 400: uncapped the reach is 1300 and
    they overlap at 1200 apart; capped at 500 the reach is 900 and they do not. The cap is the
    difference between the two answers, so a test that used a small mover could not see it.
    """
    r = Renderer(scale=16)
    f = frame_of(unit(1, 5000, 5000, 900), unit(2, 6200, 5000, 400, KIND_BUILDING))
    assert names(r, f, 1) == set(), "the mover's radius was not capped against the static"

    # The same pair as two TROOPS is not capped, and they do overlap.
    troops = frame_of(unit(1, 5000, 5000, 900), unit(2, 6200, 5000, 400))
    assert names(r, troops, 1) == {2}


def test_a_unit_is_not_its_own_neighbour_and_a_sizeless_one_is_skipped() -> None:
    """Two controls. Without the first every unit has at least one neighbour; without the
    second a source that carries no radius reports the whole board as touching."""
    r = Renderer(scale=16)
    f = frame_of(unit(1, 5000, 5000, 400), unit(2, 5000, 5000, 0))
    assert names(r, f, 1) == set()
    assert r.contact_neighbours(f, 2) == [], "a unit with no radius has no contact circle"


def test_an_unknown_uid_asks_for_nothing_rather_than_raising() -> None:
    """The window asks for whatever is pinned, and a pin can outlive the unit it named."""
    r = Renderer(scale=16)
    assert r.contact_neighbours(frame_of(unit(1, 5000, 5000, 400)), 99) == []
