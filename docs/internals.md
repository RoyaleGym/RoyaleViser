# Internals

The frame model, the recording format, the stream protocol, the command line, the window
layout, what the viewer costs, how it is tested and what it cannot draw. The README covers
usage and the key table; this page is for people changing the viewer or writing something
that feeds it.

## One frame model, three sources

Every source produces the same `royaleviser.model.Frame`, so the renderer never learns where
a battle came from.

| Source | Given as | Format | Raw units per tile | Timeline |
|---|---|---|---|---|
| `CaptureSource` | a `frames-*.jsonl` or `.jsonl.gz` path | a capture of a real battle at 20 Hz, one JSON object per line (the fields are listed on the class); a `.jsonl` name resolves to the gzipped file too | 1000 (native milli-tiles) | yes |
| `TraceSource` | a `.msgpack` / `.json` path | `royalegym.replay.Trace`, recorded by `ReplayRecorder` on a `ClashParallelEnv` | the header's `subtile` (18000) | yes |
| `StreamSource` | `--stream host:port` | msgpack `Frame` datagrams from `royalegym.viser.ViserPublisher` | the first frame's | no (latest frame only) |

The invariants a source must hold:

- Positions stay in the source's own integer units (`Frame.units_per_tile`) and in the
  **native / engine frame**, team 0's back edge at `y = 0`. Which seat sits at the bottom is
  a view choice made by the renderer, never a transform applied to the data.
- Elixir is integer thousandths; ticks and every other quantity are integers. The model
  holds no floats.
- Card names are resolved up front (`model.Names`, from `royaleviser/cards.json`, from
  `ROYALEVISER_CARDS`, or from a trace header's `CardInfo` list).
- **A source says what it does not know** rather than guessing. `Player.hand_known` is
  `False` for an opponent whose hand a capture does not carry, and the dashboard prints
  "hand: not in this source". `model.problems(frame)` lists contract violations, and the
  tests run it on every source.

The card table `royaleviser/cards.json` maps a card's register name to `[id, cost]`; it is the
live client's table with the hero-form Musketeer (203000014) folded into the Musketeer.
`ROYALEVISER_CARDS` points `Names.live()` at another table, and a trace header's `CardInfo`
list overrides both for that trace. `cards.json` and the tunnel-speed table
`sources.LIVE_TUNNEL_SPEED` are copies of the recording instrument's own tables and must
stay equal to them.

## The recording format

A recording (a *capture* in the code: `CaptureSource`, `frames-*.jsonl` or `.jsonl.gz`) is
one JSON object per line. A battle frame is `{"event": "frame", "active", "seq", "tick",
"players", "entities", "effects"}` plus a few timing and bookkeeping fields the viewer keeps
in `Frame.meta`; the other events (start/stop lines) carry no battle state.

- A **player** row carries `side` (0/1), `elixir_raw` (ten-thousandths of an elixir), `deck`
  (8 card ids, or `[]` when not known), `hand` (4 deck indices, -1 when not known) and
  `cycle` (deck indices, the next card first).
- An **entity** row carries `id` (an opaque string, reused within a battle), `card_id` (-1
  for a tower), `side`, `x`/`y` in native milli-tiles (1000 per tile), `hp`/`max_hp`,
  `behavior_state`, `target` (another entity's `id`), `movement_direction_x`/`_y` and
  `path_nodes` (half-tile cells on a 36 x 64 grid, goal first). Further fields land in
  `Unit.extra`, which is what the inspector prints under "extra".
- An **effect** row is a projectile or a spell: `side`, `card_id` (the shooter's, -1 for a
  tower, or the spell's), `x`/`y` this tick, `x2`/`y2` the previous tick and
  `projectile_x`/`_y` the aim.

The converters (`sources.capture_unit`, `capture_spell`, `capture_player`, `capture_frame`,
`CaptureEvents`) are exported, so another front end can build the same `Frame` rows from
the same fields. `CaptureSource` reads the whole file up front and keeps the raw lines of
the active frames indexed by tick; a frame is parsed when it is looked at (a bounded cache
of `PARSE_CACHE` = 200 parsed frames), which is why a 40 MB recording opens in about 0.2 s
and a seek costs one `json.loads`. Events (spawns, deaths, the local player's plays) are
derived forward from consecutive frames the first time the timeline passes them and cached
per index, so seeking backwards still shows the events up to that frame.

## Comparing two sources

A second source (a second path, or `--compare PATH`) is ghosted onto the board as hollow
white shapes at the primary's tick and compared tick by tick. The comparison is on the
multiset of `(team, name, x, y, hp)` over one tick's units: for the two seats of one battle
that multiset must be equal, since entity ids differ between clients but nothing else does.
The panel prints `tick T: N entities, M differ` and the running `K ticks compared, D differ`
(`app.Compare`; a replay seeks the second source to the exact tick, live sources are matched
through a 60-tick buffer).

Measured on the two seats of one recorded battle (client 16.402): 2404 ticks compared, 3
differ — all three on tap ticks, where the two recordings disagree for a single frame. An
engine trace is compared against the recording it was calibrated on in exactly the same way,
in milli-tiles. On the two synthetic recordings below the same comparison gives 394 ticks
compared, 0 differ, which is what the tests pin.

## The synthetic recordings

`tests/synthetic.py` scripts a two-minute battle as `Frame`s for the renderer's tests and the
look check (`tests/run_synthetic.py`). The same script is written out in the recording format
that `CaptureSource` reads, once per seat, as `tests/fixtures/frames-synthetic-A.jsonl.gz`
(seat 0) and `-B.jsonl.gz` (seat 1), committed, about 20 KB each:

- one capture frame per six script ticks, so the 118 s script is a 394-tick battle whose
  units move at six times their scripted speed; the frame's `tick` is its own index, as in a
  recording;
- a `start` line, three `active: false` frames, the battle, then 24 frames repeating tick
  394 (the results screen, where both players' hands and decks fill in) and a `stop` line;
- only the recording seat's hand, cycle and deck until the results screen; both players'
  elixir throughout;
- entity ids are opaque hex strings from a seeded generator, different per seat, and an id
  goes back into a pool when its entity dies so a later entity comes up on it (the Musketeer
  on the fallen tower's id, the Giant on the Cannon's), which is why `Unit.uid` carries the
  card and side too;
- tower shots (an attacked tower fires back, one projectile crossing to its attacker every
  four frames), the Fireball in flight and where it landed, the Zap as an area; targets are
  dropped once their entity is gone.

`python tests/synthetic.py --check` confirms the files are what the generator writes and
`--write` regenerates them; `tests/test_sources.py::test_synthetic_fixtures_match_the_generator`
runs the check, so a change to the script fails the suite until the fixtures are rewritten.
`test_capture_frames_round_trip_the_script` reads every tenth frame back and compares it with
the script's own `Frame` (positions, hp, kinds, targets, paths, hand, cycle, elixir, tower hp,
crowns), so the converters are tested against a known answer rather than against themselves.

## Test counts

The suite has two correct results. In a fresh clone, `pytest -q` gives **56 passed, 3
skipped**: the capture tests run on the synthetic recordings, and the three tests that pin
numbers only a recording of a real battle has (2407 ticks both seats hold, 2404 equal, 3
differ; the Goblin Drill of tick 2974 surfacing 73 ticks later) skip, each with a reason
beginning `SKIPPED, NOT PASSED`, and `tests/conftest.py` prints them by name at the end of the
run. With `ROYALELIVE_REPORTS` pointing at a folder that holds
`frames-demo-20260920-120752-A.jsonl`, `frames-demo-20260920-120754-B.jsonl` and
`frames-auto-20260920-083112-A.jsonl` (or their `.jsonl.gz`; the default folder is
`tests/captures`, gitignored) the result is **59 passed**.

## The stream protocol

The viewer must never be in the tick loop and must cost nothing when nobody is watching. The
engine-side half lives in the env layer (`royalegym.viser.ViserPublisher`) and imports
nothing from this package, so the dependency direction stays
`RoyaleLearn -> RoyaleGym -> RoyaleSim`. `sources.Publisher` wraps it for a caller that
already holds a `Frame`.

1. The viewer binds a UDP socket and sends the heartbeat datagram `royaleviser 1` to the
   publisher's `host:port` (default `127.0.0.1:9870`) once a second while it is open.
2. The env calls `publish` once per `reset()` / `step()`, and only when a publisher is set
   (`viser=` or the `ROYALEVISER` environment variable; the default `None` costs one `if`).
   While no heartbeat has arrived in `ATTACH_TIMEOUT_S` (3 s), `publish` returns after one
   clock read — 193 ns per call, measured 2026-09-21 over 200k calls, best of three — and it
   polls its socket for heartbeats at most once a second.
3. While attached it sends one msgpack datagram per call to the last heartbeat's address:
   measured 2.6 KB for 12 units and 6 towers, 11 KB for 60 entities. A datagram over 65507
   bytes is resent with the unit paths emptied, then dropped and counted
   (`publisher.dropped`).
4. `royalegym.viser.frame_dict` builds the wire dict from a `BattleState`;
   `sources.frame_from_state` turns it into a `Frame`, and `TraceSource` builds its rows the
   same way — so a trace and a stream of one battle draw identically. Spawn and death event
   lines come from uid diffing, play lines from the step's accepted deploys.
   `RoyaleGym/tests/test_viser.py` round-trips a published frame through this package's
   decoder, so a drift between the two ends fails there rather than in someone's window.

The stream carries **one frame per env step** (`decision_ms` worth of ticks, 10 at the
defaults), not one per engine tick. For a per-tick view, record with
`ReplayRecorder(frame_every_tick=True)` and open the trace.

## The public surface other front ends use

`royaleviser.__main__` exports the pieces a second front end needs: `add_view_arguments`
adds the view options to any `argparse` parser, `run_sources` builds and runs the window over
already-opened sources, and `TITLE` is the window title a caller can find the window by.
Those three names, the exported `sources.capture_*` converters and the `LIVE_*` constants are
the surface outside callers depend on; changing them is a breaking change.

## Performance

The process prints `royaleviser: N draws, mean X ms, max Y ms` on exit. The maximum is
always the first draw, which builds the board surface and the fonts.

| Run | Draws | Mean | Max |
|---|---|---|---|
| a capture replay, `--start-tick 1200 --speed 4 --seconds 6` | 319 | 4.34 ms | 199.5 ms |
| the same, `--seconds 10` | 564 | 2.37 ms | 152.4 ms |
| the same with `--compare` on the other seat, `--speed 8 --seconds 28` (the whole battle) | 1685 | 2.25 ms | - |
| a MockEngine trace, 60 steps / 601 frames, `--seconds 5` | 101 | 2.49 ms | 6.6 ms |
| `--stream` from a MockEngine env stepping at ~43 steps/s for 7 s | 262 | 4.19 ms | 185.8 ms |
| a RustEngine trace, 2001 frames, `--start-tick 800 --speed 4 --seconds 6` | 336 | 5.17 ms | 75.9 ms |
| `--stream` from a RustEngine env stepping at ~30 steps/s for 12 s | 335 | 5.06 ms | 654.4 ms |

Measured on Windows 10 with the SDL dummy driver on a machine with other work running (the
last two rows, 2026-09-21, with several other jobs on the box). The MockEngine
stream run sent 256 datagrams over 300 env steps with 0 dropped; the first ~44 steps ran
before the once-a-second heartbeat poll noticed the viewer. A capture opens in 0.1-0.2 s (a
4047-frame gzipped file in 0.10 s: a line index plus a parse cache, so a seek is one
`json.loads`).

At 2-4 ms per draw the viewer is far below both the 20 Hz replay budget and the 60 Hz window
cap, which is why it is still Python and pygame rather than a Rust process on a shared
buffer.

## Limitations

- **Both engines have been drawn** (2026-09-21): a 2001-frame `RustEngine` trace passes
  `model.problems` on every frame, and a `RustEngine` env streaming live sent 329 datagrams
  over 360 env steps with 0 dropped. Trace and stream go through the same
  `frame_from_state`, so the two draw identically.
- **Per-tick engine frames** are not available over the stream (one frame per env step); use
  a `frame_every_tick` trace instead.
- **Units in a capture carry no radius and no flying flag**, so every one of them is drawn at
  the renderer's default radius and air units look like ground units. Deploying is a state
  there (visible for one tick), not a countdown.
- **Engine units carry no path and no target** in a `BattleState`, so `p` and `t` draw
  nothing for traces and streams.
- **Spells in a capture** are limited to projectiles and the few spells that leave an effect
  carrying their card id (Fireball, Arrows, Rocket, Log, Barbarian Barrel); most leave none.
  They are drawn as a default-radius ring — there are no rage, poison or freeze zones.
- **An opponent's hand and deck** are not in a capture, and a trace's cycle beyond the
  revealed cards shows as "next ?".
