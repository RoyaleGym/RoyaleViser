"""An arrow from where one side has a unit to where the other side has it.

The first half of sim's option A for the contact law. Direction is the whole reason it exists:
a push the WRONG WAY and a push TOO FAR are the same number in a distance column and nothing
alike as arrows. This half needs no new data -- it reads the two sides a parity trace already
carries. The second arrow, the push the engine actually applied, waits on two fields sim is
adding to its trace rows.
"""

from __future__ import annotations

from royaleviser.model import KIND_TROOP, Frame, Unit
from royaleviser.render import Renderer


def unit(uid: int, x: int, y: int, name: str = "Knight") -> Unit:
    return Unit(
        uid=uid,
        team=0,
        kind=KIND_TROOP,
        name=name,
        x=x,
        y=y,
        hp=100,
        max_hp=100,
        radius=400,
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


def pairs(r: Renderer, a: Frame, b: Frame, tol: int) -> set[int]:
    return {u.uid for u, _ in r.divergence_arrows(a, b, tol)}


def test_only_units_further_apart_than_the_tolerance_get_an_arrow() -> None:
    r = Renderer(scale=16)
    a = frame_of(unit(1, 5000, 5000), unit(2, 6000, 5000))
    b = frame_of(unit(1, 5000, 5000), unit(2, 6300, 5000))
    assert pairs(r, a, b, 400) == set(), "300 apart is inside a 400 tolerance"
    assert pairs(r, a, b, 200) == {2}


def test_the_pairing_is_by_uid_so_a_swap_cannot_read_as_agreement() -> None:
    """The defect the pairing exists to show. Two units of one card that swap places pair with
    each other's positions under a name-and-distance join and the tick reads as agreeing.
    Distinct positions on purpose, so the swap is visible as two full-length arrows."""
    r = Renderer(scale=16)
    a = frame_of(unit(1, 5000, 5000), unit(2, 9000, 5000))
    b = frame_of(unit(1, 9000, 5000), unit(2, 5000, 5000))
    assert pairs(r, a, b, 100) == {1, 2}, "a swap is reading as agreement"


def test_a_unit_on_one_side_only_gets_no_arrow() -> None:
    """It has no direction to draw. Drawing one would put the shape DEATH TIMING makes into
    the shape CONTACT makes, and those are the two causes being told apart."""
    r = Renderer(scale=16)
    a = frame_of(unit(1, 5000, 5000), unit(2, 6000, 5000))
    b = frame_of(unit(1, 5000, 5000))
    assert pairs(r, a, b, 0) == set()


def test_two_sides_that_agree_exactly_produce_nothing_at_tolerance_zero() -> None:
    """The control. A function that returned every unit would satisfy the tests above."""
    r = Renderer(scale=16)
    a = frame_of(unit(1, 5000, 5000), unit(2, 6000, 5000))
    assert pairs(r, a, frame_of(unit(1, 5000, 5000), unit(2, 6000, 5000)), 0) == set()


def test_the_arrow_points_from_this_side_to_the_other() -> None:
    """Direction is the signal, so the order of the pair is load-bearing rather than cosmetic:
    reversed, every finding about which way a unit was pushed is backwards."""
    r = Renderer(scale=16)
    a = frame_of(unit(1, 5000, 5000))
    b = frame_of(unit(1, 7000, 5000))
    (mine, theirs), = r.divergence_arrows(a, b, 100)
    assert (mine.x, theirs.x) == (5000, 7000)
