# Internals

The frame model, the stream protocol, what the viewer costs and what it cannot draw. The
README covers usage, the key table and the layout; this page is for people changing the
viewer or writing something that feeds it.

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
   clock read, and it polls its socket for heartbeats at most once a second.
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

Measured on Windows 10 with the SDL dummy driver on a machine with other work running. The
stream run sent 256 datagrams over 300 env steps with 0 dropped; the first ~44 steps ran
before the once-a-second heartbeat poll noticed the viewer. A capture opens in 0.1-0.2 s (a
4047-frame gzipped file in 0.10 s: a line index plus a parse cache, so a seek is one
`json.loads`).

At 2-4 ms per draw the viewer is far below both the 20 Hz replay budget and the 60 Hz window
cap, which is why it is still Python and pygame rather than a Rust process on a shared
buffer.

## Limitations

- **Engine sources are exercised only on `MockEngine` so far.** A `RustEngine` trace or
  stream should draw identically — both go through `frame_from_state` — but it has not been
  run end to end.
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
