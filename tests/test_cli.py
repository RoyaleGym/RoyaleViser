"""``python -m royaleviser`` end to end, headless: the sources it opens from the command line,
--seat local, and the same-tick agreement of the two seats of one battle through the app --
on the synthetic recordings (every tick equal), and on the recorded battle where that
recording is present (2407 common ticks, 3 differing, as test_sources measures directly).
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
from test_sources import CAPTURE_A, CAPTURE_B, SYNTH_A, SYNTH_B, SYNTH_TICKS, recorded


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


def test_open_sources_from_the_command_line_and_seat_local(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args([str(SYNTH_B), "--compare", str(SYNTH_A)])
    main, other = cli.open_sources(args)
    assert isinstance(main, CaptureSource) and isinstance(other, CaptureSource)
    assert (main.local_side, other.local_side) == (1, 0)
    pygame.init()
    app = App([main, other], ViewState(), follow_local=True)
    app.pull()
    assert app.view.seat == 1  # recording B is side 1's: Red sits at the bottom
    assert app.view.compare_frame is not None and app.view.compare_frame.tick == app.frame.tick
    assert app.view.compare_text.startswith(f"tick {app.frame.tick}: ")
    pygame.quit()
    main.close()
    other.close()

    args = cli.build_parser().parse_args([str(SYNTH_A), "--seat", "1"])
    (main,) = cli.open_sources(args)
    app = App([main], ViewState(seat=1), follow_local=False)
    app.pull()
    assert app.view.seat == 1
    main.close()

    args = cli.build_parser().parse_args(["--stream", "127.0.0.1:17999"])
    (stream,) = cli.open_sources(args)
    assert isinstance(stream, StreamSource) and stream.live
    stream.close()


def run_compare(a: Path, b: Path) -> tuple[App, CaptureSource, CaptureSource]:
    """The app over two recordings of one battle, stepped to the end, for its totals."""
    args = cli.build_parser().parse_args([str(a), str(b)])
    main, other = cli.open_sources(args)
    assert isinstance(main, CaptureSource) and isinstance(other, CaptureSource)
    pygame.init()
    app = App([main, other], ViewState(), speed=8.0)
    app.pull()
    while app.source.index < app.source.length - 1:
        app.step(1)
    return app, main, other


def test_the_two_seats_agree_through_the_app() -> None:
    app, main, other = run_compare(SYNTH_A, SYNTH_B)
    agree = app.agreement
    # Every tick both recordings hold is compared once; the frozen results-screen tick once.
    assert agree.ticks == len(set(main._ticks) & set(other._ticks)) == SYNTH_TICKS
    assert agree.differ == 0
    assert agree.results[100][2] == 0 and agree.text(100).startswith("tick 100: ")
    assert agree.text(100).endswith(f"{agree.ticks} ticks compared, 0 differ")
    ticks, differ = agree.ticks, agree.differ
    app.key(pygame.K_HOME, 0)  # scrubbing back keeps the totals
    assert (agree.ticks, agree.differ) == (ticks, differ) and app.source.index == 0
    pygame.quit()
    main.close()
    other.close()


@recorded(CAPTURE_A, CAPTURE_B)
def test_the_two_recorded_seats_agree_through_the_app() -> None:
    """The recorded battle from both seats: 2407 ticks compared, 3 differ (tap ticks)."""
    app, main, other = run_compare(CAPTURE_A, CAPTURE_B)
    agree = app.agreement
    assert agree.ticks == len(set(main._ticks) & set(other._ticks)) == 2407
    assert agree.differ == 3
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


def test_the_learning_endpoint_is_paired_with_the_stream() -> None:
    """--stream alone also listens for a learner one port up, so attaching to a training run
    is still one flag; --learning moves it."""
    # NOT the default ports: opening a StreamSource says hello at once, and a hello to 9870
    # half-attaches to whatever run is using it and steals its publisher's peer from a real
    # viewer. The default pair itself is pinned without a socket in test_sources.
    args = cli.build_parser().parse_args(["--stream", "127.0.0.1:17998"])
    (src,) = cli.open_sources(args)
    assert isinstance(src, StreamSource)
    assert (src.peer, src.learning_peer) == (("127.0.0.1", 17998), ("127.0.0.1", 17999))
    src.close()
    args = cli.build_parser().parse_args(
        ["--stream", "127.0.0.1:17998", "--learning", "127.0.0.1:9999"]
    )
    (src,) = cli.open_sources(args)
    assert src.learning_peer == ("127.0.0.1", 9999)
    src.close()


def test_the_learning_endpoint_follows_a_positional_stream_too() -> None:
    """--stream and a bare host:port are the same source, so --learning must reach both."""
    # NOT the default ports, for the reason above.
    args = cli.build_parser().parse_args(["127.0.0.1:17998", "--learning", "127.0.0.1:9999"])
    (src,) = cli.open_sources(args)
    assert isinstance(src, StreamSource) and src.learning_peer == ("127.0.0.1", 9999)
    src.close()
    args = cli.build_parser().parse_args(["127.0.0.1:17998"])
    (src,) = cli.open_sources(args)
    assert src.learning_peer == ("127.0.0.1", 17999)
    src.close()


def test_no_argument_attaches_to_the_default_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """``royaleviser`` alone is "watch the run I just started": the publisher's default port."""
    seen = {}
    monkeypatch.setattr(cli, "run", lambda args: seen.setdefault("args", args) and 0)
    cli.main([])
    assert seen["args"].stream == ("127.0.0.1", 9870) and seen["args"].sources == []


def test_a_folder_opens_its_newest_saved_battle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``royaleviser runs/``: the newest .msgpack / .jsonl / .jsonl.gz anywhere under it, never
    a .json (a folder holds config and metrics JSON too); an empty folder is one clear line."""
    old, new = tmp_path / "a" / "old.msgpack", tmp_path / "b" / "c" / "new.jsonl.gz"
    for i, f in enumerate((old, new, tmp_path / "newest-but-not-a-battle.json")):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        os.utime(f, (1_000_000 + i, 1_000_000 + i))
    assert cli.newest_recording(tmp_path) == new
    seen = {}
    monkeypatch.setattr(cli, "run", lambda args: seen.setdefault("args", args) and 0)
    cli.main([str(tmp_path)])
    assert seen["args"].sources == [str(new)] and seen["args"].stream is None
    (tmp_path / "empty").mkdir()
    with pytest.raises(cli.NoRecording, match="no saved battle under"):
        cli.main([str(tmp_path / "empty")])


def test_the_command_says_one_line_for_what_a_newcomer_hits_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def said(argv: list[str]) -> str:
        monkeypatch.setattr(sys, "argv", ["royaleviser", *argv])
        with pytest.raises(SystemExit) as exit_:
            cli.cli()
        return str(exit_.value.code)

    assert (
        said([str(tmp_path / "nope.msgpack")])
        == f"royaleviser: no such file: {tmp_path / 'nope.msgpack'}"
    )
    assert said([str(tmp_path)]).startswith("royaleviser: no saved battle under")

    def no_gym(args: object) -> int:
        raise ModuleNotFoundError("No module named 'royalegym'", name="royalegym")

    monkeypatch.setattr(cli, "run", no_gym)
    (tmp_path / "t.msgpack").write_bytes(b"x")
    assert 'pip install "royalegym[viser]"' in said([str(tmp_path / "t.msgpack")])


def test_the_version_is_the_installed_one(capsys: pytest.CaptureFixture[str]) -> None:
    from importlib.metadata import version

    import royaleviser

    assert royaleviser.__version__ == version("royaleviser")
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == f"royaleviser {royaleviser.__version__}"
