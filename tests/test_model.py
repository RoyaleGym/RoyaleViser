"""The frame contract: construction, the names tables, the wire codec, the layout, the CLI."""

from __future__ import annotations

import itertools

import pytest

from royalegym.protocol import CardInfo, EntityKind, Placement, SpellMotion
from royaleviser import __main__ as cli
from royaleviser import model, theme
from royaleviser.model import Frame, Names, Player, Source, Spell, Unit
from royaleviser.render import ViewState


def player(team: int, known: bool = True) -> Player:
    return Player(
        team=team,
        elixir_milli=5500,
        elixir_known=True,
        hand=["Knight", "Archer", "Fireball", "Musketeer"] if known else ["?"] * 4,
        hand_known=known,
        next_card="Giant" if known else None,
        cycle=["Zap", "Log", "Cannon"] if known else [],
        deck=["Knight", "Archer", "Fireball", "Musketeer", "Giant", "Zap", "Log", "Cannon"]
        if known
        else [],
        deck_known=known,
        crowns=0,
        tower_hp=[4008, 2534, 2534],
        tower_max_hp=[4008, 2534, 2534],
        king_active=False,
    )


def frame() -> Frame:
    return Frame(
        tick=1234,
        tick_ms=model.TICK_MS,
        units_per_tile=model.LIVE_UNITS_PER_TILE,
        players=[player(0), player(1, known=False)],
        units=[
            Unit(
                uid="140001:26000000:0",
                team=0,
                kind=model.KIND_TROOP,
                name="Knight",
                x=3500,
                y=14500,
                hp=1200,
                max_hp=1452,
                radius=500,
                flying=False,
                deploy_ticks=0,
                stun_ticks=0,
                target="9:-1:1",
                path=[(3500, 14500), (3500, 17500), (3500, 25500)],
                direction=(0, 256),
                state=2,
                extra={"behavior_state": 2, "level": 11},
            ),
            Unit(
                uid="9:-1:1",
                team=1,
                kind=model.KIND_PRINCESS_TOWER,
                name="PrincessTower",
                x=3500,
                y=25500,
                hp=2534,
                max_hp=2534,
                radius=0,
                flying=False,
                deploy_ticks=0,
                stun_ticks=0,
                target=None,
                path=[],
                direction=None,
                state=None,
            ),
        ],
        spells=[
            Spell(
                team=1,
                name="Fireball",
                motion=SpellMotion.FLIGHT,
                x=9000,
                y=20000,
                aim_x=3500,
                aim_y=14500,
            )
        ],
        overtime=False,
        game_over=False,
        winner=model.NO_WINNER,
        crowns=[0, 0],
        events=["t1200 spawn Blue Knight (3.5, 14.5)"],
        meta={"source": "test", "seq": 7},
    )


def test_kinds_are_the_protocols() -> None:
    assert model.KIND_TROOP == EntityKind.TROOP
    assert model.KIND_BUILDING == EntityKind.BUILDING
    assert model.KIND_KING_TOWER == EntityKind.KING_TOWER
    assert model.KIND_PRINCESS_TOWER == EntityKind.PRINCESS_TOWER


def test_dataclasses_use_slots() -> None:
    for cls in (Frame, Player, Unit, Spell, ViewState):
        assert "__slots__" in cls.__dict__, cls
    f = frame()
    with pytest.raises(AttributeError):
        f.stray = 1  # type: ignore[attr-defined]


def test_frame_is_sound_and_helpers() -> None:
    f = frame()
    assert model.problems(f) == []
    assert f.clock_ms == 1234 * 50
    assert f.player(1).hand_known is False
    assert f.unit("9:-1:1") is not None and f.unit("9:-1:1").kind == model.KIND_PRINCESS_TOWER
    assert f.unit("nobody") is None


def test_problems_name_the_breakage() -> None:
    f = frame()
    f.players[0].hand = ["Knight"]
    f.units.append(f.units[0])
    f.units[1].team = 2
    out = model.problems(f)
    assert any("players[0].hand has 1" in p for p in out)
    assert any("appears twice" in p for p in out)
    assert any("team 2" in p for p in out)
    assert model.problems(Frame(0, 50, 0, [], [], [], False, False, -1, [])) == [
        "units_per_tile 0 must be positive",
        "0 players, expected 2",
        "crowns has 0 entries, expected 2",
    ]


def test_wire_round_trip() -> None:
    f = frame()
    data = model.encode_frame(f)
    assert isinstance(data, bytes) and len(data) < 2000
    back = model.decode_frame(data)
    assert back == f
    assert back.units[0].path == [(3500, 14500), (3500, 17500), (3500, 25500)]
    assert back.units[0].direction == (0, 256)


def test_live_names_table() -> None:
    names = Names.live()
    assert len(names) > 100
    assert names.name_of(26000000) == "Knight"
    assert names.cost_of(26000000) == 3
    assert names.name_of(203000014) == "Musketeer"  # the hero form, model.LIVE_FORMS
    assert names.cost_of(203000014) == names.cost_of(26000014) == 4
    assert names.name_of(27000000) == "Cannon" and names.name_of(28000000) == "Fireball"
    assert names.name_of(123) == "#123" and names.cost_of(123) is None
    assert 26000000 in names and 123 not in names
    assert names.cost_of_name("Knight") == 3 and names.cost_of_name("Nobody") is None


def test_names_from_a_card_catalogue() -> None:
    cards = [
        CardInfo(0, "Knight", 3, Placement.TROOP, 1, 9000, False, 1452),
        CardInfo(1, "Fireball", 4, Placement.SPELL, 0, 0, False, 0),
    ]
    names = Names.from_cards(cards)
    assert names.name_of(0) == "Knight" and names.cost_of(1) == 4
    assert names.name_of(2) == "#2"
    assert len(Names()) == 0


class _Replay:
    name = "fake"
    live = False
    units_per_tile = 1000
    length = 3
    index = 0

    def frame(self) -> Frame | None:
        return None

    def seek(self, index: int) -> None:
        self.index = index

    def step(self, delta: int) -> None:
        self.index += delta

    def status(self) -> str:
        return "fake"

    def close(self) -> None:
        pass


def test_source_protocol_is_structural() -> None:
    assert isinstance(_Replay(), Source)
    assert not isinstance(object(), Source)


def test_layout_is_the_old_arrangement() -> None:
    lay = theme.layout(theme.DEFAULT, scale=24, tiles=(18, 32))
    assert lay.arena == (345, 5, 18 * 24, 32 * 24)
    assert lay.inspector[0] == 345 + 432 + 5
    assert lay.window[0] == 345 + 432 + 5 + 300
    # The hands are flush with the window's left edge and its top / bottom edge, and end
    # a gutter short of the arena (4 cards of 80 + 3 gaps of 5 = 335 < 345).
    assert lay.top_hand[:2] == (0, 0) and lay.top_hand[2] == 335
    assert lay.bottom_hand[0] == 0 and lay.bottom_hand[1] + lay.bottom_hand[3] == lay.window[1]
    assert lay.top_hand[0] + lay.top_hand[2] < lay.arena[0]
    assert lay.timer[3] <= 2 * 24  # the crowns-and-clock box is a small one
    assert lay.window[1] >= 32 * 24 + theme.DEFAULT.status_h + theme.DEFAULT.timeline_h
    assert lay.status[1] == lay.arena[1] + lay.arena[3] + 5
    assert lay.timeline[1] > lay.status[1]
    # The dashboard stacks without overlap: top hand, top elixir, debug, bottom elixir, bottom hand.
    stack = [lay.top_hand, lay.top_elixir, lay.debug, lay.bottom_elixir, lay.bottom_hand]
    for above, below in itertools.pairwise(stack):
        assert above[1] + above[3] <= below[1], (above, below)
    assert lay.debug[3] > 0
    assert lay.bottom_hand[1] + lay.bottom_hand[3] <= lay.window[1]
    assert lay.timer[0] + lay.timer[2] <= lay.arena[0] + lay.arena[2]
    assert lay.hover[1] + lay.hover[3] <= lay.events[1]
    assert lay.events[1] + lay.events[3] <= lay.window[1]
    assert theme.layout(theme.DEFAULT, scale=12, tiles=(18, 32)).arena[2] == 216


def test_theme_colours() -> None:
    t = theme.DEFAULT
    assert t.team_color(0) == (71, 204, 218) and t.team_color(1) == (224, 73, 41)
    assert t.team_color(1, king=True) == (204, 53, 21)
    assert t.hp_color(100, 100) == t.hp_high
    assert t.hp_color(60, 100) == t.hp_mid  # 60 % is not above 60 %
    assert t.hp_color(31, 100) == t.hp_mid
    assert t.hp_color(30, 100) == t.hp_low
    assert t.hp_color(1, 0) == t.hp_bg


def test_view_state_defaults() -> None:
    v = ViewState()
    assert (v.seat, v.show_paths, v.show_targets, v.show_grid, v.show_debug) == (
        0,
        True,
        True,
        False,
        False,
    )
    assert v.hover_uid is None and v.compare_frame is None


def test_cli_parser() -> None:
    assert cli.parse_geometry("712x1029+604+0") == (712, 1029, 604, 0)
    assert cli.parse_geometry("800x600") == (800, 600, None, None)
    assert cli.parse_geometry("800x600-10+20") == (800, 600, -10, 20)
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli.parse_geometry("wide")
    assert cli.parse_seat("local") == "local" and cli.parse_seat("1") == 1
    with pytest.raises(cli.argparse.ArgumentTypeError):
        cli.parse_seat("2")
    assert cli.parse_endpoint("127.0.0.1:9870") == ("127.0.0.1", 9870)
    assert cli.parse_endpoint(":9870") == ("127.0.0.1", 9870)

    p = cli.build_parser()
    a = p.parse_args(
        [
            "a.jsonl.gz",
            "--compare",
            "b.jsonl",
            "--seat",
            "0",
            "--shot",
            "s.png",
            "--seconds",
            "2",
            "--speed",
            "4",
            "--start-tick",
            "600",
            "--scale",
            "12",
        ]
    )
    assert a.sources == ["a.jsonl.gz"] and a.compare == "b.jsonl" and a.seat == 0
    assert (a.shot, a.seconds, a.speed, a.start_tick, a.scale) == ("s.png", 2.0, 4.0, 600, 12)
    a = p.parse_args(["--stream", "127.0.0.1:9870", "--geometry", "712x1029+604+0"])
    assert a.stream == ("127.0.0.1", 9870) and a.geometry == (712, 1029, 604, 0)
    # The view options are one group a front end can add to its own parser (RoyaleLive's
    # viser_live.py does): the same names, the same defaults.
    q = cli.argparse.ArgumentParser()
    cli.add_view_arguments(q)
    b = q.parse_args(["--seat", "1", "--geometry", "800x600"])
    assert (b.seat, b.geometry, b.speed, b.scale) == (1, (800, 600, None, None), 1.0, None)
    assert vars(q.parse_args([])) == {
        k: v for k, v in vars(p.parse_args([])).items() if k not in ("sources", "stream", "compare")
    }
    assert p.parse_args([]).seat == "local"
    for key, action in cli.KEYS:
        assert key and action
    with pytest.raises(SystemExit):
        cli.main([])  # no source at all
    with pytest.raises(FileNotFoundError):
        cli.main(["a.jsonl"])  # sources are opened before any window exists
    with pytest.raises(SystemExit):
        cli.main(["a.jsonl", "b.jsonl", "--compare", "c.jsonl"])  # three sources
