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


def test_the_engine_s_target_is_not_pretended_to_be_a_unit_here(parity_file: Path) -> None:
    """The two target columns are different key spaces. The recording's is another recording
    key, which is a uid in this window. The engine's is an index into the harness's own list
    of engine entities, and the file publishes no way back from it, so drawing it as a target
    would point a line at whichever unit happened to hold that number."""
    rec, eng = open_parity(parity_file)
    a, b = rec.frame(), eng.frame()
    assert a.unit(7).target == 1  # a recording key, and unit 1 is in the frame
    assert b.unit(7).target is None
    assert b.unit(7).extra["engine_target_index"] == 1  # the raw value, named for what it is
    assert a.unit(7).extra["engine_target_index"] is None


def test_each_side_s_max_hp_is_its_own(tmp_path: Path) -> None:
    """The two sides are two simulations. Taking the engine's hp as the recording's maximum
    would draw a recording's bar against a number the recording never reached."""
    rows = [
        row(10, 7, "Knight", [3500, 8000, 900, 1, 0, -1], [3500, 8000, 1452, 0, 0, -1]),
        row(11, 7, "Knight", [3500, 8100, 880, 1, 0, -1], [3500, 8100, 1400, 0, 0, -1]),
    ]
    path = tmp_path / "hp.parity.json"
    path.write_text(json.dumps(report(rows, [(7, 0, "Knight")])), encoding="utf-8")
    rec, eng = open_parity(path)
    assert rec.frame().unit(7).max_hp == 900  # the most the RECORDING ever showed
    assert eng.frame().unit(7).max_hp == 1452


def test_overtime_is_the_same_tick_it_would_be_in_the_recording(tmp_path: Path) -> None:
    """A parity file's ticks are a recording's, so the same battle must not change its
    overtime by one tick depending on which source opened it."""
    from royaleviser import sources

    rows = [
        row(t, 7, "Knight", [3500, 8000, 900, 1, 0, -1], [3500, 8000, 900, 0, 0, -1])
        for t in (sources.LIVE_REGULAR_TICKS, sources.LIVE_REGULAR_TICKS + 1)
    ]
    path = tmp_path / "ot.parity.json"
    path.write_text(json.dumps(report(rows, [(7, 0, "Knight")])), encoding="utf-8")
    rec, _ = open_parity(path)
    assert not rec.frame().overtime  # exactly at regular time is not yet overtime
    rec.seek(1)
    assert rec.frame().overtime


def test_the_units_the_harness_could_not_match_are_counted_where_they_are_missing(
    tmp_path: Path,
) -> None:
    """The rows are matched pairs only, so an entity the harness could not match is in
    NEITHER side. A view that quietly dropped them would be at its most convincing exactly
    where the engine and the game agree least."""
    rows = [row(10, 7, "Knight", [3500, 8000, 900, 1, 0, -1], [3500, 8000, 900, 0, 0, -1])]
    d = report(rows, [(7, 0, "Knight")])
    d["unmatched_truth"] = [[9, "Cannon"], [11, "Skeletons"]]
    d["unmatched_sim"] = [[4, 0, "Cannon"]]
    path = tmp_path / "um.parity.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    rec, eng = open_parity(path)
    assert rec.unmatched == eng.unmatched == (2, 1)
    assert "2+1 unmatched and not shown" in rec.status()
    assert len(rec.frame().units) == 1  # and they are indeed not in the frame


def test_the_two_sides_are_compared_by_the_key_they_share(tmp_path: Path) -> None:
    """Pairing by name and distance is satisfied by the defect it should show: two units of
    one card that swap places pair with each other's positions and the tick reads as agreeing.
    Both sides of a parity trace key by the recording's entity key, so the comparison uses it.
    """
    from royaleviser.app import differing_by_uid, differing_within, keyed_rows
    from royaleviser.app import rows as row_list

    # Two Skeletons that swapped: each engine unit sits exactly where the OTHER one was.
    rows = [
        row(10, 21, "Skeletons", [3000, 9000, 81, 1, 0, -1], [4000, 9000, 81, 0, 0, -1]),
        row(10, 22, "Skeletons", [4000, 9000, 81, 1, 0, -1], [3000, 9000, 81, 0, 0, -1]),
    ]
    path = tmp_path / "swap.parity.json"
    path.write_text(
        json.dumps(report(rows, [(21, 0, "Skeletons"), (22, 0, "Skeletons")])), encoding="utf-8"
    )
    rec, eng = open_parity(path)
    a, b = rec.frame(), eng.frame()
    assert differing_within(row_list(a), row_list(b), 250) == (0, 0)  # the blind answer
    assert differing_by_uid(keyed_rows(a), keyed_rows(b), 250) == (2, 0)  # the true one

    by_name, by_key = Compare(tolerance=250), Compare(tolerance=250, pair_by_uid=True)
    for c in (by_name, by_key):
        c.note(0, a)
        c.note(1, b)
    assert by_name.results[10][2] == 0
    assert by_key.results[10][2] == 2
    # A uid one side does not have is never within any tolerance.
    assert differing_by_uid(keyed_rows(a), {}, 10**9) == (2, 0)


def test_the_command_line_pairs_a_parity_view_by_key(parity_file: Path) -> None:
    from royaleviser.app import App
    from royaleviser.render import ViewState

    args = build_parser().parse_args(["--parity", str(parity_file)])
    srcs = open_sources(args)
    app = App(srcs, ViewState(), tolerance=tolerance_of(args), pair_by_uid=True)
    assert app.agreement.pair_by_uid and app.agreement.tolerance == DEFAULT_TOLERANCE
    for s in srcs:
        s.close()
