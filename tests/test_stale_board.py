"""A live source that has gone quiet says so ON the board, not only in the status line.

The owner's verdict on 2026-09-22 was that the viewer was "basically useless -- bursts of low
fps replay between long pauses". The pauses were a training run's PPO updates and the bursts its
rollouts, so the shape came from the source; but the window could not tell a person apart a
learner that was thinking from a run that had died. It had been printing "last 12s ago" in the
status line the whole time, which nobody reads while watching a battle.

These grade against the PIXELS rather than against the call returning without raising. A draw
that silently drew nothing would satisfy "it did not crash", and that is the exact failure being
fixed: a window that says nothing while looking fine.
"""

from __future__ import annotations

import time

import pytest

from royaleviser import sources
from royaleviser.render import Renderer, Transport, ViewState
from synthetic import frame_at


@pytest.fixture
def r() -> Renderer:
    return Renderer(scale=16)


def row_across_arena(r: Renderer, y: int) -> list[tuple[int, int, int, int]]:
    """Every seventh pixel of one arena row, as drawn."""
    x, _, w, _ = r.layout.arena
    return [tuple(r.surface.get_at((x + i, y))) for i in range(0, w, 7)]


def middle(r: Renderer) -> int:
    _, y, _, h = r.layout.arena
    return y + h // 2


def band_text_pixels(r: Renderer) -> int:
    """How many pixels of the band's own text colour are on the arena, anywhere.

    Asserting the two draws merely DIFFER is not enough, and a plant proved it: a band drawn
    unconditionally still differs between a 9s reading and a 12s one, because the number in it
    is different. The claim is about the band being there or not, so the test looks for the
    band's own colour rather than for a difference.
    """
    x, y, w, h = r.layout.arena
    want = tuple(r.theme.stale_text)[:3]
    return sum(
        tuple(r.surface.get_at((x + i, y + j)))[:3] == want
        for i in range(0, w, 3)
        for j in range(0, h, 3)
    )


def test_a_quiet_board_is_marked_and_a_live_one_is_not(r: Renderer) -> None:
    """The control is the live draw, and it is a control only because it looks for the band."""
    f = frame_at(600)
    r.draw(f, ViewState(), Transport(live=True))
    assert band_text_pixels(r) == 0, "a board that is keeping up is wearing the quiet band"

    r.draw(f, ViewState(), Transport(live=True, board_age_s=12.0))
    assert band_text_pixels(r) > 0, "a board that has stood still for 12s says nothing about it"


def test_the_band_does_not_hide_the_frame_underneath(r: Renderer) -> None:
    """It must not be a full veil. On a training run the board stands still far more of the
    time than it moves, so a window that blanks itself whenever a source pauses is worse than
    one that says nothing."""
    f = frame_at(600)
    top = r.layout.arena[1] + 6
    r.draw(f, ViewState(), Transport(live=True))
    before = row_across_arena(r, top)

    r.draw(f, ViewState(), Transport(live=True, board_age_s=75.0))
    assert row_across_arena(r, top) == before, "the band covers the arena and hides the frame"


def test_the_source_decides_what_counts_as_quiet_not_the_renderer() -> None:
    """``quiet_seconds`` returns None while the stream keeps up, so the threshold lives once.

    A renderer applying its own threshold would be a second place to change it, and the two
    would drift: the status line and the band would then disagree about one stream.
    """
    src = sources.StreamSource("127.0.0.1", 59999)
    try:
        assert src.quiet_seconds() is None, "no frame has ever arrived; nothing to call stale"
        src._times.append(time.monotonic())
        assert src.quiet_seconds() is None, "a frame that just arrived is not quiet"

        src._times[-1] = time.monotonic() - (sources.STREAM_QUIET_S + 2.0)
        seconds = src.quiet_seconds()
        assert seconds is not None and seconds >= sources.STREAM_QUIET_S
        # The band and the status line read the same clock, which is the point of one threshold.
        assert src.quiet_for().strip().startswith("last")
    finally:
        src.close()
