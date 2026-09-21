"""The one frame model every source produces and the renderer draws.

WHY ONE MODEL
    Three things can feed the viewer -- a RoyaleLive capture (frames-*.jsonl), an engine
    trace (royalegym.replay) and a running engine (UDP stream) -- and they disagree on
    units, names and what is knowable. The sources translate into ``Frame``; the renderer,
    the timeline and the inspector read only ``Frame``. Nothing downstream knows which
    source it is looking at.

UNITS AND FRAMES
    Positions stay in the source's RAW integer units; ``Frame.units_per_tile`` says how
    many make one tile (1000 for the live client, 18000 for the engine, both measured:
    client 16.402 on 2026-09-18 (RoyaleLive), RoyaleSim/data/calibration.json
    representation.SUBTILE_PER_TILE). The renderer divides once when it projects. All
    positions are in the NATIVE / ENGINE frame: team 0's back edge at y=0, x to the right,
    y away from team 0. Whichever team the viewer seats at the bottom is a view choice
    (render.ViewState.seat), not a data transform.

WHAT IS AND IS NOT KNOWN
    The ``*_known`` flags are the honest version of a field, not a default. A capture knows
    both players' elixir and only the recording player's hand and deck; a trace knows
    everything. A source that cannot know something says so with the flag and fills the
    field with "?" / [] / -1, and the renderer prints the reason instead of a wrong number.

Wire form: ``encode_frame`` / ``decode_frame`` are msgspec msgpack of these dataclasses,
so the engine-side publisher and ``sources.StreamSource`` share one codec.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import msgspec

# protocol.EntityKind's values, repeated so the model imports without royalegym
# (tests/test_model.py asserts they are equal).
KIND_TROOP = 0
KIND_BUILDING = 1
KIND_KING_TOWER = 2
KIND_PRINCESS_TOWER = 3
TOWER_KINDS = (KIND_KING_TOWER, KIND_PRINCESS_TOWER)

NO_WINNER = -1
UNKNOWN_HP = -1
UNKNOWN_CARD = "?"
HAND_SIZE = 4
DECK_SIZE = 8
TOWER_SLOTS = 3  # king, left, right, in the owner's own frame (protocol.TowerSlot order)

# The live client counts positions in millitiles (measured 2026-09-18, RoyaleLive) and elixir
# in ten-thousandths (elixir_raw, 10000 = one elixir); the model keeps elixir in thousandths
# like the engine, so a capture source divides elixir_raw by LIVE_ELIXIR_PER_MILLI.
LIVE_UNITS_PER_TILE = 1000
LIVE_ELIXIR_PER_MILLI = 10
TICK_MS = 50  # the client (20 ticks/s, measured 2026-09-17) and the engine (time.TICK_MS)


@dataclass(slots=True)
class Player:
    team: int
    elixir_milli: int  # 1000 = one elixir
    elixir_known: bool
    hand: list[str]  # HAND_SIZE card names, UNKNOWN_CARD when not known
    hand_known: bool  # False: the source does not have it (the live opponent's, mid-battle)
    next_card: str | None
    cycle: list[str]  # names beyond next_card, may be empty
    deck: list[str]  # DECK_SIZE names or []
    deck_known: bool
    crowns: int
    tower_hp: list[int]  # [king, left, right] owner's frame; UNKNOWN_HP unknown
    tower_max_hp: list[int]
    king_active: bool | None


@dataclass(slots=True)
class Unit:
    uid: str | int
    team: int
    kind: int  # KIND_*
    name: str
    x: int  # raw units, native/engine frame
    y: int
    hp: int
    max_hp: int
    radius: int  # raw units; 0 unknown -> the renderer's default
    flying: bool
    deploy_ticks: int
    stun_ticks: int
    target: str | int | None  # another unit's uid
    path: list[tuple[int, int]]  # raw units, start-first
    direction: tuple[int, int] | None  # live movement_direction_x/y (unit vector * 256)
    state: int | None  # live behavior_state
    extra: dict[str, Any] = field(default_factory=dict)  # raw fields for the inspector


@dataclass(slots=True)
class Spell:
    team: int
    name: str
    motion: int  # protocol.SpellMotion; live effects use the same values where they map
    x: int
    y: int
    aim_x: int
    aim_y: int
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Frame:
    tick: int
    tick_ms: int
    units_per_tile: int
    players: list[Player]  # index = team
    units: list[Unit]
    spells: list[Spell]
    overtime: bool
    game_over: bool
    winner: int  # NO_WINNER, 0, 1 (2 = draw, protocol.Winner)
    crowns: list[int]  # [team]
    events: list[str] = field(default_factory=list)  # newest last, source-accumulated
    meta: dict[str, Any] = field(default_factory=dict)  # source, seq, timing, wall time...

    @property
    def clock_ms(self) -> int:
        return self.tick * self.tick_ms

    def player(self, team: int) -> Player:
        return self.players[team]

    def unit(self, uid: str | int) -> Unit | None:
        for u in self.units:
            if u.uid == uid:
                return u
        return None


@runtime_checkable
class Source(Protocol):
    """Where frames come from. A replay exposes frames by index; a live source the latest.

    name            what the status line calls it ("demo-1", "trace seed 7", "127.0.0.1:9870")
    live            True: ``frame()`` is the newest state and ``length`` is None; the
                    timeline is disabled and ``seek``/``step`` are no-ops
    units_per_tile  the source's raw units per tile (every Frame it returns repeats it)
    length          number of frames for a replay, None for live
    index           the current frame index (replays); the frames received so far (live)
    frame()         the current frame, or None before the first one arrives
    seek(i)         replays: jump to frame i (clamped); live: ignored
    step(delta)     replays: move by delta frames (clamped); live: ignored
    status()        one line for the status bar (frame counter, sample latency, drops)
    close()         release the file / the socket / whatever the source holds open
    """

    name: str
    live: bool
    units_per_tile: int
    length: int | None
    index: int

    def frame(self) -> Frame | None: ...

    def seek(self, index: int) -> None: ...

    def step(self, delta: int) -> None: ...

    def status(self) -> str: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------
# Card names
# --------------------------------------------------------------------------

# The live client's card table, register name -> [card id, elixir cost]: a copy of the one
# RoyaleLive/cards.py generates from the 15.535.29 csv_logic (verified against client 16.402
# for every card played on 2026-09-18/19). RoyaleLive/tests/test_viser_live.py keeps the two
# files equal; ROYALEVISER_CARDS points ``Names.live()`` at another table.
CARDS_JSON = Path(__file__).with_name("cards.json")
CARDS_ENV = "ROYALEVISER_CARDS"
# A card's other forms spawn under their own id while the hand keeps the register id. The one
# measured so far: the all-cards Musketeer slot spawns the hero form 203000014 (measured on
# frames-demo-20260920-120752, five taps; RoyaleLive keeps the same table). Entity id ->
# register id.
LIVE_FORMS = {203000014: 26000014}


class Names:
    """card id -> (name, elixir cost) for one source.

    ``Names.live()`` reads the live client's card table (``path``, else the ROYALEVISER_CARDS
    environment variable, else the package's ``cards.json``) plus LIVE_FORMS.
    ``Names.from_cards(cards)`` takes any sequence with ``card_id``, ``name`` and ``elixir``
    attributes -- a trace header's ``cards`` or ``engine.cards()``. Unknown ids print as
    ``#<id>`` so a hole in a table is visible, never silent.
    """

    def __init__(self, entries: Iterable[tuple[int, str, int]] = ()) -> None:
        self._by_id: dict[int, tuple[str, int]] = {}
        for card_id, name, cost in entries:
            self._by_id.setdefault(card_id, (name, cost))

    @classmethod
    def live(cls, path: Path | None = None) -> Names:
        table = Path(path or os.environ.get(CARDS_ENV) or CARDS_JSON)
        raw = json.loads(table.read_text(encoding="utf-8"))
        names = cls((int(cid), name, int(cost)) for name, (cid, cost) in raw.items())
        for form_id, register_id in LIVE_FORMS.items():
            if register_id in names._by_id:
                names._by_id.setdefault(form_id, names._by_id[register_id])
        return names

    @classmethod
    def from_cards(cls, cards: Sequence[Any]) -> Names:
        return cls((int(c.card_id), str(c.name), int(c.elixir)) for c in cards)

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, card_id: int) -> bool:
        return card_id in self._by_id

    def name_of(self, card_id: int) -> str:
        entry = self._by_id.get(card_id)
        return entry[0] if entry is not None else f"#{card_id}"

    def cost_of(self, card_id: int) -> int | None:
        entry = self._by_id.get(card_id)
        return entry[1] if entry is not None else None

    def cost_of_name(self, name: str) -> int | None:
        for n, cost in self._by_id.values():
            if n == name:
                return cost
        return None


# --------------------------------------------------------------------------
# Wire codec and a contract check
# --------------------------------------------------------------------------


def encode_frame(frame: Frame) -> bytes:
    """msgpack bytes of a Frame: what an engine-side publisher sends per tick."""
    return msgspec.msgpack.encode(frame)


def decode_frame(data: bytes) -> Frame:
    return msgspec.msgpack.decode(data, type=Frame)


def problems(frame: Frame) -> list[str]:
    """Every way ``frame`` breaks the contract, one line each; [] when it is sound.

    For source builders and tests: shape checks only (two players, hand and tower lengths,
    kinds, teams, one unit per uid), not game rules.
    """
    out: list[str] = []
    if frame.units_per_tile <= 0:
        out.append(f"units_per_tile {frame.units_per_tile} must be positive")
    if len(frame.players) != 2:
        out.append(f"{len(frame.players)} players, expected 2")
    for i, p in enumerate(frame.players):
        if p.team != i:
            out.append(f"players[{i}].team is {p.team}")
        if len(p.hand) != HAND_SIZE:
            out.append(f"players[{i}].hand has {len(p.hand)} entries, expected {HAND_SIZE}")
        if p.deck and len(p.deck) != DECK_SIZE:
            out.append(f"players[{i}].deck has {len(p.deck)} entries, expected {DECK_SIZE} or 0")
        if len(p.tower_hp) != TOWER_SLOTS or len(p.tower_max_hp) != TOWER_SLOTS:
            out.append(f"players[{i}] tower_hp/tower_max_hp must have {TOWER_SLOTS} entries")
    if len(frame.crowns) != 2:
        out.append(f"crowns has {len(frame.crowns)} entries, expected 2")
    seen: set[str | int] = set()
    for u in frame.units:
        if u.team not in (0, 1):
            out.append(f"unit {u.uid} team {u.team}")
        if u.kind not in (KIND_TROOP, KIND_BUILDING, KIND_KING_TOWER, KIND_PRINCESS_TOWER):
            out.append(f"unit {u.uid} kind {u.kind}")
        if u.uid in seen:
            out.append(f"unit uid {u.uid} appears twice")
        seen.add(u.uid)
    for s in frame.spells:
        if s.team not in (0, 1):
            out.append(f"spell {s.name} team {s.team}")
    return out
