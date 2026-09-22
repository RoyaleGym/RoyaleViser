# Internals

The frame model, the recording format, the stream protocol, the command line, the window
layout, the key table, what the viewer costs, how it is tested and what it cannot draw. The
README covers usage; this page is for people changing the viewer or writing something that
feeds it.

## One frame model, three sources

Every source produces the same `royaleviser.model.Frame`, so the renderer never learns where
a battle came from.

`royalegym.render`, the offline HTML page of a trace, stays in RoyaleGym as the replay that
needs none of this package.

| Source | Given as | Format | Raw units per tile | Timeline |
|---|---|---|---|---|
| `CaptureSource` | a `frames-*.jsonl` or `.jsonl.gz` path | a capture of a real battle at 20 Hz, one JSON object per line (the fields are listed on the class); a `.jsonl` name resolves to the gzipped file too | 1000 (native milli-tiles) | yes |
| `TraceSource` | a `.msgpack` / `.json` path | `royalegym.replay.Trace`, recorded by `ReplayRecorder` on a `ClashParallelEnv` | the header's `subtile` (18000) | yes |
| `StreamSource` | `--stream host:port` | msgpack `Frame` datagrams from `royalegym.viser.ViserPublisher`, and a learner's status datagrams from `sources.LearningPublisher` one port up (`--learning`) | the first frame's | no (latest frame only) |

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
in milli-tiles.

Re-run 2026-09-21 headless over the whole battle (`--speed 8 --seconds 32`, the two
`demo-20260920-1207*` recordings under `tests/captures`): the panel ends at `GAME OVER` with
`2407 ticks compared, 3 differ`, after 1910 draws at 2.80 ms mean. The test that pins the
same battle counts 2407 ticks both recordings hold, 2404 equal and 3 differing (the 2404
above is that count from a 28 s run on 2026-09-20). The results screen at the end of a
recording fills in the opponent's deck and hand, so the last frame is the one where both
hands are known.

On the two synthetic recordings below the same comparison gives 0 differing ticks over the
whole battle (ticks 0..394, so the panel prints `395 ticks compared, 0 differ`), which is
what the tests pin.

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

## Tests

`..\.venv\Scripts\python -m pytest -q` from the repo folder, and
`..\.venv\Scripts\python -m ruff check royaleviser tests`.

- `tests/test_model.py`: the frame contract, the theme, the layout, the CLI parser.
- `tests/test_sources.py`: the capture source on the synthetic and the recorded battles,
  MockEngine traces, the UDP round trip through `Publisher` and `StreamSource`.
- `tests/test_render.py`: headless draws of every panel and toggle, the pixel positions of
  both kings, the compact layout, the compare totals, the app's keys and pacing, `run` with
  `--shot` and `--geometry`.
- `tests/test_cli.py`: `python -m royaleviser` end to end, headless.

`RoyaleGym/tests/test_viser.py` round-trips a published frame through this package's decoder,
so a drift between the publisher and the viewer fails there. `tests/run_synthetic.py` opens
the real window on the scripted battle straight from the script, with no sibling repo and no
recording needed: the look check for the renderer, and its `--compare` ghosts a
half-tile-shifted copy of the same battle to exercise the compare panel.

The suite has two correct results. In a fresh clone, `pytest -q` gives **73 passed, 3
skipped**: the capture tests run on the synthetic recordings, and the three tests that pin
numbers only a recording of a real battle has (2407 ticks both seats hold, 2404 equal, 3
differ; the Goblin Drill of tick 2974 surfacing 73 ticks later) skip, each with a reason
beginning `SKIPPED, NOT PASSED`, and `tests/conftest.py` prints them by name at the end of the
run. With `ROYALELIVE_REPORTS` pointing at a folder that holds
`frames-demo-20260920-120752-A.jsonl`, `frames-demo-20260920-120754-B.jsonl` and
`frames-auto-20260920-083112-A.jsonl` (or their `.jsonl.gz`; the default folder is
`tests/captures`, gitignored) the result is **76 passed**.

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

## The learning status

The dashboard's learning panel is filled by a **second sender on a second port**: the
learner, not the environment. Its numbers are ready once per PPO iteration rather than once
per step, and they are most interesting exactly while the learner is optimising and no frame
is moving, so they do not ride on a `Frame`. Putting them there would have repeated twenty
numbers on every datagram, tied them to the environment's clock, and made the panel go quiet
whenever the board did.

1. `sources.LearningPublisher` binds `host:port` — by default the frames' port **plus one**
   (`sources.learning_endpoint`, 9871 against the default 9870), because two processes
   cannot bind one port. `--learning HOST:PORT` moves it.
2. The viewer says hello to both addresses from its one socket, so the learner attaches on
   the same heartbeat rule as the frame publisher and sends nothing until a viewer is there.
   Detached, `publish` keeps the status and returns after one clock read — 435 ns per call,
   measured 2026-09-21 over 200k calls, best of three — and the socket is polled at most once
   a second. It is not in anybody's tick loop either way: it is the learner's own process,
   and the environment's publisher is untouched by any of this.
3. A status datagram is msgpack `{"learning": {...}}`: the one-key map makes its first bytes
   (`model.LEARNING_PREFIX`) something a frame can never start with, so `StreamSource` sorts
   the two kinds apart with one comparison and no decode. ~300 bytes, far inside one
   datagram. Anything that decodes as neither is counted in `StreamSource.rejected` and
   named in the status line rather than raised.
4. **One message is the whole status** (`model.Learning`). The viewer replaces what it holds
   rather than merging, so the panel never shows a composite the learner never asserted at
   one moment; a field the learner stops sending goes back to an em dash rather than
   standing as a stale number, and a field it never sends was never a zero.
5. The last status is kept and **sent again as soon as a viewer says hello**, so a viewer
   that attaches between two iterations fills its panel within a heartbeat instead of
   waiting minutes for the next one. A daemon thread waking once a second answers that
   hello, because a learner inside an optimisation step calls nothing for a long time
   (`pump_thread=False` hands that to the caller's own `pump()`).

```python
from royaleviser.model import Learning
from royaleviser.sources import LearningPublisher

learner = LearningPublisher()                       # 127.0.0.1:9871
for it in range(iterations):
    ...                                             # rollout, then optimise
    learner.publish(Learning(run="ppo-0007", iteration=it, policy_loss=0.0241, elo=1183))
```

6. `Learning.extra` is the open tail: `{name: number or string}`, drawn under the fixed rows
   in the order the learner sent them, so a number the fixed list has no place for needs no
   change on this side. The viewer only formats them (`render.extra_text`: an integer with
   thousands, a float to four significant figures, anything else as it stands, `None` as an
   em dash); the names and their meaning are the learner's. The panel leaves out the rows
   that do not fit its column, extras first, and a status too big for one datagram is
   counted in `LearningPublisher.dropped` rather than truncated.

A key that is neither a field name nor `extra` is **ignored**: a misspelled field leaves the
em dash of the field that stayed unset, rather than showing up as a wrong number somewhere
else.

A learner that would rather not import this package sends the same datagram itself, and
needs these five constants to match:

| Constant | Value | What it is |
|---|---|---|
| `sources.STREAM_HELLO` | `b"royaleviser 1"` | the viewer's heartbeat, sent to the learner's port once a second |
| `sources.STREAM_HEARTBEAT_S` | `1.0` | how often it arrives, and how often to look for it |
| `sources.STREAM_ATTACH_TIMEOUT_S` | `3` | no hello for this long: detached, send nothing |
| `model.LEARNING_PREFIX` | `b"\x81\xa8learning"` | the first bytes of every status datagram (msgpack for a one-key map named `learning`) |
| `sources.STREAM_MAX_DATAGRAM` | `65507` | one datagram, UDP over IPv4 |

So: bind `host:port`, read heartbeats, and when one arrives from an address that has not had
the standing status, send one msgpack map `{"learning": {...}}` — any subset of `Learning`'s
field names, plus `extra` — to that address. Keeping the last status and re-sending it on a
fresh hello is the sender's job; without it a viewer attaching mid-run waits for the next
iteration. `tests/run_stream.py` is the end to end check — it
publishes the scripted battle and a moving status from one process and draws them in the
real window (`docs/viewer-learning.png` was made with it).

## The command line

`python -m royaleviser [SOURCE [SOURCE]] [--stream HOST:PORT] [--learning HOST:PORT]
[--compare SOURCE]` plus the
view options below, which `__main__.add_view_arguments` adds to any parser. The first
source is the primary; a second positional, `--compare` or `--stream` is the compared one,
and more than two is an error.

| Option | Meaning |
|---|---|
| `--seat local\|0\|1` | who sits at the bottom. `local` (the default) seats the primary source's local player once the source knows it (the side whose hand a recording holds; team 0 for a trace or a stream); `0` and `1` pin a team. Seat 1 draws the board rotated 180 degrees, which is what that player's own screen shows. |
| `--geometry WxH+X+Y` | the window's outer rectangle in physical pixels, for a caller that places the window itself. Without `--scale` it picks the largest tile scale whose layout fits; under 16 px/tile (`COMPACT_BELOW`) the inspector column is dropped and the compare lines move into the dashboard, and the window then fills the whole rectangle. On Windows the process is made per-monitor DPI aware first so the pixels are physical. |
| `--learning HOST:PORT` | where a learner publishes its training status. Unset, it is the stream's port plus one, so attaching to a training run stays one flag; see [The learning status](#the-learning-status). |
| `--scale N` | pixels per tile (24). |
| `--speed F` | replay speed (1.0); `+`/`-` step through `SPEEDS` = 0.25 ... 8. |
| `--start-tick T` | the first frame shown (replays). |
| `--seconds N` | quit by itself after N seconds (unattended runs). |
| `--shot PATH` | save the last drawn window as a PNG before quitting. |

`SDL_VIDEODRIVER=dummy` makes `--seconds` and `--shot` work with no display at all, which is
how the README's images and the render tests are produced. `--help` prints the key list
(`app.KEYS`, the same list the `H` footer shows):

| Key | Action |
|---|---|
| space | play / pause (replays) |
| left / right, wheel | step a frame (shift: 20) |
| home / end | first / last frame |
| + / - (also ] [) | speed x2 / x0.5, through `SPEEDS` |
| f | flip the seat |
| p, t, g | unit paths, target lines, tile grid |
| d | debug numbers |
| c | compare ghost |
| s / F12 | save a PNG |
| click | pin a unit / seek the timeline |
| h | the help footer |
| escape / q | quit |

## The layout

Three columns, every rectangle from `theme.layout(theme, scale, tiles)` (`royaleviser/theme.py`);
the renderer computes no size of its own and everything is an integer.

| Column | Width at 24 px/tile | Contents |
|---|---|---|
| dashboard (left) | 345 px (`dashboard_w`: 4 cards of 80 px + 3 gaps of 5 = 335, flush with the window's left edge, plus a 10 px gutter before the arena) | the top player's hand flush with the top edge (80 x 100 px cards with their cost, dimmed when it is more than the player's elixir), its elixir bar (thousandths) and next card; a status block as tall as its content (source name, tick and clock, playing/live, the source's own status line, draw time and fps, then the last `events_lines` events, newest last); under it a learning panel filling the rest of the column (`LEARNING_GROUPS` down two columns: **learner** iteration, the two losses, entropy, KL, clip fraction, explained variance, grad norm and learning rate; **rollout** env steps/s, engine ticks/s, episode ticks, crowns and towers per episode, illegal actions and elixir wasted; **ladder** ELO, win rate, pool size and games against the frozen pool; then **extra**, whatever rows the learner named itself -- every value an em dash until a learner fills `Transport.learning` -- which a stream does from the status datagrams described in [The learning status](#the-learning-status) -- the heading reading "no learner attached" while none does, and the rows that do not fit the column left out); the bottom player's elixir bar and next card, and its hand flush with the bottom edge. Crowns, tower hp and the cycle are not repeated here: the crowns and clock sit in the small box top right of the arena, the tower hp bars on the towers |
| arena (middle) | 18 x 24 = 432 px wide, 32 x 24 = 768 px tall | checkerboard grass, river band, bridges, the crown towers' no-deploy rectangles, troops as circles and buildings and towers as squares, hp bars, names, paths, target lines, spells and projectiles; the crowns and clock in a small box top right, `OVERTIME` centred, the `GAME OVER` banner; the status line and the scrub bar underneath |
| inspector (right) | 300 px (`inspector_w`; 0 in the compact layout) | the hovered or pinned unit's fields, raw and unrounded (position, hp, radius, target, the stun and deploy counters, then whatever else the source carries under "extra"), then the compare lines and the `H` help footer |

The palette is carried over from the project's earlier Python renderer: grass
(188,195,55)/(217,215,47), river (106,230,237), bridge (255,175,120), team 0 blue
(71,204,218), team 1 red (224,73,41), UI (30,30,40). The bottom player is `ViewState.seat`;
the board surface is built once per seat and grid setting and blitted on every draw.

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
before the once-a-second heartbeat poll noticed the viewer. The RustEngine stream run
stepped 360 env steps in 11.9 s and sent 329 datagrams with 0 dropped; the viewer drew 335
frames of it, and the ~31 steps that ran before the heartbeat poll noticed the viewer are
the difference. A capture opens in 0.1-0.2 s (a 4047-frame gzipped file in 0.10 s: a line
index plus a parse cache, so a seek is one `json.loads`).

The window is redrawn only when the frame or the view changed, capped at `FPS_CAP` = 60. A
replay is paced by the frame's `tick_ms` times the speed (`App.advance`): it steps through
every frame it is due, one at a time, so the event log and the compare totals see every
frame even at 8x. A live source redraws its status line every `LIVE_REFRESH_S` = 0.5 s
without a new frame, so the fps and "drops" counters stay current.

The scripted battle (`tests/run_synthetic.py --seconds 8`, a real window, 2026-09-21): 158
draws, 3.53 ms mean, 219.84 ms max.

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
