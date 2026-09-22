"""The three sources against real inputs: recordings in the capture format, a MockEngine
trace and a publisher on localhost.

The capture tests run on ``tests/fixtures/frames-synthetic-{A,B}.jsonl.gz``: the scripted
battle of ``synthetic.py`` written out as the two seats would have recorded it (394 ticks,
one seat's hand known per file, different entity ids). Those test PROPERTIES of the source
(parsing, frame conversion, the seat compare, the CLI) and pass in a fresh clone. The tests
that pin numbers only a real battle has (how many ticks the two seats' recordings differ
on, a Goblin Drill's tunnel) read recordings of real battles that are not published:
``ROYALELIVE_REPORTS`` names the folder (default ``tests/captures``, gitignored) and
without them those tests SKIP, and say so; a skip there is not a pass."""

from __future__ import annotations

import os
import socket
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic
from royalegym.done_condition import GameOverCondition, StepLimitCondition
from royalegym.env import ClashParallelEnv
from royalegym.mock_engine import MockEngine
from royalegym.protocol import EntityKind, ShuffleMode
from royalegym.replay import ReplayRecorder, save_trace
from royalegym.selfplay import RandomLegalOpponent
from royalegym.state_mutator import DefaultStateMutator
from royalegym.viser import ViserPublisher
from royaleviser import model, sources
from royaleviser.model import Frame, Learning, Names, Source

# The synthetic recordings, committed: A is seat 0's, B is seat 1's, of one scripted battle.
SYNTH_A, SYNTH_B = synthetic.fixture_paths()
SYNTH_TICKS = synthetic.LAST_TICK + 1  # 0..394, the last one frozen for the results screen

# Recordings of real battles (not published). ROYALELIVE_REPORTS names the folder.
REPORTS = Path(os.environ.get("ROYALELIVE_REPORTS") or Path(__file__).resolve().parent / "captures")
# One battle recorded from both seats (2026-09-20): A is side 0's recording, B side 1's.
CAPTURE_A = REPORTS / "frames-demo-20260920-120752-A.jsonl"
CAPTURE_B = REPORTS / "frames-demo-20260920-120754-B.jsonl"
# A second recorded battle: side 0 played Goblin Drills and Miners.
CAPTURE_DRILL = REPORTS / "frames-auto-20260920-083112-A.jsonl"
ALL_TYPES = [0, 3, 7, 9, 10, 11, 13, 14]
NOT_A_PASS = "SKIPPED, NOT PASSED"


def has_capture(path: Path) -> bool:
    return sources.resolve_capture(path).exists()


def recorded(*paths: Path):
    """Skip, loudly, when the recordings a test pins its numbers on are not present."""
    missing = [p.name for p in paths if not has_capture(p)]
    return pytest.mark.skipif(
        bool(missing),
        reason=(
            f"{NOT_A_PASS}: this test pins numbers only a real battle has and its recording "
            f"({', '.join(missing)}) is not in {REPORTS}; point ROYALELIVE_REPORTS at the "
            "folder holding it to run this test"
        ),
    )


def sound(frame: Frame) -> Frame:
    assert model.problems(frame) == []
    return frame


def scripted(tick: int) -> Frame:
    """The script's own frame behind capture tick ``tick``: what the recording should show."""
    return synthetic.frame_at(tick * synthetic.CAPTURE_STRIDE)


# --- CaptureSource on the synthetic recordings --------------------------------------


def test_synthetic_fixtures_match_the_generator() -> None:
    """The committed recordings are exactly what ``python tests/synthetic.py --write`` writes
    (``--check`` at the command line); a change to the script must regenerate them."""
    assert synthetic.check_fixtures() == []
    assert synthetic.main(["--check"]) == 0
    assert synthetic.main(["--check", "--folder", str(SYNTH_A.parent / "nowhere")]) == 1


@pytest.fixture(scope="module")
def capture_a() -> sources.CaptureSource:
    return sources.CaptureSource(SYNTH_A)


def test_capture_opens_on_the_first_active_frame(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    assert isinstance(src, Source)
    assert (src.live, src.units_per_tile, src.local_side) == (False, 1000, 0)
    assert src.name == "synthetic-A"
    assert src.length == SYNTH_TICKS - 1 + synthetic.CAPTURE_FROZEN and src.index == 0
    assert src.inactive == synthetic.CAPTURE_INACTIVE and src.other == 2  # start and stop
    f = sound(src.frame())
    assert f.tick == 0 and f.units_per_tile == model.LIVE_UNITS_PER_TILE
    assert len(f.units) == 6 and all(u.kind in model.TOWER_KINDS for u in f.units)
    assert f.meta["source"] == "capture" and f.meta["local_side"] == 0
    assert f.meta["seq"] == synthetic.CAPTURE_INACTIVE + 2 and f.meta["coherent"] is True
    assert "frame 1/" in src.status() and "3 inactive, 2 other" in src.status()


def test_capture_frames_round_trip_the_script(capture_a: sources.CaptureSource) -> None:
    """Every tenth frame against the script it was written from: positions, hp, names,
    kinds, targets, paths (goal-first in the file, start-first in the model), the local
    player's hand and cycle, both players' elixir and tower hp, crowns."""
    src = capture_a
    for tick in range(0, SYNTH_TICKS, 10):
        src.seek(src.index_at_tick(tick))
        f, want = sound(src.frame()), scripted(tick)
        assert f.tick == tick and not f.overtime and f.game_over == (tick == SYNTH_TICKS - 1)
        by_key = {(u.team, u.name, u.x): u for u in f.units}
        assert len(by_key) == len(f.units) == len(want.units)
        for w in want.units:
            u = by_key[(w.team, sources.TOWER_NAMES.get(w.kind, w.name), w.x)]
            assert (u.x, u.y, u.hp, u.max_hp, u.kind) == (w.x, w.y, w.hp, w.max_hp, w.kind)
            assert u.direction == w.direction and (u.deploy_ticks > 0) == (w.deploy_ticks > 0)
            assert (u.radius, u.flying, u.stun_ticks) == (0, False, 0)  # not in a recording
            assert "path_nodes" not in u.extra and u.extra["level"] == 11
            assert len(u.path) == len(w.path)
            for (x, y), (wx, wy) in zip(u.path, w.path, strict=True):
                assert x % 500 == 250 and y % 500 == 250  # the cell centre of each node
                assert abs(x - wx) <= 250 and abs(y - wy) <= 250
            if u.target is not None:
                t = f.unit(u.target)
                assert t is not None and (t.x, t.y) == (
                    want.unit(w.target).x,
                    want.unit(w.target).y,
                )
        p0, p1 = f.players
        w0, w1 = want.players
        assert (p0.elixir_milli, p1.elixir_milli) == (w0.elixir_milli, w1.elixir_milli)
        assert p0.elixir_known and p1.elixir_known
        assert (p0.hand, p0.next_card, p0.cycle) == (w0.hand, w0.next_card, w0.cycle)
        assert p0.hand_known and p0.deck_known and p0.deck == w0.deck
        # Tower hp in the owner's frame, [king, left, right] (LIVE_TOWER_X); a fallen tower
        # has left the entity list and reads 0.
        for team, p in enumerate(f.players):
            at = {(u.team, u.x): u.hp for u in want.units if u.kind in model.TOWER_KINDS}
            xs = (sources.LIVE_KING_X, *sources.LIVE_TOWER_X[team])
            assert p.tower_hp == [at.get((team, x), 0) for x in xs]
        assert p0.tower_max_hp == p1.tower_max_hp == [4824, 3052, 3052]
        assert f.crowns == want.crowns and (p0.crowns, p1.crowns) == tuple(want.crowns)
        assert p0.king_active is None  # not in a recording
        if not f.game_over:
            assert p1.hand == ["?"] * 4 and not p1.hand_known and not p1.deck_known
            assert p1.next_card is None and p1.deck == [] and p1.cycle == []


def test_capture_frame_mid_battle(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    tick = 100  # 30 s into the script: Knight and Archer at the towers, the Cannon standing
    src.seek(src.index_at_tick(tick))
    f = sound(src.frame())
    assert f.tick == tick and not f.overtime and not f.game_over and f.winner == model.NO_WINNER
    towers = [u for u in f.units if u.kind in model.TOWER_KINDS]
    assert len(towers) == 6 and {u.name for u in towers} == {"KingTower", "PrincessTower"}
    kings = [u for u in towers if u.kind == model.KIND_KING_TOWER]
    assert sorted((u.team, u.x, u.y) for u in kings) == [(0, 9000, 3000), (1, 9000, 29000)]
    troops = {u.name: u for u in f.units if u.kind == model.KIND_TROOP}
    assert set(troops) == {"Knight", "Archer"}
    (cannon,) = [u for u in f.units if u.kind == model.KIND_BUILDING]
    assert (cannon.name, cannon.team, cannon.x, cannon.y) == ("Cannon", 1, 11000, 22000)
    knight = troops["Knight"]
    assert knight.uid.endswith(":26000000:0") and knight.state == 1 and knight.path == []
    assert (
        f.unit(knight.target).kind == model.KIND_PRINCESS_TOWER and f.unit(knight.target).team == 1
    )
    assert knight.extra["behavior_state"] == 1 and knight.extra["card_id"] == 26000000

    p0, p1 = f.players
    assert p0.hand == ["Archer", "Fireball", "Musketeer", "BabyDragon"] and p0.hand_known
    assert p0.next_card == "Zap" and p0.cycle == ["Cannon", "MegaKnight", "Knight"]
    assert p1.hand == ["?"] * 4 and not p1.hand_known and p1.elixir_known
    assert f.events and all(e.startswith("t") for e in f.events)
    assert "t17 Blue plays Knight" in f.events and "t17 spawn Blue Knight (3.5, 8.0)" in f.events
    assert (
        "t34 spawn Red Archer (14.5, 24.0)" in f.events
        and "t67 spawn Red Cannon (11.0, 22.0)" in f.events
    )
    assert not any(" death " in e for e in f.events)
    assert not any(" Red plays " in e for e in f.events)  # the opponent's hand is not known
    assert f.events == sorted(f.events, key=lambda e: int(e[1:].split()[0]))


def test_capture_seek_step_and_the_frozen_end(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(-5)
    assert src.index == 0
    src.step(10)
    assert src.index == 10 and src.frame().tick == src.tick_at(10) == 10
    src.seek(10**9)
    assert src.index == src.length - 1
    f = sound(src.frame())
    # The last frame: the tick is frozen at 394 for the rest of the file (the results
    # screen) and the winner is read off the missing tower.
    assert f.tick == SYNTH_TICKS - 1 and f.game_over and not f.overtime
    assert f.crowns == [1, 0] and f.winner == 0
    assert 0 < len(f.events) <= sources.EVENTS_KEPT
    assert f.events[-1] == "t367 spawn Red Giant (3.5, 27.0)"
    assert "t267 death Red PrincessTower" in f.events and "t167 death Red Cannon" in f.events
    assert [e for e in f.events if " plays " in e] == [
        "t17 Blue plays Knight",
        "t134 Blue plays BabyDragon",
        "t284 Blue plays Musketeer",
        "t347 Blue plays Fireball",
    ]
    p1 = f.players[1]  # the results screen fills in the opponent's cards
    assert (
        p1.hand_known
        and p1.deck_known
        and p1.hand == ["GoblinDrill", "Musketeer", "Archer", "Cannon"]
    )
    src.step(-1)
    assert src.frame().game_over  # still inside the frozen run
    src.seek(src.index_at_tick(SYNTH_TICKS - 2))
    assert not src.frame().game_over and src.frame().crowns == [1, 0]
    assert src.index_at_tick(10**6) == src.length - 1 and src.index_at_tick(-1) == 0


def test_capture_paths_targets_and_projectiles(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(src.index_at_tick(40))
    f = sound(src.frame())
    walkers = [u for u in f.units if u.path]
    assert [(u.name, u.team) for u in walkers] == [("Knight", 0), ("Archer", 1)]
    knight, archer = walkers
    # path_nodes are goal-first in the file; the model wants start-first: the first node
    # is next to the unit, the last one is the goal near the enemy tower.
    assert abs(knight.path[0][1] - knight.y) < 6000 and knight.path[-1][1] > knight.y
    assert abs(archer.path[0][1] - archer.y) < 6000 and archer.path[-1][1] < archer.y
    assert all(x % 500 == 250 and y % 500 == 250 for u in walkers for x, y in u.path)
    assert knight.direction == (0, 256) and archer.direction == (0, -256)
    assert knight.target is None and knight.state == 2  # walking: no target yet
    # At the towers: a target, no path, and the tower shoots back.
    src.seek(src.index_at_tick(101))
    f = sound(src.frame())
    knight = next(u for u in f.units if u.name == "Knight")
    assert knight.path == [] and f.unit(knight.target).kind == model.KIND_PRINCESS_TOWER
    shots = [s for s in f.spells if s.name == "Tower shot"]
    assert len(shots) == 2 and {s.team for s in shots} == {0, 1}
    red = next(s for s in shots if s.team == 1)
    assert red.motion == 0 and (red.aim_x, red.aim_y) == (knight.x, knight.y)
    assert (red.x, red.y) != (red.aim_x, red.aim_y) and red.extra["card_id"] == -1
    # The Fireball: in flight (aimed) at 50 s, then the area where it landed.
    src.seek(src.index_at_tick(168))
    f = sound(src.frame())
    (fireball,) = [s for s in f.spells if s.name == "Fireball"]
    assert (fireball.team, fireball.motion) == (1, 0)
    assert (fireball.aim_x, fireball.aim_y) == (3500, 17500) and fireball.y > fireball.aim_y
    assert fireball.extra["card_id"] == 28000000
    src.seek(src.index_at_tick(172))
    (landed,) = [s for s in sound(src.frame()).spells if s.name == "Fireball"]
    assert landed.motion == 3 and (landed.x, landed.y) == (3500, 17500)
    src.seek(src.index_at_tick(234))
    (zap,) = [s for s in sound(src.frame()).spells if s.name == "Zap"]
    assert (zap.team, zap.motion, zap.x, zap.y) == (1, 3, 3500, 21000)


def test_capture_deploying_state_and_the_play_event(capture_a: sources.CaptureSource) -> None:
    src = capture_a
    src.seek(src.index_at_tick(17))
    f = sound(src.frame())
    assert "t17 Blue plays Knight" in f.events and "t17 spawn Blue Knight (3.5, 8.0)" in f.events
    deploying = [u for u in f.units if u.deploy_ticks > 0]
    assert [u.name for u in deploying] == ["Knight"]
    assert all(u.state == sources.LIVE_DEPLOY_STATE for u in deploying)
    src.seek(src.index_at_tick(16))
    assert not any(" plays " in e for e in src.frame().events)
    src.seek(src.index_at_tick(20))
    assert all(u.deploy_ticks == 0 for u in src.frame().units)


def test_the_two_seats_see_the_same_battle() -> None:
    a = sources.CaptureSource(SYNTH_A)
    b = sources.CaptureSource(sources.resolve_capture(SYNTH_B.with_name(SYNTH_B.name[:-3])))
    assert (a.local_side, b.local_side) == (0, 1)
    assert a.length == b.length

    def signature(src: sources.CaptureSource, tick: int) -> Counter | None:
        i = src.index_at_tick(tick)
        if src.tick_at(i) != tick:
            return None
        src.seek(i)
        return Counter((u.team, u.name, u.x, u.y, u.hp) for u in src.frame().units)

    same = differ = 0
    for tick in range(SYNTH_TICKS):
        sa, sb = signature(a, tick), signature(b, tick)
        assert sa is not None and sb is not None
        if sa == sb:
            same += 1
        else:
            differ += 1
    # Entity ids differ per seat; positions and hp are one battle.
    assert (same, differ) == (SYNTH_TICKS, 0)
    a.seek(a.index_at_tick(100))
    b.seek(b.index_at_tick(100))
    assert {u.uid for u in a.frame().units}.isdisjoint({u.uid for u in b.frame().units})
    # Each file knows its own seat's hand and not the other's.
    fa, fb = a.frame(), b.frame()
    assert fa.players[0].hand_known and not fa.players[1].hand_known
    assert fb.players[1].hand_known and not fb.players[0].hand_known
    assert (
        fb.players[1].hand
        == scripted(100).players[1].hand
        == ["Fireball", "Zap", "Knight", "GoblinDrill"]
    )
    a.close()
    b.close()
    assert a.length == 0 and a.frame() is None


def test_capture_entity_ids_come_back_on_later_units() -> None:
    """An entity id is reused within a battle once its first entity is gone, so the model's
    uid carries the card and side too, and the two never collide in one frame."""
    src = sources.CaptureSource(SYNTH_A)
    seen: dict[str, set[str]] = {}
    for i in range(src.length):
        src.seek(i)
        for u in src.frame().units:
            seen.setdefault(u.extra["id"], set()).add(u.uid)
    reused = {k: v for k, v in seen.items() if len(v) > 1}
    assert len(reused) == 2 and all(len(v) == 2 for v in reused.values())
    assert {tuple(sorted(n.split(":")[1] for n in v)) for v in reused.values()} == {
        ("-1", "26000014"),  # the Musketeer came up on the fallen tower's id
        ("26000003", "27000000"),  # the Giant on the Cannon's
    }


# --- The capture converters, without a file ----------------------------------------


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
    # The instrument that records real battles keeps the same speed table and checks it
    # against this one.


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


# --- CaptureSource on recordings of real battles (numbers only those have) ---------


@recorded(CAPTURE_A, CAPTURE_B)
def test_the_two_recorded_seats_differ_only_on_tap_ticks() -> None:
    """The two seats' recordings of one battle: 2407 ticks both hold, 2404 equal, 3 differ,
    each for a single frame on a tick a card was played."""
    a = sources.CaptureSource(CAPTURE_A)
    b = sources.CaptureSource(CAPTURE_B)
    assert (a.local_side, b.local_side) == (0, 1)
    assert a.length > 3000 and b.length > 3000 and a.other >= 1

    def signature(src: sources.CaptureSource, tick: int) -> Counter | None:
        i = src.index_at_tick(tick)
        if src.tick_at(i) != tick:
            return None
        src.seek(i)
        return Counter((u.team, u.name, u.x, u.y, u.hp) for u in src.frame().units)

    same = differ = 0
    for tick in range(0, sources.LIVE_REGULAR_TICKS + 1):
        sa, sb = signature(a, tick), signature(b, tick)
        if sa is None or sb is None:
            continue
        if sa == sb:
            same += 1
        else:
            differ += 1
    assert (same, differ) == (2404, 3)
    a.seek(a.length - 1)
    f = sound(a.frame())
    assert f.tick == sources.LIVE_REGULAR_TICKS and f.game_over and f.crowns == [1, 0]
    assert len(f.events) == sources.EVENTS_KEPT
    a.close()
    b.close()


@recorded(CAPTURE_DRILL)
def test_capture_drill_surfacing_line() -> None:
    """On the recorded battle with Goblin Drills: the drill of tick 2974 (state 6 at
    (9178,3569), 39 path nodes) is announced with its goal and the 73 ticks it took, and the
    building stood at (3000,23000) from tick 3047."""
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
    assert src.learning_peer == ("127.0.0.1", 10000)  # a learner sits one port up
    src.close()


# --- LearningPublisher: the learner's status on the same socket -----------------------


def status_of(
    src: sources.StreamSource, learner: sources.LearningPublisher | None = None
) -> Learning | None:
    """The status the viewer is holding once one arrives, pumping the learner meanwhile."""
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        if learner is not None:
            learner.pump()
        src.frame()
        if src.learning is not None:
            return src.learning
        time.sleep(0.01)
    return None


def a_status() -> Learning:
    return Learning(
        run="ppo-0007", iteration=1420, policy_loss=0.0, kl=0.0094, env_steps_per_s=18400.0
    )


def test_the_constants_a_foreign_sender_needs_are_the_documented_ones() -> None:
    """docs/internals.md prints these six for a learner that does not import this package;
    a change here without a change there silently breaks that sender."""
    assert sources.STREAM_HELLO == b"royaleviser 1"
    assert sources.STREAM_HEARTBEAT_S == 1.0
    assert sources.STREAM_ATTACH_TIMEOUT_S == 3
    assert model.LEARNING_PREFIX == b"\x81\xa8learning"
    assert sources.STREAM_MAX_DATAGRAM == 65507
    assert sources.LEARNING_REPEATS == 3
    assert sources.learning_endpoint("127.0.0.1", 9870) == ("127.0.0.1", 9871)
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    assert learner.address[0] == "127.0.0.1"  # the default host, not every interface
    learner.close()


def test_a_status_too_big_for_one_datagram_is_counted_not_truncated() -> None:
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    src = sources.StreamSource("127.0.0.1", 9999, learner.address)  # a learner, no engine
    time.sleep(0.05)
    learner.publish(a_status())  # sent now if the viewer is already there, on pump if not
    huge = Learning(run="ppo-0007", extra={"log": "x" * (sources.STREAM_MAX_DATAGRAM + 1)})
    end = time.monotonic() + 2.0
    while time.monotonic() < end and src.learning is None:
        learner.pump()
        src.frame()
        time.sleep(0.01)
    assert src.learning == a_status()  # the small one arrived
    assert learner.publish(huge) is False and learner.dropped == 1
    src.frame()
    assert src.learning == a_status()  # still the last one that fitted
    src.close()
    learner.close()


def test_a_learning_status_and_frames_share_one_socket() -> None:
    """Two senders on two ports, one socket at the viewer: the status lands in ``learning``
    and the frames keep being frames."""
    frames = sources.Publisher(port=0)
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    src = sources.StreamSource(*frames.address, learner.address)
    time.sleep(0.05)
    frames._pub._last_poll = 0.0
    assert frames.attached
    eng = MockEngine()
    eng.reset(
        1, DefaultStateMutator(decks=[ALL_TYPES] * 2).build(np.random.default_rng(0), eng.cards())
    )
    frame = sources.frame_from_state(eng.state(), Names.from_cards(eng.cards()), 18000)
    assert frames.publish(frame)
    assert learner.pump() is False  # nothing published yet: nothing to tell a viewer
    assert learner.publish(a_status()) and learner.sent == 1
    assert status_of(src, learner) == a_status()
    assert wait_for(src) is not None
    assert src.index == 1 and src.rejected == 0  # the status is not a frame
    assert "frames 1" in src.status() and "rejected" not in src.status()
    src.close()
    frames.close()
    learner.close()


def test_a_viewer_attaching_mid_run_is_sent_the_last_status() -> None:
    """The numbers are minutes apart, so the last ones are the truth until the next ones. A
    viewer that says hello between two iterations is told them rather than left blank."""
    frames = sources.Publisher(port=0)  # a battle is running; no frame is needed here
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    assert learner.publish(a_status()) is False and learner.sent == 0  # nobody watching
    assert learner.status == a_status()  # kept all the same
    src = sources.StreamSource(*frames.address, learner.address)
    assert status_of(src, learner) == a_status()
    while learner.pump():  # a few copies, because a datagram can be lost on the way
        src.frame()
    assert learner.sent == sources.LEARNING_REPEATS
    assert learner.pump() is False  # then quiet until the next status or the next viewer
    src.close()
    frames.close()
    learner.close()


def test_a_viewer_that_was_away_is_told_again() -> None:
    """A viewer gone longer than the attach timeout has either restarted or lost what it
    held, so the next hello is answered even when it comes from the same address."""
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    src = sources.StreamSource("127.0.0.1", 9999, learner.address)  # a learner, no engine
    learner.publish(a_status())
    assert status_of(src, learner) == a_status()
    while learner.pump():  # everything this viewer is owed
        pass
    told = learner.sent
    learner._last_hello -= 2 * sources.STREAM_ATTACH_TIMEOUT_S  # away, then back on one port
    src.heartbeat()
    end = time.monotonic() + 2.0
    while time.monotonic() < end and learner.sent == told:
        learner.pump()
        time.sleep(0.01)
    assert learner.sent > told  # the same address, told again
    src.close()
    learner.close()


def test_the_learner_answers_a_new_viewer_without_being_called() -> None:
    """A learner deep in an optimisation step calls nothing for minutes; its own thread
    answers the hello, so the panel fills within a heartbeat either way."""
    frames = sources.Publisher(port=0)
    learner = sources.LearningPublisher(port=0)
    assert learner.publish(a_status()) is False
    src = sources.StreamSource(*frames.address, learner.address)
    assert status_of(src) == a_status()  # nobody pumped it: the thread did
    src.close()
    frames.close()
    learner.close()


def test_a_second_status_replaces_the_first_whole() -> None:
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    src = sources.StreamSource("127.0.0.1", 9999, learner.address)  # a learner, no engine
    time.sleep(0.05)
    assert learner.publish(a_status()) and status_of(src, learner) == a_status()
    later = Learning(run="ppo-0007", iteration=1421, elo=1183.0)
    assert learner.publish(later)
    end = time.monotonic() + 2.0
    while time.monotonic() < end and src.learning != later:
        src.frame()
        time.sleep(0.01)
    assert src.learning == later
    assert src.learning.kl is None  # dropped by the learner, so unset again, not the old 0.0094
    src.close()
    learner.close()


def test_a_datagram_that_is_neither_is_counted_not_raised() -> None:
    """Anything at all can reach a UDP port, and the viewer must survive all of it: the
    window is the one thing a bad datagram must not take down."""
    src = sources.StreamSource("127.0.0.1", 9999)
    junk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bad = [
        b"not a frame",
        model.LEARNING_PREFIX + b"\xc0",  # a status of nothing
        model.LEARNING_PREFIX + b"\x81\xa3run\xa3\xff\xfe\xfd",  # a name that is not UTF-8
        model.encode_frame(synthetic.frame_at(600)).replace(b"Knight", b"\xff\xfe\xfd\xff\xfe\xfd"),
    ]
    for i, datagram in enumerate(bad, start=1):
        junk.sendto(datagram, src.address)  # one at a time: a drain decodes only the newest
        end = time.monotonic() + 2.0
        while time.monotonic() < end and src.rejected < i:
            src.frame()
            time.sleep(0.01)
        assert src.rejected == i, datagram[:16]
    assert src.frame() is None and src.learning is None
    assert f"rejected {len(bad)}" in src.status()
    junk.close()
    src.close()


def test_a_number_the_learner_computed_in_numpy_still_goes_out() -> None:
    """A PPO loop hands over whatever its framework returns. A numpy float is a float here;
    a value that is no number at all costs that status, not the learner's thread."""
    learner = sources.LearningPublisher(port=0)
    src = sources.StreamSource("127.0.0.1", 9999, learner.address)  # a learner, no engine
    status = Learning(run="ppo-0007", explained_var=np.float32(0.62), pool_size=np.int64(6))
    learner.publish(status)
    got = status_of(src)
    assert got is not None and (got.explained_var, got.pool_size) == (
        pytest.approx(0.62, abs=1e-6),
        6,
    )
    assert learner.dropped == 0
    learner.publish(Learning(run="ppo-0007", extra={"gate": object()}))
    end = time.monotonic() + 2.0
    while time.monotonic() < end and learner.dropped == 0:
        learner.pump()
        time.sleep(0.01)
    assert learner.dropped == 1
    assert learner._thread is not None and learner._thread.is_alive()  # not taken down by it
    assert learner.publish(status)  # and the next good one goes out as usual
    src.close()
    learner.close()


def test_a_stream_on_the_last_port_asks_no_learner() -> None:
    assert sources.learning_endpoint("127.0.0.1", sources.MAX_PORT) is None
    src = sources.open_source(f"127.0.0.1:{sources.MAX_PORT}")
    assert src.learning_peer is None and src.frame() is None  # says hello, does not raise
    src.close()
