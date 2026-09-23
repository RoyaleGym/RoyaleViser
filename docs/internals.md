# Internals

This page is for people changing the viewer, or writing something that feeds it. The README
covers usage. Here you get the frame model, the recording format, the stream protocol, the
command line, the window layout, the key table, what the viewer costs, how it is tested and
what it cannot draw.

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
- A building or tower carries the **box it stands on**, `Unit.footprint`, as a closed
  `[x0, y0, x1, y1]` in the frame's own units. `None` means a troop, or a source that does not
  have one: see the next section, which is about what the window does with that.

The card table `royaleviser/cards.json` maps a card's register name to `[id, cost]`; it is the
live client's table with the hero-form Musketeer (203000014) folded into the Musketeer.
`ROYALEVISER_CARDS` points `Names.live()` at another table, and a trace header's `CardInfo`
list overrides both for that trace. `cards.json` and the tunnel-speed table
`sources.LIVE_TUNNEL_SPEED` are copies of the recording instrument's own tables and must
stay equal to them.

## How big a building is drawn

A Cannon is 3 tiles by 3. Until 2026-09-22 the window drew every building as a square of
twice its collision radius, which for a Cannon is 1.2 tiles, and the two crown towers from
constants in the renderer. None of those numbers is the box a building occupies; the owner
found it by looking at a Cannon sitting against the arena wall over about a sixth of the
ground it stands on: a 1.2-tile square is 0.4 of a 3-tile side, so 0.16 of the area.

So the rule is now one line: **the drawn rectangle is `Unit.footprint`, and where a frame
carries one nothing else has a say.** `Renderer.unit_rect_px` is the single place that
decides, and the hp bar, the name label, the hover ring, the click target, the deploy veil
and the compare ghost all read it, so a 3x3 Cannon is clickable over all nine of its tiles and
carries a bar and a veil its own width. The collision radius decides only the circle inside.

Where a frame carries none -- every recording, and every trace and stream written before the
field existed -- the old guess is still drawn, because the window has to draw something, and
it is **marked**: red ticks on the building's four corners, a red count in the status block
("5 of 8 building sizes guessed"), and the reason in the inspector's `box` row. A wrong size
drawn plainly is indistinguishable from a right one, which is exactly how a Cannon one tile
wide sat on the board for days looking like a fact.

**The box and the circle are two different things, so they are drawn as two shapes, and
which one is FILLED says which is the thing itself.** The filled circle is the collision
radius: what other units and other buildings actually run into, and so the building's body.
The outline around it is the footprint, the ground it stands on, which is a fact about the
board rather than about the unit. Measured on the engine, 2026-09-22: a Cannon's body is 0.6
of a tile across a box of 3, a princess tower 1.0 across 3, a king tower 1.4 across 4.

The two shapes also carry different colours, for the same reason. The body is the TEAM's
colour; the box is neutral. Whose building it is is a fact about the building, and the ground
it stands on is a fact about the board.

A frame that carries no radius has no body to draw, and an outline on its own is a building
you can see through. Every recording is that case, so the box is filled in the team's colour
instead and the marks above say its size was guessed. Until 2026-09-22 the window drew a
filled box with an inner SQUARE at half of it, on towers only, which was neither quantity.

## The board under the battle

Two things about the ground a watcher used to have to infer.

**Where nothing may be placed is filled grey**: the back rows, the river's corners and the
blocks under the crown towers, taken from the arena's own NO_DEPLOY cells rather than from a
list here. It used to be a faint outline around each region, which asks a reader to
reconstruct a shape from its border and reads as decoration beside the grass. A player cannot
use that ground, so it does not look like ground they can use.

**Each bridge carries a brown rail down both long sides.** A bridge is the only way across the
river and its edge is where a unit stops being on it. The rails are drawn on the half-cells
whose left or right neighbour is not bridge, so they follow whatever the arena says the
bridges are rather than a hard-coded span.

Two things the window can say about footprints without knowing any of the engine's rules:

- **B** shades every carried box and marks each tile whose CENTRE lands inside one. That is
  the part of deploy legality a frame settles by itself. It is not the whole of it: a
  building brings its own size to the test, and no frame says which card a player is about to
  play, so the overlay does not pretend to.
- The status block says when a carried box **runs off the board**, which is the shape of the
  defect the owner saw. Whether a placement was legal is the engine's answer; whether a box
  is inside the arena is two comparisons on numbers already in front of the window.

Boxes may legitimately touch each other and the side walls; only a positive-area overlap is
illegal, so a building drawn flush against a tower is not by itself a defect.

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
the active frames indexed by tick. A frame is parsed when it is looked at, through a bounded
cache of `PARSE_CACHE` = 200 parsed frames. That is why a 40 MB recording opens in about
0.2 s and a seek costs one `json.loads`. Events (spawns, deaths, the local player's plays) are
derived forward from consecutive frames the first time the timeline passes them and cached
per index, so seeking backwards still shows the events up to that frame.

## Comparing two sources

The window ghosts a second source (a second path, or `--compare PATH`) onto the board as
hollow white shapes at the primary's tick, and compares it tick by tick. The comparison is on
the multiset of `(team, name, x, y, hp)` over one tick's units, that is on the bag of those
tuples with order ignored and duplicates kept: for the two seats of one battle that multiset
must be equal, since entity ids differ between clients but nothing else does.
The panel prints `tick T: N entities, M differ` and the running `K ticks compared, D differ`
(`app.Compare`; a replay seeks the second source to the exact tick, live sources are matched
through a 60-tick buffer).

Measured on the two seats of one recorded battle (client 16.402): 2404 ticks compared, 3
differ. All three are on tap ticks, where the two recordings disagree for a single frame. An
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

### A tolerance, for the case where exact equality is the wrong question

Two recordings of one battle agree to the unit, because both clients run the same lockstep
simulation. An **engine replaying a recorded battle** does not and never will, so exact
equality makes every unit differ and the count says nothing about whether the engine is close
or lost.

`--tolerance MILLITILES` changes the question to how far apart they are. Each unit is paired
with the nearest unit of the other side of the same team and name, closest pair first, and a
pair within the tolerance agrees. Three properties are deliberate:

- A unit one side has and the other does not is **never** within any tolerance, however
  large. That is the failure a position tolerance must not hide.
- HP is not part of the pairing and not part of the differ count. A unit standing in the right
  place with the wrong hp is a different finding from one in the wrong place, so it is counted
  beside them: `12 entities, 1 differ, 2 hp`.
- Every line judged by a tolerance says which one, on the totals line. A reader who sees
  `0 differ` and no tolerance will take it for exact agreement.

### The engine beside the battle it replayed

`python -m royaleviser --parity FILE` opens **both** sides of a RoyaleSim parity trace at
once: the recording as the main source, the engine's own run of the same battle ghosted over
it, compared within 250 milli-tiles (a quarter tile, the tightest band that report scores)
unless `--tolerance` says otherwise. That is one command for "where do the engine and the real
game disagree", and it needs a machine that has the fixtures and the results, the way the
parity gate does.

The file is RoyaleSim's replay harness run with `--trace`, which adds a row per scored
unit-tick: the tick, the unit, where the recording had it and where the engine put it, both in
native milli-tiles. `royaleviser.parity` turns each column into a source. Both sides key units
by the RECORDING's entity key, so the compare pairs them without guessing.

Both sides key every row by the RECORDING's entity key, so the comparison pairs them by that
key rather than by name and distance. That is not a refinement, it is the difference between a
true answer and a flattering one: pairing by name pairs two Skeletons that SWAPPED places with
each other's positions, and the tick then reads as agreeing. `Compare(pair_by_uid=True)` is on
only for `--parity`, because two recordings of one battle number their entities separately. It
is on at EVERY tolerance there, including 0: a tolerance of 0 is the strictest setting, not the
absence of one, and it used to be the single setting that fell back to pairing by name.

What a parity file does not carry, and what the viewer does about it: no elixir, hands, decks,
crowns or result, so those say "not in this source"; no radius and no footprint, so buildings
draw at the marked fallback size; the path is a COUNT of nodes rather than the nodes, so it
rides in the inspector instead of being drawn as a path nobody recorded. `max_hp` is the most
that unit was ever seen with ON ITS OWN SIDE, so a recording's hp bar is never drawn against a
number only the engine reached. The report's `first_divergence`, its one statement about a
moment rather than a total, becomes an event line to scrub to.

Two columns the viewer deliberately does not draw. The engine's `target` is an index into the
harness's own list of engine entities, not a recording key, and the file publishes no way back
from it, so drawing it as a target line would point at whichever unit happened to hold that
number; it is a number in the inspector instead. And the rows are MATCHED PAIRS only, so an
entity the harness could not match is in neither side: the report counts those and the status
line carries the counts, because a view that quietly dropped them would be at its most
convincing exactly where the engine and the game agree least.

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
  both kings, the compact layout, the compare totals and the tolerance, the app's keys and
  pacing, `run` with `--shot` and `--geometry`. The footprint tests grade the drawn PIXELS
  rather than the helper that positions them: a helper returning the right rectangle and a
  draw call using a different one is the bug they exist for.
- `tests/test_parity.py`: both sides of a parity trace, on a file written by hand in the
  harness's row shape. One test opens a file the harness itself wrote and SKIPS where no
  results file with rows is on this machine; it is the only one that can say the shape is
  still the harness's.
- `tests/test_learner_protocol.py`: this package's wire constants against RoyaleLearn's copy
  of them, and a status the learner builds carried all the way into the drawn panel. Skips
  where royalelearn is not installed.
- `tests/test_cli.py`: `python -m royaleviser` end to end, headless.

`RoyaleGym/tests/test_viser.py` round-trips a published frame through this package's decoder,
so a drift between the publisher and the viewer fails there. `tests/run_synthetic.py` opens
the real window on the scripted battle straight from the script, with no sibling repo and no
recording needed: the look check for the renderer, and its `--compare` ghosts a
half-tile-shifted copy of the same battle to exercise the compare panel.

The suite has two correct results, and both are one command apart. Measured at acec042 on 2026-09-22, with `pytest --collect-only -q` collecting 161:

| Run | Result |
|---|---|
| a clone, `ROYALELIVE_REPORTS` pointed at an empty folder | **157 passed, 4 skipped** |
| this machine, with the recordings | **160 passed, 1 skipped** |

The four skips in a clone are the three tests that pin numbers only a recording of a real
battle has (2407 ticks both seats hold, 2404 equal, 3 differ; the Goblin Drill of tick 2974
surfacing 73 ticks later), plus the parity test that needs a results file written with the
harness's `--trace`. The first three say `SKIPPED, NOT PASSED` and `tests/conftest.py` prints
them by name at the end of the run, so a clone's "N passed, M skipped" is never read as all
green. Pointing `ROYALELIVE_REPORTS` at a folder holding
`frames-demo-20260920-120752-A.jsonl`, `frames-demo-20260920-120754-B.jsonl` and
`frames-auto-20260920-083112-A.jsonl` (or their `.jsonl.gz`; the default is `tests/captures`,
gitignored) runs those three.

Two more things move the count, in either run. Without the `media` extra
(`imageio-ffmpeg`, which `royaleviser.capture` needs only for mp4 and gif) ONE more skips,
the single `@needs_ffmpeg` test. Without RoyaleLearn importable, the four in
`tests/test_learner_protocol.py` skip. A count in this file is the output of the command
beside it and nothing else; the ones that stood here before were measured at a commit fifteen
behind and were wrong at that commit too.

## The stream protocol

The viewer must never be in the tick loop and must cost nothing when nobody is watching. The
engine-side half lives in the env layer (`royalegym.viser.ViserPublisher`) and imports
nothing from this package, so the dependency direction stays
`RoyaleLearn -> RoyaleGym -> RoyaleSim`. `sources.Publisher` wraps it for a caller that
already holds a `Frame`.

1. The viewer binds a UDP socket and sends the heartbeat datagram `royaleviser 1` to the
   publisher's `host:port` (default `127.0.0.1:9870`) once a second while it is open.
2. An environment calls `publish` once per `reset()` / `step()`, and only when it has been
   handed a publisher: `ClashParallelEnv(..., viser=ViserPublisher())`, which reads no
   environment variable of its own, the default `None` costing one `if`. The vectorised env
   is what reads `ROYALEVISER=host:port`. `ClashSelfPlayVecEnv(..., viser="env")`, the
   default, binds one publisher from it and hands it to game 0 (`None` never publishes, and
   a `ViserPublisher` is used as given). A viewer watches one battle, and N games each
   binding its one fixed port is an `OSError`, so the choice belongs where the games are.
   While no heartbeat has arrived in `ATTACH_TIMEOUT_S` (3 s), `publish` returns after one
   clock read, 193 ns per call, measured 2026-09-21 over 200k calls, best of three. It polls
   its socket for heartbeats at most once a second.
3. While attached it sends one msgpack datagram per call to the last heartbeat's address:
   measured 2.6 KB for 18 units (12 troops and the 6 towers, which are units of their own
   kind, not a list beside them), 11 KB for 60. A datagram over 65507
   bytes is resent with the unit paths emptied, then dropped and counted
   (`publisher.dropped`).

   **What that costs a training run was measured on 2026-09-22, and the answer is nothing
   this measurement could detect.** Eighteen iterations alternating three attached and three
   detached, three times over, so the ratio is taken inside one window: 28.13 +- 1.25 s an
   iteration attached against 28.33 +- 1.65 s detached, a difference of -0.20 s with a
   standard error of 0.69; inference time identical to two decimals. Every difference came
   out negative, which is the tell that it is noise rather than a cost, so the honest form is
   a BOUND and not a point estimate: **under 1.4 s an iteration at 95 %, which is under 5 %
   of one**, on a two-worker 48-battle run at 8,192 timesteps an iteration with a 64x4 net on
   an RTX 3050. The geometry is part of the number; quoting the bound without it says less
   than nothing. (Measured by the training session; its log carries the raw iterations.)
4. `royalegym.viser.frame_dict` builds the wire dict from a `BattleState`;
   `sources.frame_from_state` turns it into a `Frame`, and `TraceSource` builds its rows the
   same way, so a trace and a stream of one battle draw identically. Spawn and death event
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

1. `sources.LearningPublisher` binds `host:port`. By default that is the frames' port
   **plus one** (`sources.learning_endpoint`, 9871 against the default 9870), because two
   processes cannot bind one port. `--learning HOST:PORT` moves it.
2. The viewer says hello to both addresses from its one socket, so the learner attaches on
   the same heartbeat rule as the frame publisher and sends nothing until a viewer is there.
   Detached, `publish` keeps the status and returns after one clock read, 435 ns per call,
   measured 2026-09-21 over 200k calls, best of three. It polls the socket at most once
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
   (`pump_thread=False` hands that to the caller's own `pump()`). A hello after longer than
   the attach timeout counts as a fresh attachment whatever its address, since a viewer away
   that long may have been restarted.
6. **Nothing acknowledges a datagram.** A lost frame is replaced a few milliseconds later; a
   lost status is the panel standing still for a whole iteration, and a rollout publishing
   faster than the viewer's loop drains can fill the receive queue exactly as the one status
   of that minute arrives. Two things answer that: the viewer's socket asks for a megabyte of
   receive buffer (`sources.STREAM_RCVBUF`, a few hundred frames, against the 64 KB default
   that is two dozen), and each viewer is sent the standing status `LEARNING_REPEATS` (3)
   times a heartbeat apart before the sender falls silent.

```python
from royaleviser.model import Learning
from royaleviser.sources import LearningPublisher

learner = LearningPublisher()                       # 127.0.0.1:9871
for it in range(iterations):
    ...                                             # rollout, then optimise
    learner.publish(Learning(run="ppo-0007", iteration=it, policy_loss=0.0241, elo=1183))
```

7. `Learning.extra` is the open tail: `{name: number or string}`, drawn under the fixed rows
   in the order the learner sent them, so a number the fixed list has no place for needs no
   change on this side. The viewer only formats them (`render.extra_text`: an integer with
   thousands, a float to four significant figures, anything else as it stands, `None` as an
   em dash); the names and their meaning are the learner's. The panel leaves out the rows
   that do not fit its column, extras first, and a status too big for one datagram is
   counted in `LearningPublisher.dropped` rather than truncated. A value msgpack has no type
   for is counted there too, after one attempt to read it as the number it holds
   (`model.scalar_of`, which is what makes a numpy float a float); the learner's own thread
   survives it either way.

A key that is neither a field name nor `extra` is **ignored**: a misspelled field leaves the
em dash of the field that stayed unset, rather than showing up as a wrong number somewhere
else.

**How far apart the numbers really are, and where the time goes.** Measured 2026-09-22 on
the laptop profile (RustEngine, three worker processes, minibatch 512), from both ends
independently: an iteration is **518 to 544 seconds** of wall clock on a quiet machine, and
**806 seconds** on a saturated one, which was running two `cargo` builds, a `rustc` and 36
python processes with 212 MB free. A cadence number is only a fact with the machine's state
attached.

The minibatch is part of that state, and it has moved since. The shipped default is 256
(RoyaleLearn commit 582ce96), because a minibatch of 512 does not fit a 4 GB card and spills
into system memory. Nobody has timed the current default. So the timings here and the cadence
below stand as they were measured, on 2026-09-22 at a minibatch of 512, and a reader on the
default will not reproduce them.

The decomposition is the durable part, and it is the surprise. Timed from the viewer's side
across two iterations: the environment published **228 frames over about 40 seconds**, then
sent nothing for **765 seconds** before the next status arrived. The environment's own timers
agree: collection is 13 to 19 seconds and the update is 84 to 98 per cent of the iteration,
and the engine itself collects at roughly 1800 environment steps a second. The 64 steps a
second an iteration averages is the UPDATE dragging the average down, not the engine being
slow.

So a viewer on a real run, at those settings, sees a board that stands still for eight to
thirteen minutes at a time, and a panel that moves once in that window. **The board is still
because the learner is thinking, not because the engine is slow.** Those are different
findings, owned by different people. That is the separate datagram earning its keep, and it
is why the panel prints how old the status is beside its heading: at this cadence a run that
is working and a run that died forty minutes ago look identical without it, and the still
panel is the NORMAL case.

**Two runs on one machine collide, and the panel cannot tell you so.** The ports are fixed,
so the second run's learner finds the status port taken and publishes nothing. Meanwhile its
frame publisher may well get its own port and stream the battle normally. The window then
shows a live battle under a panel reading "no learner", which is nearly indistinguishable
from a run with no learner at all: the learner that failed to bind has no socket to say so
on. The panel does what little it can from this side and NAMES THE PORT it is listening on
("no learner on 127.0.0.1:9871"), so the absence is something a person can check rather than
a shrug; the asymmetry is what makes it a trap, since the frame publisher may get its port
while the learner does not, and a moving board is the first thing anyone looks at. Measured on 2026-09-22, one run holding 9871 while another streamed frames on 9870.
Give a second run its own pair (the learner's sink takes a host and port, and the viewer
takes `--learning HOST:PORT`), and check the learner's own log if a panel stays empty while
a battle plays. A related consequence of the same fixed-peer design: each publisher keeps
ONE peer, the address of the last heartbeat, so a second viewer saying hello to a run
silently takes the stream from the first.

A learner that would rather not import this package sends the same datagram itself, and
needs these six constants to match:

| Constant | Value | What it is |
|---|---|---|
| `sources.STREAM_HELLO` | `b"royaleviser 1"` | the viewer's heartbeat, sent to the learner's port once a second |
| `sources.STREAM_HEARTBEAT_S` | `1.0` | how often it arrives, and how often to look for it |
| `sources.STREAM_ATTACH_TIMEOUT_S` | `3` | no hello for this long: detached, send nothing |
| `model.LEARNING_PREFIX` | `b"\x81\xa8learning"` | the first bytes of every status datagram (msgpack for a one-key map named `learning`) |
| `sources.STREAM_MAX_DATAGRAM` | `65507` | one datagram, UDP over IPv4 |
| `sources.LEARNING_REPEATS` | `3` | copies of one status per viewer, a heartbeat apart, because nothing is acknowledged |

So: bind `host:port`, read heartbeats, and when one arrives from an address that has not had
the standing status, send one msgpack map `{"learning": {...}}` to that address, holding any
subset of `Learning`'s field names plus `extra`. Keeping the last status and re-sending it on
a fresh hello is the sender's job, and so is sending it more than once; without either, a
viewer attaching mid-run waits for the next iteration. `tests/run_stream.py` is the end to
end check. It publishes the scripted battle and a moving status from one process and draws
them in the real window (`docs/viewer-learning.png` was made with it).

A real training run has filled this panel. `docs/viewer-learning-real-run.png` is the window
attached to one on 2026-09-22, at iteration 33 of a laptop-profile self-play run: the
learner's column and the rollout column both arriving from RoyaleLearn's own sink. The two
LADDER tiles in that picture are not data -- a live learner is never evaluated under its own
id at this scale, so `ELO vs pool` and `win rate` read 1200 and 0.0 % whatever the run is
doing -- and the run was a diagnostic rather than the first real one. `docs/media/README.md`
carries the same warning beside the file.

## The command line

`python -m royaleviser [SOURCE [SOURCE]] [--stream HOST:PORT] [--learning HOST:PORT]
[--compare SOURCE] [--parity FILE] [--tolerance MILLITILES]` plus the
view options below, which `__main__.add_view_arguments` adds to any parser. The first
source is the primary; a second positional, `--compare` or `--stream` is the compared one,
and more than two is an error.

| Option | Meaning |
|---|---|
| `--seat local\|0\|1` | who sits at the bottom. `local` (the default) seats the primary source's local player once the source knows it (the side whose hand a recording holds; team 0 for a trace or a stream); `0` and `1` pin a team. Seat 1 draws the board rotated 180 degrees, which is what that player's own screen shows. |
| `--geometry WxH+X+Y` | the window's outer rectangle in physical pixels, for a caller that places the window itself. Without `--scale` it picks the largest tile scale whose layout fits; under 16 px/tile (`COMPACT_BELOW`) the inspector column is dropped and the compare lines move into the dashboard, and the window then fills the whole rectangle. On Windows the process is made per-monitor DPI aware first so the pixels are physical. |
| `--parity FILE` | a RoyaleSim parity trace written with `--trace`: opens BOTH sides of it, the recording with the engine's run of the same battle ghosted over the top, and compares within a quarter tile unless `--tolerance` says otherwise. It fills both source slots by itself, so it cannot be combined with another source. |
| `--tolerance MILLITILES` | count two units as agreeing while they are this far apart or less. 0, the default, is exact agreement; `--parity` defaults to 250. |
| `--learning HOST:PORT` | where a learner publishes its training status. Unset, it is the stream's port plus one, so attaching to a training run stays one flag; see [The learning status](#the-learning-status). |
| `--scale N` | pixels per tile (24). |
| `--speed F` | replay speed (1.0); `+`/`-` step through `SPEEDS` = 0.25 ... 8. |
| `--start-tick T` | the first frame shown (replays). |
| `--seconds N` | quit by itself after N seconds (unattended runs). |
| `--shot PATH` | save the last drawn window as a PNG before quitting. |

`--shot` REFUSES to write a picture of a live source that never received a frame, prints why
on stderr and exits 2. Such a picture is a real screenshot of the real window showing an empty
board under full panels, and it reads as a photograph of a dead run. The likely cause is worth
knowing: a publisher keeps ONE peer, so a second viewer on a stream that is already being
watched gets no frames, while its learner panel fills normally from the other port. A status
with no frames is diagnostic and the message says so.

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
| b | building footprints: every carried box shaded, and the tile taps its centre rule refuses |
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
| dashboard (left) | 345 px (`dashboard_w`: 4 cards of 80 px + 3 gaps of 5 = 335, flush with the window's left edge, plus a 10 px gutter before the arena) | the top player's hand flush with the top edge (80 x 100 px cards with their cost, dimmed when it is more than the player's elixir), its elixir bar (thousandths) and next card; a status block as tall as its content (source name, tick and clock, playing/live, the source's own status line, draw time and fps, then the last `events_lines` events, newest last); under it a learning panel filling the rest of the column (`LEARNING_GROUPS` down two columns: **learner** iteration, the two losses, entropy, KL, clip fraction, explained variance, grad norm and learning rate; **rollout** env steps/s, engine ticks/s, episode ticks, crowns and towers per episode, illegal actions and elixir wasted; **ladder** ELO, win rate, pool size and games against the frozen pool; then **extra**, whatever rows the learner named itself -- every value an em dash until a learner fills `Transport.learning` -- which a stream does from the status datagrams described in [The learning status](#the-learning-status) -- the heading naming the port it is listening on while nothing is there ("no learner on 127.0.0.1:9871" for a stream, "no learner attached" for a source that names no learner at all), and the rows that do not fit the column left out); the bottom player's elixir bar and next card, and its hand flush with the bottom edge. Crowns, tower hp and the cycle are not repeated here: the crowns and clock sit in the small box top right of the arena, the tower hp bars on the towers |
| arena (middle) | 18 x 24 = 432 px wide, 32 x 24 = 768 px tall | checkerboard grass, river band, bridges, the crown towers' no-deploy rectangles, troops as circles and buildings and towers as squares, hp bars, names, paths, target lines, spells and projectiles; the crowns and clock in a small box top right, `OVERTIME` centred, the `GAME OVER` banner; the status line and the scrub bar underneath |
| inspector (right) | 300 px (`inspector_w`; 0 in the compact layout) | the hovered or pinned unit's fields, raw and unrounded (position, hp, radius, target, the stun and deploy counters, then whatever else the source carries under "extra"), then the compare lines and the `H` help footer |

The palette is carried over from the project's earlier Python renderer: grass
(188,195,55)/(217,215,47), river (106,230,237), bridge (255,175,120), team 0 blue
(71,204,218), team 1 red (224,73,41), UI (30,30,40). The bottom player is `ViewState.seat`;
the board surface is built once per seat and grid setting and blitted on every draw.

## Capturing media

`royaleviser.capture.capture` writes what the window would show, with no window and no clock:
the README media of all four repos is regenerated from it when the engine changes.

```python
from royaleviser.capture import capture
from royaleviser.sources import open_source

src = open_source("battle.msgpack")
capture(src, "still.png", ticks=(900, 901, 1), scale=24, crop="left")
capture(src, "clip.gif", ticks=(0, 2400, 8), scale=16, crop="left", fps=20)
```

- **The suffix picks the format.** `.png` writes one file, or `name-0000.png` upward for a
  range; `.mp4` and `.gif` are encoded by the ffmpeg binary `imageio-ffmpeg` ships.
- **`ticks` is `(start, stop, step)` in battle ticks**, the clock the window shows, resolved
  through the source's `index_at_tick`, not frame indices. Without it the whole source is
  captured. Use a tick *step* rather than a low `fps` to shorten a long clip: stepping keeps
  the motion smooth where a low frame rate makes it stutter.
- **The output is deterministic, always.** An image in a README that changes when nothing
  changed is a diff nobody can review, so the two things that follow the wall clock (the
  draw time and the frame rate) are zero in every capture, and the LIVE pill is never
  drawn: a capture replays a file rather than watching an engine. `live_timing=True` adds
  the SOURCE's own status line ("frame 412/2400 ..."), which a replay builds from what it
  read rather than from a clock, so that is reproducible too. A shot whose subject is the
  timing of a live stream is not a capture; `tests/run_stream.py --shot` takes that one.
- **`crop`** is `full`, `board` (the arena alone), `left` (everything up to where the
  inspector starts, which keeps the dashboard and the timeline), or a rectangle. A small
  `scale` does NOT drop the inspector by itself: the compact layout is chosen from a geometry
  by `app.fit_layout`, so cropping is how a capture leaves that column out.
- **`view` and `compare`** are the window's own arguments: the seat, the overlays and
  `hover_uid` to pin a unit in the inspector, and a second source ghosted at the same tick.
- **A live stream is refused.** It has no timeline to seek, so `capture` raises rather than
  recording whatever happened to arrive; `tests/run_stream.py --shot` photographs one.

Measured on the scripted battle: the whole two-minute battle cropped `left` at scale 16,
every eighth tick, is a 1.0 MB gif; the same range every fourth tick is a 0.74 MB mp4; a
900-tick clip cropped `board` is 0.11 MB. ffmpeg is the `media` extra
(`pip install royaleviser[media]`) and PNG needs nothing beyond this package, so nobody has
to install a video encoder to look at a frame.

**What the tool cannot check for you.** `capture` takes any `Source`, so it will happily
draw a recording of a real battle. Those are private, and they are not a source for published
media. Media that goes into a README comes from an engine trace or from `tests/synthetic.py`,
and that guarantee lives in whoever writes the shot list, not in this function. Two things
worth saying in a caption while you are there: the scripted battle is a *script*, whose units
move at six times their scripted speed in the recording form, so it is honest as "the window"
and dishonest as "how the engine plays".

## The public surface other front ends use

`royaleviser.__main__` exports the pieces a second front end needs: `add_view_arguments`
adds the view options to any `argparse` parser, `run_sources` builds and runs the window over
already-opened sources, and `TITLE` is the window title a caller can find the window by.
Those three names, `capture.capture` and its `CROPS`, the exported `sources.capture_*`
converters and the `LIVE_*` constants are the surface outside callers depend on; changing
them is a breaking change.

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
  They are drawn as a default-radius ring. There are no rage, poison or freeze zones.
- **An opponent's hand and deck** are not in a capture, and a trace's cycle beyond the
  revealed cards shows as "next ?".
