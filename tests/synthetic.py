"""A scripted battle of ``model.Frame``s and a list-backed Source, for the renderer's tests.

    from synthetic import ListSource, battle, dense_frame
    frames = battle()                 # 2 minutes at 20 Hz: 2400 frames, live units (1000/tile)
    src = ListSource(frames, "synthetic")
    python tests/run_synthetic.py --seconds 8 --shot shot.png    # the real window

The script (times in seconds): six towers; a Blue Knight walks the left lane from 5 s and
hits the Red left princess tower from ~30 s until it falls at 80 s (Blue crown); a Red
Archer walks the right lane from 10 s and chips the Blue right tower; a Red Cannon stands
from 20 s to 50 s; a Blue Baby Dragon flies the right lane from 40 s; a Red Fireball flies
at 50 s and lands as an area; a Zap stuns the Knight at 70 s; elixir regenerates at one
per 2.8 s (2.8 s / 1.4 s in overtime) and every play spends and rotates the hand;
OVERTIME from 100 s; GAME OVER at 118 s, Blue wins. Nothing here is a game rule, only
enough motion that every drawing path runs and a screenshot reads like a battle.

The same script is also written out as a pair of recordings in the capture format that
``sources.CaptureSource`` reads, one per seat, so the capture tests run without a recording
of a real battle (``tests/fixtures/frames-synthetic-{A,B}.jsonl.gz``):

    python tests/synthetic.py --check      # the committed fixtures match this generator
    python tests/synthetic.py --write      # regenerate them after changing the script
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import random
import sys
from pathlib import Path

from royaleviser.model import (
    CARDS_JSON,
    KIND_BUILDING,
    KIND_KING_TOWER,
    KIND_PRINCESS_TOWER,
    KIND_TROOP,
    LIVE_ELIXIR_PER_MILLI,
    LIVE_UNITS_PER_TILE,
    NO_WINNER,
    TICK_MS,
    Frame,
    Player,
    Spell,
    Unit,
)

DECKS = [
    ["Knight", "Archer", "Fireball", "Musketeer", "BabyDragon", "Zap", "Cannon", "MegaKnight"],
    ["Archer", "Cannon", "Fireball", "Zap", "Knight", "GoblinDrill", "Musketeer", "Giant"],
]
# (start second, team, card) -- the plays that rotate the hand and spend elixir
PLAYS = [
    (5, 0, "Knight"),
    (10, 1, "Archer"),
    (20, 1, "Cannon"),
    (40, 0, "BabyDragon"),
    (50, 1, "Fireball"),
    (70, 1, "Zap"),
    (85, 0, "Musketeer"),
    (95, 1, "Knight"),
    (104, 0, "Fireball"),
    (110, 1, "Giant"),
]
COSTS = {
    "Knight": 3,
    "Archer": 3,
    "Fireball": 4,
    "Musketeer": 4,
    "BabyDragon": 4,
    "Zap": 2,
    "Cannon": 3,
    "MegaKnight": 7,
    "GoblinDrill": 4,
    "Giant": 5,
}
OVERTIME_S = 100
END_S = 118


def towers(upt: int, t: float) -> list[Unit]:
    """Six towers; Red's left princess loses hp from 30 s and dies at 80 s."""
    out: list[Unit] = []
    spec = [
        (0, KIND_KING_TOWER, 9.0, 3.0, 4824),
        (0, KIND_PRINCESS_TOWER, 3.5, 6.5, 3052),
        (0, KIND_PRINCESS_TOWER, 14.5, 6.5, 3052),
        (1, KIND_KING_TOWER, 9.0, 29.0, 4824),
        (1, KIND_PRINCESS_TOWER, 3.5, 25.5, 3052),
        (1, KIND_PRINCESS_TOWER, 14.5, 25.5, 3052),
    ]
    for i, (team, kind, x, y, max_hp) in enumerate(spec):
        hp = max_hp
        if team == 1 and kind == KIND_PRINCESS_TOWER and x < 9:
            hp = max_hp - int(max_hp * max(0.0, t - 30) / 50)
            if hp <= 0:
                continue
        if team == 0 and kind == KIND_PRINCESS_TOWER and x > 9:
            hp = max_hp - int(200 * max(0.0, t - 35) / 10)
        out.append(
            Unit(
                uid=f"tower{i}",
                team=team,
                kind=kind,
                name="King" if kind == KIND_KING_TOWER else "Princess",
                x=int(x * upt),
                y=int(y * upt),
                hp=max(hp, 1),
                max_hp=max_hp,
                radius=0,
                flying=False,
                deploy_ticks=0,
                stun_ticks=0,
                target=None,
                path=[],
                direction=None,
                state=None,
            )
        )
    return out


def walker(
    uid: str,
    team: int,
    name: str,
    lane: list[tuple[float, float]],
    start_s: float,
    speed_tps: float,
    t: float,
    upt: int,
    max_hp: int,
    target: str | None,
    flying: bool = False,
    radius_tiles: float = 0.5,
) -> Unit | None:
    """A troop walking ``lane`` (tiles) from ``start_s`` at ``speed_tps`` tiles/s; None before."""
    if t < start_s:
        return None
    dist = (t - start_s) * speed_tps
    x, y = lane[0]
    i = 0
    while i + 1 < len(lane):
        seg = ((lane[i + 1][0] - x) ** 2 + (lane[i + 1][1] - y) ** 2) ** 0.5
        if dist < seg:
            x += (lane[i + 1][0] - x) * dist / seg
            y += (lane[i + 1][1] - y) * dist / seg
            break
        dist -= seg
        x, y = lane[i + 1]
        i += 1
    remaining = lane[i + 1 :]
    dx, dy = (0, 0)
    if remaining:
        vx, vy = remaining[0][0] - x, remaining[0][1] - y
        n = (vx * vx + vy * vy) ** 0.5 or 1.0
        dx, dy = int(vx / n * 256), int(vy / n * 256)
    deploy_ticks = max(0, int((start_s + 1.0 - t) * 1000 / TICK_MS))
    return Unit(
        uid=uid,
        team=team,
        kind=KIND_TROOP,
        name=name,
        x=int(x * upt),
        y=int(y * upt),
        hp=max(1, max_hp - int(max_hp * 0.4 * max(0.0, t - start_s) / 60)),
        max_hp=max_hp,
        radius=int(radius_tiles * upt),
        flying=flying,
        deploy_ticks=deploy_ticks,
        stun_ticks=0,
        target=target if not remaining else None,
        path=[(int(px * upt), int(py * upt)) for px, py in remaining],
        direction=(dx, dy) if remaining else None,
        state=2 if remaining else 4,
        extra={"behavior_state": 2 if remaining else 4, "level": 11, "speed_tps": speed_tps},
    )


def player_at(team: int, t: float) -> Player:
    """Hand, cycle, elixir and crowns from the play schedule (the game's cycle rule)."""
    queue = list(DECKS[team])
    elixir = 5000
    last_t = 0.0
    for start_s, pteam, card in PLAYS:
        if start_s > t:
            break
        elixir = min(10000, elixir + regen_milli(last_t, start_s))
        last_t = start_s
        if pteam == team:
            queue.remove(card)
            queue.append(card)
            elixir = max(0, elixir - COSTS[card] * 1000)
    elixir = min(10000, elixir + regen_milli(last_t, t))
    crowns = 1 if team == 0 and t >= 80 else 0
    tower_hp = [4824, 3052, 3052]
    if team == 1:
        tower_hp[1] = max(0, 3052 - int(3052 * max(0.0, t - 30) / 50))
    if team == 0:
        tower_hp[2] = 3052 - int(200 * max(0.0, t - 35) / 10)
    return Player(
        team=team,
        elixir_milli=elixir,
        elixir_known=True,
        hand=queue[:4],
        hand_known=True,
        next_card=queue[4],
        cycle=queue[5:],
        deck=list(DECKS[team]),
        deck_known=True,
        crowns=crowns,
        tower_hp=tower_hp,
        tower_max_hp=[4824, 3052, 3052],
        king_active=team == 1 and t >= 80,
    )


def regen_milli(t0: float, t1: float) -> int:
    """One elixir per 2.8 s, doubled in overtime (integer thousandths)."""
    ms = 0
    a, b = min(t0, OVERTIME_S), min(t1, OVERTIME_S)
    ms += int((b - a) * 1000 / 2.8)
    a, b = max(t0, OVERTIME_S), max(t1, OVERTIME_S)
    ms += int((b - a) * 1000 / 1.4)
    return ms


def frame_at(tick: int, upt: int = LIVE_UNITS_PER_TILE, events: list[str] | None = None) -> Frame:
    t = tick * TICK_MS / 1000
    units = towers(upt, t)
    left_lane = [(3.5, 8.0), (3.5, 14.0), (3.5, 17.5), (3.5, 23.5)]
    right_lane = [(14.5, 24.0), (14.5, 18.0), (14.5, 14.0), (14.5, 8.5)]
    for u in (
        walker("knight", 0, "Knight", left_lane, 5, 1.0, t, upt, 1452, "tower4"),
        walker(
            "archers", 1, "Archer", right_lane, 10, 1.0, t, upt, 304, "tower2", radius_tiles=0.4
        ),
        walker(
            "dragon",
            0,
            "BabyDragon",
            [(14.5, 6.0), (14.5, 14.0), (14.5, 17.0), (12.0, 23.0)],
            40,
            1.4,
            t,
            upt,
            1152,
            "cannon",
            flying=True,
            radius_tiles=0.6,
        ),
        walker(
            "musk",
            0,
            "Musketeer",
            [(10.0, 4.0), (10.0, 12.0), (14.5, 17.0), (14.5, 23.0)],
            85,
            1.0,
            t,
            upt,
            720,
            "tower5",
            radius_tiles=0.45,
        ),
        walker(
            "giant",
            1,
            "Giant",
            [(3.5, 27.0), (3.5, 20.0), (3.5, 9.0)],
            110,
            0.7,
            t,
            upt,
            4000,
            "tower1",
            radius_tiles=0.75,
        ),
    ):
        if u is not None:
            if u.uid == "knight" and 70 <= t < 70.6:
                u.stun_ticks = int((70.6 - t) * 1000 / TICK_MS)
            units.append(u)
    if 20 <= t < 50:
        units.append(
            Unit(
                uid="cannon",
                team=1,
                kind=KIND_BUILDING,
                name="Cannon",
                x=int(11.0 * upt),
                y=int(22.0 * upt),
                hp=max(1, 800 - int(800 * (t - 20) / 30)),
                max_hp=800,
                radius=int(0.7 * upt),
                flying=False,
                deploy_ticks=max(0, int((21 - t) * 1000 / TICK_MS)),
                stun_ticks=0,
                target="dragon" if t >= 42 else None,
                path=[],
                direction=None,
                state=1,
                extra={"lifetime_s": round(50 - t, 1)},
            )
        )
    spells: list[Spell] = []
    if 50 <= t < 51.5:
        f = (t - 50) / 1.5
        spells.append(
            Spell(
                team=1,
                name="Fireball",
                motion=0,
                x=int((9.0 + (3.5 - 9.0) * f) * upt),
                y=int((29.0 + (17.5 - 29.0) * f) * upt),
                aim_x=int(3.5 * upt),
                aim_y=int(17.5 * upt),
            )
        )
    elif 51.5 <= t < 52.2:
        spells.append(
            Spell(
                team=1,
                name="Fireball",
                motion=3,
                x=int(3.5 * upt),
                y=int(17.5 * upt),
                aim_x=int(3.5 * upt),
                aim_y=int(17.5 * upt),
                extra={"radius": int(2.5 * upt)},
            )
        )
    if 70 <= t < 70.4:
        spells.append(
            Spell(
                team=1,
                name="Zap",
                motion=3,
                x=int(3.5 * upt),
                y=int(21.0 * upt),
                aim_x=int(3.5 * upt),
                aim_y=int(21.0 * upt),
            )
        )
    game_over = t >= END_S
    return Frame(
        tick=tick,
        tick_ms=TICK_MS,
        units_per_tile=upt,
        players=[player_at(0, t), player_at(1, t)],
        units=units,
        spells=spells,
        overtime=t >= OVERTIME_S,
        game_over=game_over,
        winner=0 if game_over else NO_WINNER,
        crowns=[1 if t >= 80 else 0, 0],
        events=list(events or []),
        meta={"source": "synthetic", "seq": tick, "read_us": 1800, "wall": f"{t:.2f}s"},
    )


def battle(seconds: int = 120, upt: int = LIVE_UNITS_PER_TILE) -> list[Frame]:
    """The whole script at 20 Hz with the events accumulated (spawns, deaths, plays)."""
    frames: list[Frame] = []
    events: list[str] = []
    seen: dict[str, Unit] = {}
    for tick in range(seconds * 1000 // TICK_MS):
        f = frame_at(tick, upt)
        now = {u.uid: u for u in f.units}
        for uid, u in now.items():
            if uid not in seen:
                where = f"({u.x / upt:.1f}, {u.y / upt:.1f})"
                events.append(f"t{tick:<5d} spawn {['Blue', 'Red'][u.team]} {u.name} {where}")
        for uid, u in seen.items():
            if uid not in now:
                events.append(f"t{tick:<5d} death {['Blue', 'Red'][u.team]} {u.name}")
        for start_s, team, card in PLAYS:
            if start_s * 1000 // TICK_MS == tick:
                events.append(f"t{tick:<5d} {['Blue', 'Red'][team]} plays {card}")
        if tick == OVERTIME_S * 1000 // TICK_MS:
            events.append(f"t{tick:<5d} overtime")
        if tick == END_S * 1000 // TICK_MS:
            events.append(f"t{tick:<5d} game over, Blue wins")
        seen = now
        f.events = list(events)
        frames.append(f)
    return frames


def dense_frame(n_units: int = 100, upt: int = LIVE_UNITS_PER_TILE) -> Frame:
    """Towers plus ``n_units`` troops spread over the board with paths, targets and timers."""
    f = frame_at(60 * 1000 // TICK_MS, upt)
    for i in range(n_units):
        team = i % 2
        x = 1.0 + (i * 7) % 16 + (i % 3) * 0.3
        y = 2.0 + (i * 5) % 28 + (i % 4) * 0.25
        goal_y = 25.5 if team == 0 else 6.5
        f.units.append(
            Unit(
                uid=f"dense{i}",
                team=team,
                kind=KIND_TROOP,
                name=DECKS[team][i % 8],
                x=int(x * upt),
                y=int(y * upt),
                hp=100 + (i * 37) % 900,
                max_hp=1000,
                radius=int((0.35 + (i % 4) * 0.1) * upt),
                flying=i % 7 == 0,
                deploy_ticks=10 if i % 11 == 0 else 0,
                stun_ticks=5 if i % 13 == 0 else 0,
                target="tower4" if team == 0 else "tower1",
                path=[(int(x * upt), int((y + k * (goal_y - y) / 4) * upt)) for k in range(1, 5)],
                direction=(0, 256 if team == 0 else -256),
                state=2,
                extra={"level": 11, "index": i},
            )
        )
    f.events = [
        f"t{1200 + i:<5d} spawn {['Blue', 'Red'][i % 2]} {DECKS[i % 2][i % 8]}" for i in range(20)
    ]
    return f


class ListSource:
    """A replay Source over a list of Frames (the app's contract, without sources.py)."""

    live = False

    def __init__(self, frames: list[Frame], name: str = "synthetic") -> None:
        self.frames = frames
        self.name = name
        self.units_per_tile = frames[0].units_per_tile if frames else LIVE_UNITS_PER_TILE
        self.length: int | None = len(frames)
        self.index = 0
        self.closed = False

    def frame(self) -> Frame | None:
        return self.frames[self.index] if self.frames else None

    def seek(self, index: int) -> None:
        self.index = max(0, min(len(self.frames) - 1, index))

    def step(self, delta: int) -> None:
        self.seek(self.index + delta)

    def index_at_tick(self, tick: int) -> int:
        for i, f in enumerate(self.frames):
            if f.tick >= tick:
                return i
        return max(0, len(self.frames) - 1)

    def status(self) -> str:
        return f"{len(self.frames)} synthetic frames"

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------
# The script as two recordings, in the capture format (tests/fixtures/)
# --------------------------------------------------------------------------

# One capture frame every CAPTURE_STRIDE script ticks: the 118 s script becomes a 394-frame
# battle whose units move at six times their scripted speed. A frame's "tick" is its own
# index, as in a recording (one frame per tick at 20 Hz), so the recording's clock runs
# 0..394 while the script behind it runs 0..118 s.
CAPTURE_STRIDE = 6
CAPTURE_SEED = 20260921
CAPTURE_INACTIVE = 3  # "frame" lines with active false before the battle starts
CAPTURE_FROZEN = 24  # results-screen frames repeating the final tick (>= LIVE_FROZEN_FRAMES)
CAPTURE_SCHEMA = 1
CAPTURE_SHOT_FRAMES = 4  # a tower shot crosses to its target in this many frames
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_NAMES = ("frames-synthetic-A.jsonl.gz", "frames-synthetic-B.jsonl.gz")  # seat 0, seat 1
LAST_TICK = -(-END_S * 1000 // (TICK_MS * CAPTURE_STRIDE))  # 394: the first GAME OVER frame
# The script's unit states -> a recording's behavior_state: deploying is 4 (LIVE_DEPLOY_STATE),
# walking 2, standing at the target or as a building 1.
CAPTURE_STATE = {2: 2, 4: 1, 1: 1}
CARD_IDS: dict[str, int] = {
    name: int(cid) for name, (cid, _cost) in json.loads(CARDS_JSON.read_text("utf-8")).items()
}
PATH_COLS, PATH_CELL = 36, LIVE_UNITS_PER_TILE // 2  # path_nodes: half-tile cells, goal first


class _Ids:
    """Opaque entity ids for one seat: hex strings from a seeded generator, and an id goes
    back into a pool when its entity dies so a later entity can come up on it, as a
    recording's ids do."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.free: list[str] = []
        self.by_uid: dict[str, str] = {}
        self.effects: dict[str, str] = {}  # spells and shots: never retired, never reused

    def _fresh(self) -> str:
        return f"0x{self.rng.getrandbits(40):010x}"

    def of(self, uid: str) -> str:
        if uid not in self.by_uid:
            self.by_uid[uid] = self.free.pop() if self.free else self._fresh()
        return self.by_uid[uid]

    def of_effect(self, key: str) -> str:
        if key not in self.effects:
            self.effects[key] = self._fresh()
        return self.effects[key]

    def retire(self, alive: set[str]) -> None:
        for uid in [u for u in self.by_uid if u not in alive]:
            self.free.append(self.by_uid.pop(uid))


def path_node(x: int, y: int) -> int:
    return (y // PATH_CELL) * PATH_COLS + x // PATH_CELL


def capture_entity(u: Unit, ids: _Ids, alive: set[str]) -> dict:
    """A unit as an entity row. A target that is no longer on the board is dropped: the
    script keeps aiming at a fallen tower, a recording does not."""
    card_id = -1 if u.name in ("King", "Princess") else CARD_IDS[u.name]
    e: dict = {
        "id": ids.of(str(u.uid)),
        "card_id": card_id,
        "side": u.team,
        "level": 11,
        "x": u.x,
        "y": u.y,
        "hp": u.hp,
        "max_hp": u.max_hp,
        "behavior_state": 4 if u.deploy_ticks > 0 else CAPTURE_STATE.get(u.state or 0, 0),
        "movement_direction_x": u.direction[0] if u.direction else 0,
        "movement_direction_y": u.direction[1] if u.direction else 0,
    }
    if u.target is not None and str(u.target) in alive:
        e["target"] = ids.of(str(u.target))
    e["path_nodes"] = [path_node(x, y) for x, y in reversed(u.path)]
    return e


def capture_player(p: Player, known: bool) -> dict:
    deck = [CARD_IDS[n] for n in p.deck]
    row: dict = {"side": p.team, "elixir_raw": p.elixir_milli * LIVE_ELIXIR_PER_MILLI}
    if known:
        row["hand"] = [p.deck.index(n) for n in p.hand]
        row["cycle"] = [p.deck.index(n) for n in [p.next_card, *p.cycle]]
        row["deck"] = deck
    else:
        row["hand"], row["cycle"], row["deck"] = [-1, -1, -1, -1], [], []
    return row


def capture_effects(f: Frame, prev: Frame | None, tick: int, ids: _Ids) -> list[dict]:
    """The frame's spells as effect rows, plus the tower shots the script implies: a tower
    being attacked shoots back, one projectile crossing to its attacker every four frames."""
    out: list[dict] = []
    before = {(s.team, s.name): (s.x, s.y) for s in prev.spells} if prev else {}
    for s in f.spells:
        x2, y2 = before.get((s.team, s.name), (s.x, s.y))
        out.append(
            {
                "id": ids.of_effect(f"spell:{s.team}:{s.name}"),
                "side": s.team,
                "card_id": CARD_IDS[s.name],
                "x": s.x,
                "y": s.y,
                "x2": x2,
                "y2": y2,
                "projectile_x": s.aim_x,
                "projectile_y": s.aim_y,
            }
        )
    by_uid = {str(u.uid): u for u in f.units}
    for u in f.units:
        tower = by_uid.get(str(u.target)) if u.state == 4 else None
        if tower is None or tower.name != "Princess":
            continue
        phase = tick % CAPTURE_SHOT_FRAMES
        x = tower.x + (u.x - tower.x) * phase // CAPTURE_SHOT_FRAMES
        y = tower.y + (u.y - tower.y) * phase // CAPTURE_SHOT_FRAMES
        x2 = tower.x + (u.x - tower.x) * max(phase - 1, 0) // CAPTURE_SHOT_FRAMES
        y2 = tower.y + (u.y - tower.y) * max(phase - 1, 0) // CAPTURE_SHOT_FRAMES
        out.append(
            {
                "id": ids.of_effect(f"shot:{tower.uid}"),
                "side": tower.team,
                "card_id": -1,
                "x": x,
                "y": y,
                "x2": x2,
                "y2": y2,
                "source": ids.of(str(tower.uid)),
                "target": ids.of(str(u.uid)),
                "projectile_x": u.x,
                "projectile_y": u.y,
            }
        )
    return out


def capture_lines(seat: int, seed: int = CAPTURE_SEED) -> list[bytes]:
    """The script as seat ``seat`` would have recorded it: one JSON object per line in the
    capture format (``sources.CaptureSource`` lists the fields). Only the recording seat's
    hand, cycle and deck are present until the results screen, where both fill in; entity
    ids are the seat's own, so the two seats' files describe one battle with different ids."""
    rng = random.Random(seed * 2 + seat)
    ids = _Ids(seed * 4 + seat)
    lines: list[bytes] = []
    seq = 0
    t_us = 1_000_000

    def emit(row: dict) -> None:
        nonlocal seq
        seq += 1
        lines.append(json.dumps({**row, "seq": seq}, separators=(",", ":")).encode() + b"\n")

    emit({"event": "start", "schema_version": CAPTURE_SCHEMA, "interval_ms": TICK_MS})
    for _ in range(CAPTURE_INACTIVE):
        t_us += TICK_MS * 1000
        emit({"event": "frame", "active": False, "t_us": t_us, "read_us": rng.randint(20, 60)})
    prev: Frame | None = None
    ticks = list(range(LAST_TICK)) + [LAST_TICK] * CAPTURE_FROZEN
    for tick in ticks:
        f = frame_at(tick * CAPTURE_STRIDE)
        results = tick == LAST_TICK
        t_us += TICK_MS * 1000
        alive = {str(u.uid) for u in f.units}
        ids.retire(alive)
        entities = [capture_entity(u, ids, alive) for u in f.units]
        effects = capture_effects(f, prev, tick, ids)
        row = {
            "event": "frame",
            "t_us": t_us,
            "read_us": rng.randint(80, 400),
            "active": True,
            "coherent": True,
            "failure": "none",
            "tick": tick,
            "replay_tick": tick - tick % 10,
            "players": [capture_player(p, results or p.team == seat) for p in f.players],
            "entities": entities,
            "effects": effects,
        }
        emit(row)
        prev = f
    emit({"event": "stop", "frames": len(ticks)})
    return lines


def capture_bytes(seat: int, seed: int = CAPTURE_SEED) -> bytes:
    """The gzipped fixture, byte-reproducible (no name, no mtime in the gzip header)."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as gz:
        gz.write(b"".join(capture_lines(seat, seed)))
    return buf.getvalue()


def fixture_paths(folder: Path = FIXTURES) -> list[Path]:
    return [folder / name for name in FIXTURE_NAMES]


def check_fixtures(folder: Path = FIXTURES) -> list[str]:
    """What differs between the committed fixtures and this generator; [] when nothing."""
    problems: list[str] = []
    for seat, path in enumerate(fixture_paths(folder)):
        if not path.exists():
            problems.append(f"{path.name}: missing (python tests/synthetic.py --write)")
            continue
        with gzip.open(path, "rb") as fh:
            have = fh.read().splitlines(keepends=True)
        want = capture_lines(seat)
        if have == want:
            continue
        if len(have) != len(want):
            problems.append(f"{path.name}: {len(have)} lines, the generator writes {len(want)}")
        for i, (a, b) in enumerate(zip(have, want, strict=False)):
            if a != b:
                problems.append(f"{path.name}: line {i + 1} differs from the generator")
                break
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fixtures match the generator")
    mode.add_argument("--write", action="store_true", help="(re)write the fixtures")
    ap.add_argument("--folder", type=Path, default=FIXTURES)
    args = ap.parse_args(argv)
    if args.write:
        args.folder.mkdir(parents=True, exist_ok=True)
        for seat, path in enumerate(fixture_paths(args.folder)):
            data = capture_bytes(seat)
            path.write_bytes(data)
            print(f"{path}: {len(capture_lines(seat))} lines, {len(data)} bytes gzipped")
        return 0
    problems = check_fixtures(args.folder)
    for line in problems:
        print(line)
    if not problems:
        print(f"{len(FIXTURE_NAMES)} fixtures in {args.folder} match the generator")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
