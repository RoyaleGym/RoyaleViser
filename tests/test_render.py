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
    # A percent row keeps its unit, or the number silently changes by a hundred.
    assert field_text(1e-05, ".1%") == "1.0e-03%"
    assert field_text(0.0004, ".1%") == "4.0e-02%"
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
    # NOT the default ports: a test that heart-beats 9870 half-attaches to whatever run is
    # using them, and steals the publisher's peer from a real viewer.
    src = sources.StreamSource("127.0.0.1", 9998, ("127.0.0.1", 9999))
    a = App([src], ViewState())
    a.pull()
    assert a.transport.learning_peer == "127.0.0.1:9999" and a.transport.learning is None
    src.close()
    plain = ListSource(FRAMES, "synthetic")  # a replay has no learner and says nothing of one
    b = App([plain], ViewState())
    b.pull()
    assert b.transport.learning_peer == ""


def test_the_panel_says_how_old_the_status_is() -> None:
    """A real iteration is minutes apart -- 8.9 of them on the laptop profile, measured
    2026-09-22 -- so a panel with no age on it cannot tell a run that is working from one
    that died half an hour ago. Both show the same numbers, standing still."""
    from royaleviser.render import age_text

    assert age_text(None) == "" and age_text(0.0) == "0s"
    assert age_text(12.7) == "12s" and age_text(59.9) == "59s"
    assert age_text(60) == "1m" and age_text(533) == "8m"  # one laptop-profile iteration
    assert age_text(3600) == "1h00" and age_text(3600 + 47 * 60) == "1h47"
    r = Renderer(scale=24, help_lines=KEYS)
    ln = Learning(run="ppo-0007", iteration=1420)
    r.draw(FRAMES[600], ViewState(), Transport(source_name="s", learning=ln, learning_age_s=533))
    r.draw(FRAMES[600], ViewState(), Transport(source_name="s", learning=None, learning_age_s=None))


def test_the_app_ages_the_status_from_when_it_arrived() -> None:
    pygame.init()
    learner = sources.LearningPublisher(port=0, pump_thread=False)
    src = sources.StreamSource("127.0.0.1", 9999, learner.address)
    a = App([src], ViewState())
    a.pull()
    a.draw(time.perf_counter())
    assert a.transport.learning_age_s is None  # nothing has ever arrived
    learner.publish(model.Learning(run="ppo-0007", iteration=1))
    end = time.monotonic() + 2.0
    while time.monotonic() < end and src.learning is None:
        learner.pump()
        a.pull()
        time.sleep(0.01)
    assert src.learning is not None and src.learning_at is not None
    a.draw(time.perf_counter())
    assert a.transport.learning_age_s is not None and a.transport.learning_age_s < 5
    src.close()
    learner.close()


def test_a_group_heading_never_lands_without_its_rows() -> None:
    """Packing a group into the first column with room can strand a heading: the heading fits
    where the rows do not, and the rows then read as belonging to the group above it."""
    ln = Learning(run="r", iteration=1, extra={"rating": 1183.4, "rating se": 12.7})
    r = Renderer(scale=24)
    headings = {h for h, _ in LEARNING_GROUPS} | {"extra"}
    for rows in range(1, 40):
        cols = r.learning_columns(ln, rows)
        assert all(len(c) <= rows for c in cols), rows
        for col in cols:
            for i, (label, value) in enumerate(col):
                if not value:  # a heading
                    assert label in headings
                    assert i + 1 < len(col), f"heading {label!r} alone at rows={rows}"
                    assert col[i + 1][1], f"heading {label!r} followed by a heading, rows={rows}"
    fixed = sum(len(f) for _, f in LEARNING_GROUPS)
    headings = len(LEARNING_GROUPS) + 1  # the three fixed groups and the learner's own
    assert len(r.learning_lines(ln)) == fixed + headings + 2


def test_nothing_a_learner_names_can_take_the_window_down() -> None:
    """The learner supplies its run name, its extra names and their values. None of it is
    trusted: a control character in any of them used to reach the font layer."""
    r = Renderer(scale=24, help_lines=KEYS)
    nasty = Learning(
        run="ppo\x00-0007\r\n",
        iteration=1420,
        extra={"a\x00b": "x\x07y", "": "", "long": "y" * 400, "nested": {"a": 1}},
    )
    r.draw(FRAMES[600], ViewState(), Transport(source_name="s", learning=nasty))  # no raise
    assert "\x00" not in fit_text("a\x00b", r.fonts["tiny"], 500)
    assert extra_text("") == UNSET  # an empty value would draw as a group heading
    assert extra_text({"a": 1}) == "{'a': 1}"


# ---------------------------------------------------------------------- footprints


def building(
    uid: str, kind: int, x: float, y: float, upt: int, box_tiles: float | None
) -> model.Unit:
    """One building or tower at (x, y) tiles, with a square footprint of ``box_tiles`` tiles
    centred on it, or none at all. ``radius`` is the Cannon's real CollisionRadius scaled to
    ``upt``: the number the viewer used to size the square with, so a test that passes on the
    footprint cannot be passing on the radius by coincidence."""
    box = None
    if box_tiles is not None:
        half = int(box_tiles * upt / 2)
        box = (int(x * upt) - half, int(y * upt) - half, int(x * upt) + half, int(y * upt) + half)
    return model.Unit(
        uid=uid,
        team=0,
        kind=kind,
        name="Cannon",
        x=int(x * upt),
        y=int(y * upt),
        hp=380,
        max_hp=380,
        radius=600 * upt // 1000,  # 1.2 tiles across, the model this viewer used to draw
        flying=False,
        deploy_ticks=0,
        stun_ticks=0,
        target=None,
        path=[],
        direction=None,
        state=None,
        footprint=box,
    )


def one_unit_frame(unit: model.Unit, upt: int = model.LIVE_UNITS_PER_TILE) -> model.Frame:
    """A contract-sound frame holding one unit and nothing else, so anything drawn on the
    board is that unit."""
    f = copy.deepcopy(FRAMES[0])
    f.units = [unit]
    f.spells = []
    f.events = []
    f.units_per_tile = upt
    assert model.problems(f) == []
    return f


def drawn_box(r: Renderer, frame: model.Frame, view: ViewState) -> tuple[int, int, int, int]:
    """The bounding box, in ARENA pixels, of everything the frame put on the empty board.

    Graded against the pixels rather than against the renderer's own geometry helper: a
    helper that returns the right rectangle and a draw call that uses a different one is
    exactly the bug this is here to catch.
    """
    ax, ay, aw, ah = r.layout.arena
    board = r.board_surface(view.seat, view.show_grid).copy()
    r.draw(frame, view, Transport(source_name="t"))
    xs, ys = [], []
    for py in range(ah):
        for px in range(aw):
            if r.surface.get_at((ax + px, ay + py))[:3] != board.get_at((px, py))[:3]:
                xs.append(px)
                ys.append(py)
    assert xs, "nothing was drawn on the board"
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


@pytest.mark.parametrize("seat", [0, 1])
@pytest.mark.parametrize("scale", [24, 12])
def test_a_3x3_cannon_is_drawn_three_tiles_wide(seat: int, scale: int) -> None:
    """The P0 the owner found by looking at the window: a Cannon is 3x3 in the game, and the
    viewer drew every building as a square of twice its collision radius -- 1.2 tiles for a
    Cannon. The size now comes from the box the frame carries, and from nothing else."""
    r = Renderer(scale=scale, help_lines=KEYS)
    upt = model.LIVE_UNITS_PER_TILE
    cannon = building("cannon", model.KIND_BUILDING, 6.5, 10.5, upt, 3.0)
    frame = one_unit_frame(cannon, upt)
    view = ViewState(seat=seat)
    rect = r.unit_rect_px(cannon, upt, seat)
    assert (rect.width, rect.height) == (3 * scale, 3 * scale)
    x, _, w, h = drawn_box(r, frame, view)
    ax, ay, aw, _ = r.layout.arena
    # The name label under the unit can be wider than the box, so the box fixes the left edge
    # and the whole extent is at least three tiles.
    assert ax + x == rect.left and w >= 3 * scale and h >= 3 * scale
    # ... and the fill spans three whole tiles on the row through its middle, rather than one
    # tile of building inside a wide label.
    board = r.board_surface(seat, view.show_grid)
    mid = rect.top + rect.height // 2
    row = [
        px
        for px in range(aw)
        if r.surface.get_at((ax + px, mid))[:3] != board.get_at((px, mid - ay))[:3]
    ]
    assert max(row) - min(row) + 1 == 3 * scale


def test_a_frame_without_a_footprint_says_so_rather_than_drawing_a_guess_plainly() -> None:
    """Every recording and every trace written before the field carries no footprint. The
    viewer still has to draw something; what it must not do is draw a guess that looks like
    a measurement."""
    from royaleviser.render import footprint_line, footprint_note

    r = Renderer(scale=24, help_lines=KEYS)
    upt = model.LIVE_UNITS_PER_TILE
    guessed = one_unit_frame(building("c", model.KIND_BUILDING, 6.5, 10.5, upt, None), upt)
    carried = one_unit_frame(building("c", model.KIND_BUILDING, 6.5, 10.5, upt, 3.0), upt)

    assert footprint_note(guessed) == "1 of 1 sizes guessed"
    assert footprint_note(carried) == ""
    assert footprint_note(FRAMES[600]).endswith("sizes guessed")  # the synthetic battle
    assert "guessed" in footprint_line(guessed.units[0], upt)
    assert footprint_line(carried.units[0], upt) == "3.0 x 3.0 tiles at (5.0, 9.0)"
    assert footprint_line(FRAMES[600].unit("knight"), upt) == "-"  # a troop has no box

    def marks(frame: model.Frame) -> int:
        r.surface.fill((0, 0, 0))
        r.draw(frame, ViewState(), Transport(source_name="t"))
        ax, ay, aw, ah = r.layout.arena
        return sum(
            r.surface.get_at((ax + px, ay + py))[:3] == DEFAULT.footprint_guess
            for py in range(ah)
            for px in range(aw)
        )

    assert marks(guessed) > 0, "a guessed size was drawn with nothing to say so"
    assert marks(carried) == 0, "a carried footprint must not be marked as a guess"
    # The note reaches the status block, where a reader looks for it.
    assert r.notes(guessed, Transport()) == ["1 of 1 sizes guessed"]
    assert r.notes(carried, Transport()) == []


def test_the_overlay_shades_the_carried_box_and_the_taps_it_refuses() -> None:
    """B: the footprint cells, and the tile taps whose centre lands inside one. It draws the
    carried boxes and nothing else -- a building's own size is a property of the card being
    played, which no frame carries, so the overlay shows the part the frame settles."""
    r = Renderer(scale=24, help_lines=KEYS)
    upt = model.LIVE_UNITS_PER_TILE
    frame = one_unit_frame(building("c", model.KIND_BUILDING, 6.5, 10.5, upt, 3.0), upt)
    ax, ay, _, _ = r.layout.arena
    on = ViewState(show_footprints=True)

    def refused_tiles() -> list[tuple[int, int]]:
        return sorted(
            (tx, ty)
            for ty in range(32)
            for tx in range(18)
            if r.surface.get_at((ax + tx * 24 + 12, ay + (31 - ty) * 24 + 12))[:3]
            == DEFAULT.footprint_refused
        )

    r.draw(frame, on, Transport(source_name="t"))
    # Nine tile centres lie inside a 3x3 box on a tile centre: 5, 6, 7 by 9, 10, 11.
    assert refused_tiles() == sorted((tx, ty) for tx in (5, 6, 7) for ty in (9, 10, 11))
    # A frame that carries no box shades nothing: the overlay never invents one.
    bare = one_unit_frame(building("c", model.KIND_BUILDING, 6.5, 10.5, upt, None), upt)
    r.draw(bare, on, Transport(source_name="t"))
    assert refused_tiles() == []
    # And nothing is shaded while the overlay is off.
    r.draw(frame, ViewState(), Transport(source_name="t"))
    assert refused_tiles() == []


def test_a_building_is_clickable_over_its_whole_footprint() -> None:
    """The inspector is how anyone checks a building, and the hit test used to be a disc of
    the collision radius: the corners of a 3x3 Cannon were not clickable at all."""
    r = Renderer(scale=24, help_lines=KEYS)
    upt = model.LIVE_UNITS_PER_TILE
    frame = one_unit_frame(building("cannon", model.KIND_BUILDING, 6.5, 10.5, upt, 3.0), upt)
    for seat in (0, 1):
        view = ViewState(seat=seat)
        rect = r.unit_rect_px(frame.units[0], upt, seat)
        assert r.unit_at(rect.centerx, rect.centery, frame, view) == "cannon"
        assert r.unit_at(rect.left + 2, rect.top + 2, frame, view) == "cannon"
        assert r.unit_at(rect.right - 2, rect.bottom - 2, frame, view) == "cannon"
        assert r.unit_at(rect.centerx, rect.top - 30, frame, view) is None


def test_the_panel_flags_frames_and_a_status_from_different_runs() -> None:
    """Two runs on one machine collide on the fixed ports. The frame publisher may get its
    port while the learner does not, so a moving board under another run's numbers looks
    exactly like one run. The run ids are carried; the window says when they disagree."""
    r = Renderer(scale=24, help_lines=KEYS)
    frame = copy.deepcopy(FRAMES[600])
    frame.meta["run"] = "ppo-0007"
    same = Transport(learning=Learning(run="ppo-0007"))
    other = Transport(learning=Learning(run="ppo-0008"))
    assert "frames ppo-0007 / learner ppo-0008" in r.notes(frame, other)
    assert not [n for n in r.notes(frame, same) if "learner" in n]
    # Neither half alone is a mismatch: a source that carries no run id says nothing.
    assert not [n for n in r.notes(FRAMES[600], other) if "learner" in n]
    frame.meta["run"] = ""
    assert not [n for n in r.notes(frame, other) if "learner" in n]
    r.draw(frame, ViewState(), other)  # the line fits the panel


def test_b_toggles_the_footprint_overlay() -> None:
    pygame.init()
    a = App([ListSource(FRAMES, "synthetic")], ViewState())
    assert not a.view.show_footprints
    a.key(pygame.K_b, 0)
    assert a.view.show_footprints
    a.key(pygame.K_b, 0)
    assert not a.view.show_footprints
    assert ("b", "building footprints") in KEYS


def test_the_window_says_when_a_carried_box_runs_off_the_board() -> None:
    """What the owner saw was a building standing where its own box does not fit. Whether the
    placement was legal is the engine's answer and the viewer does not hold the rule, but a
    box against the board is two comparisons, and both are in front of the window."""
    r = Renderer(scale=24, help_lines=KEYS)
    upt = model.LIVE_UNITS_PER_TILE
    inside = one_unit_frame(building("c", model.KIND_BUILDING, 1.5, 14.5, upt, 3.0), upt)
    off = one_unit_frame(building("c", model.KIND_BUILDING, 1.0, 14.5, upt, 3.0), upt)
    assert r.outside_arena(inside) == []  # x 0.0 to 3.0: flush against the wall, but on it
    assert r.outside_arena(off) == ["Cannon's box leaves the arena"]  # x -0.5 to 2.5
    top = one_unit_frame(building("c", model.KIND_BUILDING, 9.0, 31.5, upt, 3.0), upt)
    assert r.outside_arena(top) == ["Cannon's box leaves the arena"]  # y 30.0 to 33.0 of 32
    # A unit with no box makes no claim either way, and the line reaches the panel.
    assert (
        r.outside_arena(
            one_unit_frame(building("c", model.KIND_BUILDING, 1.0, 14.5, upt, None), upt)
        )
        == []
    )
    assert "Cannon's box leaves the arena" in r.notes(off, Transport())
    r.draw(off, ViewState(), Transport(source_name="t"))
