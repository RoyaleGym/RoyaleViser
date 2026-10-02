# AGENTS.md

Notes for AI coding agents working in this repo. People should start at the [README](README.md).

## What this is

RoyaleViser is the viewer for Clash Royale battles from the RoyaleGym family: RoyaleSim is the
engine, RoyaleGym the environments, RoyaleLearn the learner, and this repo shows the battles. It
draws one battle in a pygame window, out of process. A training run publishes frames over UDP and
never waits for the viewer. It also opens saved battles and recordings.

## Layout

| Path | What it holds |
|---|---|
| `royaleviser/model.py` | The one frame model (`Frame`, `Unit`, `Player`, ...), msgspec structs, and `problems()` (the contract check) |
| `royaleviser/sources.py` | The sources: `CaptureSource` (recordings), `TraceSource` (saved battles), `StreamSource` (a live run), `frame_from_state` |
| `royaleviser/render.py` | `Renderer`: draws one `Frame` onto a surface |
| `royaleviser/theme.py` | Colours, fonts, sizes and the window layout |
| `royaleviser/app.py` | The window, the loop, input and the timeline |
| `royaleviser/capture.py` | Pictures and clips with no window (png, mp4, gif) |
| `royaleviser/parity.py` | A recorded battle and the engine's replay of it, side by side |
| `royaleviser/engine_tables.py` | Tables generated from RoyaleSim's card data; a test re-derives them |
| `royaleviser/cards.json` | The live client's card table (id and elixir per card) |
| `royaleviser/__main__.py` | The `royaleviser` command |
| `tests/` | The suite; `tests/fixtures/` holds two small scripted recordings |
| `tools/replant.py` | Puts a defect back to prove the test that claims to catch it fails |
| `docs/guide.md` | The user guide |
| `docs/internals.md` | The frame model, recording format, stream protocol, layout and testing, in depth |

## Build and test

Python 3.12 or newer. CI (`.github/workflows/suite.yml`) runs on Windows, Ubuntu and macOS:

    python -m venv .venv
    .venv/bin/python -m pip install -e "RoyaleViser[media]"    # Windows: .venv\Scripts\python
    .venv/bin/python -m pip install pytest hypothesis ruff
    cd RoyaleViser
    ../.venv/bin/python -m pytest -q
    ../.venv/bin/python -m ruff check royaleviser tests

The repo is cloned into a folder beside a shared `.venv`, with RoyaleGym, RoyaleSim and RoyaleLearn
as sibling folders. The `full-stack` CI job installs all of them; tests that need a sibling skip
without it and say why. A skip prints "SKIPPED, NOT PASSED".

## Rules that tests enforce

- Doc counts: every "N passed, M skipped" in `docs/guide.md` and `docs/internals.md` must add up
  to what the suite collects, and name the commit it was measured at
  (`tests/test_documented_counts.py`). Measure them; never derive them.
- Links and images in tracked pages must point at tracked files (`tests/test_doc_links.py`).
- Shell blocks on reader pages run as pasted on the platform they name (`tests/test_shell_fences.py`).
- No invisible Unicode in tracked text (`tests/test_no_invisible_characters.py`).
- Generated tables match RoyaleSim's data (`tests/test_status_effects.py`). Regenerate them;
  never hand-edit them to pass.
- Line endings are LF.

## Rules for public text

This repo is public. Keep game mechanics, the viewer and the formats. Never add training results,
training recipes, bot settings, run names or anything about a particular bot's play. Never add
paths or names from private repos or machines.

## Design rules

- The viewer is never in the tick loop. A publisher sends frames only while a viewer is attached.
- Draw what the source says and nothing more. A value the source does not report is "not said"
  (None, -1 or empty), never a guess. Status bits are read only through `model.status_bits`.
- A new field on `Frame` or `Player` goes at the end with a default, so older frames still decode.

## Where to go deeper

[docs/internals.md](docs/internals.md) covers every format and design decision.
