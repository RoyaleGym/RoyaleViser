"""Open the real window on the synthetic battle: the look check for render.py and app.py.

    python tests/run_synthetic.py --seconds 8 --shot shot.png
    python tests/run_synthetic.py --seat 1 --compare --start-tick 900 --scale 20

--compare ghosts a second copy of the battle (its units shifted by half a tile) to exercise
the compare panel. Everything else is app.run's arguments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from royaleviser.app import run
from royaleviser.render import ViewState
from synthetic import ListSource, battle


def shifted(frames: list) -> list:
    """The same battle with every unit half a tile to the right (a 'different client')."""
    import copy

    out = []
    for f in frames:
        g = copy.deepcopy(f)
        for u in g.units:
            u.x += g.units_per_tile // 2
        out.append(g)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--shot", default=None)
    ap.add_argument("--seat", type=int, default=0)
    ap.add_argument("--scale", type=int, default=None)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--start-tick", type=int, default=None)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--geometry", default=None, help="WxH+X+Y")
    args = ap.parse_args(argv)
    frames = battle()
    sources = [ListSource(frames, "synthetic")]
    if args.compare:
        sources.append(ListSource(shifted(frames[::2]), "synthetic-shifted"))
    geometry = None
    if args.geometry:
        from royaleviser.__main__ import parse_geometry

        geometry = parse_geometry(args.geometry)
    return run(
        sources,
        ViewState(seat=args.seat),
        geometry=geometry,
        seconds=args.seconds,
        shot=args.shot,
        scale=args.scale,
        speed=args.speed,
        start_tick=args.start_tick,
    )


if __name__ == "__main__":
    sys.exit(main())
