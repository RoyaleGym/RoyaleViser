r"""No tracked text file in this repo carries a character a reader cannot see.

WHY, AND WHY OVER THE WHOLE REPO RATHER THAN THE READER PAGES
    A backslash escape written through a shell heredoc becomes the byte it names. A
    backslash-b becomes 0x08. It survives every reading that renders the file -- an editor,
    ``git diff``, a terminal, a failure message -- and only the bytes show it. The project
    hit this five times on 2026-09-22, and THREE of those were in the text describing the
    trap, including one in this repo's own handoff and one in the guard written to catch it.

    The shell-fence guard checks the pages a reader pastes from. That is the right scope for
    it and the wrong scope for this: the instance that motivated this file was in a ``.py``,
    and the fifth was inside a checker's source. A rule that exempts the code enforcing it is
    the shape this project keeps finding.

WHAT COUNTS, AND THE ONE THAT IS NOT A CATEGORY
    ``Cc`` and ``Cf`` catch the control and format characters, which is where a mangled
    escape lands, and asking the character what it IS rather than where it sits also closes
    DEL and the C1 range that a numeric ``< 0x20`` bound misses.

    NBSP is the exception and it cannot be done by category: ``unicodedata.category`` calls
    it ``Zs``, exactly like a plain space. It has to be named, and it must be named as an
    ESCAPE. The canonical checker names it as a literal NBSP, which behaves correctly and
    reads in an editor as ``ch == " "``; any whitespace cleanup would turn it into exactly
    that and the rule would then refuse every space on every page -- 4157 of them in this
    repo's README alone. That copy is deliberately NOT vendored here until it is an escape.
"""

from __future__ import annotations

import subprocess
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Built from CODE POINTS, never written as the characters themselves and never as string
#: escapes either. The first version of this line used "\\u00a0" and friends; whatever wrote the
#: file resolved them, so the set naming seven invisible characters was SPELT with seven
#: invisible characters. It went unnoticed until the file was committed, because the sweep reads
#: `git ls-files` and an untracked file is not swept -- so the guard could not see itself until
#: it was tracked, and then caught itself on the first run. chr() cannot be resolved by an editor,
#: a heredoc or a paste.
ALLOWED = {chr(0x09), chr(0x0A), chr(0x0D)}
NAMED_INVISIBLE = {chr(c) for c in (0x00A0, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x202F)}


def tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, timeout=120
    ).stdout.split("\0")
    out = []
    for rel in filter(None, listed):
        path = REPO / rel
        try:
            if b"\0" in path.read_bytes()[:4096]:  # binary
                continue
        except OSError:
            continue
        out.append(path)
    return out


def test_no_tracked_text_file_carries_an_invisible_character() -> None:
    files = tracked_text_files()
    assert len(files) > 20, f"only {len(files)} tracked text files found; the listing is wrong"

    problems = []
    for path in files:
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path.relative_to(REPO)}: not readable as UTF-8 ({exc})")
            continue
        for i, ch in enumerate(text):
            if ch in ALLOWED:
                continue
            if unicodedata.category(ch) in ("Cc", "Cf") or ch in NAMED_INVISIBLE:
                line = text[:i].count("\n") + 1
                name = unicodedata.name(ch, "unnamed")
                problems.append(
                    f"{path.relative_to(REPO)}:{line}: U+{ord(ch):04X} {name}, which a reader "
                    "cannot see. A shell heredoc turns a backslash escape into the byte it "
                    "names; write the file with an editing tool, or use a quoted heredoc."
                )

    assert not problems, "\n  ".join([f"{len(problems)} invisible character(s):", *problems])
