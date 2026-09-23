"""The numbers the README and the technical doc quote about this repo, against this repo.

Three counts went stale here in one afternoon, and one of them had never been right at the
commit it cited. Every time, the count was true when written and nothing made it false out
loud: tests were added, the documents were not re-measured, and the first number a reader of
a public repo sees drifted seventeen tests behind.

A doc cannot be re-measured by a test -- running the suite from inside the suite is not a
thing to do -- but the number that MOVES can be. Adding or removing a test changes what
``--collect-only`` counts, so pinning that against the documents fails the moment they go out
of date, and the failure says to re-measure rather than to edit a number.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
INTERNALS = REPO / "docs" / "internals.md"


@pytest.fixture(scope="module")
def collected() -> int:
    """What ``pytest --collect-only -q`` says right now, which is what the docs quote."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    match = re.search(r"(\d+) tests? collected", out.stdout)
    assert match, out.stdout[-2000:]
    return int(match.group(1))


#: "N passed, M skipped", in prose or inside a shields.io badge URL (%2C is its comma).
#
# WHAT THIS PATTERN DOES NOT SEE, and it is deliberate rather than an oversight. It requires the
# two words adjacent with a comma or an underscore between them, so "135 passed and 7 skipped"
# and any pair split across a line break are invisible to it. The README carries exactly one such
# sentence, the BARE-CLONE figure, and it is outside this check ON PURPOSE: this guard's whole
# model is that every documented pair describes ONE suite and therefore sums to the collected
# total, and the bare-clone run is a different population -- whole modules skip at import, so it
# sums to 142 where the others sum to 202. Widening the pattern without teaching it about
# populations would make it fail on a sentence that is correct.
#
# So: the two full-stack figures are guarded and the bare-clone one is not. If that third figure
# goes stale, nothing here catches it.
COUNT = r"(\d+)[ _]passed(?:%2C)?,?[ _](\d+)[ _]skipped"


def counts_in(text: str) -> list[tuple[int, int]]:
    """Every "N passed, M skipped" pair in a document, in the order they appear."""
    return [(int(p), int(s)) for p, s in re.findall(COUNT, text)]


def test_every_documented_suite_count_adds_up_to_what_is_collected(collected: int) -> None:
    """A "N passed, M skipped" pair describes one run of the whole suite, so N + M is the
    number of tests there are. Any pair that does not add up is from a different tree."""
    pytest.importorskip(
        "royalegym",
        reason=(
            "SKIPPED, NOT PASSED: the documented counts describe a FULL-STACK install and this "
            "environment has no royalegym, so whole modules skip at import and the collected "
            "total is a different population. The figures are not wrong here; this check has "
            "nothing to compare them against. See the README's Setup for the three cases."
        ),
    )
    for path in (README, INTERNALS):
        pairs = counts_in(path.read_text(encoding="utf-8"))
        assert pairs, f"{path.name} quotes no suite count at all; it used to quote three"
        for passed, skipped in pairs:
            assert passed + skipped == collected, (
                f"{path.name} says {passed} passed / {skipped} skipped, which is a suite of "
                f"{passed + skipped}; this one collects {collected}. Re-measure with "
                f"`pytest -q` and with ROYALELIVE_REPORTS pointed at an empty folder, and "
                f"write both down with the commit they came from."
            )


def test_the_two_documents_agree_with_each_other(collected: int) -> None:
    """They describe the same two runs, so they must quote the same two pairs. They did not:
    one said a clone gives 119 passed and the other 94, sixteen commits apart."""
    in_readme = set(counts_in(README.read_text(encoding="utf-8")))
    in_internals = set(counts_in(INTERNALS.read_text(encoding="utf-8")))
    assert in_readme == in_internals, (
        f"README.md quotes {sorted(in_readme)} and docs/internals.md quotes "
        f"{sorted(in_internals)}; both describe this one suite of {collected}."
    )


NL = chr(10)
#: A git object name as the documents write one: seven hex characters or more, on its own.
SHA = r"\b[0-9a-f]{7,40}\b"


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) for each markdown section, so a count and its commit are judged in the
    block a reader reads them in rather than within some number of lines of each other."""
    out: list[tuple[str, str]] = []
    heading, body = "(top)", []
    for line in text.splitlines():
        if re.match(r"#{1,6} ", line):
            out.append((heading, NL.join(body)))
            heading, body = line.lstrip("# ").strip(), []
        else:
            body.append(line)
    out.append((heading, NL.join(body)))
    return out


def a_commit_here(sha: str) -> bool:
    """Whether ``sha`` names a commit in THIS repository."""
    out = subprocess.run(["git", "cat-file", "-t", sha], cwd=REPO, capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.strip() == "commit"


def test_the_documents_quote_the_commit_their_numbers_came_from() -> None:
    """A count without the command and the commit behind it is not evidence, and this repo's
    own rule says so. The commit is what lets the next reader tell stale from wrong.

    This used to assert only that a seven-character hex token appeared SOMEWHERE in the
    document, which the bare number 3690000 satisfies with no commit in it at all: a check
    standing next to the thing it claims to grade, which is the class of defect this file
    exists to catch. So every SECTION that quotes a count has to name a commit, and that
    commit has to resolve in this repository.
    """
    if not a_commit_here("HEAD"):
        pytest.skip("SKIPPED, NOT PASSED: not a git work tree, so a commit cannot be resolved")
    for path in (README, INTERNALS):
        for heading, section in sections(path.read_text(encoding="utf-8")):
            if not re.search(COUNT, section):
                continue
            shas = {sha for sha in re.findall(SHA, section) if not sha.isdigit()}
            assert shas, (
                f"{path.name}, under {heading!r}: suite counts with no commit in the same "
                "section; a seven-digit number is not a commit"
            )
            assert [sha for sha in shas if a_commit_here(sha)], (
                f"{path.name}, under {heading!r}: names {sorted(shas)} beside its counts and "
                "none is a commit in this repository"
            )


def test_the_window_is_the_size_the_media_generator_says_it_is() -> None:
    """``docs/media/make_tiles.py`` justifies cropping with the window's size, and the crops
    are only right if that size is."""
    from royaleviser.render import Renderer

    text = (REPO / "docs" / "media" / "make_tiles.py").read_text(encoding="utf-8")
    match = re.search(r"the window is (\d+) x (\d+)", text)
    assert match, "make_tiles.py no longer says what size the window is"
    assert (int(match.group(1)), int(match.group(2))) == Renderer(scale=24).layout.window
