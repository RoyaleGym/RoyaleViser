"""The two sides of a RoyaleSim parity trace as viewer sources.

The fixture here is written by hand in the row shape the harness serialises (its ``Report``
and ``TraceRow``), because a real one needs the private recordings and a built engine. What
that buys and what it does not: the tests below pin how this module READS a file of that
shape, and they cannot tell anyone whether the shape is still the harness's. The single test
that would -- opening a file the harness itself wrote -- is
``test_a_file_the_harness_itself_wrote_opens``, which SKIPS where no results file with rows
is on disk, and says so. A skip there is not a pass.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from royaleviser import model
from royaleviser.__main__ import build_parser, open_sources, source_specs, tolerance_of
from royaleviser.app import Compare
from royaleviser.parity import (
    DEFAULT_TOLERANCE,
    ENGINE,
    RECORDING,
    ParitySource,
    is_parity,
    kind_of,
    open_parity,
)

# Where a real results file would be: RoyaleSim's gitignored data folder, as the parity gate
# reads it. Absent, the harness-written test skips.
SIM_DATA = Path(
    os.environ.get("ROYALESIM_DATA_DIR") or Path(__file__).resolve().parents[2] / "RoyaleSim/data"
)


def row(tick: int, key: int, card: str, truth: list[int] | None, sim: list[int] | None) -> dict:
    """One TraceRow: truth [x, y, hp, state, path_n, target], sim [x, y, hp, attacking, ...]."""
    dist = None
    if truth and sim:
        dist = int(((truth[0] - sim[0]) ** 2 + (truth[1] - sim[1]) ** 2) ** 0.5)
    return {"tick": tick, "key": key, "card": card, "truth": truth, "sim": sim, "dist": dist}


def report(rows: list[dict], pairs: list[tuple[int, int, str]], divergence: dict | None = None):
    return {
        "fixture": "20260918-112215",
        "capture": "20260918-112215",
        "playable": True,
        "last_tick": max((r["tick"] for r in rows), default=0),
        "pairs": [
            {
                "truth_key": key,
                "side": side,
                "root": root,
                "sim_index": i,
                "sim_generation": 0,
                "sim_card": root,
                "root_how": "tower" if root.endswith("Tower") else "deploy",
                "truth_first_tick": 0,
                "sim_first_tick": 0,
            }
            for i, (key, side, root) in enumerate(pairs)
        ],
        "first_divergence": divergence,
        "trace": rows,
    }


@pytest.fixture
def parity_file(tmp_path: Path) -> Path:
    """Three ticks of one battle: a tower that never moves, a Knight the engine puts a quarter
    of a tile behind the recording, and a Cannon that the engine never spawned."""
    rows = []
    for i, tick in enumerate((100, 101, 102)):
        rows.append(
            row(tick, 1, "KingTower", [9000, 3000, 4824, 0, 0, -1], [9000, 3000, 4824, 0, 0, -1])
        )
        rows.append(
            row(
                tick,
                7,
                "Knight",
                [3500, 8000 + 100 * i, 1452, 1, 4, 1],
                [3500, 7800 + 100 * i, 1452, 0, 4, 1],
            )
        )
        rows.append(row(tick, 9, "Cannon", [14500, 20000, 380 - i, 0, 0, -1], None))
    d = {
        "tick": 102,
        "truth_key": 7,
        "side": 0,
        "card": "Knight",
        "families": [],
        "what": "1100 native apart",
        "cause": "walking",
        "onset_tick": 101,
        "detail": "",
    }
    path = tmp_path / "20260918-112215.parity.json"
    path.write_text(
        json.dumps(report(rows, [(1, 0, "KingTower"), (7, 0, "Knight"), (9, 1, "Cannon")], d)),
        encoding="utf-8",
    )
    return path


def test_each_side_is_the_battle_that_side_had(parity_file: Path) -> None:
    rec, eng = open_parity(parity_file)
    assert (rec.side, eng.side) == (RECORDING, ENGINE)
    assert rec.length == eng.length == 3
    assert rec.units_per_tile == eng.units_per_tile == 1000  # native millitiles, both sides
    a, b = rec.frame(), eng.frame()
    assert a is not None and b is not None
    assert model.problems(a) == [] and model.problems(b) == []
    assert a.tick == b.tick == 100

    knight_a = a.unit(7)
    knight_b = b.unit(7)
    assert (knight_a.x, knight_a.y) == (3500, 8000)
    assert (knight_b.x, knight_b.y) == (3500, 7800)  # the engine's own position, not converted
    assert knight_a.team == knight_b.team == 0 and knight_a.name == "Knight"
    assert knight_a.kind == model.KIND_TROOP and knight_a.target == 1

    # A unit one side never had is simply not in that side's frame, which is what makes the
    # compare count it rather than pairing it with something.
    assert a.unit(9) is not None and b.unit(9) is None
    assert a.unit(9).team == 1 and a.unit(9).kind == model.KIND_BUILDING  # 27xxxxxx: a building
    assert a.unit(1).kind == model.KIND_KING_TOWER

    # What a parity file does not carry is said as not carried.
    assert all(not p.elixir_known and not p.hand_known and not p.deck_known for p in a.players)
    assert a.players[0].tower_hp == [model.UNKNOWN_HP] * 3
    assert not a.game_over and a.winner == model.NO_WINNER and a.spells == []
    assert all(u.footprint is None and u.radius == 0 and u.path == [] for u in a.units)
    assert knight_a.extra["path_n"] == 4 and knight_a.extra["apart"] == 200
    assert knight_a.state == 1 and knight_b.state is None  # the sim column is not a state
    assert knight_b.extra["attacking"] is False


def test_max_hp_is_the_most_that_unit_was_ever_seen_with(parity_file: Path) -> None:
    """A parity file has hp and no maximum, and an hp bar needs both. The most the unit was
    ever seen with is what the file itself supports; nothing is read out of a card table."""
    rec, _ = open_parity(parity_file)
    rec.seek(2)
    f = rec.frame()
    assert f.unit(9).hp == 378 and f.unit(9).max_hp == 380  # 380 on the first tick
    assert f.unit(1).max_hp == 4824


def test_the_harness_s_reading_of_where_they_parted_is_an_event(parity_file: Path) -> None:
    """The report's first_divergence is its only statement about a MOMENT, so it goes where a
    reader can scrub to it rather than staying in a total."""
    rec, _ = open_parity(parity_file)
    assert rec.frame().events == []
    rec.seek(rec.index_at_tick(101))
    assert rec.frame().events == ["t101 error passes 250: Knight (walking)"]
    rec.seek(2)
    assert rec.frame().events == [
        "t101 error passes 250: Knight (walking)",
        "t102 first divergence: Knight, 1100 native apart",
    ]


def test_the_two_sides_seek_to_the_same_tick(parity_file: Path) -> None:
    rec, eng = open_parity(parity_file)
    for tick in (100, 101, 102):
        rec.seek(rec.index_at_tick(tick))
        eng.seek(eng.index_at_tick(tick))
        assert rec.frame().tick == eng.frame().tick == tick
    assert rec.index_at_tick(10**9) == 2  # past the end clamps, as every replay source does
    assert "frame 3/3" in rec.status() and "3 matched units" in rec.status()


def test_the_comparison_reads_as_how_far_apart_within_a_quarter_tile(parity_file: Path) -> None:
    """The whole point of the view: a quarter-tile tolerance turns "everything differs" into
    the one unit that is actually missing."""
    rec, eng = open_parity(parity_file)
    exact, loose = Compare(), Compare(tolerance=DEFAULT_TOLERANCE)
    for c in (exact, loose):
        c.note(0, rec.frame())
        c.note(1, eng.frame())
    # Exact: the Knight is 200 millitiles off, so it differs too, and the Cannon is missing.
    assert exact.results[100][2] == 2
    assert loose.results[100][2] == 1  # only the Cannon the engine never spawned
    assert "within 0.25 tiles" in loose.text(100)


def test_a_file_without_rows_says_which_flag_writes_them(tmp_path: Path) -> None:
    """Running the harness without --trace gives a report with every total and no rows, which
    would otherwise open as an empty window."""
    path = tmp_path / "empty.parity.json"
    path.write_text(json.dumps(report([], [])), encoding="utf-8")
    with pytest.raises(ValueError, match="--trace"):
        ParitySource(path)
    with pytest.raises(ValueError, match="parity side"):
        ParitySource(path, "neither")


def test_the_command_line_opens_both_sides_and_compares_within_a_quarter_tile(
    parity_file: Path,
) -> None:
    args = build_parser().parse_args(["--parity", str(parity_file)])
    assert source_specs(args) == [("parity", str(parity_file))]
    assert tolerance_of(args) == DEFAULT_TOLERANCE
    srcs = open_sources(args)
    assert [s.side for s in srcs] == [RECORDING, ENGINE]
    for s in srcs:
        s.close()
    # An explicit tolerance wins, including an exact one.
    assert (
        tolerance_of(build_parser().parse_args(["--parity", str(parity_file), "--tolerance", "0"]))
        == 0
    )
    assert tolerance_of(build_parser().parse_args(["a.jsonl"])) == 0
    assert tolerance_of(build_parser().parse_args(["a.jsonl", "--tolerance", "500"])) == 500


def test_the_names_a_kind_comes_from(parity_file: Path) -> None:
    names = model.Names.live()
    assert kind_of("KingTower", names) == model.KIND_KING_TOWER
    assert kind_of("PrincessTower", names) == model.KIND_PRINCESS_TOWER
    assert kind_of("Cannon", names) == model.KIND_BUILDING
    assert kind_of("Knight", names) == model.KIND_TROOP
    # A name the table does not have is a troop: a box is a claim about the ground a unit
    # stands on, and this module has no evidence for one.
    assert kind_of("NoSuchCard", names) == model.KIND_TROOP
    assert names.id_of("Cannon") == 27000000 and names.id_of("NoSuchCard") is None
    assert is_parity("a.PARITY.json") and not is_parity("a.json")


def test_a_file_the_harness_itself_wrote_opens() -> None:
    """The one test that can say the shape above is still the harness's. It needs a results
    file with rows, which needs the recordings and a built engine, so it skips where those
    are not on this machine -- and a skip here is not a pass."""
    folder = SIM_DATA / "derived" / "replay" / "results"
    files = sorted(folder.glob("*.parity.json")) if folder.is_dir() else []
    with_rows = [p for p in files if '"trace"' in p.read_text(encoding="utf-8")[:2000000]]
    if not with_rows:
        pytest.skip(
            f"no parity results file with per-tick rows under {folder} "
            "(write one with the replay harness's --trace)"
        )
    rec, eng = open_parity(with_rows[0])
    assert rec.length == eng.length > 0
    f = rec.frame()
    assert f is not None and model.problems(f) == [] and f.units
