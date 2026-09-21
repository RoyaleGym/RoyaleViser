# RoyaleViser

The rlviser analog of the Royale family: one interactive viewer, a separate process, for
everything in the family that produces battle state. It is the tool for watching recorded
matches (RoyaleLive captures and engine traces) and a running engine. `royalegym.render`
(the offline HTML page of a trace) stays as the no-dependency replay.

```
python -m royaleviser frames-<label>-<stamp>.jsonl.gz                             # a recorded battle
python -m royaleviser a.jsonl.gz --compare b.jsonl.gz --speed 4                  # both seats, one battle
python -m royaleviser trace.msgpack                                              # an engine trace
python -m royaleviser --stream 127.0.0.1:9870                                    # a running engine
```

The first source is the primary; a second one (a second path, or `--compare`) is ghosted onto
the board at the same tick and compared tick by tick (below). `--seat local` (the default)
seats the primary's local player at the bottom once the source knows it; `0` and `1` pin a
team. `--geometry WxH+X+Y` places the window and picks the largest tile scale whose layout
fits (under 16 px/tile the inspector column is dropped and the compare lines move into the
dashboard: the compact layout a narrow slot gets); the window then fills the whole rectangle.

## Where it sits in the family

Five sibling repos under the GitHub organization [RoyaleGym](https://github.com/RoyaleGym),
one workspace folder, one venv (`Setup` below):

| Repo | Analog | What it is to the viewer |
|---|---|---|
| [RoyaleSim](https://github.com/RoyaleGym/RoyaleSim) | RocketSim | the engine; its traces and streams reach the viewer through RoyaleGym |
| [RoyaleGym](https://github.com/RoyaleGym/RoyaleGym) | RLGym | the env API. `royalegym.replay` (the trace format), `royalegym.viser.ViserPublisher` (the engine-side publisher), `royalegym.protocol` (the arena geometry); the only sibling this package imports |
| [RoyaleLearn](https://github.com/RoyaleGym/RoyaleLearn) | RLGym-PPO | the learner; a training run streams through `ViserPublisher` like any env |
| **RoyaleViser** | rlviser | this package |
| RoyaleLive (private) | - | the client instrument that records ground-truth traces from the real game: it writes the captures the viewer replays and imports this package, never the other way round |

Dependency direction: `RoyaleViser -> RoyaleGym` (traces, the publisher, the arena) and
`RoyaleLive -> RoyaleViser`. `royaleviser` imports without RoyaleLive present: a capture is a
data format (below), the card table is shipped with the package.

## The three sources (`royaleviser/sources.py`)

| Source | Argument | Format | Units per tile | Timeline |
|---|---|---|---|---|
| `CaptureSource` | `frames-*.jsonl` / `.jsonl.gz` | a RoyaleLive capture: one JSON object per line, a battle frame of the live client at 20 Hz | 1000 (native millitiles) | yes |
| `TraceSource` | `trace.msgpack` / `.json` | `royalegym.replay.Trace` | the header's `subtile` (18000) | yes |
| `StreamSource` | `--stream host:port` | msgpack `Frame` datagrams from a `Publisher` | the first frame's | no (latest frame) |

A capture, as data: a frame line is `{"event": "frame", "active", "seq", "tick", "players",
"entities", "effects"}` plus a few timing and bookkeeping fields the viewer keeps in `Frame.meta`. A player
row carries `side`, `elixir_raw` (ten-thousandths), `deck` (8 card ids or `[]`), `hand` (4 deck
indices, -1 when not known) and `cycle`; an entity row `id` (an opaque string, reused
within a battle), `card_id` (-1 for a tower), `side`, `x`/`y` in native millitiles, `hp`/`max_hp`,
`behavior_state`, `target`, `movement_direction_x`/`_y` and `path_nodes` (half-tile cells, goal
first); an effect row is a projectile or a spell with its position this tick and last tick and
its aim. The converters (`sources.capture_*`, `CaptureEvents`) are exported, so another front
end can build the same `Frame` rows from the same fields.

All sources produce `royaleviser.model.Frame`: positions in the source's raw integer units in
the NATIVE / ENGINE frame (team 0's back edge at `y=0`), elixir in thousandths, card names
already resolved (`model.Names`: `royaleviser/cards.json`, register name -> [id, cost], a copy
of the table RoyaleLive generates from the card data with the hero-form Musketeer 203000014
folded in; `ROYALEVISER_CARDS` points at another table; or a trace header's `CardInfo` list).
What a source cannot know it says so: `Player.hand_known` is False for the live opponent,
and the dashboard prints "hand: not in this source" instead of a guess.
`model.problems(frame)` lists contract violations for tests.

A second source is ghosted onto the board (hollow white shapes) at the primary's tick and
compared with it: the multisets of (team, name, x, y, hp) over the units of one tick must be
equal for the two seats of one friendly (entity ids differ, everything else is lockstep), so
the panel prints "tick T: N entities, M differ" and the running "K ticks compared, D differ"
(`app.Compare`; a replay seeks the second source to the exact tick, live sources are matched
through a 60-tick buffer). Measured on the two seats of one recorded battle (client 16.402):
2404 ticks compared, 3 differ (tap ticks, where the two recordings disagree for one frame). An
engine trace against the capture it was calibrated on is compared the same way, in millitiles.

## The layout (`royaleviser/theme.py`)

```
+------------------+-----------------------------+---------------+
| top hand: 4 cards| arena, 18 x 32 tiles at     | inspector:    |
| 80x100, cost,    | 24 px/tile (--scale):       | hovered unit, |
| dimmed if too    | checkerboard grass, river   | every raw     |
| dear; elixir bar | band, bridges, tower zones, | field         |
| + next card      | troops (circles), buildings |               |
| debug column:    | and towers (squares), hp    | events,       |
| king activation, | bars, names, paths, target  | newest last   |
| pending deploys  | lines, spells, projectiles; |               |
| bottom elixir +  | timer + crowns top right,   |               |
| next; bottom hand| OVERTIME centred; status    |               |
|                  | line, scrub bar underneath  |               |
+------------------+-----------------------------+---------------+
   340 px             18*24 = 432 px                300 px
```

Colours are carried over from the project's earlier Python renderer: grass
(188,195,55)/(217,215,47), river (106,230,237), bridge
(255,175,120), team 0 blue (71,204,218), team 1 red (224,73,41), UI (30,30,40). Every rect
comes from `theme.layout(theme, scale, tiles)`; the renderer computes no size of its own. The
bottom player is `ViewState.seat` (`--seat local` = the capture's local player, `0`, `1`); seat
1 draws the board rotated 180 degrees, which is what that client shows (measured 2026-09-18,
RoyaleLive).

## Keys

| Key | Action |
|---|---|
| space | play / pause (replays) |
| left / right | step a frame (shift: 20) |
| home / end | first / last frame |
| + / - | speed x2 / x0.5 (also ] [) |
| wheel | step frames |
| f | flip the seat |
| p | unit paths |
| t | target lines |
| g | tile grid |
| d | debug numbers |
| c | compare ghost |
| h | this help |
| s / F12 | save a PNG |
| click | pin a unit / seek the timeline |
| escape / q | quit |

The list is `royaleviser.app.KEYS` (`--help` and the H footer print it). For unattended
runs, `--seconds N` quits by itself and `--shot PATH` writes the last drawn window as PNG
(`SDL_VIDEODRIVER=dummy` works for headless tests). The window is redrawn only when the
frame or the view changed (60 fps cap); a replay is paced by the frame's `tick_ms` times
the speed and steps through every frame it skips, so the events and the compare totals
see them all. On exit the process prints `royaleviser: N draws, mean X ms, max Y ms`
(measured 2026-09-20 on a busy machine: 2.2-2.7 ms mean on capture, trace and stream frames
at scale 24; the max is the first draw, which renders the board and the fonts).

## The engine publisher

The viewer must never be in the tick loop and must cost nothing when nobody is watching.
`royalegym.viser.ViserPublisher` is the engine-side end of `StreamSource`, inside the env
layer (it imports nothing from RoyaleViser, so the dependency direction stays
`RoyaleLearn -> RoyaleGym -> RoyaleSim`); `sources.Publisher` wraps it for a script that
already holds a `Frame`:

```
env = ClashParallelEnv(viser=ViserPublisher())        # 127.0.0.1:9870
set ROYALEVISER=127.0.0.1:9870                        # the same without touching the constructor
python -m royaleviser --stream 127.0.0.1:9870         # in another process
```

1. The viewer binds a UDP socket and sends `STREAM_HELLO` to the publisher's `host:port`
   (default `127.0.0.1:9870`) once a second while it is open (from `StreamSource.frame()`).
2. The env calls `publish` once per `reset()` / `step()`, and only when a publisher is set
   (`viser=` or the env var; `None` costs one `if`). While no heartbeat has arrived in
   `ATTACH_TIMEOUT_S` (3 s) `publish` returns after one clock read; it polls its socket
   for heartbeats at most once a second.
3. While attached it sends one msgpack datagram per call to the last heartbeat's address
   (measured 2026-09-20: 2.6 KB for 12 units + 6 towers). A datagram over 65507 bytes is
   resent with unit paths emptied, then dropped and counted.
4. `royalegym.viser.frame_dict` builds the wire dict from a `BattleState`;
   `sources.frame_from_state` turns it into a `Frame`, and `TraceSource` uses the same
   unit/spell rows, so a trace and a stream of the same battle draw identically.
   The spawn/death/play event lines come from the publisher (uid diffing, accepted deploys).

## Reusing the window from another front end

`royaleviser.__main__` exports the pieces a second front end needs: `add_view_arguments` adds
this package's view options to any `argparse` parser, `run_sources` builds and runs the window
over already opened sources, and `TITLE` is the window title. RoyaleLive uses them; it also
keeps the two tables the viewer copies from it equal (the tunnel-speed table
`sources.LIVE_TUNNEL_SPEED`, the card table `cards.json`).

## Setup: the shared workspace venv

The five repos are cloned as siblings into one folder with one venv at that folder's root
(Python 3.12; Rust 1.80+ with cargo for RoyaleSim):

```
mkdir Royale && cd Royale
git clone https://github.com/RoyaleGym/RoyaleSim.git
git clone https://github.com/RoyaleGym/RoyaleGym.git
git clone https://github.com/RoyaleGym/RoyaleViser.git
git clone https://github.com/RoyaleGym/RoyaleLearn.git
python -m venv .venv
.venv\Scripts\python -m pip install maturin pytest hypothesis ruff
cd RoyaleSim && ..\.venv\Scripts\python tools\extract_arena.py && ..\.venv\Scripts\python tools\extract_cards.py && ..\.venv\Scripts\python tools\extract_globals.py && cd ..     # 0. RoyaleSim/data/derived/ (gitignored; the crate compiles arena.json in)
cd RoyaleSim && ..\.venv\Scripts\maturin develop --release && cd ..     # 1. the engine (~1 min, ~1.5 GB RAM)
.venv\Scripts\python -m pip install -e RoyaleGym                          # 2. the env layer (numpy, gymnasium, pettingzoo, msgspec)
.venv\Scripts\python -m pip install -e RoyaleViser                        # 3. this package (pygame, msgspec, numpy)
.venv\Scripts\python -m pip install -e RoyaleLearn                        # 4. the learner (see its README)
                                                                          # RoyaleLive (private): scripts, no package (see its README)
```

Run from inside a repo folder as `..\.venv\Scripts\python`. Only steps 1-3 are needed to view
captures, traces and streams.

## Tests

```
cd RoyaleViser && ..\.venv\Scripts\python -m pytest -q        # 54 as of 2026-09-21
..\.venv\Scripts\python -m ruff check royaleviser tests
```

`tests/test_model.py` (the contract, the theme, the layout, the CLI parser),
`tests/test_sources.py` (one recorded battle from each seat, MockEngine traces, the UDP
round trip), `tests/test_render.py` (headless draws of every panel and toggle, the pixel
positions of both kings, the compact layout, the compare totals, the app's keys and pacing,
`run` with `--shot` and `--geometry`), `tests/test_cli.py` (`python -m royaleviser` end to
end, headless). The capture tests read RoyaleLive's gitignored captures: `ROYALELIVE_REPORTS`
names the folder (default `tests/captures`, which is gitignored and empty in a fresh clone)
and they skip without them; `RoyaleGym/tests/test_viser.py` round-trips a published frame
through this package's decoder.

## Status

Working end to end: capture replay, the two-seat compare (2404 ticks compared, 3 differ), a
`MockEngine` trace, and a `MockEngine` env streaming through `ViserPublisher` (201 frames, 0
dropped). Draw time is 2-4 ms, well inside both the 20 Hz replay budget and the 60 Hz window
cap.

Open: a `RustEngine` trace or stream has not been run through the viewer, and the stream
carries one frame per env step rather than one per engine tick. Several things a recording
simply does not contain — unit radius, the flying flag, most spell zones, an opponent's hand
— are stated as unknown rather than guessed at. `docs/internals.md` has the full list, the
frame contract, the stream protocol and the performance table.

## Community

Viewer feedback, capture formats and rendering work happen in the project's Discord:
[**https://discord.gg/4D2BS5JBHP**](https://discord.gg/4D2BS5JBHP)

Issues and pull requests on this repo are welcome too.
