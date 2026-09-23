"""The plant checker, checked. It reports SAFE; this is the evidence it can report anything else.

A tool whose whole job is to catch a plant that did not land is the last place to accept a green
reading on trust -- it grades every other guard here, so a broken one would certify the lot. Both
ways it is meant to refuse are exercised against the real files, and both restore what they read.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import replant  # noqa: E402

# Fast and unrelated to the plants below: only its runtime matters here.
QUICK = "tests/test_parity.py::test_overtime_is_the_same_tick_it_would_be_in_the_recording"


def test_an_anchor_that_is_not_unique_is_refused_rather_than_patched() -> None:
    """The near-miss that hid a dud plant for a whole commit. `if self.pair_by_uid:` occurs at
    two branches, and patching the wrong one leaves the test green and the reader satisfied.

    The verdict is asserted, not merely that it refused. Deleting the uniqueness check makes
    this run refuse anyway -- for the other reason, having patched both branches -- so
    ``assert not check(...)`` passed with the check gone. Seen.
    """
    before = (REPO / "royaleviser/app.py").read_bytes()
    assert (
        replant.check(
            replant.Plant(
                "an anchor with more than one home",
                "royaleviser/app.py",
                "        if self.pair_by_uid:",
                "        if False:",
                QUICK,
            )
        )
        == replant.UNPLANTABLE
    )
    assert (REPO / "royaleviser/app.py").read_bytes() == before, "refusing must not edit the file"


def test_a_plant_that_changes_nothing_the_test_sees_is_reported_not_guarded() -> None:
    """The other half, and the one that matters: the plant APPLIES cleanly and the suite stays
    green. That is indistinguishable from a healthy guard unless the tool says so out loud."""
    target = REPO / "royaleviser/parity.py"
    before = target.read_bytes()
    assert (
        replant.check(
            replant.Plant(
                "a comment nothing asserts on",
                "royaleviser/parity.py",
                "# a parity file has no maximum; see _max_hp above",
                "# a parity file has no maximum (see _max_hp above)",
                QUICK,
            )
        )
        == replant.NOT_GUARDED
    )
    assert target.read_bytes() == before, "the plant must be taken back out again"


def test_the_shipped_plants_are_the_ones_that_were_verified() -> None:
    """Names drift and a renamed test silently stops being checked. Every plant must name a test
    that exists, so a rename is caught here rather than by a plant that quietly cannot run."""
    import subprocess

    listed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "tests/test_parity.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout
    for plant in replant.PLANTS:
        assert plant.test.split("::")[-1] in listed, f"{plant.test} names no test that exists"
