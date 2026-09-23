"""The engine and the battle it was calibrated on, side by side in the window.

    python -m royaleviser --parity <name>.parity.json

RoyaleSim's replay harness plays a recorded battle through the engine and scores the two
per-tick states against each other. Run with ``--trace`` it also writes a row per scored
MATCHED pair: the tick, the unit, where the RECORDING had it and where the ENGINE put it.
This module turns each side of those rows into a viewer source, so a figure like "84.6 % of
unit-ticks within a quarter tile" -- what the harness's own aggregate over the recordings on
this machine reports, `Score.within[0]` over `unit_ticks` -- can be looked at instead of read:
WHERE do they part, and what was the unit doing when they did.

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
fallback size. No maximum hp, so no hp bars: the most a unit was seen with is a lower bound,
and it is in the inspector under a name that says so rather than standing in for a maximum.
The path is a COUNT of nodes, not the nodes, so it rides in the inspector's extra rather than
being drawn as a path that was never recorded. Tower slots are not named in
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

    Feed this the entity's OWN card, never the root it was attributed to. A Tombstone's
    Skeletons carry the Tombstone's card id in both the recording and the harness's `root`,
    and 27000009 is inside the building range, so rooting them would draw four skeletons as
    four buildings, each with a guessed footprint and the marks that go with one.
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
            # An unplayable fixture is never played, so no flag would have produced rows. Its
            # report says why in a field this would otherwise talk over. A report that IS
            # playable and carries `unplayable_reasons` was played up to a cut (`prefix_until`)
            # and does get rows under --trace, so only `playable: false` takes this branch.
            if report.get("playable") is False:
                why = "; ".join(str(r) for r in (report.get("unplayable_reasons") or ()))
                raise ValueError(
                    f"{self.path.name} is a report on a fixture the harness could not play"
                    + (f": {why}" if why else ", and it does not say why")
                )
            raise ValueError(
                f"{self.path.name} has no per-tick rows: write it with the harness's --trace"
            )
        self.fixture = str(report.get("fixture") or self.path.stem)
        self.name = f"{self.side} {self.fixture}"
        # key -> (team, root card, the engine entity's OWN card, how it was rooted). The root
        # is what both sides call the unit, because a recording carries the root's card id for
        # a spawned unit too; ``sim_card`` is the only place the file names the entity itself,
        # and that is what decides whether a box gets drawn under it.
        pairs = {
            int(p["truth_key"]): (
                int(p["side"]),
                str(p["root"]),
                str(p.get("sim_card") or p["root"]),
                str(p.get("root_how") or ""),
            )
            for p in report["pairs"]
        }
        self._by_tick: dict[int, list[dict[str, Any]]] = {}
        # The most hp this side was ever seen with. That is a LOWER BOUND on the unit's
        # maximum, not the maximum: a parity report carries no max hp at all, the recording's
        # own frames are up to a dozen ticks apart, and a unit whose rows begin mid-life is
        # never once seen whole. A crown tower is the case that matters: one whose rows start
        # after it has taken damage is never recorded at full hp for the whole battle, and a
        # tower at half health drawn as untouched is a worse lie than a skeleton doing it.
        #
        # So it does not become ``max_hp``. The renderer draws an hp bar only when
        # ``0 <= hp < max_hp``, so a max_hp of the most-seen value would draw a half-dead
        # tower as an untouched one and, once a later hit landed, draw a bar against a maximum
        # the unit never had. ``max_hp`` stays 0, which is this viewer's word for "not in this
        # source", and the bound goes in the inspector where it is labelled.
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
        # The tick the ENGINE declared the battle over. The harness stops ticking it there and
        # keeps snapshotting the frozen state, and scores everything past it as
        # ``after_engine_end`` because it is frozen. Without this the tail reads as a large
        # unexplained divergence, which is the one part of the battle the file has already
        # told the viewer not to score. It is NOT game_over: the report has no winner, and
        # game_over with no winner draws "draw" on the board, which would be inventing one.
        end = report.get("engine_end_tick")
        self.engine_end_tick: int | None = None if end is None else int(end)
        # How far the harness was asked to play, when it was cut short of the whole battle.
        cut = report.get("prefix_until")
        self.prefix_until: int | None = None if cut is None else int(cut)
        self._ticks = sorted(self._by_tick)
        self.length = len(self._ticks)
        self.index = 0
        # Keyed by the report's own ticks, then SNAPPED to the ticks that actually have rows.
        # The report dates its events by the battle's clock and the rows are only the scored
        # ticks, which for a strided or gappy trace are not the same set: an event on a tick
        # with no row was looked up by frame() and silently dropped, so the engine's end, the
        # prefix cut and both divergence lines could all vanish. Every one of them is the
        # reason someone opened the file.
        self._events = self._snap_to_scored_ticks(self._divergence_events(report))

    def _snap_to_scored_ticks(self, events: dict[int, list[str]]) -> dict[int, list[str]]:
        """Move each event onto the first scored tick at or after its own, so none is lost.

        ``frame()`` can only show an event on a tick it draws, and the ticks it draws are the
        ones with trace rows. An event the report dates between two of them, or after the last
        one, belongs to the battle just as much; it appears at the first frame a reader can
        scrub to from there. Each line already carries its true tick in its own text, so
        nothing is misdated by the move.
        """
        if not self._ticks:
            return {}
        out: dict[int, list[str]] = {}
        for tick, lines in sorted(events.items()):
            at = self._ticks[self.index_at_tick(tick)]
            out.setdefault(at, []).extend(lines)
        return out

    def _divergence_events(self, report: dict[str, Any]) -> dict[int, list[str]]:
        """The harness's own reading of where this battle first parted, on the tick it says.

        It is the one line in the report that names a MOMENT rather than a total, so it
        belongs in the events list where a reader can scrub to it.

        THE ONSET LINE IS NOT ALWAYS THERE, and that is the point. Only one of the harness's
        four divergence paths measures a growing position error and dates the moment it began;
        the other three are a pair alive on one side only, an unmatched recording entity and an
        unmatched engine entity, and all three set the onset equal to the tick and carry no
        distance at all. Announcing an error that passed a threshold on those would be a
        position claim the same file contradicts, printed on the same tick as the real line.
        The position case is the one whose ``what`` begins "position error"; nothing else in
        the report says which path it came from.
        """
        out: dict[int, list[str]] = {}
        if self.engine_end_tick is not None:
            out.setdefault(self.engine_end_tick, []).append(
                f"t{self.engine_end_tick} the engine ended the battle; "
                "its side is frozen from here and the harness stops scoring it"
            )
        if self.prefix_until is not None:
            out.setdefault(self.prefix_until, []).append(
                f"t{self.prefix_until} the harness was only asked to play this far"
            )
        d = report.get("first_divergence")
        if not d:
            return out
        onset, tick = int(d.get("onset_tick", d["tick"])), int(d["tick"])
        what = str(d.get("what") or "")
        if what.startswith("position error") and onset != tick:
            out.setdefault(onset, []).append(
                f"t{onset} the gap starts opening: {d['card']} ({d['cause']})"
            )
        out.setdefault(tick, []).append(f"t{tick} first divergence: {d['card']}, {what}")
        return out

    def _cell(self, row: dict[str, Any]) -> list[int] | None:
        return row.get("truth") if self.side == RECORDING else row.get("sim")

    def first_divergence(
        self, key: int | None = None, tolerance: int = 0
    ) -> tuple[int, int, int | None] | None:
        """The earliest tick where the two sides disagree: ``(tick, key, distance)``.

        WHY THIS AND NOT THE DIFFERENCE COUNT. A count says how far apart the two runs are
        NOW, which after the first disagreement is mostly accumulated drift: a unit pushed
        wrongly at tick 900 is still in the wrong place at 1400 without anything new having
        gone wrong. For a causal law -- contact, spawn point, death timing -- the question is
        which tick FIRST disagreed, because that is the only one where the inputs on both
        sides were still the same.

        ``key`` restricts it to one unit, which is the pinned-unit case. Without it this is
        the first disagreement in the battle, whoever it belongs to.

        ``distance`` is in raw units, or None when the unit exists on one side and not the
        other at that tick -- a presence difference rather than a position one, which no
        tolerance can excuse and which is reported rather than folded into a number. Both
        sides missing the unit is not a disagreement and is skipped.

        Reads the rows the file already holds, so it costs one pass and no decoding.
        """
        for tick in self._ticks:
            for row in self._by_tick[tick]:
                if key is not None and int(row["key"]) != key:
                    continue
                truth, sim = row.get("truth"), row.get("sim")
                if not truth and not sim:
                    continue
                if not truth or not sim:
                    return tick, int(row["key"]), None
                dx, dy = int(truth[0]) - int(sim[0]), int(truth[1]) - int(sim[1])
                d2 = dx * dx + dy * dy
                if d2 > tolerance * tolerance:
                    return tick, int(row["key"]), int(d2**0.5)
        return None

    def _unit(self, row: dict[str, Any]) -> Unit | None:
        cell = self._cell(row)
        if not cell:
            return None  # the unit is not alive on this side at this tick
        key = int(row["key"])
        team, root, own_card, how = self._pairs.get(
            key, (0, str(row.get("card") or ""), str(row.get("card") or ""), "")
        )
        name = root or str(row.get("card") or f"#{key}")
        x, y, hp, fourth, path_n, target = (int(v) for v in cell)
        deploying = self.side == RECORDING and fourth in DEPLOY_STATES
        return Unit(
            uid=key,
            team=team,
            kind=kind_of(own_card, self.names),
            name=name,
            x=x,
            y=y,
            hp=hp,
            max_hp=0,  # a parity file has no maximum; see _max_hp above
            # The engine's own collision radius for THIS entity, which a recording does not
            # carry at all. Applied to BOTH sides deliberately: it is a static property of the
            # unit rather than an observation of either run, and the two sides are the same
            # unit. Per-unit rather than per-card because a summoned unit has its own radius
            # and the row's card is the ROOT that produced it -- a card table would have drawn
            # a Witch's Skeletons as Witches, wrong in exactly the crowded cases contact is
            # about (sim's reasoning, RoyaleSim d4a6f5e). 0 when the row does not carry it,
            # which is this viewer's word for "not in this source" and makes the contact ring
            # refuse rather than draw an empty one.
            radius=int(row["radius"]) if row.get("radius") is not None else 0,
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
                # The separation step the ENGINE applied on this tick: (dx, dy, neighbours),
                # clamped, i.e. the value that explains the position delta rather than what the
                # law wanted before the 150 cap. RoyaleSim 786c738 gives it its own optional
                # row field precisely so ABSENT and ZERO stay different: most ticks nothing
                # overlaps and the push is a real (0, 0, 0), while a row with no engine entry
                # carries no `push` key at all. None here means the file did not say; a triple
                # means it did, whatever the numbers are. The recording has no such thing, so
                # this is engine-side only.
                "push": (
                    tuple(row["push"])
                    if self.side == ENGINE and row.get("push") is not None
                    else None
                ),
                "attacking": bool(fourth) if self.side == ENGINE else None,
                "apart": row.get("dist"),
                # The engine's own index for what it was shooting at, in the harness's list of
                # engine entities. Not a uid here, so it is a number to read rather than a line
                # to draw (see the class docstring).
                "engine_target_index": None if self.side != ENGINE or target < 0 else target,
                # The most hp this side was ever seen with, which is a lower bound on the
                # unit's maximum and not the maximum. Named so nobody reads it as one.
                "most_hp_seen": self._max_hp.get(key, 0),
                # What the engine calls this entity, and how the harness attributed it to the
                # card above. A spawned unit is named for its spawner on both sides, because
                # that is the card a recording carries for it; this is where its own is.
                "engine_card": own_card,
                "rooted_how": how,
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
