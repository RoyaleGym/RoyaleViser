"""Do this page's links and images resolve for a reader who only has a clone?

Two claim classes from the doc-gates table, both pure shape, neither needing to understand
anything: does every internal link resolve, and does every referenced image exist.

THE POINT IS "IN A CLONE", NOT "ON THIS MACHINE". A working copy carries generated data, scratch
output and files that are gitignored, so a link can resolve perfectly here and 404 for everyone
else. That is not hypothetical: the RoyaleViser README's first code block ran green for a week
because a stale `battle.msgpack` was sitting at the workspace root, and it raised FileNotFoundError
in a clean folder. So the question this asks is whether the target is TRACKED, via `git ls-files`,
and existing on disk is not enough.

What it refuses:

1. A relative link whose target is not tracked. Includes links into other repos, `../RoyaleSim/...`,
   which a reader only has if they cloned that one too; those are reported separately because the
   install tells them to clone all four.
2. An image whose `src` is not tracked. A missing image is invisible in a terminal and obvious on
   GitHub, which is the wrong way round for catching it.
3. An image with no alt text. Not a resolution problem, but it is the same five lines to check and
   a reader using a screen reader gets nothing at all.

What it does NOT check, because a gate oversold is worse than none: whether a link points at the
RIGHT thing, whether an image SHOWS what its caption claims, whether an anchor exists inside a
target file, or anything about external URLs. Those need judgement or a network.

Usage:  python doc_links_check.py <repo> [more repos...]
        python doc_links_check.py --selftest
Exit 0 if every file passes, 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)")
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")
HTML_IMAGE = re.compile(r"<img\b([^>]*)>", re.I)
HTML_SRC = re.compile(r"""\bsrc\s*=\s*["']([^"']+)["']""", re.I)
HTML_ALT = re.compile(r"""\balt\s*=\s*["']([^"']*)["']""", re.I)
EXTERNAL = re.compile(r"^(https?:|mailto:|#)", re.I)


def tracked(repo: str) -> set[str]:
    out = subprocess.run(["git", "-C", repo, "ls-files"], capture_output=True)
    if out.returncode != 0:
        raise SystemExit(f"not a git repo: {repo}")
    return set(out.stdout.decode("utf-8", errors="replace").split())


def resolve(page: str, target: str) -> str:
    """Where a relative target lands, as a repo-root-relative posix path."""
    base = PurePosixPath(page).parent
    joined = (base / target.split("#")[0]).as_posix()
    parts: list[str] = []
    for piece in joined.split("/"):
        if piece in ("", "."):
            continue
        if piece == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(piece)
    return "/".join(parts)


def is_tracked(where: str, files: set[str]) -> bool:
    """A file, or a DIRECTORY that holds one.

    `git ls-files` lists files only, so a link to `examples/` looked untracked and the first run
    of this check refused three correct links in RoyaleGym's README. Linking a directory is normal
    and GitHub renders it, so a target counts as tracked when any tracked file sits under it.
    Getting this wrong is the cry-wolf failure: a check that refuses correct pages is one somebody
    switches off, which is how the defect this harness exists for survived since the repo split.
    """
    if where in files:
        return True
    prefix = where.rstrip("/") + "/"
    return any(f.startswith(prefix) for f in files)


FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[^\n]*\n.*?^\1?\2`*~*[ \t]*$", re.M | re.S)
INLINE_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)


def without_code(text: str) -> str:
    """The prose only, with code blanked out and every newline kept so line numbers still hold.

    A page ABOUT markup contains markup. Both media READMEs explain that "GitHub plays a gif
    inside an `<img>` tag", and the first run of this check refused both of them for an `<img>`
    with no alt text. There is no image there; there is a sentence about images. Same for a link
    written inside a fence as an example. Blanking code spans and fenced blocks is the difference
    between a check that reads a page and one that greps it.
    """
    def blank(m: "re.Match[str]") -> str:
        return "\n" * m.group(0).count("\n")

    return INLINE_CODE.sub(blank, FENCE.sub(blank, text))


def check_page(repo: str, page: str, text: str, files: set[str]) -> list[str]:
    out = []
    text = without_code(text)

    def report(kind: str, target: str, line: int) -> None:
        where = resolve(page, target)
        if where.startswith(".."):
            out.append(
                f"{page}:{line}: {kind} points outside this repo, {target!r}. A reader has it only "
                f"if they cloned that repo too; say so or link the web URL."
            )
        else:
            out.append(
                f"{page}:{line}: {kind} {target!r} is not tracked. It may exist on this machine and "
                f"it will not exist in a clone."
            )

    for m in MD_LINK.finditer(text):
        target = m.group(1)
        if EXTERNAL.match(target):
            continue
        if not is_tracked(resolve(page, target), files):
            report("link", target, text[:m.start()].count(chr(10)) + 1)

    for m in MD_IMAGE.finditer(text):
        alt, target = m.group(1), m.group(2)
        line = text[:m.start()].count(chr(10)) + 1
        if not EXTERNAL.match(target) and not is_tracked(resolve(page, target), files):
            report("image", target, line)
        if not alt.strip():
            out.append(f"{page}:{line}: image {target!r} has no alt text.")

    for m in HTML_IMAGE.finditer(text):
        attrs = m.group(1)
        line = text[:m.start()].count(chr(10)) + 1
        src = HTML_SRC.search(attrs)
        alt = HTML_ALT.search(attrs)
        if src and not EXTERNAL.match(src.group(1)):
            if not is_tracked(resolve(page, src.group(1)), files):
                report("image", src.group(1), line)
        if not alt or not alt.group(1).strip():
            out.append(
                f"{page}:{line}: <img> has no alt text. It is invisible to a screen reader and to "
                f"anyone whose images did not load."
            )
    return out


SELFTEST = [
    ("a link to a tracked file", {"docs/a.md"}, "README.md", "See [a](docs/a.md).", False),
    ("a link to an untracked file", {"docs/a.md"}, "README.md", "See [b](docs/b.md).", True),
    ("an external link is left alone", set(), "README.md", "See [x](https://example.com).", False),
    ("an anchor is left alone", set(), "README.md", "See [x](#status).", False),
    ("an image that is tracked", {"docs/i.png"}, "README.md",
     '<img alt="a thing" src="docs/i.png">', False),
    ("an image that is not tracked", set(), "README.md",
     '<img alt="a thing" src="docs/i.png">', True),
    ("an image with no alt", {"docs/i.png"}, "README.md", '<img src="docs/i.png">', True),
    ("a markdown image with no alt", {"i.png"}, "README.md", "![](i.png)", True),
    ("a link into a sibling repo", set(), "README.md", "See [s](../RoyaleSim/README.md).", True),
    ("a relative path up and down", {"docs/b.md"}, "docs/sub/a.md", "See [b](../b.md).", False),
    # git ls-files lists files, not directories; linking a directory is normal and must pass.
    ("a link to a directory", {"examples/one.py"}, "README.md", "See [e](examples/).", False),
    ("a link to a directory with no slash", {"examples/one.py"}, "README.md", "See [e](examples).", False),
    # A page about markup contains markup. Prose is not a tag and a fence is not a link.
    ("an <img> named in prose", set(), "README.md", "GitHub plays a gif inside an `<img>` tag.", False),
    ("a link written inside a fence", set(), "README.md",
     "Run:\n\n```\nsee [x](nope.md)\n```\n", False),
    ("a real <img> beside prose about one", set(), "README.md",
     'An `<img>` tag.\n\n<img src="gone.png" alt="a thing">', True),
]


def selftest() -> int:
    bad = 0
    for name, files, page, text, must_fail in SELFTEST:
        got = bool(check_page("<repo>", page, text, files))
        ok = got == must_fail
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {'refused' if got else 'passed'} "
              f"({'should refuse' if must_fail else 'should pass'})")
    print(f"\n{len(SELFTEST) - bad} of {len(SELFTEST)} self-tests behaved")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--selftest":
        return selftest()
    if len(argv) < 2:
        raise SystemExit("usage: doc_links_check.py <repo> [more repos...] | --selftest")
    bad = pages = 0
    for repo in argv[1:]:
        files = tracked(repo)
        for page in sorted(f for f in files if f.endswith(".md")):
            with open(f"{repo}/{page}", encoding="utf-8", errors="replace") as fh:
                problems = check_page(repo, page, fh.read(), files)
            pages += 1
            for p in problems:
                print(f"REFUSE {repo}/{p}")
            bad += bool(problems)
    print(f"\n{pages - bad} of {pages} pages passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
