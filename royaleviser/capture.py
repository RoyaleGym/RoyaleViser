"""What the window would show, written to a file with no window: stills, sequences, mp4, gif.

    from royaleviser.capture import capture
    from royaleviser.sources import open_source

    src = open_source("battle.msgpack")
    capture(src, "still.png", ticks=(900, 901, 1), crop="left")
    capture(src, "clip.gif", ticks=(0, 2400, 4), scale=16, crop="left", fps=20)

WHY THIS IS NOT ``app.run``
    ``run`` opens a window and paces a replay against the wall clock: it draws what it has
    time to draw. A capture has no clock. It seeks the source to each tick in turn, draws
    that frame, and hands the pixels straight to a file, so a busy machine changes how long
    the capture takes and nothing about what comes out. Nothing here opens a display -- the
    renderer draws to its own off-screen surface, and no ``pygame.init()`` is needed.

WHAT IS FROZEN, AND WHY
    The status block prints the draw time and the frames per second, which differ every run.
    An image in a README that changes when nothing changed is a diff nobody can review, so a
    capture zeroes both of those, and the LIVE pill with them. ``live_timing=True`` puts them
    back, for the one shot whose subject IS the timing.

WHAT IT COSTS TO CARRY
    PNG needs nothing beyond this package. mp4 and gif are encoded by ffmpeg, which arrives
    with ``imageio-ffmpeg`` -- the ``media`` extra (``pip install royaleviser[media]``).
    Without it, ``capture`` to those formats raises and says so; PNG keeps working, so
    nobody has to install a video encoder to look at a frame.

A capture reads a replay: a capture file, a trace, or any Source with a timeline. A live
stream has no timeline to seek, so it raises rather than quietly recording whatever happened
to arrive; ``tests/run_stream.py --shot`` is the way to photograph a live stream.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # a capture's stdout is its own

import pygame

from .model import Frame, Names, Source
from .render import Renderer, Transport, ViewState
from .theme import DEFAULT, Layout, Rect, Theme

#: What ``crop`` may name. ``board`` is the arena alone; ``left`` is the dashboard and the
#: board with the inspector column left off, which is most of a README's clips.
CROPS = ("full", "board", "left")
MEDIA_SUFFIXES = (".mp4", ".gif")
DEFAULT_FPS = 20  # the game's own rate: 20 ticks a second (model.TICK_MS)
GIF_DITHER = "bayer:bayer_scale=3"  # small files, no shimmer on flat colour
MP4_CRF = 18  # visually lossless for flat UI colour

MEDIA_MISSING = (
    "writing {suffix} needs ffmpeg: pip install royaleviser[media] (imageio-ffmpeg), or "
    "capture to .png and encode the sequence yourself"
)


def crop_rect(layout: Layout, crop: str | Rect) -> Rect:
    """The rectangle a ``crop`` name means for this layout, or the rectangle it already is.

    ``full`` the whole window, ``board`` the arena alone, ``left`` everything up to where the
    inspector column starts -- which keeps the dashboard and the timeline and drops the
    inspector. NOTE that a small ``scale`` does not drop the inspector by itself: the compact
    layout is chosen from a geometry by ``app.fit_layout``, so cropping is how a capture
    leaves the column out.
    """
    if not isinstance(crop, str):
        return crop
    if crop not in CROPS:
        raise ValueError(f"crop {crop!r} is not one of {', '.join(CROPS)} or a rectangle")
    if crop == "board":
        return layout.arena
    if crop == "left":
        ax, _, aw, _ = layout.arena
        return (0, 0, ax + aw, layout.window[1])
    return (0, 0, *layout.window)


def tick_indices(source: Any, ticks: tuple[int, int, int] | None) -> list[int]:
    """Source indices for ``(start, stop, step)`` battle ticks, or every frame it holds.

    A tick the source does not have lands on the frame it does have -- a stream carries one
    frame per environment step, so a tick range over one asks for frames that repeat, which
    is what a clock-accurate clip of it looks like.
    """
    if ticks is None:
        return list(range(source.length or 1))
    start, stop, step = ticks
    if step <= 0:
        raise ValueError(f"ticks step must be positive, not {step}")
    at = getattr(source, "index_at_tick", None)
    wanted = range(start, stop, step)
    return [at(t) for t in wanted] if at is not None else list(wanted)


def _names_of(source: Any) -> Names | None:
    """The card table for the cost badges: the source's own, else the live one."""
    names = getattr(source, "names", None)
    if isinstance(names, Names):
        return names
    try:
        return Names.live()
    except (OSError, ValueError):
        return None


def _renderer_for(source: Any, scale: int, theme: Theme) -> Renderer:
    r = Renderer(scale=scale, theme=theme)
    arena = getattr(source, "arena", None)
    if arena is not None:
        r.set_arena(arena, arena.subtile)
    names = _names_of(source)
    if names is not None:
        r.cost_of = names.cost_of_name
    return r


def _transport(source: Any, index: int, live_timing: bool) -> Transport:
    """What the status block reads. Timing is zero unless the shot is about the timing."""
    return Transport(
        source_name=getattr(source, "name", ""),
        source_status=source.status() if live_timing else "",
        live=live_timing and bool(getattr(source, "live", False)),
        index=index,
        length=getattr(source, "length", None),
        draw_ms=0.0,
        fps=0.0,
    )


def _pin_compare(view: ViewState, frame: Frame, compare: Any) -> None:
    """Seek the compare source to this frame's tick and ghost it, the way the app does."""
    from .app import compare_text  # the one implementation of "N entities, M differ"

    at = getattr(compare, "index_at_tick", None)
    if at is not None:
        compare.seek(at(frame.tick))
    other = compare.frame()
    view.compare_frame = other
    view.compare_name = getattr(compare, "name", "")
    view.compare_text = "" if other is None else f"tick {frame.tick}: {compare_text(frame, other)}"


def _to_bytes(surface: pygame.Surface) -> bytes:
    """The surface as raw RGB, whichever name this pygame gives it."""
    fn = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
    return fn(surface, "RGB")


def draw_frames(
    source: Any,
    indices: Sequence[int],
    *,
    scale: int = 24,
    view: ViewState | None = None,
    crop: str | Rect = "full",
    compare: Any = None,
    theme: Theme = DEFAULT,
    live_timing: bool = False,
) -> Iterator[tuple[pygame.Surface, int]]:
    """Draw each index in turn and yield ``(surface, index)``, cropped.

    The surface is reused between frames: copy it if you keep it. This is the loop every
    output format shares, and the only place that touches the renderer.
    """
    if getattr(source, "live", False):
        raise ValueError(
            f"{getattr(source, 'name', source)!r} is a live stream, which has no timeline to "
            "seek; photograph one with tests/run_stream.py --shot, or record a trace and "
            "capture that"
        )
    view = view or ViewState()
    renderer = _renderer_for(source, scale, theme)
    rect = crop_rect(renderer.layout, crop)
    for index in indices:
        source.seek(index)
        frame = source.frame()
        if frame is None:
            continue
        if compare is not None:
            _pin_compare(view, frame, compare)
        renderer.draw(frame, view, _transport(source, index, live_timing))
        yield renderer.surface.subsurface(rect), index


def _ffmpeg() -> str:
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except ImportError as exc:  # the media extra is not installed
        raise ImportError(MEDIA_MISSING.format(suffix="mp4/gif")) from exc
    return get_ffmpeg_exe()


def _encode(frames: Iterator[tuple[pygame.Surface, int]], out: Path, fps: int) -> Path:
    """Pipe raw frames into ffmpeg. The first frame fixes the size for the rest."""
    exe = _ffmpeg()
    first = next(frames, None)
    if first is None:
        raise ValueError("nothing to encode: the tick range chose no frames")
    surface, _ = first
    w, h = surface.get_size()
    head = ["-y", "-f", "rawvideo", "-pix_fmt", "rgb24"]
    head += ["-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if out.suffix == ".gif":
        # One pass: split the stream, build a palette from it, then map it back. Without a
        # palette a gif of flat UI colour bands badly.
        tail = [
            "-filter_complex",
            f"[0:v]split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither={GIF_DITHER}",
        ]
    else:
        # libx264 needs even dimensions, and a crop rectangle is whatever the layout made it.
        tail = [
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(MP4_CRF),
            "-movflags",
            "+faststart",
        ]
    proc = subprocess.Popen(  # the binary is imageio-ffmpeg's own, and every argument is ours
        [exe, *head, "-an", *tail, str(out)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        proc.stdin.write(_to_bytes(surface))
        for surf, _ in frames:
            proc.stdin.write(_to_bytes(surf))
        proc.stdin.close()
    except BrokenPipeError:  # ffmpeg died; its own words are more useful than ours
        pass
    _, err = proc.communicate()
    if proc.returncode != 0:
        said = err.decode(errors="replace")[-600:]
        raise RuntimeError(f"ffmpeg failed writing {out.name}: {said}")
    return out


def capture(
    source: Source,
    out: str | Path,
    *,
    ticks: tuple[int, int, int] | None = None,
    scale: int = 24,
    view: ViewState | None = None,
    crop: str | Rect = "full",
    fps: int = DEFAULT_FPS,
    compare: Source | None = None,
    theme: Theme = DEFAULT,
    live_timing: bool = False,
) -> list[Path]:
    """Write ``source`` to ``out``; the suffix picks the format. Returns the files written.

    ``ticks`` is ``(start, stop, step)`` in BATTLE ticks (the clock the window shows), not
    frame indices; without it the whole source is captured. A ``.png`` writes one file per
    frame, numbered ``name-0000.png`` when the range holds more than one; ``.mp4`` and
    ``.gif`` encode the range at ``fps`` frames a second.

    ``crop`` is one of CROPS or a rectangle, ``compare`` ghosts a second source at the same
    tick, and ``view`` carries everything else the window can be told to show -- the seat,
    the overlays, and ``hover_uid`` to pin a unit in the inspector.

    The same source and arguments give the same bytes: the only thing on screen that moves
    with the wall clock is frozen (see ``live_timing``).
    """
    out = Path(out)
    if out.suffix in MEDIA_SUFFIXES:
        frames = draw_frames(
            source,
            tick_indices(source, ticks),
            scale=scale,
            view=view,
            crop=crop,
            compare=compare,
            theme=theme,
            live_timing=live_timing,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        return [_encode(frames, out, fps)]
    if out.suffix != ".png":
        raise ValueError(f"{out.name}: capture writes .png, .mp4 or .gif, not {out.suffix!r}")
    indices = tick_indices(source, ticks)
    out.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for n, (surface, _) in enumerate(
        draw_frames(
            source,
            indices,
            scale=scale,
            view=view,
            crop=crop,
            compare=compare,
            theme=theme,
            live_timing=live_timing,
        )
    ):
        path = out if len(indices) == 1 else out.with_name(f"{out.stem}-{n:04d}{out.suffix}")
        pygame.image.save(surface, str(path))
        written.append(path)
    return written
