"""The push the engine applied, and the check it makes possible.

A's second arrow. Sim added `push: [dx, dy, neighbour_count]` to its trace rows (RoyaleSim
786c738) as its OWN optional field rather than slots on the sim cell, precisely so ABSENT and
ZERO stay different: on the 38,107-row trace it measured, 37,269 rows carry a push, 3,545 of
those are non-zero, and 838 rows carry no push at all because there is no engine entry on that
tick. So a zero is a fact about the tick and a missing field is a fact about the file.

The count is the half that turns the recomputed contact ring into a check. On its own a
wrong-looking ring is equally consistent with this repo's rule being wrong; against the engine's
own count, exactly one of the two is wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

from royaleviser.parity import ENGINE, RECORDING, ParitySource
from test_parity import report, row


def source(tmp_path: Path, rows: list[dict], side: str = ENGINE) -> ParitySource:
    p = tmp_path / "push.parity.json"
    keys = {(r["key"], r["card"]) for r in rows}
    p.write_text(json.dumps(report(rows, [(k, 0, c) for k, c in keys])), encoding="utf-8")
    return ParitySource(p, side)


def with_push(r: dict, push: list[int] | None, radius: int | None = None) -> dict:
    if push is not None:
        r["push"] = push
    if radius is not None:
        r["radius"] = radius
    return r


def test_the_radius_comes_from_the_row_and_reaches_both_sides(tmp_path: Path) -> None:
    """Sim added it per UNIT (RoyaleSim d4a6f5e) because a summoned unit has its own and the
    row's card is the ROOT that produced it: a card table would draw a Witch's Skeletons as
    Witches, wrong in exactly the crowded cases contact is about.

    Both sides get it on purpose. It is a static property of the unit rather than an
    observation of either run, and a recording carries no radius at all, so without this the
    ring is empty on every tick of a parity trace.
    """
    cells = ([1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1])
    rows = [with_push(row(10, 7, "Knight", *cells), [0, 0, 0], radius=600)]
    for side in (ENGINE, RECORDING):
        src = source(tmp_path, rows, side)
        try:
            assert src.frame().unit(7).radius == 600, side
        finally:
            src.close()


def test_a_row_without_a_radius_reads_zero_rather_than_a_guess(tmp_path: Path) -> None:
    """0 is this viewer's word for "not in this source", and it is what makes the contact ring
    REFUSE rather than draw an empty ring that reads as "nothing overlapped". Older traces have
    no radius field at all and must not silently become units of some default size."""
    cells = ([1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1])
    src = source(tmp_path, [with_push(row(10, 7, "Knight", *cells), [0, 0, 0])])
    try:
        assert src.frame().unit(7).radius == 0
    finally:
        src.close()


def test_a_zero_push_is_carried_and_a_missing_one_is_not(tmp_path: Path) -> None:
    """The distinction the field exists for, and the one this repo got wrong once before with
    ``max_hp``: absent must be None, present-and-zero must be a triple."""
    rows = [
        with_push(row(10, 7, "Knight", [1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1]), [0, 0, 0]),
        with_push(row(11, 7, "Knight", [1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1]), None),
    ]
    src = source(tmp_path, rows)
    try:
        assert src.frame().unit(7).extra["push"] == (0, 0, 0)
        src.seek(1)
        assert src.frame().unit(7).extra["push"] is None
    finally:
        src.close()


def test_the_recording_side_never_carries_a_push(tmp_path: Path) -> None:
    """It is the engine's own accumulator. A recording has observed positions and nothing else,
    so attributing one to it would invent a measurement."""
    rows = [with_push(row(10, 7, "Knight", [1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1]), [9, 9, 2])]
    src = source(tmp_path, rows, RECORDING)
    try:
        assert src.frame().unit(7).extra["push"] is None
    finally:
        src.close()


def test_the_order_inside_the_triple_is_dx_dy_count(tmp_path: Path) -> None:
    """Sim's layout, pinned. A reader that guessed would draw the count as a dx the first time
    the order was not what it assumed, and the arrow would look plausible."""
    cells = ([1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1])
    rows = [with_push(row(10, 7, "Knight", *cells), [140, -30, 3])]
    src = source(tmp_path, rows)
    try:
        assert src.frame().unit(7).extra["push"] == (140, -30, 3)
    finally:
        src.close()


def test_the_ring_is_checked_against_the_engines_own_count(tmp_path: Path) -> None:
    """The point of asking for the count. Two units far apart, so the recomputed ring is empty,
    against a file that says the engine saw one neighbour: that is a disagreement and it is
    reported rather than smoothed over."""
    from royaleviser.render import Renderer

    r = Renderer(scale=16)
    near = ([1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1])
    far = ([90000, 1, 5, 1, 0, -1], [90000, 1, 5, 0, 0, -1])
    rows = [
        with_push(row(10, 7, "Knight", *near), [0, 0, 1]),
        with_push(row(10, 8, "Giant", *far), [0, 0, 0]),
    ]
    src = source(tmp_path, rows)
    try:
        f = src.frame()
        # Without radii in the rows the ring is empty on EVERY tick, so comparing it would
        # report a disagreement wherever the engine saw anything. It refuses instead. This is
        # the state every parity trace written before RoyaleSim d4a6f5e is in.
        assert r.ring_disagrees_with_the_file(f, 7).startswith("no radii")
    finally:
        src.close()

    # With radii carried, the check is live and does its job: the two units are 89 tiles apart,
    # so the ring holds nothing while the file says the engine saw one neighbour.
    live = [
        with_push(row(10, 7, "Knight", *near), [0, 0, 1], radius=400),
        with_push(row(10, 8, "Giant", *far), [0, 0, 0], radius=400),
    ]
    src = source(tmp_path, live)
    try:
        f = src.frame()
        assert r.ring_disagrees_with_the_file(f, 7) == "ring 0 / engine 1"
        assert r.ring_disagrees_with_the_file(f, 8) == "", "agreement must be silent"
    finally:
        src.close()


def test_a_unit_whose_file_carries_no_push_reports_no_disagreement(tmp_path: Path) -> None:
    """The control. With no count there is nothing to disagree WITH, and saying so silently is
    correct; saying it loudly would make every older trace look broken."""
    from royaleviser.render import Renderer

    r = Renderer(scale=16)
    rows = [with_push(row(10, 7, "Knight", [1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1]), None)]
    src = source(tmp_path, rows)
    try:
        assert r.ring_disagrees_with_the_file(src.frame(), 7) == ""
    finally:
        src.close()
