"""The three sources against real inputs: the 12:07 captures of one friendly as both seats
saw it, a MockEngine trace and a publisher on localhost. The captures are RoyaleLive's
(gitignored there): ROYALELIVE_REPORTS names the folder, default ``tests/captures`` inside
this checkout; without them the capture tests skip."""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from royalegym.done_condition import GameOverCondition, StepLimitCondition
from royalegym.env import ClashParallelEnv
from royalegym.mock_engine import MockEngine
from royalegym.protocol import EntityKind, ShuffleMode
from royalegym.replay import ReplayRecorder, save_trace
from royalegym.selfplay import RandomLegalOpponent
from royalegym.state_mutator import DefaultStateMutator
from royalegym.viser import ViserPublisher
from royaleviser import model, sources
from royaleviser.model import Frame, Names, Source

REPORTS = Path(os.environ.get("ROYALELIVE_REPORTS") or Path(__file__).resolve().parent / "captures")
# The 12:07 demo battle (2026-09-20): capture A is side 0 local, capture B side 1 local.
CAPTURE_A = REPORTS / "frames-demo-20260920-120752-A.jsonl"
CAPTURE_B = REPORTS / "frames-demo-20260920-120754-B.jsonl"
# A second recorded battle: side 0 played Goblin Drills and Miners.
CAPTURE_DRILL = REPORTS / "frames-auto-20260920-083112-A.jsonl"
ALL_TYPES = [0, 3, 7, 9, 10, 11, 13, 14]


def has_capture(path: Path) -> bool:
    return sources.resolve_capture(path).exists()


needs_captures = pytest.mark.skipif(
    not (has_capture(CAPTURE_A) and has_capture(CAPTURE_B)),
    reason=f"the 12:07 demo captures are not in {REPORTS}",
)


def sound(frame: Frame) -> Frame:
    assert model.problems(frame) == []
    return frame


# --- CaptureSource ---------------------------------------------------------------


@pytest.fixture(scope="module")
def capture_a() -> sources.CaptureSource:
    pytest.importorskip("royaleviser")
    if not has_capture(CAPTURE_A):
        pytest.skip("no capture")
    return sources.CaptureSource(CAPTURE_A)


@needs_captures
def test_capture_opens_on_the_first_active_frame(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    assert isinstance(src, Source)
    assert (src.live, src.units_per_tile, src.local_side) == (False, 1000, 0)
    assert src.length > 3000 and src.index == 0
    assert src.inactive > 0 and src.other >= 1  # the capture's start line
    f = sound(src.frame())
    assert f.tick == 0 and f.units_per_tile == model.LIVE_UNITS_PER_TILE
    assert len(f.units) == 6 and all(u.kind in model.TOWER_KINDS for u in f.units)
    assert f.meta["source"] == "capture" and f.meta["local_side"] == 0
    assert "frame 1/" in src.status()


@needs_captures
def test_capture_frame_at_tick_1000(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(src.index_at_tick(1000))
    f = sound(src.frame())
    assert f.tick == 1000 and not f.overtime and not f.game_over and f.winner == model.NO_WINNER
    assert len(f.units) == 7
    towers = [u for u in f.units if u.kind in model.TOWER_KINDS]
    assert len(towers) == 6
    assert {u.name for u in towers} == {"KingTower", "PrincessTower"}
    kings = [u for u in towers if u.kind == model.KIND_KING_TOWER]
    assert sorted((u.team, u.x, u.y) for u in kings) == [(0, 9000, 3000), (1, 9000, 29000)]
    (bomber,) = [u for u in f.units if u.kind == model.KIND_TROOP]
    assert (bomber.team, bomber.name, bomber.x, bomber.y, bomber.hp) == (
        1,
        "Bomber",
        14696,
        20202,
        304,
    )
    assert bomber.uid.endswith(":26000013:1") and bomber.state == 2
    assert bomber.direction == (-39, -253) and "behavior_state" in bomber.extra
    assert "path_nodes" not in bomber.extra

    p0, p1 = f.players
    assert p0.elixir_milli > 0 and p1.elixir_milli > 0
    assert p0.elixir_known and p1.elixir_known
    assert p0.hand == ["IceSpirits", "Bomber", "Giant", "Tesla"] and p0.hand_known
    assert p0.next_card == "Skeletons" and p0.cycle == ["Knight", "Musketeer", "Goblins"]
    assert p0.deck_known and len(p0.deck) == 8 and "Musketeer" in p0.deck
    assert p1.hand == ["?"] * 4 and not p1.hand_known and not p1.deck_known
    assert p1.next_card is None and p1.deck == []
    # Towers in the owner's frame (engine convention): side 0 left = x 3500 (hp 2401 here),
    # side 1 left = x 14500 (full), right = x 3500 (2835).
    assert p0.tower_hp == [4824, 2401, 3052] and p0.tower_max_hp == [4824, 3052, 3052]
    assert p1.tower_hp == [4824, 3052, 2835]
    assert f.crowns == [0, 0] and p0.king_active is None

    assert f.events and all(e.startswith("t") for e in f.events)
    assert any(" spawn Red " in e for e in f.events) and any(" death " in e for e in f.events)
    assert any(" Blue plays " in e for e in f.events)
    assert not any(" Red plays " in e for e in f.events)  # the opponent's hand is not known
    assert f.events == sorted(f.events, key=lambda e: int(e[1:].split()[0]))


@needs_captures
def test_capture_seek_step_and_the_frozen_end(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(-5)
    assert src.index == 0
    src.step(10)
    assert src.index == 10 and src.frame().tick == src.tick_at(10)
    src.seek(10**9)
    assert src.index == src.length - 1
    f = sound(src.frame())
    # The last frame: the tick is frozen at 3690 for the rest of the file and the winner is
    # read off the missing tower.
    assert f.tick == 3690 and f.game_over and not f.overtime
    assert f.crowns == [1, 0] and f.winner == 0
    assert len(f.events) == sources.EVENTS_KEPT
    src.step(-1)
    assert src.frame().game_over  # still inside the frozen run
    src.seek(src.index_at_tick(3000))
    assert not src.frame().game_over
    assert src.index_at_tick(10**6) == src.length - 1 and src.index_at_tick(-1) == 0


@needs_captures
def test_capture_paths_targets_and_projectiles(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(src.index_at_tick(362))
    f = sound(src.frame())
    walkers = [u for u in f.units if u.path]
    assert walkers
    u = walkers[0]
    assert u.name == "Skeletons" and u.team == 1
    # path_nodes are goal-first in the capture; the model wants start-first: the first
    # node is next to the unit, the last one is the goal near Blue's tower.
    assert abs(u.path[0][1] - u.y) < 2000 and u.path[-1][1] < u.y
    assert all(x % 500 == 250 and y % 500 == 250 for x, y in u.path)
    assert u.target is not None and f.unit(u.target) is not None
    assert f.unit(u.target).kind == model.KIND_PRINCESS_TOWER
    src.seek(src.index_at_tick(460))
    f = sound(src.frame())
    (shot,) = f.spells
    assert shot.name == "Tower shot" and shot.team == 0 and shot.motion == 0
    assert (shot.x, shot.y) == (14547, 7997) and (shot.aim_x, shot.aim_y) == (14739, 13821)
    assert shot.extra["card_id"] == -1


@needs_captures
def test_capture_deploying_state_and_the_play_event(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(src.index_at_tick(472))
    f = sound(src.frame())
    assert "t472 Blue plays IceSpirits" in f.events
    src.seek(src.index_at_tick(350))
    deploying = [u for u in src.frame().units if u.deploy_ticks > 0]
    assert deploying and all(u.state == sources.LIVE_DEPLOY_STATE for u in deploying)


@needs_captures
def test_the_two_clients_see_the_same_battle() -> None:
    a = sources.CaptureSource(CAPTURE_A)
    b = sources.CaptureSource(sources.resolve_capture(CAPTURE_B))
    assert (a.local_side, b.local_side) == (0, 1)

    def signature(src: sources.CaptureSource, tick: int) -> Counter | None:
        i = src.index_at_tick(tick)
        if src.tick_at(i) != tick:
            return None
        src.seek(i)
        return Counter((u.team, u.name, u.x, u.y, u.hp) for u in src.frame().units)

    same = differ = 0
    for tick in range(0, 3691):
        sa, sb = signature(a, tick), signature(b, tick)
        if sa is None or sb is None:
            continue
        if sa == sb:
            same += 1
        else:
            differ += 1
    # Entity ids differ per client; positions and hp are the same lockstep simulation. The
    # few differing ticks are tap ticks where the two captures disagree for one frame (6 of 2407).
    assert same > 2000 and differ <= 10, (same, differ)
    assert signature(a, 1000) == signature(b, 1000)
    a.close()
    b.close()
    assert a.length == 0 and a.frame() is None


def test_capture_helpers(tmp_path: Path) -> None:
    assert sources.capture_stem(Path("frames-demo-1.jsonl.gz")) == "demo-1"
    assert sources.capture_stem(Path("x.jsonl")) == "x"
    raw = tmp_path / "frames-t.jsonl"
    assert sources.resolve_capture(raw) == raw  # missing: the path itself
    gz = tmp_path / "frames-t.jsonl.gz"
    gz.write_bytes(b"")
    assert sources.resolve_capture(raw) == gz
    assert sources.capture_node_xy(533) == (533 % 36 * 500 + 250, 533 // 36 * 500 + 250)
    assert sources.tiles_text(14696, 1000) == "14.7" and sources.tiles_text(63000, 18000) == "3.5"
    empty = sources.CaptureSource(gz)
    assert empty.length == 0 and empty.frame() is None and "no active frames" in empty.status()
    with pytest.raises(ValueError, match="not a capture"):
        sources.open_source("something.txt")


def test_capture_frame_conversion_without_a_file() -> None:
    names = Names.live()
    raw = {
        "tick": 3700,
        "seq": 5,
        "players": [
            {
                "side": 0,
                "elixir_raw": 45000,
                "hand": [0, 1, 2, 3],
                "cycle": [4, 5, 6, 7],
                "deck": [
                    26000000,
                    26000001,
                    28000000,
                    26000014,
                    27000000,
                    26000010,
                    28000001,
                    26000013,
                ],
            },
            {"side": 1, "elixir_raw": 12345, "hand": [-1, -1, -1, -1], "cycle": [], "deck": []},
        ],
        "entities": [
            {
                "id": "0x1",
                "card_id": -1,
                "side": 0,
                "x": 9000,
                "y": 3000,
                "hp": 4824,
                "max_hp": 4824,
                "behavior_state": 0,
            },
            {
                "id": "0x2",
                "card_id": -1,
                "side": 0,
                "x": 3500,
                "y": 6500,
                "hp": 100,
                "max_hp": 3052,
                "behavior_state": 0,
            },
            {
                "id": "0x3",
                "card_id": -1,
                "side": 1,
                "x": 9000,
                "y": 29000,
                "hp": 4824,
                "max_hp": 4824,
                "behavior_state": 0,
            },
            {
                "id": "0x4",
                "card_id": 27000000,
                "side": 1,
                "x": 9000,
                "y": 25000,
                "hp": 500,
                "max_hp": 700,
                "behavior_state": 2,
                "target": "0x2",
                "movement_direction_x": 0,
                "movement_direction_y": 0,
            },
        ],
        "effects": [
            {
                "id": "0x9",
                "side": 1,
                "card_id": 28000000,
                "x": 9000,
                "y": 20000,
                "x2": 9000,
                "y2": 20500,
                "projectile_x": 3500,
                "projectile_y": 6500,
            }
        ],
    }
    f = sound(sources.capture_frame(raw, names, ["e"], True, {"source": "test"}))
    p0, p1 = f.players
    assert p0.hand == ["Knight", "Archer", "Fireball", "Musketeer"]
    assert p0.next_card == "Cannon" and p0.cycle == ["Skeletons", "Arrows", "Bomber"]
    assert (p0.elixir_milli, p1.elixir_milli) == (4500, 1234)
    assert p0.tower_hp == [4824, 100, 0] and p0.tower_max_hp == [4824, 3052, 3052]
    assert p1.tower_hp == [4824, 0, 0] and p1.tower_max_hp == [4824, 3052, 3052]
    assert (p0.crowns, p1.crowns) == (2, 1) and f.crowns == [2, 1]
    assert f.overtime and f.game_over and f.winner == 0
    kinds = {u.name: u.kind for u in f.units}
    assert kinds == {"KingTower": 2, "PrincessTower": 3, "Cannon": 1}
    cannon = f.unit("0x4:27000000:1")
    assert cannon is not None and cannon.target == "0x2:-1:0" and cannon.direction is None
    (fireball,) = f.spells
    assert (fireball.name, fireball.team, fireball.motion) == ("Fireball", 1, 0)
    assert (fireball.aim_x, fireball.aim_y) == (3500, 6500)
    # No towers at all: hp unknown, not zero.
    bare = sources.capture_frame({"tick": 1, "players": [], "entities": []}, names, [], False, {})
    assert bare.players[0].tower_hp == [model.UNKNOWN_HP] * 3 and not bare.players[0].elixir_known
    assert bare.winner == model.NO_WINNER and model.problems(bare) == []


def test_event_log_lines() -> None:
    log = sources._EventLog()
    towers = {"k": (0, "KingTower", 9000, 3000, True)}
    log.note(0, towers, [None, None], 1000)
    assert log.lines == []  # the first note only seeds
    log.note(10, {**towers, "a": (0, "Knight", 3500, 14500, False)}, [[(1, "Knight")], None], 1000)
    log.note(20, dict(towers), [[(2, "Giant")], None], 1000)
    assert log.lines == [
        "t10 spawn Blue Knight (3.5, 14.5)",
        "t20 Blue plays Knight",
        "t20 death Blue Knight",
    ]
    log.reset()
    log.note(0, {}, [None, None], 1000)
    assert log.lines[-1] == "t20 death Blue Knight"


GOAL_CELL = 46 * 36 + 6  # path node of the half-tile cell (6, 46): centre (3250, 23250)


def test_capture_surfacing_of_a_tunnelling_entity() -> None:
    e = {
        "card_id": 27000013,
        "side": 0,
        "x": 4252,
        "y": 21943,
        "behavior_state": 6,
        "path_nodes": [GOAL_CELL, 45 * 36 + 7],  # goal first; the nearest cell (3750, 22750) last
    }
    length = 950 + 707  # isqrt(502^2 + 807^2) to the nearest cell, then one diagonal half-tile
    assert sources.capture_surfacing(e) == (3250, 23250, length // 300)
    assert sources.capture_surfacing({**e, "card_id": 26000032}) == (3250, 23250, length // 650)
    deploying = {**e, "behavior_state": sources.LIVE_DEPLOY_STATE}
    assert sources.capture_surfacing(deploying) is None
    assert sources.capture_surfacing({**e, "card_id": 26000000}) is None  # a Knight never tunnels
    assert sources.capture_surfacing({**e, "path_nodes": []}) is None
    # RoyaleLive's opponent tracker keeps the same speed table; its test_viser_live.py
    # asserts the two are equal.


def test_capture_events_announce_a_tunnel_once() -> None:
    ev = sources.CaptureEvents(Names.live())
    drill = {
        "id": "0x10",
        "card_id": 27000013,
        "side": 1,
        "x": 4252,
        "y": 21943,
        "behavior_state": 6,
        "path_nodes": [GOAL_CELL, 45 * 36 + 7],
    }

    def frame(tick: int, entities: list[dict]) -> dict:
        return {"tick": tick, "players": [], "entities": entities}

    ev.note(frame(10, []))
    ev.note(frame(11, [drill]))
    nearer = {**drill, "x": 3820, "y": 22671, "path_nodes": [GOAL_CELL]}
    ev.note(frame(12, [nearer]))  # the same trip, no new line
    ev.note(frame(13, [{**drill, "behavior_state": 4, "path_nodes": []}]))  # surfaced, same uid
    ev.note(frame(14, []))
    assert ev.lines == [
        "t11 spawn Red GoblinDrill (4.3, 21.9)",
        "t11 Red GoblinDrill -> (3250,23250) ~5 ticks",
        "t14 death Red GoblinDrill",
    ]
    # A later drill whose entity id (uid) is the surfaced one's again is its own trip: the
    # same card and side come back on a reused id in 16 of 58 captures scanned (09-18/19).
    ev.note(frame(200, [drill]))
    ev.note(frame(201, [nearer]))
    assert ev.lines[-2:] == [
        "t200 spawn Red GoblinDrill (4.3, 21.9)",
        "t200 Red GoblinDrill -> (3250,23250) ~5 ticks",
    ]
    ev.note(frame(500, []))
    ev.note(frame(0, [drill]))  # a new battle: the uid may come back and is announced again
    assert ev.lines[-1] == "t0 Red GoblinDrill -> (3250,23250) ~5 ticks"


@pytest.mark.skipif(not has_capture(CAPTURE_DRILL), reason=f"no 08:31 capture in {REPORTS}")
def test_capture_drill_surfacing_line() -> None:
    """The drill of tick 2974 (state 6 at (9178,3569), 39 path nodes) is announced with its goal
    and the 73 ticks it took: the building stood at (3000,23000) from tick 3047."""
    src = sources.CaptureSource(CAPTURE_DRILL)
    line = "t2974 Blue GoblinDrill -> (3250,23250) ~73 ticks"
    src.seek(src.index_at_tick(2973))
    assert line not in src.frame().events
    src.seek(src.index_at_tick(2974))
    f = sound(src.frame())
    assert f.tick == 2974 and line in f.events
    assert f.events.index("t2974 spawn Blue GoblinDrill (9.2, 3.6)") < f.events.index(line)
    state = sources.LIVE_UNDERGROUND_STATE
    (drill,) = [u for u in f.units if u.name == "GoblinDrill" and u.state == state]
    assert (drill.x, drill.y, len(drill.path)) == (9178, 3569, 39) and drill.path[-1] == (
        3250,
        23250,
    )
    src.seek(src.index_at_tick(3047))
    f = sound(src.frame())
    assert f.tick == 3047
    buildings = [u for u in f.units if u.name == "GoblinDrill" and u.kind == model.KIND_BUILDING]
    assert [(u.x, u.y) for u in buildings] == [(3000, 23000)]
    lines = src._events.lines
    assert lines.count(line) == 1  # announced once per trip
    assert "t249 Blue GoblinDrill -> (6250,2250) ~8 ticks" in lines  # own side, building at 257
    assert "t2947 Blue Miner -> (3750,1750) ~7 ticks" in lines  # the Miner tunnels too, 650/tick
    assert not any(" Red " in e and "->" in e for e in lines)  # side 1 never tunnelled
    src.close()


# --- TraceSource ------------------------------------------------------------------


def record(steps: int = 60, frame_every_tick: bool = True, **mutator_kwargs: object):
    """test_env_render._record: a 60-step MockEngine battle with random legal deploys."""
    rec = ReplayRecorder(frame_every_tick=frame_every_tick)
    env = ClashParallelEnv(
        recorder=rec,
        state_mutator=DefaultStateMutator(decks=[ALL_TYPES, ALL_TYPES[::-1]], **mutator_kwargs),
        termination_cond=GameOverCondition(),
        truncation_cond=StepLimitCondition(steps),
    )
    obs, _ = env.reset(seed=2026)
    rng = np.random.default_rng(0)
    opp = RandomLegalOpponent(noop_prob=0.6)
    while env.agents:
        obs, *_ = env.step({a: opp.act(obs[a], obs[a]["action_mask"], rng) for a in env.agents})
    assert rec.trace is not None
    return rec.trace


@pytest.mark.parametrize("shuffle", [ShuffleMode.INDEPENDENT, ShuffleMode.NONE])
def test_trace_source(tmp_path: Path, shuffle: int) -> None:
    trace = record(shuffle=shuffle)
    path = save_trace(trace, tmp_path / "t.msgpack")
    src = sources.open_source(str(path))
    assert isinstance(src, sources.TraceSource) and isinstance(src, Source)
    assert (src.live, src.units_per_tile, src.length) == (False, 18000, len(trace.frames))
    assert src.name == f"trace seed {trace.header.seed}"
    assert (src.arena.tiles_x, src.arena.tiles_y, src.arena.subtile) == (18, 32, 18000)
    assert src.arena.grid == trace.header.grid

    f = sound(src.frame())
    assert f.tick == 0 and len(f.units) == 6 and f.meta["source"] == "trace"
    p0 = f.players[0]
    assert p0.hand_known and p0.deck_known and len(p0.deck) == 8 and p0.elixir_known
    assert p0.tower_hp == [2400, 1400, 1400] == p0.tower_max_hp
    if shuffle == ShuffleMode.NONE:
        assert p0.hand == ["Knight", "Giant", "Minions", "Valkyrie"]
        assert p0.next_card == "Cannon" and p0.cycle == ["Fireball", "Zap", "Log"]
    else:
        assert p0.next_card is None and p0.cycle == ["?"] * 3

    # The cycle rule against the recorded hands: every drawn card was the predicted front.
    checked = 0
    for i in range(1, src.length):
        for team in (0, 1):
            was, now = trace.frames[i - 1].hands[team], trace.frames[i].hands[team]
            for a, b in zip(was, now, strict=True):
                if a != b:
                    front = src._queues[i - 1][team][0]
                    assert front is None or front == b
                    checked += front is not None
    assert checked >= (10 if shuffle == ShuffleMode.NONE else 1)

    src.seek(src.length - 1)
    f = sound(src.frame())
    assert f.tick == trace.frames[-1].tick and f.crowns == trace.result.crowns
    assert not f.game_over  # StepLimitCondition ended it: no winner, not a battle end
    assert any(" plays " in e and "(" in e for e in f.events)
    assert any(" spawn " in e for e in f.events)
    troops = [u for u in f.units if u.kind == EntityKind.TROOP]
    assert troops and all(u.radius > 0 and u.path == [] and u.target is None for u in troops)
    assert all(u.extra["tower_slot"] == -1 for u in troops)
    assert src.index_at_tick(30) == 30 and "every tick" in src.status()


def test_trace_source_per_decision(tmp_path: Path) -> None:
    trace = record(steps=20, frame_every_tick=False)
    src = sources.TraceSource(save_trace(trace, tmp_path / "t.json"))
    assert src.length == 21 and "per decision" in src.status()
    src.seek(20)
    f = sound(src.frame())
    assert f.tick == trace.frames[-1].tick and len(f.events) > 0


def test_frame_from_state_matches_the_publisher_rows() -> None:
    eng = MockEngine()
    setup = DefaultStateMutator(decks=[ALL_TYPES, ALL_TYPES]).build(
        np.random.default_rng(1), eng.cards()
    )
    eng.reset(7, setup)
    eng.step([], 20)
    f = sound(sources.frame_from_state(eng.state(), Names.from_cards(eng.cards()), 18000))
    assert f.tick == 20 and f.units_per_tile == 18000 and len(f.units) == 6
    assert all(p.hand_known and p.elixir_known and not p.deck_known for p in f.players)


# --- StreamSource / Publisher ------------------------------------------------------


def wait_for(src: sources.StreamSource, timeout: float = 2.0) -> Frame | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        f = src.frame()
        if f is not None:
            return f
        time.sleep(0.01)
    return None


def test_stream_round_trip_with_the_env_publisher() -> None:
    pub = ViserPublisher(port=0)
    env = ClashParallelEnv(viser=pub, state_mutator=DefaultStateMutator(decks=[ALL_TYPES] * 2))
    env.reset(seed=3)
    assert not pub.attached and pub.sent == 0  # nobody watching: nothing sent
    src = sources.StreamSource(*pub.address)
    assert src.live and src.length is None and "waiting for a publisher" in src.status()
    time.sleep(0.05)
    pub._last_poll = 0.0  # the publisher polls for heartbeats once a second
    env.step({"blue": 0, "red": 0})
    f = wait_for(src)
    assert f is not None and sound(f).meta["source"] == "engine"
    assert src.units_per_tile == 18000 and src.index >= 1
    first_tick = f.tick
    env.step({"blue": 0, "red": 0})
    f2 = wait_for(src)
    assert f2 is not None and f2.tick > first_tick and f2.meta["seq"] == f.meta["seq"] + 1
    assert "fps" in src.status() and src.drops == 0
    src.close()
    env.close()


def test_stream_round_trip_with_the_frame_publisher() -> None:
    pub = sources.Publisher(port=0)
    src = sources.StreamSource(*pub.address)
    time.sleep(0.05)
    pub._pub._last_poll = 0.0
    assert pub.attached
    eng = MockEngine()
    eng.reset(
        1, DefaultStateMutator(decks=[ALL_TYPES] * 2).build(np.random.default_rng(0), eng.cards())
    )
    frame = sources.frame_from_state(eng.state(), Names.from_cards(eng.cards()), 18000, ["e1"])
    assert pub.publish(frame) and pub.sent == 1
    back = wait_for(src)
    assert back is not None
    assert (back.units, back.players, back.events, back.tick) == (
        frame.units,
        frame.players,
        ["e1"],
        frame.tick,
    )
    src.close()
    pub.close()
    assert not pub.publish(frame)  # closed: nothing goes out


def test_open_source_by_shape() -> None:
    src = sources.open_source("127.0.0.1:9999")
    assert isinstance(src, sources.StreamSource) and src.name == "127.0.0.1:9999"
    src.close()
