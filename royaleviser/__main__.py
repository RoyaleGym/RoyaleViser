"""RoyaleViser: view a capture, a trace or a running engine.

    python -m royaleviser frames-<label>-<stamp>.jsonl.gz
    python -m royaleviser trace.msgpack --speed 4 --start-tick 600
    python -m royaleviser --stream 127.0.0.1:9870
    python -m royaleviser a.jsonl --compare b.jsonl --seat 0 --shot shot.png --seconds 2

One positional source (or --stream) is the primary; a second one (a second positional, or
--compare) is ghosted onto the board at the same tick and compared with it tick by tick
(two clients of the same battle, or a trace against a capture). --seat local seats the
primary's local player at the bottom (the side whose hand the capture holds; team 0 for a
trace or a stream); --geometry is the window's outer rectangle in physical pixels, for a
caller that places the window itself; --seconds N and --shot PATH exist for unattended tests.

Another front end can reuse this module: it builds its own sources, adds the view options
below with ``add_view_arguments`` and runs the same window through ``run_sources``.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

from .app import KEYS
from .app import run as run_app
from .model import Names, Source
from .render import ViewState

TITLE = "RoyaleViser"  # the window title; a caller that places the window finds it by this

GEOMETRY = re.compile(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$")


def parse_geometry(text: str) -> tuple[int, int, int | None, int | None]:
    """'WxH+X+Y' (X and Y optional) -> (w, h, x, y); the X11 form."""
    m = GEOMETRY.match(text)
    if m is None:
        raise argparse.ArgumentTypeError(f"geometry {text!r} is not WxH or WxH+X+Y")
    w, h, x, y = m.groups()
    return int(w), int(h), (int(x) if x is not None else None), (int(y) if y is not None else None)


def parse_seat(text: str) -> str | int:
    if text == "local":
        return text
    if text in ("0", "1"):
        return int(text)
    raise argparse.ArgumentTypeError(f"seat {text!r} is not local, 0 or 1")


def parse_endpoint(text: str) -> tuple[str, int]:
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit():
        raise argparse.ArgumentTypeError(f"{text!r} is not host:port")
    return host or "127.0.0.1", int(port)


def add_view_arguments(p: argparse.ArgumentParser) -> None:
    """The window options every front end shares: --seat, --geometry, --scale, --speed,
    --start-tick, --seconds, --shot (``run_sources`` reads them back)."""
    p.add_argument("--seat", type=parse_seat, default="local", help="local (default), 0 or 1")
    p.add_argument(
        "--geometry",
        type=parse_geometry,
        metavar="WxH+X+Y",
        help="outer window rectangle in physical pixels; default is the layout's own size",
    )
    p.add_argument("--scale", type=int, default=None, metavar="N", help="pixels per tile (24)")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier (1.0)")
    p.add_argument("--start-tick", type=int, default=None, help="first frame to show (replays)")
    p.add_argument("--seconds", type=float, default=None, help="quit by itself after N seconds")
    p.add_argument("--shot", metavar="PATH", help="save a PNG of the window before quitting")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="royaleviser",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="keys:\n" + "\n".join(f"  {k:<14} {a}" for k, a in KEYS),
    )
    p.add_argument(
        "sources",
        nargs="*",
        help="capture (.jsonl, .jsonl.gz) or trace (.msgpack, .json); a second one is compared",
    )
    p.add_argument(
        "--stream",
        type=parse_endpoint,
        metavar="HOST:PORT",
        help="a running engine's publisher to attach to (royalegym.viser.ViserPublisher)",
    )
    p.add_argument(
        "--compare",
        metavar="SOURCE",
        help="a second capture/trace/host:port ghosted onto the board at the same tick",
    )
    add_view_arguments(p)
    return p


def source_specs(args: argparse.Namespace) -> list[tuple[str, Any]]:
    """[(kind, spec)] in priority order: positionals, then the stream; --compare is second."""
    specs: list[tuple[str, Any]] = [("path", s) for s in args.sources]
    if args.stream is not None:
        specs.append(("stream", args.stream))
    if args.compare:
        specs.insert(1, ("path", args.compare))
    return specs


def open_sources(args: argparse.Namespace) -> list[Source]:
    """The primary and, when given, the compare source; every opened source is closed when a
    later one fails to open."""
    from .sources import StreamSource, open_source

    specs = source_specs(args)
    names = Names.live()
    sources: list[Source] = []
    try:
        for kind, spec in specs[:2]:
            if kind == "path":
                sources.append(open_source(spec, names))
            else:
                sources.append(StreamSource(*spec))
    except BaseException:
        for s in sources:
            s.close()
        raise
    return sources


def run_sources(sources: list[Source], args: argparse.Namespace) -> int:
    """Build the window over already opened sources with the ``add_view_arguments`` options
    and run the loop. Returns the process exit code."""
    view = ViewState(seat=args.seat if args.seat != "local" else 0)
    return run_app(
        sources,
        view,
        geometry=args.geometry,
        seconds=args.seconds,
        shot=args.shot,
        scale=args.scale,
        speed=args.speed,
        start_tick=args.start_tick,
        title=TITLE,
        follow_local=args.seat == "local",
    )


def run(args: argparse.Namespace) -> int:
    """Open the sources, build the window, run the loop. Returns the process exit code."""
    return run_sources(open_sources(args), args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.sources and args.stream is None:
        parser.error("give a capture/trace path or --stream")
    if len(source_specs(args)) > 2:
        parser.error("at most two sources: a primary and one to compare with")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
