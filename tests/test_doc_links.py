"""Every link on this repo's pages resolves, for a reader on the web as well as a cloner.

WHAT IT CAUGHT. `docs/media/README.md` linked the shared media generator as
`../../../RoyaleGym/docs/media/make_media.py`. That is correct on disk in a side-by-side clone
and the prose was right, but the page is two directories deep and the link climbs three, so it
escapes the repository -- and GitHub does not clamp it. It rewrites the path under this repo's
own blob tree and serves a 404. A cloner was fine and every reader on the web was not, which is
the half of the audience that cannot check.

The checker is `tests/_doc_links.py`, a vendored copy kept byte-identical to the original in the
cross-repo harness, for the same reason as `_shell_fences.py`: a public repo has to be checkable
from a fresh clone with nothing else present, and the four copies only stay useful if they can be
diffed against each other. Do not reformat it.

That is easier to break than it sounds. `ruff check . --fix` silently rewrote one line of this
copy while it was being added -- an annotation it was right about -- and the drift was caught only
because the commit was gated on a byte comparison. The lint rules the copy trips are waived in
`pyproject.toml` so the linter never offers to fix them; the WAIVER is what widens, never the
file.

WHAT IT DOES NOT DO. It never fetches anything. An `http` link is not checked at all, so a dead
external URL passes; what it checks is that a RELATIVE link resolves to a tracked file and that
an image a page references exists. The class it exists for is a link that is right on one
machine and wrong for everyone else, which is the one nobody notices.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _doc_links import check_page, selftest, tracked

REPO = Path(__file__).resolve().parents[1]
CHECKER = Path(__file__).resolve().parent / "_doc_links.py"


def test_every_link_on_every_page_resolves() -> None:
    """Every tracked page, not a named list: a page added later is checked without anyone
    remembering to add it here, which is the failure mode of a hand-kept list of pages."""
    files = tracked(str(REPO))
    pages = sorted(f for f in files if f.endswith(".md"))
    assert pages, "no tracked markdown pages found; the listing is wrong, not the links"

    problems = []
    for page in pages:
        text = (REPO / page).read_text(encoding="utf-8")
        problems += check_page(str(REPO), page, text, files)
    assert not problems, (
        f"{len(problems)} broken link(s) or missing image(s) over {len(pages)} pages:\n  "
        + "\n  ".join(problems)
    )


def test_the_checkers_own_self_test_passes() -> None:
    """Run it, do not assume it. Compared against 0 rather than tested for truthiness, because
    it returns a COUNT: `assert not selftest()` passes on 0 for the wrong reason and would
    format an int as a list on a real failure."""
    assert selftest() == 0, (
        "the link checker's own self-test does not behave; run "
        "`python tests/_doc_links.py --selftest` to see which case"
    )


def test_the_checker_runs_standalone_on_a_fresh_clone() -> None:
    """No pytest, no package, no dependencies. A contributor who has cloned and built nothing
    can still check a page they are editing, which is the whole point of vendoring it."""
    done = subprocess.run(
        [sys.executable, str(CHECKER), "--selftest"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-1500:]


def test_the_checker_is_the_vendored_copy_and_not_a_local_rewrite() -> None:
    """It cannot be compared against the original, which is not in a clone. It can be held to
    the shape that makes it a copy: standard library only, self-test and entry point present.

    The import rule asks `sys.stdlib_module_names` rather than listing known-good names. A
    hardcoded list is a different claim from "ships with Python" and fails on the first correct
    change; that happened on the sibling guard when it legitimately grew `unicodedata`.
    """
    text = CHECKER.read_text(encoding="utf-8")
    assert "def selftest(" in text, "the vendored checker has lost its self-test"
    assert "__main__" in text, "the vendored checker is no longer runnable on its own"
    imports = {
        line.split()[1].split(".")[0]
        for line in text.splitlines()
        if line.startswith(("import ", "from "))
    }
    outside = imports - sys.stdlib_module_names - {"__future__"}
    assert not outside, (
        f"the vendored checker imports {sorted(outside)}, which is not in the standard library, "
        "so it no longer runs on a fresh clone with nothing installed"
    )
