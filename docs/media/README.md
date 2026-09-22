# README media

Every file the README embeds. All of it is generated, and none of it needs a screen.

The generator is [`../../../RoyaleGym/docs/media/make_media.py`](../../../RoyaleGym/docs/media/make_media.py).
It lives in RoyaleGym because it writes into all four repos, which are cloned side by side.
From the folder that holds them:

```
.venv\Scripts\python RoyaleGym\docs\media\make_media.py engine-trace compare-ghost
```

Underneath it is this repo's own `capture()`, which writes the window to a png, mp4 or gif
with no window, no display and no clock. The same source and arguments give the same bytes,
so regenerating is a clean diff. See `docs/internals.md` for the function.

| File | How it is made | What it shows |
|---|---|---|
| `engine-trace.png` / `.gif` / `.mp4` | `make_media.py engine-trace` | An engine trace opened at tick 900, with a unit pinned in the inspector. |
| `compare-ghost.gif` / `.mp4` | `make_media.py compare-ghost` | The two recordings of the scripted battle, the second drawn on the first as hollow ghosts. |
| `replay-scrubbed-4x.gif` / `.mp4` | `make_media.py replay-scrubbed` | The scripted battle that ships with the tests, played back. |
| `live-training-env.png` | `make_media.py live-training-env` | A real environment in another process, streaming over UDP to the viewer. |
| `tile-*.png` | screenshots | final, not regenerated here |
| `family.svg` | hand-drawn | the five repos and how they depend on each other; final |

## Two things the captions have to keep saying

**The scripted battle is a script.** `tests/synthetic.py` builds frames directly and no engine
is involved. In its recording form the units move at six times their scripted speed. It is
honest as a picture of the window and dishonest as a picture of how the engine plays, so
`replay-scrubbed-4x` and `compare-ghost` are captioned as the scripted battle and never as a
real match. The two recordings are also identical to each other, so a caption must not imply
the ghost clip shows a disagreement it does not show.

**The live shot is the one that is not reproducible, on purpose.** Everything else here has
its on-screen timing frozen, because a picture that changes every run is a diff nobody can
review. `live-training-env` keeps the real frame rate and the real drop count, because the
frame rate is the thing it is showing. The copy taken for the README admits 86 dropped frames
of 116, which is what a busy machine looks like, and the README says so rather than cropping
it out.

## Nothing here comes from a recording of a real match

Every source is either an engine trace or the scripted battle in `tests/fixtures`. That is a
boundary rather than a limitation of the tooling: the recordings of real battles are private,
so no public picture is made from one.
