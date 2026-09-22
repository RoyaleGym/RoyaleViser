"""``capture``: what the window would show, written to a file with no window.

Runs on the scripted battle, so a fresh clone tests all of it. The mp4 and gif tests need
ffmpeg (``pip install royaleviser[media]``) and skip, saying so, when it is not installed --
the PNG paths, which are what the rest of the module is, run either way.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from royaleviser import capture as cap
from royaleviser import sources
from royaleviser.render import Renderer, ViewState
from royaleviser.sources import STREAM_HOST, StreamSource
from synthetic import ListSource, battle, fixture_paths

SYNTH_A = fixture_paths()[0]

FRAMES = battle()
HAS_FFMPEG = importlib.util.find_spec("imageio_ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(
    not HAS_FFMPEG,
    reason="mp4/gif need ffmpeg: pip install royaleviser[media] (imageio-ffmpeg)",
)


def source() -> ListSource:
    return ListSource(FRAMES, "synthetic")


def test_the_three_crops_are_the_rectangles_the_media_scripts_hard_code() -> None:
    """A shot list names a crop; these are what the names mean. ``left`` stops where the
    inspector starts, which is the only way to leave that column out -- a small scale does
    not drop it (``app.fit_layout`` does, from a geometry)."""
    for scale, full, board, left in (
        (24, (0, 0, 1082, 832), (345, 5, 432, 768), (0, 0, 777, 832)),
        (16, (0, 0, 938, 576), (345, 5, 288, 512), (0, 0, 633, 576)),
    ):
        lay = Renderer(scale=scale).layout
        assert cap.crop_rect(lay, "full") == full
        assert cap.crop_rect(lay, "board") == board == lay.arena
        assert cap.crop_rect(lay, "left") == left
        assert cap.crop_rect(lay, (1, 2, 3, 4)) == (1, 2, 3, 4)  # a rectangle passes through
    with pytest.raises(ValueError, match="not one of"):
        cap.crop_rect(Renderer(scale=24).layout, "middle")


def test_a_still_is_one_file_and_a_range_is_numbered(tmp_path: Path) -> None:
    one = cap.capture(source(), tmp_path / "still.png", ticks=(600, 601, 1), scale=16)
    assert one == [tmp_path / "still.png"] and one[0].stat().st_size > 1000
    many = cap.capture(source(), tmp_path / "clip.png", ticks=(600, 620, 5), scale=16)
    assert [p.name for p in many] == [f"clip-{i:04d}.png" for i in range(4)]
    assert all(p.stat().st_size > 1000 for p in many)


def test_the_same_ticks_give_the_same_bytes(tmp_path: Path) -> None:
    """The point of the whole module: a README image that changes when nothing changed is a
    diff nobody can review. The draw time and the frame rate are the only things on screen
    that move with the wall clock, and a capture freezes them."""
    first = cap.capture(source(), tmp_path / "a.png", ticks=(600, 606, 2), scale=16, crop="left")
    time.sleep(0.05)  # a different machine load, a different clock
    second = cap.capture(source(), tmp_path / "b.png", ticks=(600, 606, 2), scale=16, crop="left")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    assert len(first) == 3


def test_live_timing_is_the_deliberate_exception() -> None:
    src = source()
    frozen = cap._transport(src, 10, live_timing=False)
    assert (frozen.draw_ms, frozen.fps, frozen.source_status) == (0.0, 0.0, "")
    real = cap._transport(src, 10, live_timing=True)
    assert real.source_status and not real.live  # a replay is not live whatever is asked


def test_ticks_are_battle_ticks_not_frame_indices() -> None:
    """On a source whose ticks are its indices the distinction is invisible, so this uses a
    recording where they differ: it opens on the first active frame, so index 2 is tick 300."""
    rec = sources.open_source(str(SYNTH_A))
    ticks = (300, 900, 300)
    indices = cap.tick_indices(rec, ticks)
    assert indices == [rec.index_at_tick(t) for t in (300, 600)] == [300, 417]
    assert indices != list(range(*ticks))  # a tick past the battle is not its own index
    assert max(indices) < rec.length
    drawn = [i for _, i in cap.draw_frames(rec, indices, scale=12)]
    assert drawn == indices
    assert rec.frame().tick == 394  # the last tick it holds, not the 600 that was asked for
    rec.close()
    src = source()
    assert cap.tick_indices(src, None) == list(range(len(FRAMES)))
    with pytest.raises(ValueError, match="step must be positive"):
        cap.tick_indices(src, (0, 10, 0))


def test_a_live_stream_is_refused_rather_than_photographed_at_random(tmp_path: Path) -> None:
    src = StreamSource(STREAM_HOST, 9999)
    with pytest.raises(ValueError, match="live stream"):
        cap.capture(src, tmp_path / "no.png", ticks=(0, 1, 1))
    src.close()


def test_an_unknown_suffix_says_what_it_writes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.png, \.mp4 or \.gif"):
        cap.capture(source(), tmp_path / "shot.webp", ticks=(600, 601, 1))


def test_the_compare_ghost_and_the_view_reach_the_capture(tmp_path: Path) -> None:
    """A shot that wants the compare view or a pinned unit says so through the same
    arguments the window takes, and the pixels change accordingly."""
    plain = cap.capture(source(), tmp_path / "plain.png", ticks=(600, 601, 1), scale=16)
    ghosted = cap.capture(
        source(),
        tmp_path / "ghost.png",
        ticks=(600, 601, 1),
        scale=16,
        compare=ListSource(FRAMES, "other"),
    )
    # The same battle ghosted on itself: the units sit under their ghosts and the compare
    # panel names the second source and counts the agreement, so the pixels differ.
    assert plain[0].read_bytes() != ghosted[0].read_bytes()
    pinned = cap.capture(
        source(),
        tmp_path / "pinned.png",
        ticks=(600, 601, 1),
        scale=16,
        view=ViewState(hover_uid="knight"),
    )
    assert pinned[0].read_bytes() != plain[0].read_bytes()  # the inspector filled


@needs_ffmpeg
def test_a_gif_and_an_mp4_come_out_playable(tmp_path: Path) -> None:
    gif = cap.capture(
        source(), tmp_path / "clip.gif", ticks=(600, 700, 5), scale=12, crop="board", fps=20
    )
    assert gif[0].read_bytes()[:6] in (b"GIF89a", b"GIF87a")
    assert 1_000 < gif[0].stat().st_size < 6_000_000  # a README gif must not be hostile
    mp4 = cap.capture(
        source(), tmp_path / "clip.mp4", ticks=(600, 700, 5), scale=12, crop="left", fps=20
    )
    assert mp4[0].read_bytes()[4:8] == b"ftyp" and mp4[0].stat().st_size > 1_000


def test_an_empty_range_is_an_error_not_an_empty_file(tmp_path: Path) -> None:
    """Silently writing nothing is the worst answer: a shot list would carry on believing it
    had the picture. Both formats say so, and the png path needs no ffmpeg to say it."""
    with pytest.raises(ValueError, match="chose no frames"):
        cap.capture(source(), tmp_path / "none.png", ticks=(600, 600, 1))
    assert not list(tmp_path.iterdir())
    if HAS_FFMPEG:
        with pytest.raises(ValueError, match="chose no frames"):
            cap.capture(source(), tmp_path / "none.gif", ticks=(600, 600, 1))


def test_the_media_extra_names_itself_when_it_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ffmpeg, PNG still works and mp4/gif say what to install -- nobody has to
    install a video encoder to look at a frame."""
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    with pytest.raises(ImportError, match=r"royaleviser\[media\]"):
        cap._ffmpeg()
