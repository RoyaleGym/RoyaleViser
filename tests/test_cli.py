"""``python -m royaleviser`` end to end, headless: the sources it opens from the command line,
--seat local, and the same-tick agreement of the two clients of one battle through the app
(the 12:07 captures: 2407 common ticks, 6 differing, as test_sources measures directly).
A second front end reusing ``add_view_arguments`` / ``run_sources`` is tested where it lives."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame
import pytest

from royaleviser import __main__ as cli
from royaleviser.app import App
from royaleviser.render import ViewState
from royaleviser.sources import CaptureSource, StreamSource, TraceSource
from test_sources import CAPTURE_A, CAPTURE_B, needs_captures


def test_source_specs_order_and_limit() -> None:
    p = cli.build_parser()
    a = p.parse_args(["a.jsonl", "b.msgpack", "--stream", ":9870"])
    assert cli.source_specs(a) == [
        ("path", "a.jsonl"),
        ("path", "b.msgpack"),
        ("stream", ("127.0.0.1", 9870)),
    ]
    a = p.parse_args(["--stream", "127.0.0.1:9870", "--compare", "b.jsonl.gz"])
    assert cli.source_specs(a) == [("stream", ("127.0.0.1", 9870)), ("path", "b.jsonl.gz")]
    with pytest.raises(SystemExit):
        cli.main(["a.jsonl", "b.jsonl", "--stream", ":9870"])


@needs_captures
def test_open_sources_from_the_command_line_and_seat_local(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args([str(CAPTURE_B), "--compare", str(CAPTURE_A)])
    main, other = cli.open_sources(args)
    assert isinstance(main, CaptureSource) and isinstance(other, CaptureSource)
    assert (main.local_side, other.local_side) == (1, 0)
    pygame.init()
    app = App([main, other], ViewState(), follow_local=True)
    app.pull()
    assert app.view.seat == 1  # capture B is side 1 local: Red sits at the bottom
    assert app.view.compare_frame is not None and app.view.compare_frame.tick == app.frame.tick
    assert app.view.compare_text.startswith(f"tick {app.frame.tick}: ")
    pygame.quit()
    main.close()
    other.close()

    args = cli.build_parser().parse_args([str(CAPTURE_A), "--seat", "1"])
    (main,) = cli.open_sources(args)
    app = App([main], ViewState(seat=1), follow_local=False)
    app.pull()
    assert app.view.seat == 1
    main.close()

    args = cli.build_parser().parse_args(["--stream", "127.0.0.1:17999"])
    (stream,) = cli.open_sources(args)
    assert isinstance(stream, StreamSource) and stream.live
    stream.close()


@needs_captures
def test_the_two_clients_agree_through_the_app() -> None:
    args = cli.build_parser().parse_args([str(CAPTURE_A), str(CAPTURE_B)])
    main, other = cli.open_sources(args)
    assert isinstance(main, CaptureSource) and isinstance(other, CaptureSource)
    pygame.init()
    app = App([main, other], ViewState(), speed=8.0)
    app.pull()
    while app.source.index < app.source.length - 1:
        app.step(1)
    agree = app.agreement
    # Every tick both captures hold is compared once; the frozen results-screen tick once.
    assert agree.ticks == len(set(main._ticks) & set(other._ticks))
    assert agree.ticks > 2000 and agree.differ <= 10, (agree.ticks, agree.differ)
    assert agree.results[1000][2] == 0 and agree.text(1000).startswith("tick 1000: ")
    assert agree.text(1000).endswith(f"{agree.ticks} ticks compared, {agree.differ} differ")
    ticks, differ = agree.ticks, agree.differ
    app.key(pygame.K_HOME, 0)  # scrubbing back keeps the totals
    assert (agree.ticks, agree.differ) == (ticks, differ) and app.source.index == 0
    pygame.quit()
    main.close()
    other.close()


def test_main_runs_a_trace_headless(tmp_path: Path) -> None:
    from royalegym.replay import save_trace
    from test_sources import record

    path = save_trace(record(20), tmp_path / "t.msgpack")
    shot = tmp_path / "t.png"
    assert cli.main([str(path), "--seconds", "0.3", "--speed", "8", "--shot", str(shot)]) == 0
    assert shot.exists()
    src = TraceSource(path)
    assert src.length == 201 and src.local_side is None
