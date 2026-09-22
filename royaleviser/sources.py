"""The three sources, each turning its own format into ``model.Frame``.

    CaptureSource   a RoyaleLive capture, frames-*.jsonl(.gz): one JSON object per line, a
                    battle frame of the live client at 20 Hz (the format is described on
                    the class; RoyaleLive's tools write it)
    TraceSource     a royalegym.replay trace (.msgpack / .json) recorded from an engine
    StreamSource    a running engine publishing frames over UDP (``Publisher`` is the other
                    end; royalegym.viser.ViserPublisher is the same protocol inside the env)

The RoyaleLive instrument drives this viewer the same way any other caller does: it imports
this package and calls ``app.run``.

Every source fills ``Frame.units_per_tile`` with its own raw unit (1000 for the live client,
the trace header's ``subtile`` for the engine), keeps positions in the NATIVE / ENGINE frame
(team 0's back edge at y=0) and says what it does not know through the ``*_known`` flags.

    src = open_source("frames-demo-20260920-120752.jsonl.gz")
    src.seek(src.index_at_tick(1000)); frame = src.frame()

Nothing below imports pygame. royalegym is imported only where the engine's formats are
read (TraceSource, the arena helpers, Publisher / frame_from_state), so CaptureSource and
StreamSource work from the model alone (pyproject.toml).
"""

from __future__ import annotations

import contextlib
import gzip
import json
import math
import re
import socket
import time
from collections import OrderedDict, deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import msgspec

from .model import (
    DECK_SIZE,
    HAND_SIZE,
    KIND_BUILDING,
    KIND_KING_TOWER,
    KIND_PRINCESS_TOWER,
    KIND_TROOP,
    LIVE_ELIXIR_PER_MILLI,
    LIVE_UNITS_PER_TILE,
    NO_WINNER,
    TICK_MS,
    TOWER_KINDS,
    TOWER_SLOTS,
    UNKNOWN_CARD,
    UNKNOWN_HP,
    Frame,
    Names,
    Player,
    Spell,
    Unit,
    decode_frame,
)

STREAM_HOST = "127.0.0.1"
STREAM_PORT = 9870
STREAM_HELLO = b"royaleviser 1"  # the viewer's heartbeat datagram, once a second while open
STREAM_HEARTBEAT_S = 1.0
STREAM_ATTACH_TIMEOUT_S = 3  # no heartbeat for this long: the publisher stops sending
STREAM_MAX_DATAGRAM = 65507  # UDP over IPv4; a bigger frame is resent without unit paths

# The live client's numbers (measured 2026-09-18/20 on client 16.402, RoyaleLive captures).
LIVE_ARENA = (18 * LIVE_UNITS_PER_TILE, 32 * LIVE_UNITS_PER_TILE)  # native x, y extent
LIVE_KING_X = 9000  # both kings stand on x 9000 (calibration: (9000,3000) and (9000,29000))
LIVE_BUILDING_MIN = 27_000_000  # card ids: 26xxxxxx troop, 27xxxxxx building, 28xxxxxx spell
LIVE_SPELL_MIN = 28_000_000
LIVE_PATH_COLS = 36  # path_nodes index a 36x64 half-tile grid, goal first
LIVE_PATH_CELL = LIVE_UNITS_PER_TILE // 2
LIVE_REGULAR_TICKS = 3690  # a friendly's regular time; the tick runs on in overtime
LIVE_DEPLOY_STATE = 4  # behavior_state while a unit deploys (kind 14, ~18 ticks after the tap)
LIVE_UNDERGROUND_STATE = (
    6  # behavior_state while a unit tunnels from its king tower to the placement
)
# Tunnel speed in native units per tick, the same table as RoyaleLive's opponent tracker
# (RoyaleLive/tests/test_viser_live.py keeps them equal), measured on
# frames-auto-20260920-083112 (drill 300/tick, Miner 650/tick); the Mighty Miner's lane switch
# is given the Miner's speed and has not been seen in a capture yet.
LIVE_TUNNEL_SPEED = {27000013: 300, 26000032: 650, 26000065: 650}
LIVE_FROZEN_FRAMES = 20  # one second of the same tick at the end of a capture: results screen
LIVE_NEW_BATTLE_DROP = 100  # the tick falling back by more than this: a new battle
# Own-frame tower slots by native x, the ENGINE convention (protocol.to_own /
# Arena.princess_centers): team 0's left is low x, team 1's left is high x, because team 1's
# own frame is the board turned 180 degrees. NOTE the side-0 CLIENT shows native x mirrored
# (measured 2026-09-18, RoyaleLive), so its on-screen left tower is native x 14500; the viewer
# names towers in the engine's convention, which is what seat 0 / seat 1 draw.
LIVE_TOWER_X = {0: (3500, 14500), 1: (14500, 3500)}  # team -> (left, right) native x

TEAM_NAMES = ("Blue", "Red")
TOWER_NAMES = {KIND_KING_TOWER: "KingTower", KIND_PRINCESS_TOWER: "PrincessTower"}
SLOT_KINDS = (KIND_KING_TOWER, KIND_PRINCESS_TOWER, KIND_PRINCESS_TOWER)  # by TowerSlot
WINNER_DRAW = 2  # protocol.Winner.DRAW
EVENTS_KEPT = 200  # event lines carried in a Frame (newest last); royalegym.viser keeps the same
PARSE_CACHE = 200  # parsed capture frames kept (a frame is ~5 KB of JSON, ~80 KB of objects)
LOCAL_SIDE_SCAN = 200  # frames a capture is scanned for its local side (10 s at 20 Hz)

_FRAME_PREFIX = b'{"event":"frame"'
_ACTIVE = b'"active":true'
_TICK = re.compile(rb'"tick":(\d+)')  # "replay_tick" has an underscore before "tick"


def open_source(spec: str, names: Names | None = None) -> Any:
    """A source for one command-line argument, by shape.

    ``*.jsonl`` / ``*.jsonl.gz`` -> CaptureSource; ``*.msgpack`` / ``*.json`` -> TraceSource;
    ``host:port`` -> StreamSource. Anything else raises ValueError naming the spec.
    """
    lower = spec.lower()
    if lower.endswith((".jsonl", ".jsonl.gz")):
        return CaptureSource(spec, names)
    if lower.endswith((".msgpack", ".json")):
        return TraceSource(spec)
    host, sep, port = spec.rpartition(":")
    if sep and port.isdigit():
        return StreamSource(host or STREAM_HOST, int(port))
    raise ValueError(f"{spec!r}: not a capture (.jsonl[.gz]), trace (.msgpack/.json) or host:port")


# --------------------------------------------------------------------------
# Event lines, the same text for every source
# --------------------------------------------------------------------------


def tiles_text(v: int, units_per_tile: int) -> str:
    """Integer units -> tiles rounded to a tenth, without a float (royalegym.viser._tiles)."""
    tenths = (v * 10 + units_per_tile // 2) // units_per_tile
    return f"{tenths // 10}.{tenths % 10}"


def play_line(tick: int, team: int, name: str, at: tuple[int, int] | None, upt: int) -> str:
    """One play line, "t120 Blue plays Knight (3.5, 14.5)"; no position when the source has none."""
    where = f" ({tiles_text(at[0], upt)}, {tiles_text(at[1], upt)})" if at is not None else ""
    return f"t{tick} {TEAM_NAMES[team]} plays {name}{where}"


class _EventLog:
    """Spawn / death / play lines from consecutive frames, shared by every source.

    ``note(tick, units, hands, upt)``: ``units`` is {uid: (team, name, x, y, is_tower)} of the
    frame, ``hands`` the per-team hand as (key, name) pairs or None when not known. Against
    the previous note, a uid that appeared is a spawn (towers never spawn), one that
    disappeared a death, a key that left a known hand a play. The first note only seeds
    the comparison, so a viewer that joins mid-battle does not see every unit "spawn".
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._seen: dict[Any, tuple[int, str, int, int, bool]] | None = None
        self._hands: list[list[tuple[Any, str]] | None] = [None, None]

    def reset(self) -> None:
        self._seen = None
        self._hands = [None, None]

    def note(
        self,
        tick: int,
        units: dict[Any, tuple[int, str, int, int, bool]],
        hands: Sequence[list[tuple[Any, str]] | None],
        upt: int,
    ) -> None:
        if self._seen is not None:
            for team, (before, now) in enumerate(zip(self._hands, hands, strict=True)):
                if before is None or now is None:
                    continue
                keys_now = {k for k, _ in now}
                for key, name in before:
                    if key not in keys_now:
                        self.lines.append(play_line(tick, team, name, None, upt))
            for uid, (team, name, x, y, tower) in units.items():
                if uid not in self._seen and not tower:
                    at = f"({tiles_text(x, upt)}, {tiles_text(y, upt)})"
                    self.lines.append(f"t{tick} spawn {TEAM_NAMES[team]} {name} {at}")
            for uid, (team, name, _x, _y, _tower) in self._seen.items():
                if uid not in units:
                    self.lines.append(f"t{tick} death {TEAM_NAMES[team]} {name}")
        self._seen = units
        self._hands = list(hands)


# --------------------------------------------------------------------------
# The capture format (CaptureSource; these converters are exported and reused)
# --------------------------------------------------------------------------


def capture_uid(e: dict[str, Any]) -> str:
    """An entity's identity as a string: the entity ``id`` (an opaque string that may be
    reused for a later entity within a battle) with the card and the side."""
    return f"{e['id']}:{e['card_id']}:{e['side']}"


def capture_kind(e: dict[str, Any]) -> int:
    cid = e.get("card_id", -1)
    if cid < 0:
        return KIND_KING_TOWER if e.get("x") == LIVE_KING_X else KIND_PRINCESS_TOWER
    if LIVE_BUILDING_MIN <= cid < LIVE_SPELL_MIN:
        return KIND_BUILDING
    return KIND_TROOP


def capture_name(e: dict[str, Any], names: Names) -> str:
    kind = capture_kind(e)
    return TOWER_NAMES.get(kind) or names.name_of(e["card_id"])


def capture_node_xy(n: int) -> tuple[int, int]:
    """A path node -> its half-tile cell centre in native units (col = n % 36, row = n // 36)."""
    col, row = n % LIVE_PATH_COLS, n // LIVE_PATH_COLS
    return col * LIVE_PATH_CELL + LIVE_PATH_CELL // 2, row * LIVE_PATH_CELL + LIVE_PATH_CELL // 2


def capture_surfacing(e: dict[str, Any]) -> tuple[int, int, int] | None:
    """(x, y, eta ticks) where a tunnelling entity comes out: the centre of its goal cell (the
    first path node) and the remaining path length over its tunnel speed, floored. Measured on
    frames-auto-20260920-083112: the drill of tick 2974 at (9178,3569) predicts (3250,23250)
    in 73 ticks and the building appeared at (3000,23000) at tick 3047. None for anything else."""
    cid = e.get("card_id", -1)
    nodes = e.get("path_nodes") or []
    tunnelling = e.get("behavior_state") == LIVE_UNDERGROUND_STATE and cid in LIVE_TUNNEL_SPEED
    if not tunnelling or not nodes:
        return None
    x, y = e["x"], e["y"]
    length = 0
    for n in reversed(nodes):
        nx, ny = capture_node_xy(n)
        length += math.isqrt((nx - x) ** 2 + (ny - y) ** 2)
        x, y = nx, ny
    gx, gy = capture_node_xy(nodes[0])
    return gx, gy, length // LIVE_TUNNEL_SPEED[cid]


def capture_local_side(f: dict[str, Any]) -> int | None:
    """The one side whose hand this frame knows, None when neither or both are known. A
    source that knows only one player's hand leaves the other's slots at -1."""
    sides = [
        s for s, p in enumerate(f.get("players", [])) if any(h >= 0 for h in p.get("hand", []))
    ]
    return sides[0] if len(sides) == 1 else None


def capture_unit(e: dict[str, Any], names: Names, uid_of: dict[str, str]) -> Unit:
    kind = capture_kind(e)
    dx, dy = e.get("movement_direction_x", 0), e.get("movement_direction_y", 0)
    nodes = e.get("path_nodes") or []
    return Unit(
        uid=capture_uid(e),
        team=e["side"],
        kind=kind,
        name=TOWER_NAMES.get(kind) or names.name_of(e["card_id"]),
        x=e["x"],
        y=e["y"],
        hp=e.get("hp", UNKNOWN_HP),
        max_hp=e.get("max_hp", UNKNOWN_HP),
        radius=0,  # not in a capture: the renderer's default
        flying=False,  # not in a capture either
        # A capture carries the deploying STATE, not the countdown: 1 = still deploying.
        deploy_ticks=1 if e.get("behavior_state") == LIVE_DEPLOY_STATE else 0,
        stun_ticks=0,
        target=uid_of.get(e.get("target") or ""),
        path=[capture_node_xy(n) for n in reversed(nodes)],  # goal-first -> start-first
        direction=(dx, dy) if dx or dy else None,
        state=e.get("behavior_state"),
        extra={k: v for k, v in e.items() if k != "path_nodes"},
    )


def capture_spell(ef: dict[str, Any], names: Names) -> Spell:
    """An effect row -> Spell. Measured on the 12:07 and 13:54 captures: ``x``/``y`` is the
    projectile's centre this tick, ``x2``/``y2`` the previous tick's, ``projectile_x``/``_y``
    the aim (the tracked target's position for a shot, the placement for a Fireball);
    ``card_id`` is the SHOOTER's card (-1 a tower) or the spell card (28xxxxxx)."""
    cid = ef.get("card_id", -1)
    if cid < 0:
        name = "Tower shot"
    elif cid >= LIVE_SPELL_MIN:
        name = names.name_of(cid)
    else:
        name = f"{names.name_of(cid)} shot"
    x, y = ef.get("x", 0), ef.get("y", 0)
    ax, ay = ef.get("projectile_x", -1), ef.get("projectile_y", -1)
    aimed = 0 <= ax <= LIVE_ARENA[0] and 0 <= ay <= LIVE_ARENA[1]
    if not aimed:
        ax, ay = x, y
    return Spell(
        team=ef.get("side", 0),
        name=name,
        motion=0 if aimed and (ax, ay) != (x, y) else 3,  # SpellMotion FLIGHT / AREA
        x=x,
        y=y,
        aim_x=ax,
        aim_y=ay,
        extra=dict(ef),
    )


def capture_player(team: int, p: dict[str, Any] | None, names: Names, units: list[Unit]) -> Player:
    p = p or {}
    deck = list(p.get("deck") or [])
    deck_known = len(deck) == DECK_SIZE
    hand_idx = list(p.get("hand") or [])[:HAND_SIZE]
    hand_idx += [-1] * (HAND_SIZE - len(hand_idx))
    hand_known = any(i >= 0 for i in hand_idx)

    def card(i: int) -> str:
        return names.name_of(deck[i]) if deck_known and 0 <= i < DECK_SIZE else UNKNOWN_CARD

    cycle = [card(i) for i in p.get("cycle") or []]
    towers = {(u.team, u.kind, u.x): u for u in units if u.kind in TOWER_KINDS}
    own = [towers.get((team, KIND_KING_TOWER, LIVE_KING_X))] + [
        towers.get((team, KIND_PRINCESS_TOWER, x)) for x in LIVE_TOWER_X[team]
    ]
    if towers:
        # A destroyed tower vanishes from the entity list (13:54 capture, tick 3636): hp 0.
        # Its max hp is taken from another tower of the same kind, a friendly's are equal.
        max_by_kind = {u.kind: u.max_hp for u in towers.values()}
        tower_hp = [u.hp if u is not None else 0 for u in own]
        tower_max = [
            u.max_hp if u is not None else max_by_kind.get(kind, 0)
            for u, kind in zip(own, SLOT_KINDS, strict=True)
        ]
        enemy = 1 - team
        if towers.get((enemy, KIND_KING_TOWER, LIVE_KING_X)) is None:
            crowns = 3
        else:
            crowns = sum(
                towers.get((enemy, KIND_PRINCESS_TOWER, x)) is None for x in LIVE_TOWER_X[enemy]
            )
    else:
        tower_hp, tower_max, crowns = [UNKNOWN_HP] * TOWER_SLOTS, [UNKNOWN_HP] * TOWER_SLOTS, 0
    return Player(
        team=team,
        elixir_milli=int(p.get("elixir_raw", 0)) // LIVE_ELIXIR_PER_MILLI,
        elixir_known="elixir_raw" in p,
        hand=[card(i) if hand_known else UNKNOWN_CARD for i in hand_idx],
        hand_known=hand_known,
        next_card=cycle[0] if cycle else None,
        cycle=cycle[1:],
        deck=[names.name_of(c) for c in deck] if deck_known else [],
        deck_known=deck_known,
        crowns=crowns,
        tower_hp=tower_hp,
        tower_max_hp=tower_max,
        king_active=None,  # not in a capture
    )


def capture_frame(
    f: dict[str, Any],
    names: Names,
    events: Sequence[str],
    game_over: bool,
    meta: dict[str, Any],
) -> Frame:
    """One capture frame dict (active) -> Frame; events and game_over come from the caller."""
    entities = f.get("entities", [])
    uid_of = {e["id"]: capture_uid(e) for e in entities}
    units = [capture_unit(e, names, uid_of) for e in entities]
    by_side = {p.get("side"): p for p in f.get("players", [])}
    players = [capture_player(team, by_side.get(team), names, units) for team in (0, 1)]
    crowns = [p.crowns for p in players]
    tick = int(f.get("tick", 0))
    winner = NO_WINNER
    if game_over:
        # Crowns decide; the tiebreak by tower hp is not in a capture, so equal crowns say draw.
        winner = 0 if crowns[0] > crowns[1] else 1 if crowns[1] > crowns[0] else WINNER_DRAW
    return Frame(
        tick=tick,
        tick_ms=TICK_MS,
        units_per_tile=LIVE_UNITS_PER_TILE,
        players=players,
        units=units,
        spells=[capture_spell(ef, names) for ef in f.get("effects", [])],
        overtime=tick > LIVE_REGULAR_TICKS,
        game_over=game_over,
        winner=winner,
        crowns=crowns,
        events=list(events[-EVENTS_KEPT:]),
        meta=meta,
    )


class CaptureEvents:
    """The event log of a capture stream: feed every active frame in order, read ``lines``.

    Besides the spawn / death / play lines of ``_EventLog`` it adds one line when a unit is
    first seen tunnelling (``capture_surfacing``):
    "t2974 Blue GoblinDrill -> (3250,23250) ~73 ticks", the goal in native units because
    that is where the building will stand.
    """

    def __init__(self, names: Names) -> None:
        self.names = names
        self.log = _EventLog()
        self.last_tick: int | None = None
        # uids tunnelling in the LAST frame, not every uid ever announced: an entity id comes
        # back for a later unit of the same card and side (16 such reuses in 58 captures of
        # 09-18/19, e.g. a Tombstone's skeletons), and that unit's own trip must be announced.
        self._tunnelling: set[str] = set()

    @property
    def lines(self) -> list[str]:
        return self.log.lines

    def note(self, f: dict[str, Any]) -> None:
        tick = int(f.get("tick", 0))
        if self.last_tick is not None and tick < self.last_tick - LIVE_NEW_BATTLE_DROP:
            self.log.reset()  # a new battle in the same stream: identities restart
            self._tunnelling.clear()
        self.last_tick = tick
        units = {
            capture_uid(e): (
                e["side"],
                capture_name(e, self.names),
                e["x"],
                e["y"],
                e["card_id"] < 0,
            )
            for e in f.get("entities", [])
        }
        hands: list[list[tuple[Any, str]] | None] = [None, None]
        for p in f.get("players", []):
            deck, hand, side = p.get("deck") or [], p.get("hand") or [], p.get("side")
            known = len(deck) == DECK_SIZE and hand and all(0 <= i < DECK_SIZE for i in hand)
            if side in (0, 1) and known:
                hands[side] = [(i, self.names.name_of(deck[i])) for i in hand]
        self.log.note(tick, units, hands, LIVE_UNITS_PER_TILE)
        tunnelling: set[str] = set()
        for e in f.get("entities", []):
            if (goal := capture_surfacing(e)) is None:
                continue
            uid = capture_uid(e)
            tunnelling.add(uid)
            if uid in self._tunnelling:
                continue  # the same trip as last frame
            gx, gy, eta = goal
            team, name = TEAM_NAMES[e["side"]], self.names.name_of(e["card_id"])
            self.log.lines.append(f"t{tick} {team} {name} -> ({gx},{gy}) ~{eta} ticks")
        self._tunnelling = tunnelling


def resolve_capture(path: Path) -> Path:
    """The capture as it exists: the path, else its .gz twin (RoyaleLive gzips its captures
    in place, so a ``.jsonl`` name still resolves), else the path itself."""
    if path.exists():
        return path
    if path.name.endswith(".jsonl") and path.with_name(path.name + ".gz").exists():
        return path.with_name(path.name + ".gz")
    if path.name.endswith(".jsonl.gz") and path.with_name(path.name[:-3]).exists():
        return path.with_name(path.name[:-3])
    return path


def capture_stem(path: Path) -> str:
    name = path.name
    for suffix in (".jsonl.gz", ".jsonl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.removeprefix("frames-")


class CaptureSource:
    """Frames of a RoyaleLive capture, by index, with the events the stream implies.

    THE FORMAT. One JSON object per line. A battle frame is ``{"event": "frame",
    "active": bool, "seq", "tick", "players": [...], "entities": [...], "effects": [...]}``
    plus a few timing and bookkeeping fields (kept in ``meta``); the other events
    (start/stop lines) carry no battle state. A
    player row: ``side`` (0/1), ``elixir_raw`` (ten-thousandths of an elixir), ``deck`` (8
    card ids, or [] when not known), ``hand`` (4 deck indices, -1 when not known), ``cycle``
    (deck indices, the next card first). An entity row: ``id`` (an opaque string; it may be
    reused within a battle), ``card_id`` (-1 for a tower), ``side``, ``x``/``y``
    in native millitiles, ``hp``/``max_hp``, ``behavior_state``, ``target`` (another
    entity's ``id``), ``movement_direction_x``/``_y``, ``path_nodes`` (half-tile cells,
    goal first) and further fields kept in ``Unit.extra``. An effect row is a projectile
    or a spell: ``side``, ``card_id`` (the shooter's, -1 a tower, or the spell's), ``x``/
    ``y`` this tick, ``x2``/``y2`` the previous tick, ``projectile_x``/``_y`` the aim.

    Reads the whole file up front (a 184 s friendly at 20 Hz is ~3700 lines; the demo files
    are 12-30 MB, gzip 0.7 MB) and keeps the RAW LINES of the "frame" events with ``active``
    true, indexed by tick; the others (start/stop lines, inactive frames before the
    battle) are counted for ``status()``. A frame is parsed when it is looked at (a bounded
    cache of PARSE_CACHE parsed frames), so opening a 40 MB capture costs ~0.2 s and a seek
    one json.loads. Events are derived forward from consecutive frames (spawns, deaths, the
    local player's plays) the first time the timeline passes a frame and cached per index,
    so seeking backwards shows the events up to that frame.
    Conversions, all measured on 2026-09-20 captures (``capture_*`` above):
      elixir      players[i].elixir_raw // 10 -> elixir_milli, elixir_known True for BOTH sides
      hand        deck indices -> names through players[i].deck when the deck is populated;
                  hand_known False (hand [-1]*4, deck []) for the opponent until the results
                  screen, where both players' deck and hand fill in
      cycle       players[i].cycle indices -> names, same rule (next_card = the first)
      units       every entity; uid = "id:card:side" (``capture_uid``); kind from card_id
                  and the tower positions; path_nodes decoded and REVERSED to start-first;
                  target the target entity's uid; direction (movement_direction_x, _y);
                  state behavior_state; extra = the raw entity minus path_nodes
      spells      every effect (projectiles and spells alike), see ``capture_spell``
      towers      tower_hp [king, left, right] in the owning team's frame (LIVE_TOWER_X), 0 for a
                  destroyed tower (it leaves the entity list); crowns from the OTHER side's
                  missing towers (a princess 1, the king 3)
      overtime    tick > 3690;  game_over: the trailing run of one frozen tick (>= 1 s)
      meta        {"source": "capture", "path", "seq", the frame's timing and bookkeeping
                  fields, "local_side"}
    ``local_side`` (the side whose hand alone the frames hold, ``capture_local_side``) is
    what ``--seat local`` seats at the bottom; None when no frame in the first 10 s shows one.
    """

    live: bool = False
    units_per_tile: int = LIVE_UNITS_PER_TILE
    length: int
    index: int
    local_side: int | None

    def __init__(self, path: str | Path, names: Names | None = None) -> None:
        self.path = resolve_capture(Path(path))
        self.name = capture_stem(self.path)
        self.names = names if names is not None else Names.live()
        opener = gzip.open if self.path.name.endswith(".gz") else open
        with opener(self.path, "rb") as fh:
            lines = fh.readlines()
        self._lines: list[bytes] = []
        self._ticks: list[int] = []
        self.inactive = 0  # frame events with active false
        self.other = 0  # start/stop events and unparsable lines
        for raw in lines:
            if not raw.startswith(_FRAME_PREFIX):
                self.other += 1
            elif _ACTIVE not in raw:
                self.inactive += 1
            else:
                m = _TICK.search(raw)
                if m is None:
                    self.other += 1
                    continue
                self._lines.append(raw)
                self._ticks.append(int(m.group(1)))
        self.length = len(self._lines)
        self.index = 0
        self._frozen_from = self._frozen_start()
        self._cache: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._events = CaptureEvents(self.names)
        self._events_end: list[int] = []  # per processed index: len(lines) after that frame
        self.local_side = self._find_local_side()

    def _frozen_start(self) -> int | None:
        """Index where the trailing run of one tick starts, if it lasts LIVE_FROZEN_FRAMES."""
        if not self._ticks:
            return None
        last = self._ticks[-1]
        i = self.length
        while i > 0 and self._ticks[i - 1] == last:
            i -= 1
        return i if self.length - i >= LIVE_FROZEN_FRAMES else None

    def _find_local_side(self) -> int | None:
        for i in range(min(self.length, LOCAL_SIDE_SCAN)):
            side = capture_local_side(self.raw(i))
            if side is not None:
                return side
        return None

    def raw(self, index: int) -> dict[str, Any]:
        """The parsed capture frame at ``index`` (cached)."""
        f = self._cache.get(index)
        if f is not None:
            self._cache.move_to_end(index)
            return f
        f = json.loads(self._lines[index])
        self._cache[index] = f
        if len(self._cache) > PARSE_CACHE:
            self._cache.popitem(last=False)
        return f

    def _events_up_to(self, index: int) -> list[str]:
        while len(self._events_end) <= index:
            self._events.note(self.raw(len(self._events_end)))
            self._events_end.append(len(self._events.lines))
        return self._events.lines[: self._events_end[index]]

    def frame(self) -> Frame | None:
        if self.length == 0:
            return None
        i = self.index
        f = self.raw(i)
        meta = {
            "source": "capture",
            "path": str(self.path),
            "index": i,
            "seq": f.get("seq"),
            "t_us": f.get("t_us"),
            "read_us": f.get("read_us"),
            "coherent": f.get("coherent"),
            "failure": f.get("failure"),
            "replay_tick": f.get("replay_tick"),
            "local_side": self.local_side,
        }
        frozen = self._frozen_from is not None and i >= self._frozen_from
        return capture_frame(f, self.names, self._events_up_to(i), frozen, meta)

    def seek(self, index: int) -> None:
        self.index = min(max(index, 0), max(self.length - 1, 0))

    def step(self, delta: int) -> None:
        self.seek(self.index + delta)

    def index_at_tick(self, tick: int) -> int:
        """First frame index whose tick is >= ``tick`` (for --start-tick), clamped."""
        for i, t in enumerate(self._ticks):
            if t >= tick:
                return i
        return max(self.length - 1, 0)

    def tick_at(self, index: int) -> int:
        return self._ticks[index]

    def status(self) -> str:
        if self.length == 0:
            return f"{self.name}: no active frames ({self.inactive} inactive, {self.other} other)"
        f = self.raw(self.index)
        read_ms = (f.get("read_us") or 0) / 1000
        return (
            f"{self.name} frame {self.index + 1}/{self.length} tick {self._ticks[self.index]}"
            f" seq {f.get('seq')} sample {read_ms:.1f} ms"
            f" ({self.inactive} inactive, {self.other} other lines skipped)"
        )

    def close(self) -> None:
        self._lines, self.length = [], 0
        self._cache.clear()


# --------------------------------------------------------------------------
# The engine's formats
# --------------------------------------------------------------------------


def regular_ticks(tick_ms: int) -> int:
    """Regulation length in ticks from calibration.json (the trace header does not carry
    BattleState.regular_ticks; MockEngine derives it the same way)."""
    from royalegym.protocol import default_calibration

    seconds = default_calibration().int("match.REGULAR_TIME_S")
    return -(-(seconds * 1000) // tick_ms)


class TraceSource:
    """Frames of a royalegym.replay trace, by index; the arena drawn from the trace header.

    ``load_trace`` decodes .msgpack or .json. Per frame: entities -> units through
    royalegym.viser.unit_dict (the rows a running engine publishes, so a trace and a stream
    of the same battle draw identically: uid the engine's, towers named by kind, path [] --
    the engine does not record paths -- target None), spells -> Spell the same way,
    elixir_milli / crowns / hands from the frame, deck from the header's setup, next_card and
    the cycle from the hand history (the cycle rule: the front of the 8-card queue enters
    the hand and the played card goes to the back; with ShuffleMode.NONE the queue is
    deck[4:] from the start, otherwise a position is "?" until a play reveals it), tower_hp
    from the tower entities by tower_slot. king_active is not recorded (None).
    ``overtime`` is tick >= the calibration's regulation ticks, ``game_over`` / ``winner``
    the last frame with ``trace.result``. Events: spawns and deaths by uid diffing, plays
    from the step log (commands with status 0, with their position). ``units_per_tile`` is
    ``header.subtile`` (18000). ``arena`` is a royalegym.protocol.Arena assembled from the
    header (grid, bridges, water rows, tiles, subtile; tower centres from the default
    arena, which the header does not carry).
    """

    live: bool = False
    units_per_tile: int
    length: int
    index: int
    arena: Any  # royalegym.protocol.Arena
    local_side: int | None = None

    def __init__(self, path: str | Path) -> None:
        from royalegym.protocol import EMPTY_CARD, DeployStatus, ShuffleMode
        from royalegym.replay import load_trace

        self.path = Path(path)
        self.trace = load_trace(self.path)
        h = self.trace.header
        self.name = f"trace seed {h.seed}"
        self.names = Names.from_cards(h.cards)
        self.units_per_tile = h.subtile
        self.arena = arena_from_header(h)
        self.length = len(self.trace.frames)
        self.index = 0
        self._ticks = [f.tick for f in self.trace.frames]
        self._regular = regular_ticks(h.tick_ms)
        self._empty = EMPTY_CARD
        setup = h.setup
        self.decks: list[list[int]] | None = (
            [list(d) for d in setup.decks] if setup is not None else None
        )
        # Queue per team, front first; None = not yet revealed.
        queues: list[list[int | None]] = [[None] * (DECK_SIZE - HAND_SIZE) for _ in (0, 1)]
        if self.decks is not None and setup is not None and setup.shuffle == ShuffleMode.NONE:
            first = self.trace.frames[0].hands if self.trace.frames else []
            for team, deck in enumerate(self.decks):
                if team < len(first) and list(first[team]) == deck[:HAND_SIZE]:
                    queues[team] = list(deck[HAND_SIZE:])
        # Plays from the step log, attached to the first frame after the step's tick.
        plays: dict[int, list[str]] = {}
        by_tick: dict[int, int] = {}
        for i, t in enumerate(self._ticks):
            by_tick.setdefault(t, i)  # the frame BEFORE the step's commands
        for st in self.trace.steps:
            for cmd, status in zip(st.commands, st.statuses, strict=True):
                if status != DeployStatus.OK or st.tick not in by_tick:
                    continue
                before = self.trace.frames[by_tick[st.tick]]
                card = before.hands[cmd.team][cmd.hand_slot]
                line = play_line(
                    st.tick, cmd.team, self.names.name_of(card), (cmd.x, cmd.y), h.subtile
                )
                after = by_tick[st.tick] + 1
                plays.setdefault(after, []).append(line)
        # One forward pass: events and the queue snapshot per frame (a trace is loaded whole).
        log = _EventLog()
        self._events_end: list[int] = []
        self._queues: list[list[list[int | None]]] = []
        prev_hands: list[list[int]] | None = None
        for i, fr in enumerate(self.trace.frames):
            log.lines.extend(plays.get(i, ()))
            units = {
                e.uid: (e.team, self._unit_name(e), e.x, e.y, e.tower_slot >= 0)
                for e in fr.entities
            }
            log.note(fr.tick, units, [None, None], h.subtile)
            if prev_hands is not None:
                for team, (was, now) in enumerate(zip(prev_hands, fr.hands, strict=True)):
                    for a, b in zip(was, now, strict=True):
                        if a != b and a != EMPTY_CARD:
                            q = queues[team]
                            q.pop(0)  # b, the drawn card, was the front
                            q.append(a)
            prev_hands = [list(hd) for hd in fr.hands]
            self._events_end.append(len(log.lines))
            self._queues.append([list(q) for q in queues])
        self._lines = log.lines

    def _unit_name(self, e: Any) -> str:
        """royalegym.viser.unit_dict's naming: towers by kind, everything else by card."""
        if e.card_id == self._empty and e.kind in TOWER_NAMES:
            return TOWER_NAMES[e.kind]
        return self.names.name_of(e.card_id)

    def frame(self) -> Frame | None:
        from royalegym.viser import spell_dict, unit_dict

        if self.length == 0:
            return None
        i = self.index
        fr = self.trace.frames[i]
        h = self.trace.header
        name_of = self.names.name_of
        units = [msgspec.convert(unit_dict(e, name_of), Unit) for e in fr.entities]
        spells = [msgspec.convert(spell_dict(s, name_of), Spell) for s in fr.spells]
        players = [self._player(team, fr, units, self._queues[i][team]) for team in (0, 1)]
        result = self.trace.result
        # A result with no winner is a truncated episode (StepLimitCondition), not a battle end.
        game_over = i == self.length - 1 and result is not None and result.winner != NO_WINNER
        return Frame(
            tick=fr.tick,
            tick_ms=h.tick_ms,
            units_per_tile=h.subtile,
            players=players,
            units=units,
            spells=spells,
            overtime=fr.tick >= self._regular,
            game_over=game_over,
            winner=result.winner if game_over and result is not None else NO_WINNER,
            crowns=list(fr.crowns),
            events=self._lines[: self._events_end[i]][-EVENTS_KEPT:],
            meta={
                "source": "trace",
                "path": str(self.path),
                "index": i,
                "seed": h.seed,
                "frame_every_tick": h.frame_every_tick,
                "state_hash": fr.state_hash,
            },
        )

    def _player(self, team: int, fr: Any, units: list[Unit], queue: list[int | None]) -> Player:
        name_of = self.names.name_of
        towers = {
            u.extra["tower_slot"]: u for u in units if u.team == team and u.kind in TOWER_KINDS
        }
        if any(u.kind in TOWER_KINDS for u in units):
            tower_hp = [towers[s].hp if s in towers else 0 for s in range(TOWER_SLOTS)]
            max_by_kind = {u.kind: u.max_hp for u in units if u.kind in TOWER_KINDS}
            tower_max = [
                towers[s].max_hp if s in towers else max_by_kind.get(k, 0)
                for s, k in enumerate(SLOT_KINDS)
            ]
        else:
            tower_hp, tower_max = [UNKNOWN_HP] * TOWER_SLOTS, [UNKNOWN_HP] * TOWER_SLOTS
        hand = [name_of(c) if c != self._empty else "" for c in fr.hands[team]]
        return Player(
            team=team,
            elixir_milli=fr.elixir_milli[team],
            elixir_known=True,
            hand=hand,
            hand_known=True,
            next_card=name_of(queue[0]) if queue and queue[0] is not None else None,
            cycle=[name_of(c) if c is not None else UNKNOWN_CARD for c in queue[1:]],
            deck=[name_of(c) for c in self.decks[team]] if self.decks is not None else [],
            deck_known=self.decks is not None,
            crowns=fr.crowns[team],
            tower_hp=tower_hp,
            tower_max_hp=tower_max,
            king_active=None,  # not recorded in a trace
        )

    def seek(self, index: int) -> None:
        self.index = min(max(index, 0), max(self.length - 1, 0))

    def step(self, delta: int) -> None:
        self.seek(self.index + delta)

    def index_at_tick(self, tick: int) -> int:
        for i, t in enumerate(self._ticks):
            if t >= tick:
                return i
        return max(self.length - 1, 0)

    def tick_at(self, index: int) -> int:
        return self._ticks[index]

    def status(self) -> str:
        if self.length == 0:
            return f"{self.name}: no frames"
        every = "every tick" if self.trace.header.frame_every_tick else "per decision"
        return (
            f"{self.name} frame {self.index + 1}/{self.length} tick {self._ticks[self.index]}"
            f" ({every}, {len(self.trace.steps)} steps)"
        )

    def close(self) -> None:
        """Nothing to release: the trace was loaded whole."""


class StreamSource:
    """Frames a running engine publishes over UDP (see ``Publisher``), newest only.

    Binds an ephemeral UDP socket, sends STREAM_HELLO to (host, port) once a second (from
    ``frame()``, which the app calls every loop; ``heartbeat()`` forces one), and decodes
    every datagram that arrives with ``model.decode_frame``. ``frame()`` drains the socket
    and returns the newest decoded frame, or the last one when nothing new arrived, or
    None before the first; ``index`` counts frames received; ``status()`` reports frames/s
    and the datagrams dropped (sequence gaps in ``meta["seq"]``). ``units_per_tile`` is
    taken from the first frame (0 before). ``close()`` stops the heartbeat and closes the
    socket; the publisher notices within STREAM_ATTACH_TIMEOUT_S and goes quiet.
    """

    live: bool = True
    length: int | None = None
    local_side: int | None = None

    def __init__(self, host: str = STREAM_HOST, port: int = STREAM_PORT) -> None:
        self.peer = (host, port)
        self.name = f"{host}:{port}"
        self.units_per_tile = 0
        self.index = 0
        self.drops = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Loopback stays on loopback (no firewall prompt); anything else needs any-address.
        self._sock.bind(("127.0.0.1" if host in ("127.0.0.1", "localhost") else "0.0.0.0", 0))
        self._sock.setblocking(False)
        self.address: tuple[str, int] = self._sock.getsockname()[:2]
        self._last_hello = 0.0
        self._last: Frame | None = None
        self._last_seq: int | None = None
        self._times: deque[float] = deque()
        self._closed = False
        self.heartbeat()

    def heartbeat(self) -> None:
        with contextlib.suppress(OSError):  # nobody listening yet: the next one tries again
            self._sock.sendto(STREAM_HELLO, self.peer)
        self._last_hello = time.monotonic()

    def frame(self) -> Frame | None:
        if self._closed:
            return self._last
        now = time.monotonic()
        if now - self._last_hello >= STREAM_HEARTBEAT_S:
            self.heartbeat()
        newest: bytes | None = None
        while True:
            try:
                data, _addr = self._sock.recvfrom(STREAM_MAX_DATAGRAM)
            except (BlockingIOError, ConnectionResetError, OSError):
                # WSAECONNRESET on Windows: an earlier heartbeat hit a closed port.
                break
            newest = data
            self.index += 1
            self._times.append(now)
        while self._times and now - self._times[0] > 1.0:
            self._times.popleft()
        if newest is not None:
            f = decode_frame(newest)
            seq = f.meta.get("seq")
            if isinstance(seq, int) and self._last_seq is not None and seq > self._last_seq + 1:
                self.drops += seq - self._last_seq - 1
            if isinstance(seq, int):
                self._last_seq = seq
            if not self.units_per_tile:
                self.units_per_tile = f.units_per_tile
            self._last = f
        return self._last

    def seek(self, index: int) -> None:
        """Ignored: a stream has no timeline."""

    def step(self, delta: int) -> None:
        """Ignored: a stream has no timeline."""

    def status(self) -> str:
        if self._last is None:
            return f"{self.name} waiting for a publisher (hello every {STREAM_HEARTBEAT_S:g} s)"
        return (
            f"{self.name} frames {self.index} {len(self._times):.1f} fps drops {self.drops}"
            f" tick {self._last.tick}"
        )

    def close(self) -> None:
        self._closed = True
        self._sock.close()


class Publisher:
    """The engine side of StreamSource for a script that has a ``Frame``: sends ONLY while
    a viewer is attached.

    The rule the viewer is built on: a separate process, never in the tick loop,
    zero cost when nobody is watching. It is royalegym.viser.ViserPublisher (the env's
    publisher, which takes a BattleState) under one socket: ``publish(frame)`` costs one
    clock read while detached, and while attached encodes the frame with msgspec and sends
    one datagram to the last heartbeat's address (over STREAM_MAX_DATAGRAM: resent without
    unit paths, then dropped). ``frame_from_state`` builds a Frame from a BattleState.
    """

    def __init__(self, host: str = STREAM_HOST, port: int = STREAM_PORT) -> None:
        from royalegym.viser import ViserPublisher

        self._pub = ViserPublisher(host, port)
        self.address = self._pub.address

    @property
    def attached(self) -> bool:
        return self._pub.attached

    @property
    def sent(self) -> int:
        return self._pub.sent

    @property
    def dropped(self) -> int:
        return self._pub.dropped

    def publish(self, frame: Frame) -> bool:
        if not self.attached:
            return False
        d = msgspec.to_builtins(frame)
        d["meta"] = {"seq": self._pub.seq, **d["meta"]}
        return self._pub.publish_dict(d)

    def close(self) -> None:
        self._pub.close()


def frame_from_state(
    state: Any,
    names: Names,
    units_per_tile: int,
    events: Sequence[str] = (),
    meta: dict[str, Any] | None = None,
) -> Frame:
    """A Frame from a royalegym.protocol.BattleState, the rows royalegym.viser publishes.

    Entities -> units (kind, tower_slot, timers as they are; path [], target None, radius
    the engine's), spells -> Spell, players -> Player with every flag True (the engine knows
    everything), hand/next_card names through ``names``, the cycle beyond next_card empty
    and the deck unknown (a BattleState does not expose them), ``winner``/``overtime``/
    ``game_over`` as reported.
    """
    from royalegym.viser import frame_dict

    d = frame_dict(state, names.name_of, units_per_tile, None, events, meta)
    return msgspec.convert(d, Frame)


def arena_from_header(header: Any) -> Any:
    """royalegym.protocol.Arena from a royalegym.replay.TraceHeader (tower centres from the
    default arena, which the header does not carry)."""
    from royalegym.protocol import Arena

    base = default_arena()
    return Arena(
        subtile=header.subtile,
        half=header.half,
        tiles_x=header.tiles_x,
        tiles_y=header.tiles_y,
        grid=[list(row) for row in header.grid],
        water_half_rows=tuple(header.water_half_rows),
        bridges_half_cols=[tuple(b) for b in header.bridges_half_cols],
        king_centers=base.king_centers,
        princess_centers=base.princess_centers,
        princess_center_status=base.princess_center_status,
    )


def default_arena() -> Any:
    """royalegym.protocol.Arena.load(default_calibration()): the geometry for live sources."""
    from royalegym.protocol import Arena, default_calibration

    return Arena.load(default_calibration())
