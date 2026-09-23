"""Which tick FIRST disagreed, which is the only one a causal law can be read from.

A difference count says how far apart two runs are now. After the first disagreement that is
mostly accumulated drift: a unit pushed wrongly at tick 900 is still in the wrong place at 1400
without anything new having gone wrong. Sim is working the contact law, spawn point and death
timing, and for each of those the question is the tick where the inputs on both sides were still
the same and the outputs stopped matching.

Built against the schema the replay harness writes, from the rows the file already holds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from royaleviser.parity import ENGINE, RECORDING, ParitySource
from test_parity import report, row


def source(tmp_path: Path, rows: list[dict], side: str = RECORDING) -> ParitySource:
    p = tmp_path / "d.parity.json"
    keys = {(r["key"], r["card"]) for r in rows}
    p.write_text(json.dumps(report(rows, [(k, 0, c) for k, c in keys])), encoding="utf-8")
    return ParitySource(p, side)


def test_the_first_tick_that_disagrees_is_the_one_reported(tmp_path: Path) -> None:
    """Three ticks agree, the fourth does not, and two later ones are worse. The worst tick is
    not the answer; the first one is."""
    rows = [
        row(10, 7, "Knight", [1000, 1000, 500, 1, 0, -1], [1000, 1000, 500, 0, 0, -1]),
        row(11, 7, "Knight", [1100, 1000, 500, 1, 0, -1], [1100, 1000, 500, 0, 0, -1]),
        row(12, 7, "Knight", [1200, 1000, 500, 1, 0, -1], [1260, 1000, 500, 0, 0, -1]),
        row(13, 7, "Knight", [1300, 1000, 500, 1, 0, -1], [1900, 1000, 500, 0, 0, -1]),
    ]
    src = source(tmp_path, rows)
    try:
        assert src.first_divergence(tolerance=50) == (12, 7, 60)
        # And with a tolerance that forgives tick 12, the answer MOVES to 13 rather than
        # staying put: the tolerance is part of the question, not a filter on the answer.
        assert src.first_divergence(tolerance=100) == (13, 7, 600)
    finally:
        src.close()


def test_a_unit_present_on_one_side_only_is_reported_as_a_presence_difference(
    tmp_path: Path,
) -> None:
    """No tolerance can excuse it, so it must not be folded into a distance. Death timing is
    exactly this shape: the unit is alive on one side and gone on the other."""
    rows = [
        row(10, 7, "Knight", [1000, 1000, 500, 1, 0, -1], [1000, 1000, 500, 0, 0, -1]),
        row(11, 7, "Knight", [1100, 1000, 300, 1, 0, -1], None),
    ]
    src = source(tmp_path, rows)
    try:
        assert src.first_divergence(tolerance=10**6) == (11, 7, None)
    finally:
        src.close()


def test_a_unit_absent_from_both_sides_is_not_a_disagreement(tmp_path: Path) -> None:
    """The control. A row where neither side has the unit says both runs agree it is gone,
    and counting it would make every battle diverge at its first death."""
    rows = [
        row(10, 7, "Knight", None, None),
        row(11, 7, "Knight", [1100, 1000, 300, 1, 0, -1], [1100, 1000, 300, 0, 0, -1]),
    ]
    src = source(tmp_path, rows)
    try:
        assert src.first_divergence(tolerance=0) is None
    finally:
        src.close()


def test_one_unit_can_be_asked_about_without_the_others_answering(tmp_path: Path) -> None:
    """The pinned-unit case. A noisy neighbour diverging first must not answer for the unit
    being looked at, which is what makes this usable while hunting one law."""
    rows = [
        row(10, 7, "Knight", [1000, 1000, 500, 1, 0, -1], [9000, 1000, 500, 0, 0, -1]),
        row(10, 8, "Giant", [2000, 2000, 500, 1, 0, -1], [2000, 2000, 500, 0, 0, -1]),
        row(11, 8, "Giant", [2100, 2000, 500, 1, 0, -1], [2400, 2000, 500, 0, 0, -1]),
    ]
    src = source(tmp_path, rows)
    try:
        assert src.first_divergence(tolerance=50) == (10, 7, 8000)  # the whole battle
        assert src.first_divergence(key=8, tolerance=50) == (11, 8, 300)  # just the Giant
        assert src.first_divergence(key=8, tolerance=500) is None  # and it can be forgiven
    finally:
        src.close()


def test_two_runs_that_never_disagree_report_nothing(tmp_path: Path) -> None:
    """The other control. A function that always names a tick would satisfy every test above."""
    rows = [
        row(t, 7, "Knight", [1000 + t, 1000, 500, 1, 0, -1], [1000 + t, 1000, 500, 0, 0, -1])
        for t in (10, 11, 12)
    ]
    src = source(tmp_path, rows)
    try:
        assert src.first_divergence(tolerance=0) is None
    finally:
        src.close()


@pytest.mark.parametrize("side", [RECORDING, ENGINE])
def test_the_answer_does_not_depend_on_which_side_opened_the_file(
    tmp_path: Path, side: str
) -> None:
    """The two sides are two views of ONE comparison. A first divergence that moved depending
    on which ParitySource was asked would mean the question was about the view, not the data.
    """
    rows = [
        row(10, 7, "Knight", [1000, 1000, 500, 1, 0, -1], [1000, 1000, 500, 0, 0, -1]),
        row(11, 7, "Knight", [1100, 1000, 500, 1, 0, -1], [1400, 1000, 500, 0, 0, -1]),
    ]
    src = source(tmp_path, rows, side)
    try:
        assert src.first_divergence(tolerance=50) == (11, 7, 300)
    finally:
        src.close()
