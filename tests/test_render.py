"""The renderer and the app loop, headless (SDL_VIDEODRIVER=dummy) on the synthetic battle.

Draw time for a 100-troop frame is measured and printed (``-s``): the best of 60 draws must
be under 8 ms (this laptop, 2026-09-20: 3.8-5.1 ms at scale 24 with paths, targets and
labels on; the mean was 5.1 ms on a quiet machine and 9-11 ms on a busy one).
"""

from __future__ import annotations

import copy
import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pygame
import pytest

from royaleviser import app, model, sources
from royaleviser.app import KEYS, App, Compare, compare_text, fit_layout, fit_scale, run
from royaleviser.render import (
    LEARNING_GROUPS,
    UNSET,
    Board,
    Learning,
    Renderer,
    Transport,
    ViewState,
    clock_text,
    default_board,
    elixir_text,
    extra_text,
    fit_text,
    learning_run,
    split_name,
)
from royaleviser.theme import DEFAULT, layout
from synthetic import ListSource, battle, dense_frame, frame_at

FRAMES = battle()
CHECKPOINT_TICKS = (0, 215, 1015, 1035, 1405, 2050, 2390)


@pytest.fixture(scope="module")
def renderer() -> Renderer:
    return Renderer(scale=24, help_lines=KEYS)


def transport(index: int) -> Transport:
    return Transport(source_name="synthetic", length=len(FRAMES), index=index, playing=True)


def test_synthetic_frames_meet_the_contract() -> None:
    assert len(FRAMES) == 2400
    for f in FRAMES[::50]:
        assert model.problems(f) == [], f.tick
    assert FRAMES[2050].overtime and not FRAMES[1990].overtime
    assert FRAMES[-1].game_over and FRAMES[-1].winner == 0
    assert FRAMES[-1].crowns == [1, 0]
    assert any("plays" in e for e in FRAMES[-1].events)
    assert any("death" in e for e in FRAMES[-1].events)
    assert FRAMES[1015].spells and FRAMES[1015].spells[0].motion == 0
    assert FRAMES[1035].spells and FRAMES[1035].spells[0].motion == 3
    knight = FRAMES[1405].unit("knight")
    assert knight is not None and knight.stun_ticks > 0
    assert FRAMES[215].unit("archers").deploy_ticks > 0
    assert FRAMES[215].unit("knight").path


@pytest.mark.parametrize("scale", [24, 12])
@pytest.mark.parametrize("seat", [0, 1])
def test_draws_every_checkpoint_without_error(scale: int, seat: int) -> None:
    r = Renderer(scale=scale, help_lines=KEYS)
    assert r.surface.get_size() == layout(DEFAULT, scale, (18, 32)).window
    for toggles in ({}, {"show_grid": True, "show_debug": True, "show_help": True}):
        view = ViewState(seat=seat, hover_uid="knight", **toggles)
        for i in CHECKPOINT_TICKS:
            f = FRAMES[i]
            view.compare_frame = copy.deepcopy(f)
            view.compare_text = compare_text(f, view.compare_frame)
            r.draw(f, view, transport(i))
    assert r.surface.get_at((r.layout.arena[0] + 1, r.layout.arena[1] + 1))[:3] in (
        DEFAULT.grass_light,
        DEFAULT.grass_dark,
        DEFAULT.tower_zone,
    )


def test_unknown_hand_and_unknown_hp_draw() -> None:
    r = Renderer(scale=24)
    f = copy.deepcopy(FRAMES[600])
    p = f.players[1]
    p.hand, p.hand_known, p.next_card, p.cycle, p.deck, p.deck_known = (
        ["?"] * 4,
        False,
        None,
        [],
        [],
        False,
    )
    p.tower_hp = [model.UNKNOWN_HP] * 3
    f.units[0].hp = model.UNKNOWN_HP
    r.draw(f, ViewState(), transport(600))
    r.draw(f, ViewState(seat=1), Transport(source_name="live", live=True, source_status="seq 9"))


def test_team_0_king_is_drawn_at_its_pixel_for_both_seats(renderer: Renderer) -> None:
    f = FRAMES[0]
    king = f.unit("tower0")
    assert king is not None and king.team == 0
    ax, ay, aw, ah = renderer.layout.arena
    for seat, expected in ((0, (ax + 216, ay + ah - 72)), (1, (ax + aw - 216, ay + 72))):
        assert renderer.to_px(king.x, king.y, f.units_per_tile, seat) == expected
        renderer.draw(f, ViewState(seat=seat, show_paths=False, show_targets=False), transport(0))
        px, py = expected
        assert renderer.surface.get_at((px + 20, py))[:3] == DEFAULT.team_king[0], seat
        # the other king sits at the mirrored pixel in the other team's shade
        red = f.unit("tower3")
        rx, ry = renderer.to_px(red.x, red.y, f.units_per_tile, seat)
        assert (rx, ry) == (px, 2 * ay + ah - py)
        assert renderer.surface.get_at((rx + 20, ry))[:3] == DEFAULT.team_king[1], seat
        assert renderer.unit_at(px, py, f, ViewState(seat=seat)) == "tower0"
    assert renderer.unit_at(ax + 1, ay + 1, f, ViewState()) is None


def test_timeline_and_text_helpers(renderer: Renderer) -> None:
    x, y, w, h = renderer.layout.timeline
    assert renderer.timeline_index_at(x, y + h // 2, 100) == 0
    assert renderer.timeline_index_at(x + w - 1, y + h // 2, 100) == 99
    assert renderer.timeline_index_at(x + w // 2, y + h // 2, 3) == 1
    assert renderer.timeline_index_at(x - 5, y, 100) is None
    assert renderer.timeline_index_at(x, y - 40, 100) is None
    assert renderer.timeline_index_at(x, y, 0) is None
    assert split_name("Knight", 10) == ["Knight"]
    assert split_name("GoblinDrill", 10) == ["Goblin", "Drill"]
    assert split_name("ElectroDragon", 10) == ["Electro", "Dragon"]
    assert split_name("P.E.K.K.A", 10) == ["P.E.K.K.A"]
    assert clock_text(1234, 50) == "1:01" and clock_text(0, 50) == "0:00"
    assert elixir_text(5437) == "5.437" and elixir_text(10000) == "10.000"
    font = renderer.fonts["tiny"]
    assert fit_text("short", font, 500) == "short"
    long = fit_text("a very long line of text that cannot fit", font, 60)
    assert long.endswith("…") and font.size(long)[0] <= 60


def test_board_from_the_default_arena_matches_the_builtin() -> None:
    b = default_board()
    built = Board.builtin()
    assert (b.tiles_x, b.tiles_y, b.half) == (18, 32, 2)
    assert b.water == built.water and b.bridge == built.bridge
    assert b.king_zones == built.king_zones
    assert sorted(b.princess_zones) == sorted(built.princess_zones)
    assert b.no_deploy  # the grid's NO_DEPLOY cells: king blocks, back rows, river corners


def test_text_cache_is_bounded(renderer: Renderer) -> None:
    for i in range(5000):
        renderer.text(f"line {i}", "tiny")
    assert len(renderer._text_cache) <= 4096
    assert renderer.text("line 4999", "tiny") is renderer.text("line 4999", "tiny")


def test_dense_frame_draw_time() -> None:
    r = Renderer(scale=24, help_lines=KEYS)
    f = dense_frame(100)
    assert len(f.units) >= 100
    view = ViewState()
    r.draw(f, view, Transport())
    times = []
    for _ in range(60):
        t0 = time.perf_counter()
        r.draw(f, view, Transport())
        times.append((time.perf_counter() - t0) * 1000)
    mean, median, best = statistics.fmean(times), statistics.median(times), min(times)
    print(f"\n100-troop frame at scale 24: mean {mean:.2f} ms, median {median:.2f}, min {best:.2f}")
    # The best of 60 is the draw's own cost; the mean carries whatever else the machine is
    # doing (a busy machine put it at 85 % CPU on 2026-09-20: mean 9-11 ms, min 4-6 ms).
    assert best < 8, times
    assert median < 40, times


def test_compare_text_counts_the_strays() -> None:
    f = FRAMES[600]
    g = copy.deepcopy(f)
    assert compare_text(f, g) == f"{len(g.units)} entities, 0 differ"
    g.units[0].x += 3 * g.units_per_tile
    assert compare_text(f, g).endswith("1 differ")
    h = copy.deepcopy(f)
    h.units_per_tile = 18000
    for u in h.units:
        u.x, u.y = u.x * 18, u.y * 18
    assert compare_text(f, h).endswith("0 differ")


def test_fit_scale_picks_the_largest_that_fits() -> None:
    w, h = layout(DEFAULT, 24, (18, 32)).window
    assert fit_scale(DEFAULT, (18, 32), w, h) == 24
    assert fit_scale(DEFAULT, (18, 32), w - 1, h) == 23
    assert fit_scale(DEFAULT, (18, 32), w, h - 1) == 23
    assert fit_scale(DEFAULT, (18, 32), 100, 100) == app.MIN_SCALE


def test_app_keys_and_pacing_headless() -> None:
    pygame.init()
    src = ListSource(FRAMES, "synthetic")
    other = ListSource([frame_at(t) for t in range(0, 2400, 2)], "half-rate")
    a = App([src, other], ViewState(), speed=4.0)
    a.pull()
    assert a.frame is FRAMES[0] and a.view.compare_frame is not None
    assert a.view.compare_text.endswith("0 differ")
    a.advance(1000)  # one wall second at 4x = 80 frames
    assert src.index == 80 and other.index == 40
    a.key(pygame.K_SPACE, 0)
    assert a.transport.playing is False
    a.key(pygame.K_RIGHT, pygame.KMOD_SHIFT)
    assert src.index == 100
    a.key(pygame.K_LEFT, 0)
    assert src.index == 99
    a.key(pygame.K_END, 0)
    assert src.index == 2399 and a.frame.game_over
    a.key(pygame.K_HOME, 0)
    assert src.index == 0
    a.key(pygame.K_f, 0)
    a.key(pygame.K_p, 0)
    a.key(pygame.K_t, 0)
    a.key(pygame.K_g, 0)
    a.key(pygame.K_d, 0)
    a.key(pygame.K_c, 0)
    a.key(pygame.K_h, 0)
    v = a.view
    assert (v.seat, v.show_paths, v.show_targets, v.show_grid, v.show_debug) == (
        1,
        False,
        False,
        True,
        True,
    )
    assert v.show_compare is False and v.show_help is True
    a.key(pygame.K_RIGHTBRACKET, 0)
    assert a.transport.speed == 8.0
    for _ in range(9):
        a.key(pygame.K_MINUS, 0)
    assert a.transport.speed == 0.25
    a.key(pygame.K_SPACE, 0)
    a.advance(200)  # 0.25x: 200 wall ms = 50 battle ms = one frame
    assert src.index == 1
    a.key(pygame.K_END, 0)
    a.transport.playing = True
    a.advance(400)  # 0.25x: 100 battle ms at the last frame
    assert a.transport.at_end and a.transport.playing is False
    a.key(pygame.K_SPACE, 0)  # play from the end restarts
    assert src.index == 0 and a.transport.playing
    a.key(pygame.K_q, 0)
    assert a.running is False
    a.draw(time.perf_counter())
    assert a.draw_times and "draws" in a.stats()
    pygame.quit()


def test_run_headless_writes_the_shot(tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"
    src = ListSource(FRAMES, "synthetic")
    code = run(src, ViewState(seat=1), seconds=0.5, shot=str(shot), speed=8.0, start_tick=600)
    assert code == 0 and src.closed
    assert shot.exists()
    surf = pygame.image.load(str(shot))
    assert surf.get_size() == layout(DEFAULT, 24, (18, 32)).window
    assert src.index > 12  # 0.5 s at 8x from tick 600 advanced the replay


def test_fit_layout_drops_the_inspector_for_a_narrow_window() -> None:
    w, h = layout(DEFAULT, 24, (18, 32)).window
    assert fit_layout(DEFAULT, (18, 32), w, h) == (DEFAULT, 24)
    theme, scale = fit_layout(DEFAULT, (18, 32), 712, 1029)  # a narrow slot
    assert theme.inspector_w == 0 and scale == 20
    assert layout(theme, scale, (18, 32)).window == (710, 704)
    assert layout(theme, scale, (18, 32)).inspector[2] == 0
    theme, scale = fit_layout(DEFAULT, (18, 32), 100, 100)  # nothing fits: the smallest compact
    assert theme.inspector_w == 0 and scale == app.MIN_SCALE


def test_compact_layout_draws_the_compare_lines_in_the_dashboard() -> None:
    theme, scale = fit_layout(DEFAULT, (18, 32), 712, 1029)
    r = Renderer(scale=scale, theme=theme)
    view = ViewState(compare_frame=FRAMES[600], compare_name="other", compare_text="a\nb")
    r.draw(FRAMES[600], view, Transport(source_name="s"))
    assert r.surface.get_size() == (710, 704)
    assert r.compare_lines(view) == ["compare: other  shown", "a", "b"]


def test_learning_fields_are_the_same_list_attached_or_not() -> None:
    r = Renderer(scale=24)
    detached = r.learning_lines(None)
    expected = [h for h, _ in LEARNING_GROUPS]
    expected += [lbl for _, fs in LEARNING_GROUPS for lbl, _, _ in fs]
    assert sorted(lbl for lbl, _ in detached) == sorted(expected)
    assert {v for lbl, v in detached if v} == {UNSET}  # headings carry no value
    partial = Learning(run="ppo-0007", iteration=1420, kl=0.0094, illegal_rate=0.0173)
    assert [lbl for lbl, _ in r.learning_lines(partial)] == [lbl for lbl, _ in detached]
    got = dict(r.learning_lines(partial))
    assert (got["iteration"], got["KL"], got["illegal actions"]) == ("1420", "0.0094", "1.7%")
    assert got["policy loss"] == UNSET  # a field the learner left unset, not a zero
    assert got["learner"] == "" and got["ladder"] == ""


def test_the_learners_own_rows_are_drawn_under_the_fixed_ones() -> None:
    """``Learning.extra`` is the open tail: rows the learner names itself, in the order it
    sent them, after the twenty the panel always lists."""
    r = Renderer(scale=24)
    fixed = [lbl for lbl, _ in r.learning_lines(Learning())]
    ln = Learning(
        run="ppo-0007",
        iteration=1420,
        extra={"rating": 1183.4, "rating se": 12.74, "env steps": 48_000_000, "gate": "passed"},
    )
    lines = r.learning_lines(ln)
    assert [lbl for lbl, _ in lines][: len(fixed)] == fixed  # nothing reordered above them
    assert [lbl for lbl, _ in lines][len(fixed) :] == [
        "extra",
        "rating",
        "rating se",
        "env steps",
        "gate",
    ]
    got = dict(lines)
    assert (got["rating"], got["rating se"]) == ("1183", "12.74")
    assert got["env steps"] == "48,000,000" and got["gate"] == "passed"
    assert got["extra"] == ""  # a heading, like the other three
    assert extra_text(None) == UNSET and extra_text(True) == "yes"
    r.draw(FRAMES[600], ViewState(), Transport(source_name="s", learning=ln))


def test_a_status_off_the_wire_fills_the_panel_through_the_app() -> None:
    """The whole path in one test: a learner publishes a status, the StreamSource takes it
    off the same socket the frames come in on, the app hands it to the transport, and the
    panel draws the numbers the learner sent -- and only those."""
    pygame.init()
    frames = sources.Publisher(port=0)
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    src = sources.StreamSource(*frames.address, learner.address)
    time.sleep(0.05)
    frames._pub._last_poll = 0.0
    a = App([src], ViewState())
    a.pull()
    assert a.transport.learning is None  # nothing published yet: "no learner attached"
    assert frames.publish(FRAMES[600])
    status = model.Learning(
        run="ppo-0007",
        iteration=1420,
        policy_loss=0.0,
        kl=0.0094,
        elo=1183.0,
        extra={"rating": 1191.6},
    )
    assert learner.publish(status) and learner.sent == 1
    end = time.monotonic() + 2.0
    while time.monotonic() < end and (a.transport.learning is None or a.frame is None):
        learner.pump()
        a.pull()
        time.sleep(0.01)
    assert a.frame is not None and a.transport.learning == status
    lines = dict(a.renderer.learning_lines(a.transport.learning))
    assert (lines["iteration"], lines["KL"], lines["ELO vs pool"]) == ("1420", "0.0094", "1183")
    assert lines["policy loss"] == "0.000"  # the learner said zero, so the panel says zero
    assert lines["value loss"] == lines["win rate"] == UNSET  # never sent, so never a number
    assert lines["rating"] == "1192"  # the learner's own row, off the same datagram
    assert learning_run(status) == "ppo-0007" and learning_run(None) == "no learner attached"
    assert learning_run(model.Learning(iteration=7)) == UNSET  # a learner with no name is one

    a.draw(time.perf_counter())  # the real draw, with a status that came off the wire
    assert a.transport.draw_ms > 0 and not a.dirty
    later = model.Learning(run="ppo-0007", iteration=1421)
    learner.publish(later)
    end = time.monotonic() + 2.0
    while time.monotonic() < end and a.transport.learning != later:
        learner.pump()
        a.pull()
        time.sleep(0.01)
    # The next iteration redraws the window, though the board has not moved since the draw.
    assert a.transport.learning == later and a.dirty
    assert dict(a.renderer.learning_lines(later))["ELO vs pool"] == UNSET  # replaced, not merged
    src.close()
    frames.close()
    learner.close()


def test_learning_panel_closes_the_dashboard_under_the_match_log() -> None:
    """The column under the match log is panel, not window background.

    Before the panel existed the match log ended where its content did and left the rest of
    the column dark down to the bottom elixir row -- some 190 px at scale 24. What may remain
    is the one-margin seam between the two panels, so the test is that no run of background
    is taller than a margin.
    """
    r = Renderer(scale=24, help_lines=KEYS)
    lay, t = r.layout, r.theme
    x, y, w, h = lay.debug
    for learning in (None, Learning(run="ppo-0007", iteration=1420)):
        r.draw(FRAMES[600], ViewState(), Transport(source_name="s", learning=learning))
        runs, current = [0], 0
        for py in range(y, y + h):
            if all(r.surface.get_at((px, py))[:3] == t.ui_bg for px in (x, x + w // 2, x + w - 1)):
                current += 1
            else:
                runs.append(current)
                current = 0
        runs.append(current)
        assert max(runs) <= t.margin, f"{max(runs)} px of dead column with learning={learning}"


def test_learning_panel_is_left_out_when_the_column_is_too_short() -> None:
    theme, scale = fit_layout(DEFAULT, (18, 32), 100, 100)  # the smallest compact layout
    r = Renderer(scale=scale, theme=theme)
    r.draw(FRAMES[600], ViewState(), Transport(source_name="s"))  # draws, does not raise
    tall = Renderer(scale=24)
    n_fields = sum(len(fs) for _, fs in LEARNING_GROUPS)
    assert len(tall.learning_lines(None)) == n_fields + len(LEARNING_GROUPS)


def test_compare_totals_and_replay_scrubbing() -> None:
    c = Compare()
    f = FRAMES[600]
    g = copy.deepcopy(f)
    c.note(0, f)
    assert c.text(f.tick).startswith(f"tick {f.tick}: not seen by both")
    c.note(1, g)
    assert c.results[f.tick] == (len(f.units), len(g.units), 0) and (c.ticks, c.differ) == (1, 0)
    h = copy.deepcopy(FRAMES[601])
    h.units[0].hp -= 1
    c.note(0, FRAMES[601])
    c.note(1, h)
    assert c.text(h.tick).endswith("2 ticks compared, 1 differ")
    c.note(0, FRAMES[0])  # scrubbed back: a replay keeps its totals
    c.note(1, FRAMES[0])
    assert (c.ticks, c.differ) == (3, 1)
    live = Compare(restart_on_drop=True)
    live.note(0, FRAMES[600])
    live.note(0, FRAMES[0])  # a new battle
    assert live.ticks == 0 and live.results == {}


def test_run_accepts_geometry_and_scale(tmp_path: Path) -> None:
    shot = tmp_path / "small.png"
    src = ListSource(FRAMES[:10], "short")
    assert run([src], geometry=(700, 600, 10, 10), seconds=0.2, shot=str(shot)) == 0
    dw, dh = app.frame_extras()
    theme, scale = fit_layout(DEFAULT, (18, 32), 700 - dw, 600 - dh)
    assert theme.inspector_w == 0 and 15 <= scale <= 16
    lw, lh = layout(theme, scale, (18, 32)).window
    assert pygame.image.load(str(shot)).get_size() == (max(lw, 700 - dw), max(lh, 600 - dh))
    assert os.environ.get("SDL_VIDEO_WINDOW_POS") == "10,10"


def test_a_number_that_rounds_away_to_nothing_says_how_small_it_is() -> None:
    """From the first real training run to fill the panel: a policy loss of -3e-05 drew as
    "-0.000", which reads as a bug and hides the magnitude. A true zero still draws as a
    zero, and never with a minus sign."""
    from royaleviser.render import field_text

    assert field_text(-3.2e-05, ".3f") == "-3.2e-05"
    assert field_text(4e-07, ".4f") == "4.0e-07"
    assert field_text(1e-05, ".1%") == "1.0e-05"
    assert field_text(0.0, ".3f") == "0.000"  # an honest zero is a zero
    assert field_text(-0.0, ".3f") == "0.000"  # and never a negative one
    assert field_text(0, "d") == "0"
    assert field_text(None, ".3f") == UNSET  # unset is still unset
    assert field_text(0.0241, ".3f") == "0.024" and field_text(1161.5, ",.0f") == "1,162"
    r = Renderer(scale=24)
    lines = dict(r.learning_lines(Learning(policy_loss=-3.2e-05, kl=0.0, value_loss=0.0131)))
    got = (lines["policy loss"], lines["KL"], lines["value loss"])
    assert got == ("-3.2e-05", "0.0000", "0.013")


def test_an_empty_panel_names_the_port_it_is_listening_on() -> None:
    """An absence that looks like the ordinary case: a learner whose status port was taken by
    another run on the same machine publishes nothing and cannot say so, while its
    environment may still be streaming a battle. Naming the port makes that checkable."""
    assert learning_run(None) == "no learner attached"
    assert learning_run(None, "127.0.0.1:9871") == "no learner on 127.0.0.1:9871"
    assert learning_run(Learning(run="ppo-0007"), "127.0.0.1:9871") == "ppo-0007"
    r = Renderer(scale=24, help_lines=KEYS)
    r.draw(FRAMES[600], ViewState(), Transport(source_name="s", learning_peer="127.0.0.1:9871"))


def test_the_app_tells_the_panel_where_a_learner_would_be_heard() -> None:
    pygame.init()
    src = sources.StreamSource("127.0.0.1", 9870, ("127.0.0.1", 9871))
    a = App([src], ViewState())
    a.pull()
    assert a.transport.learning_peer == "127.0.0.1:9871" and a.transport.learning is None
    src.close()
    plain = ListSource(FRAMES, "synthetic")  # a replay has no learner and says nothing of one
    b = App([plain], ViewState())
    b.pull()
    assert b.transport.learning_peer == ""
