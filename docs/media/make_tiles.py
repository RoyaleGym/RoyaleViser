#!/usr/bin/env python3
"""The README's `tile-*.png` crops, from the recordings that ship with the tests.

    ..\\..\\..\\.venv\\Scripts\\python docs\\media\\make_tiles.py            # every tile
    ..\\..\\..\\.venv\\Scripts\\python docs\\media\\make_tiles.py compare    # one of them

Every tile here is a crop of the real window drawn on `tests/fixtures`, the scripted battle
as the two seats would have recorded it. No screen and no clock are involved: the window is
rendered off-display and the same source and tick give the same bytes, so a regenerated tile
is a clean diff or no diff at all.

That is the point of the file. These two tiles were hand-taken screenshots of a real match,
which made them the only pictures in the repo that nobody else could reproduce and that no
change to the viewer would ever update. A picture of a window is worth keeping only while it
still shows what the window does.

A tile is a CROP because the window is 1082 x 1029 and a README thumbnail of the whole thing
shows nothing. The crops are named regions rather than magic numbers: see `TILES`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import pygame  # noqa: E402

from royaleviser.app import KEYS, App  # noqa: E402
from royaleviser.render import ViewState  # noqa: E402
from royaleviser.sources import CaptureSource  # noqa: E402

SCALE = 24


def fixtures() -> tuple[Path, Path]:
    import synthetic  # the generator that also writes them; tests check the two agree

    return synthetic.fixture_paths()


def window(
    main: Path, other: Path | None, tick: int, *, scrub: bool = False, **view_kw
) -> pygame.Surface:
    """The whole window, drawn on ``main`` (and ``other`` ghosted) at ``tick``.

    ``scrub`` plays every frame up to ``tick`` first, which is what a person watching does and
    the only way the compare totals are the battle's rather than one frame's.
    """
    pygame.init()
    srcs = [CaptureSource(main)]
    if other is not None:
        srcs.append(CaptureSource(other))
    app = App(srcs, ViewState(**view_kw), scale=SCALE)
    app.renderer.help_lines = KEYS
    end = srcs[0].index_at_tick(tick)
    for i in range(end + 1) if scrub else (end,):
        srcs[0].seek(i)
        app.pull()
    app.draw(0.0)
    for s in srcs:
        s.close()
    return app.renderer.surface.copy()


def crop(surface: pygame.Surface, rect: tuple[int, int, int, int], to: Path) -> None:
    out = pygame.Surface((rect[2], rect[3]))
    out.blit(surface, (0, 0), rect)
    pygame.image.save(out, str(to))
    print(f"{to.name}: {rect[2]}x{rect[3]} from {rect[:2]}")


def compare() -> None:
    """The compare panel at the end of the battle, with the two recordings' agreement on it.

    Both fixtures are the same scripted battle recorded from the two seats, so the honest
    number for them is zero: what the panel is showing is that it CAN say so tick by tick.
    """
    a, b = fixtures()
    import synthetic

    surf = window(a, b, synthetic.LAST_TICK, scrub=True)
    crop(surf, (460, 320, 528, 297), HERE / "tile-compare.png")


def paths_and_targets() -> None:
    """Units with the route they walk and the thing they attack, both drawn."""
    a, _ = fixtures()
    surf = window(a, None, 162, show_paths=True, show_targets=True)
    crop(surf, (345, 225, 432, 243), HERE / "tile-paths-and-targets.png")


TILES = {"compare": compare, "paths-and-targets": paths_and_targets}


def main(argv: list[str]) -> int:
    names = argv or list(TILES)
    unknown = [n for n in names if n not in TILES]
    if unknown:
        print(f"unknown tile(s): {', '.join(unknown)}; have {', '.join(TILES)}", file=sys.stderr)
        return 2
    for name in names:
        TILES[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
