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


def with_push(r: dict, push: list[int] | None) -> dict:
    if push is not None:
        r["push"] = push
    return r


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
    rows = [
        with_push(row(10, 7, "Knight", [1, 1, 5, 1, 0, -1], [1, 1, 5, 0, 0, -1]), [0, 0, 1]),
        with_push(row(10, 8, "Giant", [90000, 1, 5, 1, 0, -1], [90000, 1, 5, 0, 0, -1]), [0, 0, 0]),
    ]
    src = source(tmp_path, rows)
    try:
        f = src.frame()
        # A PARITY TRACE CARRIES NO RADIUS, so the ring is empty on every tick of it and
        # comparing it would report a disagreement whenever the engine saw anything. The check
        # refuses instead of inventing findings; this is the state sim's own traces are in.
        assert r.ring_disagrees_with_the_file(f, 7).startswith("no radii")

        # Given radii, it compares: a ring of 0 against a file saying 1 is the real case.
        for u in f.units:
            u.radius = 400
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
