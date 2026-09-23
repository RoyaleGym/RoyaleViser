"""Put a defect back and prove the test that claims to catch it actually fails.

WHY THIS EXISTS AS A SCRIPT rather than as a sentence in a commit message. "Seen red with the
defect put back" is the evidence every guard here rests on, and it is unfalsifiable once the
defect is removed again. Re-running it found that two of four plants behind one commit had never
landed: one changed a line its test does not assert on, the other anchored on a string that
occurs twice in the file and patched the wrong occurrence. Neither plant looks wrong to a reader.
That is the whole argument for a script: by eye, a plant that misses and a plant that lands are
the same diff, and both end in a green suite.

So a plant counts as evidence only when THREE things are shown, and this refuses to say SAFE
otherwise:

  1. the baseline is green   -- a test already failing proves nothing when it fails again
  2. the plant APPLIED       -- the anchor matched exactly once, so the edit went where intended
  3. the test FAILED         -- and, for a plant aimed at one test, that THAT test failed

Restoring: this rewrites the exact BYTES it read, and never runs ``git checkout``. Sibling
sessions edit these trees at the same time, and a checkout to undo a plant would throw away
whatever they had not committed yet. Bytes rather than text because the first version of this
read and wrote in text mode, which rewrote every line ending on Windows: the content restored
perfectly, ``git diff`` was empty, and the working tree was still left modified for everyone
else. A tool that proves a test works must not itself leave a mark.

    python tools/replant.py            # every plant
    python tools/replant.py max_hp     # the ones whose name contains this
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LF = b"\n"
CRLF = b"\r\n"

GUARDED = "GUARDED"
UNPLANTABLE = "UNPLANTABLE"
BASELINE_RED = "BASELINE_RED"
NOT_GUARDED = "NOT_GUARDED"


@dataclass(frozen=True)
class Plant:
    what: str
    file: str
    good: str  # the anchor, which must occur EXACTLY once
    bad: str  # the defect put back
    test: str


PLANTS = (
    Plant(
        "the engine's target drawn as a unit of the recording's key space",
        "royaleviser/parity.py",
        "target=None if (target < 0 or self.side == ENGINE) else target,",
        "target=None if target < 0 else target,",
        "tests/test_parity.py::test_the_engine_s_target_is_not_pretended_to_be_a_unit_here",
    ),
    Plant(
        "both sides reading the recording's hp column",
        "royaleviser/parity.py",
        'column = "truth" if side == RECORDING else "sim"',
        'column = "truth"',
        "tests/test_parity.py::test_each_side_s_max_hp_is_its_own",
    ),
    Plant(
        "a maximum hp invented from the most the file happened to show",
        "royaleviser/parity.py",
        "max_hp=0,  # a parity file has no maximum; see _max_hp above",
        "max_hp=self._max_hp.get(key, 0),",
        "tests/test_parity.py::test_a_unit_s_maximum_hp_is_not_in_the_file_and_is_not_invented",
    ),
    Plant(
        "overtime one tick early, disagreeing with a recording of the same battle",
        "royaleviser/parity.py",
        "overtime=tick > REGULAR_TICKS,",
        "overtime=tick >= REGULAR_TICKS,",
        "tests/test_parity.py::test_overtime_is_the_same_tick_it_would_be_in_the_recording",
    ),
    Plant(
        # The anchor carries the line BELOW it: `if self.pair_by_uid:` alone occurs twice, at
        # the buffering branch and at the comparison branch, and the buffering one is not what
        # this test is about. That near-miss is why the anchor count is checked, not assumed.
        "the compare pairing by name and distance, which two swapped units satisfy",
        "royaleviser/app.py",
        "            if self.pair_by_uid:\n                d, hp = differing_by_uid(",
        "            if False:\n                d, hp = differing_by_uid(",
        "tests/test_parity.py::test_the_two_sides_are_compared_by_the_key_they_share",
    ),
)


def pytest_passes(target: str) -> bool:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", target],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return done.returncode == 0


def check(plant: Plant) -> str:
    """One of GUARDED, UNPLANTABLE, BASELINE_RED, NOT_GUARDED.

    A verdict rather than a bool, because the three refusals are not interchangeable and a
    caller that only asks "did it refuse?" cannot tell them apart. That is not hypothetical:
    the first version returned a bool, and the test for "a duplicate anchor is refused" passed
    just as happily when the uniqueness check was deleted -- the run then refused for the OTHER
    reason, which is the wrong reason arriving at the right answer.
    """
    path = REPO / plant.file
    original = path.read_bytes()
    good, bad = plant.good.encode(), plant.bad.encode()
    if CRLF in original:  # keep the file's own line endings, whatever they are
        good, bad = good.replace(LF, CRLF), bad.replace(LF, CRLF)

    found = original.count(good)
    if found != 1:
        print(f"  UNPLANTABLE  the anchor occurs {found} times in {plant.file}, not once")
        return UNPLANTABLE
    if not pytest_passes(plant.test):
        print("  BASELINE RED the test already fails, so its failure under the plant is no news")
        return BASELINE_RED

    path.write_bytes(original.replace(good, bad))
    try:
        survived = pytest_passes(plant.test)
    finally:
        path.write_bytes(original)

    if survived:
        print("  NOT GUARDED  the defect is back and the test still passes")
        return NOT_GUARDED
    print("  guarded")
    return GUARDED


def main(argv: list[str]) -> int:
    wanted = [p for p in PLANTS if not argv or any(a in p.what or a in p.file for a in argv)]
    if not wanted:
        print("no plant matches", *argv)
        return 2
    ok = True
    for plant in wanted:
        print(f"{plant.what}\n  {plant.test.split('::')[-1]}")
        ok &= check(plant) == GUARDED
    print()
    print(
        f"SAFE: {len(wanted)} of {len(wanted)} plants landed and were caught"
        if ok
        else "NOT SAFE: see above. A plant that does not land is not a passing test."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
