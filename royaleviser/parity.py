"""The engine and the battle it was calibrated on, side by side in the window.

    python -m royaleviser --parity <name>.parity.json

RoyaleSim's replay harness plays a recorded battle through the engine and scores the two
per-tick states against each other. Run with ``--trace`` it also writes every scored unit-tick
as a row: the tick, the unit, where the RECORDING had it and where the ENGINE put it. That
file is a whole battle from both sides, and this module turns each side into a viewer source,
so the question the numbers answer as a percentage ("99.24 % of positions within 250") can be
answered as a picture: WHERE do they part, and what was the unit doing when they did.

    src = ParitySource(path, RECORDING)      # what the recording had
    eng = ParitySource(path, ENGINE)         # what the engine did with the same battle
    app.run([src, eng], tolerance=250)       # the engine ghosted over the recording

Both sides come out in NATIVE MILLITILES (1000 per tile), which is what the harness writes
for both: the recording's own units, and the engine's subtiles divided down. Nothing here
converts or rescales, so a disagreement on the board is the file's, not this module's.

WHAT THE ROWS DO NOT COVER. The harness writes a row per MATCHED pair, so an entity it could
not match is in neither side of this view: a recording entity with no engine counterpart, and
an engine entity with no recording one. The report counts both (``unmatched_truth`` and
``unmatched_sim``) and the status line carries those counts, because a view that quietly drops
the units that failed to match would be at its most convincing exactly where the engine and the
game agree least.

WHAT A PARITY FILE DOES NOT HAVE, and what this does about it. No elixir, no hands, no decks:
those say "not in this source" the way a recording's opponent does. No crowns and no result.
No collision radius and no footprint, so buildings and towers draw at the viewer's marked
fallback size. The path is a COUNT of nodes, not the nodes, so it rides in the inspector's
extra rather than being drawn as a path that was never recorded. Tower slots are not named in
the file, so the dashboard's tower rows stay unknown while the towers themselves stand on the
board with their hp.

The file is data. Reading one needs a machine that has the fixtures and the results, the way
the parity gate does; nothing here depends on them, and the tests build a file of their own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import (
    HAND_SIZE,
    KIND_BUILDING,
    KIND_KING_TOWER,
    KIND_PRINCESS_TOWER,
    KIND_TROOP,
    LIVE_UNITS_PER_TILE,
    NO_WINNER,
    TICK_MS,
    TOWER_SLOTS,
    UNKNOWN_CARD,
    UNKNOWN_HP,
    Frame,
    Names,
    Player,
    Unit,
)

#: The two sides of a parity file, and what each is called in the window.
RECORDING = "recording"
ENGINE = "engine"
SIDES = (RECORDING, ENGINE)

PARITY_SUFFIX = ".parity.json"
#: Truth behaviour states of a unit that has not started moving: deploying, and the summon
#: delay of a staggered formation member (RoyaleSim's replay harness names the same two).
DEPLOY_STATES = (4, 11)
#: A friendly's regular time in ticks; the tick runs on into overtime. The ticks in a parity
#: file are a recording's, so the rule here is the one CaptureSource applies to the same ticks
#: (``sources.LIVE_REGULAR_TICKS``, and STRICTLY greater), or the same battle would change its
#: overtime by one tick depending on which source opened it.
REGULAR_TICKS = 3690
TOWER_ROOTS = {"KingTower": KIND_KING_TOWER, "PrincessTower": KIND_PRINCESS_TOWER}
#: card ids: 26xxxxxx troop, 27xxxxxx building, 28xxxxxx spell (sources.LIVE_BUILDING_MIN).
BUILDING_MIN = 27_000_000
SPELL_MIN = 28_000_000


def is_parity(spec: str) -> bool:
    return spec.lower().endswith(PARITY_SUFFIX)


def kind_of(name: str, names: Names) -> int:
    """A unit's kind from its card name: the towers by name, everything else by card id.

    A card the table does not have is a TROOP rather than a guess at a building, because a
    troop is drawn as a disc of the default radius and a building as a box -- and a box is a
    claim about the ground it stands on.
    """
    if name in TOWER_ROOTS:
        return TOWER_ROOTS[name]
    card_id = names.id_of(name)
    if card_id is not None and BUILDING_MIN <= card_id < SPELL_MIN:
        return KIND_BUILDING
    return KIND_TROOP


class ParitySource:
    """One side of a RoyaleSim parity trace, by frame index (see the module docstring).

    ``side`` is RECORDING or ENGINE. Every frame is one tick of the trace rows, in native
    millitiles. ``length`` is the number of ticks the file scored, and the two sides of one
    file have the same ticks in the same order, so a viewer seeks one to the other's tick
    exactly.

    The row layout is the harness's: truth ``[x, y, hp, state, path_n, target]`` and sim
    ``[x, y, hp, attacking, path_n, target]``. Every ROW is keyed by the RECORDING's entity
    key, so a unit is the same unit on both sides and the compare pairs them by that key
    rather than by guessing from a name and a distance.

    The two ``target`` columns are NOT in the same space, and that is why only one of them
    becomes a ``Unit.target``. The recording's is another recording key, which is a uid here.
    The engine's is an index into the harness's own list of engine entities (harness.rs, the
    value side of ``sim_index_of``), and the file publishes no way to turn it back into a
    recording key: ``Pair.sim_index`` is the engine's entity id, a third space again. Drawing
    it as a target would point a line at whichever unit happened to hold that number, so the
    engine side's target stays None and the raw value rides in ``extra`` under a name that
    says what it is.
    """

    live: bool = False
    units_per_tile: int = LIVE_UNITS_PER_TILE
    local_side: int | None = None

    def __init__(self, path: str | Path, side: str = RECORDING, names: Names | None = None) -> None:
        if side not in SIDES:
            raise ValueError(f"{side!r}: a parity side is {RECORDING!r} or {ENGINE!r}")
        self.path = Path(path)
        self.side = side
        self.names = names or Names.live()
        report = json.loads(self.path.read_text(encoding="utf-8"))
        trace = report.get("trace") or []
        if not trace:
            raise ValueError(
                f"{self.path.name} has no per-tick rows: write it with the harness's --trace"
            )
        self.fixture = str(report.get("fixture") or self.path.stem)
        self.name = f"{self.side} {self.fixture}"
        # key -> (team, name, kind, max hp seen). The card is the ROOT card the harness
        # attributes the unit to, which is what a watcher would call it.
        pairs = {int(p["truth_key"]): (int(p["side"]), str(p["root"])) for p in report["pairs"]}
        self._by_tick: dict[int, list[dict[str, Any]]] = {}
        # The most this side was ever seen with. Per SIDE, not shared: the two sides are two
        # simulations, and taking the engine's hp as the recording's maximum would draw a
        # recording's hp bar against a number the recording never reached.
        self._max_hp: dict[int, int] = {}
        column = "truth" if side == RECORDING else "sim"
        for row in trace:
            key = int(row["key"])
            self._by_tick.setdefault(int(row["tick"]), []).append(row)
            cell = row.get(column)
            if cell:
                self._max_hp[key] = max(self._max_hp.get(key, 0), int(cell[2]))
        self._pairs = pairs
        self.unmatched = (
            len(report.get("unmatched_truth") or ()),
            len(report.get("unmatched_sim") or ()),
        )
        self._ticks = sorted(self._by_tick)
        self.length = len(self._ticks)
        self.index = 0
        self._events = self._divergence_events(report)

    def _divergence_events(self, report: dict[str, Any]) -> dict[int, list[str]]:
        """The harness's own reading of where this battle first parted, on the tick it says.

        It is the one line in the report that names a MOMENT rather than a total, so it
        belongs in the events list where a reader can scrub to it.
        """
        d = report.get("first_divergence")
        if not d:
            return {}
        out: dict[int, list[str]] = {}
        onset, tick = int(d.get("onset_tick", d["tick"])), int(d["tick"])
        out.setdefault(onset, []).append(f"t{onset} error passes 250: {d['card']} ({d['cause']})")
        out.setdefault(tick, []).append(f"t{tick} first divergence: {d['card']}, {d['what']}")
        return out

    def _cell(self, row: dict[str, Any]) -> list[int] | None:
        return row.get("truth") if self.side == RECORDING else row.get("sim")

    def _unit(self, row: dict[str, Any]) -> Unit | None:
        cell = self._cell(row)
        if not cell:
            return None  # the unit is not alive on this side at this tick
        key = int(row["key"])
        team, root = self._pairs.get(key, (0, str(row.get("card") or "")))
        name = root or str(row.get("card") or f"#{key}")
        x, y, hp, fourth, path_n, target = (int(v) for v in cell)
        deploying = self.side == RECORDING and fourth in DEPLOY_STATES
        return Unit(
            uid=key,
            team=team,
            kind=kind_of(name, self.names),
            name=name,
            x=x,
            y=y,
            hp=hp,
            max_hp=self._max_hp.get(key, 0),
            radius=0,
            flying=False,  # not in the file; the viewer draws no shadow rather than a wrong one
            deploy_ticks=1 if deploying else 0,
            stun_ticks=0,
            # Only the recording's target is a key this viewer can resolve; see the class.
            target=None if (target < 0 or self.side == ENGINE) else target,
            path=[],  # the file has the NUMBER of path nodes, not the nodes
            direction=None,
            state=fourth if self.side == RECORDING else None,
            extra={
                "path_n": path_n,
                "attacking": bool(fourth) if self.side == ENGINE else None,
                "apart": row.get("dist"),
                # The engine's own index for what it was shooting at, in the harness's list of
                # engine entities. Not a uid here, so it is a number to read rather than a line
                # to draw (see the class docstring).
                "engine_target_index": None if self.side != ENGINE or target < 0 else target,
            },
        )

    def _player(self, team: int) -> Player:
        """Everything a parity file does not carry, said as not carried rather than as zero."""
        return Player(
            team=team,
            elixir_milli=0,
            elixir_known=False,
            hand=[UNKNOWN_CARD] * HAND_SIZE,
            hand_known=False,
            next_card=None,
            cycle=[],
            deck=[],
            deck_known=False,
            crowns=0,
            tower_hp=[UNKNOWN_HP] * TOWER_SLOTS,
            tower_max_hp=[UNKNOWN_HP] * TOWER_SLOTS,
            king_active=None,
        )

    def frame(self) -> Frame | None:
        if not self.length:
            return None
        tick = self._ticks[self.index]
        units = [u for u in (self._unit(r) for r in self._by_tick[tick]) if u is not None]
        events = [line for t in self._ticks[: self.index + 1] for line in self._events.get(t, ())]
        return Frame(
            tick=tick,
            tick_ms=TICK_MS,
            units_per_tile=LIVE_UNITS_PER_TILE,
            players=[self._player(0), self._player(1)],
            units=units,
            spells=[],  # the trace rows are units; a spell in flight is not scored
            overtime=tick > REGULAR_TICKS,
            game_over=False,  # the file scores ticks, and says nothing about who won
            winner=NO_WINNER,
            crowns=[0, 0],
            events=events,
            meta={
                "source": "parity",
                "side": self.side,
                "fixture": self.fixture,
                "path": str(self.path),
                "index": self.index,
            },
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
        if not self.length:
            return f"{self.name}: no rows"
        unmatched = ""
        if any(self.unmatched):
            unmatched = f", {self.unmatched[0]}+{self.unmatched[1]} unmatched and not shown"
        return (
            f"{self.name} frame {self.index + 1}/{self.length}"
            f" tick {self._ticks[self.index]} ({len(self._pairs)} matched units{unmatched})"
        )

    def close(self) -> None:
        """Nothing to release: the file was read whole."""


def open_parity(path: str | Path, names: Names | None = None) -> list[ParitySource]:
    """Both sides of one parity file: the recording first, the engine ghosted over it.

    The recording leads because it is the thing being matched; the engine is the one that can
    be wrong, and the ghost is drawn as an outline over what actually happened.
    """
    names = names or Names.live()
    return [ParitySource(path, side, names) for side in SIDES]


#: What ``--parity`` compares within unless told otherwise: a quarter of a tile, the tightest
#: band RoyaleSim's own parity report scores ("within 250 native"), so the window and the
#: report answer the same question.
DEFAULT_TOLERANCE = 250
