"""Would a reader's shell actually run the commands on this repo's pages?

WHY THIS EXISTS
    On 2026-09-22 the published install block ran in NO shell a newcomer would use, and
    had not since the repos were split. Four readers hit it from four entry points. The
    shapes, each confirmed on a real machine rather than argued: ``&&`` is a parse error
    in Windows PowerShell 5.1 before anything executes; a trailing ``#`` comment is not a
    comment in ``cmd``, so ``python -m venv .venv  # Python 3.12`` silently creates four
    directories and no environment; and a Windows backslash path inside a ```bash fence
    loses its backslashes.

    The second is the dangerous one, because it does not fail. The reader gets no error
    and no working venv.

WHAT IT CHECKS, AND WHAT IT DELIBERATELY DOES NOT
    Only those shapes, on the pages a newcomer actually lands on. It does not read the
    commands and cannot tell you the instructions are correct: the real check is a person
    running the recipe verbatim in PowerShell from an empty folder, which is how this was
    found. This is the guard that stops it coming back through ordinary editing.

    The checker is ``tests/_shell_fences.py``, a vendored copy kept byte-identical to the
    original so the four public repos' copies can be diffed. Its own ``--selftest`` plants
    all four shapes plus three that must NOT fire, and that self-test is run here too: a
    guard whose self-test is never run is a guard nobody has seen work.

THE READER-PAGE CHECK IS NOT WIRED HERE YET, AND THAT IS THE POINT OF THIS PARAGRAPH
    ``README.md`` has 17 of these blocks, the most of the four repos. The prose repair
    belongs to the session that owns that file and had not landed when this was written,
    and a guard pointed at a page somebody else is mid-edit on is a red suite for every
    session in the tree. The moment the repair lands, add::

        READER_PAGES = ("README.md",)

        @pytest.mark.parametrize("page", READER_PAGES)
        def test_a_reader_could_paste_this_page_into_their_shell(page: str) -> None:
            problems = check_text((REPO / page).read_text(encoding="utf-8"), page)
            assert not problems, "\\n  ".join(problems)

    It returns 17 problems today, so it is a real assertion rather than a formality, and
    leaving it out is the one gap in this file. Do not let it be forgotten: the guard's
    whole value is on that page, and everything below only proves the guard still works.

A GUARD WITH FALSE POSITIVES IS ONE SOMEBODY SWITCHES OFF
    The checker's first version demanded a named shell near EVERY block rather than once
    per page, so it flagged the two pages that had just been fixed. That was corrected
    before it shipped. If this ever goes red on a page a person has just verified by
    hand, suspect the guard before the page.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _shell_fences import check_text, selftest

REPO = Path(__file__).resolve().parents[1]
CHECKER = Path(__file__).resolve().parent / "_shell_fences.py"


def test_the_guards_own_self_test_passes() -> None:
    """It plants all four shapes and three that must not fire. Run it, do not assume it.

    Compared against 0 rather than tested for truthiness: ``selftest`` returns a COUNT,
    and ``assert not selftest()`` would pass on 0 for the wrong reason while a real
    failure formatted an int as a list and raised, turning a finding into a confusing
    error. Gym hit exactly that while writing its copy.
    """
    assert selftest() == 0, (
        "the shell-fence guard's own self-test does not behave. Its printed lines say "
        "which planted shape went unrefused, or which safe page was refused; run "
        "`python tests/_shell_fences.py --selftest` to see them."
    )


def test_the_guard_runs_standalone_on_a_fresh_clone() -> None:
    """No pytest, no package, no dependencies: ``python tests/_shell_fences.py --selftest``.

    That is the whole point of vendoring it. A contributor who has cloned the repo and
    not yet built anything can still check a page they are editing.
    """
    done = subprocess.run(
        [sys.executable, str(CHECKER), "--selftest"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
    )
    assert done.returncode == 0, (
        f"the vendored checker does not run standalone (exit {done.returncode}):\n"
        f"{(done.stdout + done.stderr)[-1500:]}"
    )


def test_the_checker_is_the_vendored_copy_and_not_a_local_rewrite() -> None:
    """It is a copy on purpose, and a copy that has drifted is worth knowing about.

    This cannot compare against the original, which lives outside this repo and is not
    present in a clone. What it can do is hold the copy to the shape that makes it a
    copy: no imports beyond the standard library, so it runs anywhere, and the self-test
    and entry point still present so a reader can run it.
    """
    text = CHECKER.read_text(encoding="utf-8")
    assert "def selftest(" in text, "the vendored checker has lost its self-test"
    assert "__main__" in text, "the vendored checker is no longer runnable on its own"
    imports = {
        line.split()[1].split(".")[0]
        for line in text.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    }
    assert imports <= {"re", "sys", "pathlib", "__future__"}, (
        f"the vendored checker imports {sorted(imports - {'re', 'sys', 'pathlib', '__future__'})}, "
        "so it no longer runs on a fresh clone with nothing installed"
    )


def test_the_guard_still_has_something_to_say_about_this_repo_s_own_page() -> None:
    """The stand-in for the missing reader-page check, and it is NOT the same promise.

    It asserts only that the guard RUNS on README.md and returns a verdict rather than
    an exception. A guard that crashes on the only document it exists for looks identical
    to one with nothing to say, and that is the state this file must not sit in quietly
    while the real check is waiting on another session.
    """
    problems = check_text((REPO / "README.md").read_text(encoding="utf-8"), "README.md")
    assert isinstance(problems, list)
    assert all(p.startswith("README.md:") for p in problems), problems
