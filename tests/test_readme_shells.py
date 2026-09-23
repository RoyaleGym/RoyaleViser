"""The published commands have to run in a shell a reader actually has.

On 2026-09-22 this repo's install block ran in NO shell a newcomer would use: PowerShell 5.1
cannot parse ``&&``, ``cmd`` has no ``#`` comment so a trailing one silently becomes four
arguments to ``venv``, and bash eats the backslashes in ``.venv\\Scripts\\python``. The guard in
``tools/shell_fence_check.py`` refuses those shapes. It is a copy rather than a shared import
because a public repo has to be checkable from a fresh clone with nothing else present, and the
reader it protects is exactly the person who has only this clone.

WHAT IS NOT HERE YET: the check over ``README.md`` itself. The prose fix belongs to the session
that owns that file and had not landed when this was written, and a guard wired against a file
somebody else is mid-edit on is a red suite for everyone. The moment it lands, add::

    def test_the_readme_runs_in_a_shell_a_reader_has() -> None:
        assert shell_fence_check.check(README.read_text(encoding="utf-8"), "README.md") == []

At the time of writing that call returns 17 problems, so it is a real assertion, not a formality.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "shell_fence_check.py"


def test_the_guard_still_refuses_what_it_claims_to_refuse() -> None:
    """The copy, checked. A guard that has quietly stopped refusing anything reports a clean
    page forever, so its own plants are what make its silence mean something: four shapes it
    must refuse and three it must not."""
    done = subprocess.run(
        [sys.executable, str(TOOL), "--selftest"], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "7 of 7 self-tests behaved" in done.stdout, done.stdout


def test_the_guard_finds_the_shapes_in_this_repo_s_own_page() -> None:
    """Pointed at the real README, it must return a VERDICT rather than an error.

    This deliberately does not assert the count. The count is the prose session's to drive to
    zero and will change under this file; what must not change is that the guard runs here, on
    this page, from this clone. A guard that crashes on the only document it exists for would
    otherwise look identical to one that has nothing to say.
    """
    done = subprocess.run(
        [sys.executable, str(TOOL), str(REPO / "README.md")], capture_output=True, text=True
    )
    assert done.returncode in (0, 1), done.stderr
    assert "README.md" in done.stdout
    assert "Traceback" not in done.stderr, done.stderr
