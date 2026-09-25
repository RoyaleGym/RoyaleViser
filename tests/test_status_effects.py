"""Status effects, spell cards and projectiles: what a battle looks like at a glance.

Owner, 2026-09-24: the viewer must make it easy to tell what is going on. Until then a stun was
the only effect drawn, every spell was one magenta dot at one size, and nothing a unit or tower
fired reached the window at all. These tests pin what replaced that, mostly by the PIXELS it
draws, because the defects this change fixed were all visible ones: a yellow stun lost on yellow
grass, a splash ring that read as a mark on the unit standing in it, a freeze that hid whose unit
it was, a Poison drawn a fifth of its size.

Three kinds of evidence, kept apart:
  * what a buff IS and how big a spell is come from the engine's card data
    (``royaleviser/engine_tables.py``), re-derived here from RoyaleSim's data of the same vintage
    when that is on disk;
  * what is DRAWN for each kind is read back from the surface, on unit rows shaped the way the
    engine sends them (a hold arrives as a buff AND stun ticks, never as stun ticks alone);
  * what ARRIVES is decoded from the wire forms a source sends, old frames included.
"""

from __future__ import annotations

import json
import math
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import msgspec
import pygame
import pytest

from royaleviser import model
from royaleviser.engine_tables import BUFF_KINDS, SPELL_RADIUS_MILLI, VINTAGE
from royaleviser.model import KIND_TROOP, Frame, Names, Player, Projectile, Spell, Unit
from royaleviser.render import (
    MOTION_AIRBORNE,
    MOTION_AREA,
    MOTION_FLIGHT,
    MOTION_ROLLING,
    RING_KINDS,
    SPELL_STYLES,
    STATUS_ORDER,
    Renderer,
    ViewState,
    buff_kinds,
    classify_status,
    damage_colour_attr,
    effect_line,
    fit_text,
    spell_style,
    status_kinds,
)
from royaleviser.sources import StreamSource, TraceSource, capture_spell
from royaleviser.theme import DEFAULT

UPT = 1000
SCALE = 24
NOT_A_PASS = "SKIPPED, NOT PASSED"


# ------------------------------------------------------------------ builders


def troop(
    uid: int,
    x: int = 9000,
    y: int = 11000,
    team: int = 0,
    stun: int = 0,
    status: list[tuple[str, int]] | None = None,
    shield: int = 0,
    direction: tuple[int, int] | None = None,
) -> Unit:
    u = Unit(
        uid=uid,
        team=team,
        kind=KIND_TROOP,
        name="Knight",
        x=x,
        y=y,
        hp=1000,
        max_hp=1000,  # full hp: no bar, so nothing but the effect is drawn above the body
        radius=450,
        flying=False,
        deploy_ticks=0,
        stun_ticks=stun,
        target=None,
        path=[],
        direction=direction,
        state=None,
    )
    u.status = list(status or [])
    if shield:
        u.extra["shield"] = shield
    return u


def player(team: int) -> Player:
    return Player(
        team=team,
        elixir_milli=5000,
        elixir_known=True,
        hand=["Knight"] * 4,
        hand_known=True,
        next_card=None,
        cycle=[],
        deck=[],
        deck_known=False,
        crowns=0,
        tower_hp=[4000, 2500, 2500],
        tower_max_hp=[4000, 2500, 2500],
        king_active=False,
    )


def frame_of(
    units: list[Unit] = (),  # type: ignore[assignment]
    spells: list[Spell] = (),  # type: ignore[assignment]
    projectiles: list[Projectile] = (),  # type: ignore[assignment]
) -> Frame:
    return Frame(
        tick=100,
        tick_ms=50,
        units_per_tile=UPT,
        players=[player(0), player(1)],
        units=list(units),
        spells=list(spells),
        overtime=False,
        game_over=False,
        winner=-1,
        crowns=[0, 0],
        projectiles=list(projectiles),
    )


def spell(name: str, motion: int, x: int, y: int, ax: int, ay: int, team: int = 0) -> Spell:
    return Spell(team=team, name=name, motion=motion, x=x, y=y, aim_x=ax, aim_y=ay)


def drawn(f: Frame, seat: int = 0) -> Renderer:
    r = Renderer(scale=SCALE)
    r.draw(f, ViewState(seat=seat, show_targets=False, show_paths=False))
    return r


def at(r: Renderer, xy: tuple[int, int]) -> tuple[int, int, int]:
    return tuple(r.surface.get_at(xy))[:3]


def ring(
    r: Renderer, c: tuple[int, int], r0: float, r1: float, below: int | None = None
) -> set[tuple[int, int, int]]:
    """Every colour in the annulus r0..r1 around ``c``.

    ``below`` keeps only the band 2 < dy < below under the centre: no pip and no glyph is ever
    drawn there, and it stops ABOVE the unit's label, which is white text with a black shadow
    and would otherwise answer both "is the white freeze rim there" and "is there a black
    underlay" by itself. Pass the body radius."""
    out = set()
    x0, y0 = c
    n = math.ceil(r1)
    for dy in range(-n, n + 1):
        if below is not None and not 2 < dy < below:
            continue
        for dx in range(-n, n + 1):
            if r0 <= math.hypot(dx, dy) <= r1:
                out.add(at(r, (x0 + dx, y0 + dy)))
    return out


def box(r: Renderer, x0: int, y0: int, x1: int, y1: int) -> set[tuple[int, int, int]]:
    return {at(r, (x, y)) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def body_px(r: Renderer, u: Unit) -> int:
    return r.unit_radius_px(u, UPT)


def pips(r: Renderer, u: Unit, half_width: int = 16) -> set[tuple[int, int, int]]:
    """The pip row above a full-hp unit (no hp bar is drawn for one)."""
    px, py = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    return box(r, px - half_width, py - b - 17, px + half_width, py - b - 9)


# ------------------------------------------------------------------ the engine's own tables


def engine_card_data() -> dict:
    """RoyaleSim's card data OF THE VINTAGE the tables were generated from, or a LOUD skip.

    Chosen by the file's own ``provenance.vintage_key``, not by the name ``cards.json``: that
    file is whichever vintage a checkout generated, and CI's full-stack job generates the 2018
    one, whose 9 buffs would make 69 correct entries here look stale and whose table, pasted in
    as the failure once suggested, would have deleted them.
    """
    override = os.environ.get("ROYALESIM_DATA_DIR")
    root = (
        Path(override) if override else Path(__file__).resolve().parents[2] / "RoyaleSim" / "data"
    )
    seen = []
    for p in sorted((root / "derived").glob("cards*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        vintage = (data.get("provenance") or {}).get("vintage_key")
        seen.append(f"{p.name}={vintage}")
        if vintage == VINTAGE:
            return data
    found = ", ".join(seen) or "no cards files"
    pytest.skip(
        f"{NOT_A_PASS}: royaleviser/engine_tables.py is checked against RoyaleSim's card data "
        f"of vintage {VINTAGE}, and {root / 'derived'} has none ({found}). Clone RoyaleSim "
        "beside this repo, or set ROYALESIM_DATA_DIR. Without it the tables are unchecked, "
        "not correct."
    )


def kinds_from_numbers(name: str, b: dict) -> tuple[str, ...]:
    """The rule engine_tables.BUFF_KINDS was generated with, RESTATED from status.rs ``compose``:
    -100 holds the unit (a Freeze by name, any other hold a stun); a positive multiplier counts
    only above 100 (``maxpos`` starts at 100); 0 is a blank column, not a speed."""
    speed = b.get("speed_multiplier_raw") or 0
    hit = b.get("hit_speed_multiplier_raw") or 0
    held = speed <= -100
    out = []
    if held:
        n = name.lower()
        out.append("freeze" if "freeze" in n and "zap" not in n else "stun")
    if (b.get("damage_per_second") or 0) > 0:
        out.append("poison")
    if (b.get("heal_per_second") or 0) > 0:
        out.append("heal")
    if not held and (speed < 0 or hit < 0):
        out.append("slow")
    if speed > 100 or hit > 100:
        out.append("rage")
    if (b.get("attract_percentage") or 0) > 0:
        out.append("pull")
    return tuple(out)


def radius_from_card(card: dict) -> int | None:
    """An area's radius, else the spell's, else a roll's half-width, else its splash."""
    sp, pr = card.get("spell") or {}, card.get("projectile") or {}
    for v in (
        (sp.get("area_effect_object") or {}).get("radius_milli"),
        sp.get("radius_milli"),
        (pr.get("spawn_projectile") or {}).get("projectile_radius_milli"),
        pr.get("radius_milli"),
    ):
        if v:
            return int(v)
    return None


def test_the_tables_are_what_the_engine_data_says() -> None:
    """engine_tables.py is a SNAPSHOT of one vintage of RoyaleSim's card data. This keeps it one:
    a buff or spell the data adds, renames or re-tunes shows up here as a difference."""
    data = engine_card_data()
    kinds = {n.lower(): k for n, b in data["buffs"].items() if (k := kinds_from_numbers(n, b))}
    radii = {
        "".join(ch for ch in c["name"].lower() if ch.isalnum()): r
        for c in data["cards"]
        if c.get("spell") is not None and (r := radius_from_card(c)) is not None
    }
    for label, want, have in (
        ("BUFF_KINDS", kinds, BUFF_KINDS),
        ("SPELL_RADIUS", radii, SPELL_RADIUS_MILLI),
    ):
        diff = {
            n: (have.get(n), want.get(n))
            for n in set(want) | set(have)
            if have.get(n) != want.get(n)
        }
        assert not diff, (
            f"{label} has drifted from RoyaleSim's {VINTAGE} data, as (table, data): {diff}. "
            "Regenerate royaleviser/engine_tables.py from that data."
        )


@pytest.mark.parametrize(
    ("name", "kinds", "why"),
    [
        ("Rage", ["rage"], "speed 130"),
        ("IgnoreBarrel", ["other"], "speed 100 is compose's identity, not a speed-up"),
        ("Freeze", ["freeze"], "speed -100 and a Freeze by name"),
        ("ZapFreeze", ["stun"], "the same hold, but a Zap: the game shows it as a stun"),
        ("Stun", ["stun"], "a hold that is not a Freeze"),
        ("electro_dragon_hit_buff", ["stun"], "speed -100, and no word in the name says so"),
        ("IceWizardCold", ["slow"], "speed -30, and no word in the name says so"),
        ("BolaSnare", ["slow"], "speed -70"),
        ("Poison", ["poison", "slow"], "damage AND speed -15: it does both, and both show"),
        ("Earthquake", ["poison", "slow"], "damage over time and half speed"),
        # A speed of 0 is a BLANK column, not "slowed to nothing": compose skips it (sim,
        # 2026-09-24). Read as a speed, every damage-only buff would draw as a hold.
        ("Tornado", ["poison", "pull"], "speed blank; damage and an attract"),
        ("ShieldBoost", ["other"], "every column blank: it changes nothing the engine runs"),
        ("HealSpiritBuff", ["heal"], "heal per second"),
        ("WarmUp", ["heal"], "heal per second, and the name says nothing"),
        ("Freeze|ZapFreeze", ["stun"], "merged: the parts disagree, so the hold is generic"),
        ("RAGE", ["rage"], "case does not matter"),
        ("SomeFutureSnare", ["slow"], "an unknown name falls back to a word in it"),
        ("SomeFutureZapFreeze", ["stun"], "an unknown zap is a stun, not a freeze"),
        ("MysteryThing", ["other"], "an unknown name with no word is other, still drawn"),
        ("", ["other"], "an empty name is other"),
    ],
)
def test_hand_checked_buffs_classify_by_what_they_do(name: str, kinds: list[str], why: str) -> None:
    """Checked by hand against status.rs and the card data, INDEPENDENTLY of the generated table:
    the test above compares the table with a rule, and a rule wrong in both places would agree
    with itself."""
    assert buff_kinds(name) == kinds, why
    assert classify_status(name) == kinds[0]


def test_every_kind_has_a_colour_and_every_spell_style_names_a_real_one() -> None:
    """A kind or style whose theme colour does not exist raises inside draw(), on the frame
    that first carries it, which is a battle already running."""
    for kind in STATUS_ORDER:
        assert isinstance(getattr(DEFAULT, f"status_{kind}"), tuple), kind
    assert {k for ks in BUFF_KINDS.values() for k in ks} <= set(STATUS_ORDER)
    for attr, _shape in SPELL_STYLES.values():
        assert isinstance(getattr(DEFAULT, attr), tuple), attr


def test_status_kinds_collects_every_source_strongest_first() -> None:
    u = troop(1, stun=3, status=[("Poison", 2000), ("Rage", 5000)], shield=40)
    assert status_kinds(u) == ["stun", "poison", "slow", "rage", "shield"]
    assert status_kinds(troop(2)) == []
    odd = troop(3)
    odd.extra["shield"] = "not a number"  # a raw inspector field; must not take draw() down
    assert status_kinds(odd) == []


@pytest.mark.parametrize(
    ("stun", "status", "kinds"),
    [
        (72, [("Freeze", 3600)], ["freeze"]),
        (10, [("ZapFreeze", 500)], ["stun"]),
        (72, [("Freeze|ZapFreeze", 3600)], ["stun"]),
        (10, [], ["stun"]),
    ],
)
def test_a_hold_is_one_effect_as_the_engine_sends_it(
    stun: int, status: list[tuple[str, int]], kinds: list[str]
) -> None:
    """The engine holds a unit through its stun timer WHATEVER holds it, so a hold arrives as a
    buff AND stun ticks for the same hold: measured, a frozen Ice Wizard carried Freeze|ZapFreeze
    at 3600 ms beside stun_ticks 72. The buff says which hold; the ticks add a stun only when no
    buff explains them. These are the rows the engine sends, not stun ticks on their own."""
    assert status_kinds(troop(1, stun=stun, status=status)) == kinds


def test_damage_over_time_takes_its_own_spells_colour() -> None:
    """Earthquake and Tornado deal damage over time, and in Poison's green they told a reader a
    Poison had been cast."""
    assert damage_colour_attr(troop(1, status=[("Poison", 1000)])) == "status_poison"
    assert damage_colour_attr(troop(1, status=[("Earthquake", 1000)])) == "spell_quake"
    assert damage_colour_attr(troop(1, status=[("Tornado", 500)])) == "spell_wind"


def test_the_inspector_keeps_the_kind_and_the_time_when_the_line_is_cut() -> None:
    """The inspector cuts each line to its column. With the engine's merged name first, the
    line read "effect   Freeze|ZapFreeze (fr..." and lost the two things it exists to show."""
    r = Renderer(scale=SCALE)
    width = r.layout.hover[2] - 12  # what _draw_inspector gives each line
    shown = fit_text(effect_line("Freeze|ZapFreeze", 3900), r.fonts["mono"], width)
    assert "stun" in shown and "3.9s" in shown, shown
    shown = fit_text(effect_line("Poison", 7500), r.fonts["mono"], width)
    assert "poison+slow 7.5s" in shown, shown


# ------------------------------------------------------------------ what is DRAWN


BODY_MARKS = {
    # kind -> (unit kwargs shaped as the engine sends them, the colour of its body mark)
    "stun": ({"stun": 10, "status": [("ZapFreeze", 500)]}, DEFAULT.status_stun),
    "slow": ({"status": [("IceWizardSlowDown", 2000)]}, DEFAULT.status_slow),
    "rage": ({"status": [("Rage", 2000)]}, DEFAULT.status_rage),
    "poison": ({"status": [("Poison", 2000)]}, DEFAULT.status_poison),
    "freeze": ({"stun": 72, "status": [("Freeze", 3600)]}, (255, 255, 255)),  # the white rim
}


def test_every_ring_kind_is_covered_here() -> None:
    assert set(BODY_MARKS) == set(RING_KINDS)


@pytest.mark.parametrize("kind", sorted(BODY_MARKS))
def test_each_ring_effect_draws_its_mark_round_the_body(kind: str) -> None:
    """Below the body's centre, where no pip or glyph is drawn: the mark is there with the
    effect and absent without it, so this is the mark and not something that is always there."""
    kwargs, colour = BODY_MARKS[kind]
    u = troop(1, **kwargs)
    r = drawn(frame_of([u]))
    c = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    assert colour in ring(r, c, b + 1, b + 9, below=b), f"{kind} drew no mark"
    plain = drawn(frame_of([troop(1)]))
    assert colour not in ring(plain, c, b + 1, b + 9, below=b), "always drawn"


@pytest.mark.parametrize("kind", sorted(BODY_MARKS))
def test_every_mark_sits_on_a_dark_underlay(kind: str) -> None:
    """The first look at the new drawing lost the yellow stun on yellow grass; the underlay is
    the fix, on every ring. Without it the ring's neighbourhood has no black beyond the body's
    own 1 px outline (radius b), which this annulus starts outside."""
    u = troop(1, **BODY_MARKS[kind][0])
    r = drawn(frame_of([u]))
    c = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    assert (0, 0, 0) in ring(r, c, b + 2, b + 9, below=b), f"{kind} has no underlay"
    plain = drawn(frame_of([troop(1)]))
    assert (0, 0, 0) not in ring(plain, c, b + 2, b + 9, below=b)


def test_a_zap_looks_like_a_stun_and_not_like_a_freeze() -> None:
    """The row the engine sends for a Zap: a ZapFreeze buff and stun ticks. It draws the jagged
    stun ring and no ice. Before the buffs were split by name, this row wore the Freeze veil."""
    u = troop(1, stun=10, status=[("ZapFreeze", 500)])
    r = drawn(frame_of([u]))
    c = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    around = ring(r, c, b + 1, b + 9, below=b)
    assert DEFAULT.status_stun in around and (255, 255, 255) not in around


def test_the_strongest_ring_wins_the_body_and_the_pips_say_the_rest() -> None:
    """Frozen AND poisoned: the body shows the freeze and not the poison, and every pip shows,
    the Poison's slow included."""
    u = troop(1, stun=20, status=[("Poison", 3000), ("Freeze", 1000)])
    r = drawn(frame_of([u]))
    c = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    around = ring(r, c, b + 1, b + 9, below=b)
    assert (255, 255, 255) in around, "the freeze lost the body"
    assert DEFAULT.status_poison not in around, "a weaker ring was drawn over the stronger"
    assert {DEFAULT.status_freeze, DEFAULT.status_poison, DEFAULT.status_slow} <= pips(r, u)


def test_a_poisoned_unit_shows_the_poison_on_its_body_not_the_slow() -> None:
    """Poison also slows. Drawn slow-first, a poisoned unit would show only the slow ring."""
    u = troop(1, status=[("Poison", 3000)])
    r = drawn(frame_of([u]))
    c = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    around = ring(r, c, b + 1, b + 9, below=b)
    assert DEFAULT.status_poison in around and DEFAULT.status_slow not in around


def test_an_earthquake_is_drawn_in_its_own_colour() -> None:
    u = troop(1, status=[("Earthquake", 1000)])
    r = drawn(frame_of([u]))
    c = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    assert DEFAULT.spell_quake in ring(r, c, b + 1, b + 9, below=b)
    assert DEFAULT.status_poison not in pips(r, u), "an Earthquake pipped as a Poison"


def test_an_effect_this_viewer_cannot_name_still_gets_a_pip() -> None:
    """Never dropped: an engine buff added after the table was generated must still show."""
    u = troop(1, status=[("SomethingNew", 1000)])
    assert DEFAULT.status_other in pips(drawn(frame_of([u])), u, half_width=6)


def test_a_tornado_pull_gets_a_pip_of_its_own() -> None:
    """Two pips, the damage in Tornado's grey and the pull in a colour of its own. They were the
    same grey at first, which made them one effect twice to a reader, and let this test pass on
    the damage pip alone with the pull removed."""
    assert DEFAULT.status_pull != DEFAULT.spell_wind
    u = troop(1, status=[("Tornado", 500)])
    shown = pips(drawn(frame_of([u])), u)
    assert DEFAULT.status_pull in shown and DEFAULT.spell_wind in shown


def test_heal_and_shield_are_glyphs_at_the_corners_beside_a_ring() -> None:
    """Heal top right, shield top left, and both ALONGSIDE a ring effect: the first drawing had
    the shield as a thick white band, which read as Freeze's white rim."""
    u = troop(1, status=[("Heal", 1000), ("Rage", 1000)], shield=100)
    r = drawn(frame_of([u]))
    px, py = r.to_px(u.x, u.y, UPT, 0)
    b = body_px(r, u)
    right = box(r, px + 5, py - b - 9, px + b + 9, py - b + 3)
    left = box(r, px - b - 9, py - b - 9, px - 5, py - b + 3)
    assert DEFAULT.status_heal in right, "no heal glyph at the top right"
    assert DEFAULT.status_shield in left, "no shield glyph at the top left"
    assert DEFAULT.status_rage in ring(r, (px, py), b + 1, b + 9, below=b)


def test_a_frozen_unit_still_shows_whose_it_is() -> None:
    """The ice veil is translucent enough that a frozen enemy and a frozen friend differ."""
    blue = troop(1, team=0, stun=18, status=[("Freeze", 900)])
    red = troop(2, team=1, stun=18, status=[("Freeze", 900)])
    rb, rr = drawn(frame_of([blue])), drawn(frame_of([red]))
    cb = at(rb, rb.to_px(blue.x, blue.y, UPT, 0))
    cr = at(rr, rr.to_px(red.x, red.y, UPT, 0))
    assert cr[0] > cb[0] + 40, f"frozen red {cr} and frozen blue {cb} look alike"
    assert cb != DEFAULT.team[0], "no veil at all: the freeze is not on the body"


# ------------------------------------------------------------------ projectiles


def shot(name: str | None, splash: int = 0, team: int = 0) -> Projectile:
    # Flying straight to the right on seat 0: the tail is to the left of the dot.
    return Projectile(team=team, x=5000, y=11000, aim_x=9000, aim_y=11000, splash=splash, name=name)


def test_a_towers_bolt_has_a_white_core_and_an_unknown_firer_does_not() -> None:
    """name None is royalegym's "the engine did not record who fired this": it must never read
    as a tower's shot."""
    for name, core in (("tower", True), (None, False), ("Musketeer", False)):
        p = shot(name)
        r = drawn(frame_of(projectiles=[p]))
        centre = at(r, r.to_px(p.x, p.y, UPT, 0))
        assert (centre == DEFAULT.tower_shot_core) is core, f"{name!r} drew {centre}"


def test_the_tail_points_back_along_the_flight() -> None:
    p = shot("Musketeer")
    r = drawn(frame_of(projectiles=[p]))
    px, py = r.to_px(p.x, p.y, UPT, 0)
    assert at(r, (px - 6, py)) == DEFAULT.team[0], "no tail behind"
    assert at(r, (px + 6, py)) != DEFAULT.team[0], "a tail ahead"


def test_a_splash_is_a_patch_under_its_target_not_a_ring_on_it() -> None:
    """A thin ring round the target read as a mark on the unit standing there. The patch goes
    UNDER the units: the target's body keeps its exact team colour, the ground around it is
    tinted, and there is no opaque ring at the patch's edge."""
    target = troop(1, x=9000, y=11000, team=1)
    p = shot("Wizard", splash=1500)
    r = drawn(frame_of([target], projectiles=[p]))
    bare = drawn(frame_of([target]))
    tx, ty = r.to_px(target.x, target.y, UPT, 0)
    assert at(r, (tx, ty)) == DEFAULT.team[1], "the splash dyed its target"
    ground = (tx + 25, ty)  # inside the 36 px patch, beside the 10 px body, clear of its label
    assert r.surface.get_at(ground) != bare.surface.get_at(ground), "no patch on the ground"
    for d in range(30, 41):  # the patch's edge, both clear of the shot and of the label
        assert at(r, (tx + d, ty)) != DEFAULT.team[0] and at(r, (tx, ty - d)) != DEFAULT.team[0]


def test_an_area_spell_is_under_the_units_it_covers_and_as_big_as_the_card() -> None:
    """A Poison is 3.5 tiles across in the card data. It was drawn 1.5 for every area, so units
    poisoned by it stood on plain grass with the poison's marks on them."""
    u = troop(1, x=9000, y=11000, team=1)
    poison = spell("Poison", MOTION_AREA, 9000, 11000, 9000, 11000)
    r = drawn(frame_of([u], spells=[poison]))
    bare = drawn(frame_of([u]))
    px, py = r.to_px(u.x, u.y, UPT, 0)
    assert r.spell_radius_px(poison, UPT) == 3500 * SCALE // 1000
    assert at(r, (px, py)) == DEFAULT.team[1], "the cloud dyed the unit"
    for d in (22, 70):  # inside 1.5 tiles, and at 2.9 tiles: inside only at the card's size
        assert r.surface.get_at((px + d, py)) != bare.surface.get_at((px + d, py)), d


def test_a_radius_the_source_sends_beats_the_table_and_an_unknown_has_none() -> None:
    r = Renderer(scale=SCALE)
    sent = spell("Poison", MOTION_AREA, 0, 0, 0, 0)
    sent.extra["radius"] = 1000
    assert r.spell_radius_px(sent, UPT) == SCALE
    assert r.spell_radius_px(spell("NoSuchSpell", MOTION_AREA, 0, 0, 0, 0), UPT) is None


# ------------------------------------------------------------------ spell cards


@pytest.mark.parametrize(
    ("name", "motion", "want"),
    [
        ("Fireball", MOTION_AIRBORNE, ("spell_fire", "ball")),
        ("Arrows", MOTION_AIRBORNE, ("spell_arrows", "volley")),
        ("Rocket", MOTION_FLIGHT, ("spell_rocket", "streak")),
        ("The Log", MOTION_ROLLING, ("spell_log", "log")),
        ("Log", MOTION_ROLLING, ("spell_log", "log")),
        # The Barbarian Barrel is "BarbLog" in the card data and in a recording, and a
        # recording sends it with FLIGHT motion: the name alone has to find its style.
        ("BarbLog", MOTION_ROLLING, ("spell_log", "log")),
        ("BarbLog", MOTION_FLIGHT, ("spell_log", "log")),
        ("Poison", MOTION_AREA, ("spell_poison", "area")),
        ("Zap", MOTION_AREA, ("spell_zap", "bolt")),
        ("Lightning", MOTION_AREA, ("spell_lightning", "bolt")),
        ("SomeNewSpell", MOTION_AREA, ("spell", "area")),
        ("SomeNewSpell", MOTION_ROLLING, ("spell", "log")),
        ("SomeNewSpell", MOTION_FLIGHT, ("spell", "ball")),
    ],
)
def test_each_spell_card_has_its_family_and_an_unknown_one_its_motion(
    name: str, motion: int, want: tuple[str, str]
) -> None:
    assert spell_style(name, motion) == want


def test_each_spell_family_draws_its_own_shape() -> None:
    """Sampled where ONLY the family's shape reaches: clear of the dashed flight line and of the
    label under it (midtop at py + 7). A family drawn as another shape, or in another colour,
    leaves these pixels something else. The labels alone used to satisfy this test's first
    version, which checked only that each colour appeared somewhere."""
    fb = spell("Fireball", MOTION_AIRBORNE, 3000, 6000, 6000, 9000)  # up and right
    ar = spell("Arrows", MOTION_AIRBORNE, 9000, 6000, 12000, 9000)  # up and right
    rk = spell("Rocket", MOTION_FLIGHT, 15000, 6000, 15000, 10000, team=1)  # straight up
    zp = spell("Zap", MOTION_AREA, 9000, 24000, 9000, 24000)
    r = drawn(frame_of(spells=[fb, ar, rk, zp]))

    x, y = r.to_px(fb.x, fb.y, UPT, 0)
    assert at(r, (x - 3, y)) == DEFAULT.spell_fire, "the Fireball is not a ball"
    x, y = r.to_px(ar.x, ar.y, UPT, 0)
    assert at(r, (x + 6, y)) == DEFAULT.spell_arrows, "Arrows are not a volley of strokes"
    x, y = r.to_px(rk.x, rk.y, UPT, 0)
    assert at(r, (x, y + 5)) == DEFAULT.spell_rocket, "the Rocket has no streak behind it"
    x, y = r.to_px(zp.x, zp.y, UPT, 0)
    rr = r.area_radius_px(zp, UPT)
    edge = {at(r, (x, y - d)) for d in range(rr - 3, rr + 5)}
    # Checks the edge is AT the card's radius and in Zap's colour. Not that it is jagged: a
    # plain ring in the same place would pass, and the shape is left to the eye.
    assert DEFAULT.spell_zap in edge, "the Zap has no edge at its radius"
    everything = box(
        r,
        *r.layout.arena[:2],
        r.layout.arena[0] + r.layout.arena[2] - 1,
        r.layout.arena[1] + r.layout.arena[3] - 1,
    )
    assert DEFAULT.spell not in everything, "a known card fell back to the generic colour"


# ------------------------------------------------------------------ facing


def test_the_facing_tick_is_the_same_whatever_length_the_direction_arrives_at() -> None:
    """A recording sends a unit vector times 256 and the engine its facing in raw units. The
    tick is a direction, so a long vector and a short one draw the same pixels. The control
    first: a tick IS drawn, or two frames with none would be equal too."""
    short = drawn(frame_of([troop(1, direction=(1, 0))]))
    long = drawn(frame_of([troop(1, direction=(5000, 0))]))
    px, py = short.to_px(9000, 11000, UPT, 0)
    assert at(short, (px + 12, py)) == DEFAULT.direction_line, "no facing tick drawn"
    area = pygame.Rect(short.layout.arena)
    assert pygame.image.tobytes(short.surface.subsurface(area), "RGB") == pygame.image.tobytes(
        long.surface.subsurface(area), "RGB"
    )


# ------------------------------------------------------------------ what ARRIVES


def test_a_frame_from_before_status_and_projectiles_still_decodes() -> None:
    """Every source written before 2026-09-24 sends neither key. Both are trailing and
    defaulted, so an old sender's frame decodes to empty rather than failing."""
    d = msgspec.to_builtins(frame_of([troop(1)]))
    del d["projectiles"]
    del d["units"][0]["status"]
    f = msgspec.convert(d, Frame)
    assert f.projectiles == [] and f.units[0].status == []


def test_a_shot_with_no_firer_decodes_rather_than_refusing_the_frame() -> None:
    """royalegym sends name None for a shot the engine did not record the firer of. A str-only
    field would refuse the WHOLE frame over one unlabelled shot, and a replay would go blank."""
    d = msgspec.to_builtins(frame_of(projectiles=[shot("tower")]))
    d["projectiles"].append({**d["projectiles"][0], "name": None})
    f = msgspec.convert(d, Frame)
    assert [p.name for p in f.projectiles] == ["tower", None]


def test_status_and_projectiles_survive_the_wire() -> None:
    f = frame_of([troop(1, status=[("Freeze|ZapFreeze", 450)])], projectiles=[shot(None, 900)])
    back = model.decode_frame(model.encode_frame(f))
    assert back.units[0].status == [("Freeze|ZapFreeze", 450)]
    assert back.projectiles == f.projectiles


def test_a_royalegym_without_projectiles_replays_with_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A royalegym older than its projectile_dict: the replay plays with no shots rather than
    failing on the import. Stubbed as the real case is shaped -- the package and module import,
    the NAME is missing, which raises a plain ImportError -- and then with royalegym absent."""
    fr = SimpleNamespace(projectiles=[object()])
    monkeypatch.setitem(sys.modules, "royalegym", types.ModuleType("royalegym"))
    monkeypatch.setitem(sys.modules, "royalegym.viser", types.ModuleType("royalegym.viser"))
    assert TraceSource._projectiles(fr, lambda i: f"#{i}") == []
    monkeypatch.setitem(sys.modules, "royalegym.viser", None)
    assert TraceSource._projectiles(fr, lambda i: f"#{i}") == []


def test_a_trace_frames_shots_come_through_royalegyms_own_projectile_dict() -> None:
    viser = pytest.importorskip(
        "royalegym.viser", reason=f"{NOT_A_PASS}: needs royalegym to build a trace frame's shots"
    )
    protocol = pytest.importorskip("royalegym.protocol")
    if not hasattr(viser, "projectile_dict") or not hasattr(protocol, "ProjectileState"):
        pytest.skip(f"{NOT_A_PASS}: this royalegym predates projectiles (gym 1c68e6b)")
    ps = [
        protocol.ProjectileState(
            team=t, x=1, y=2, aim_x=3, aim_y=4, target_uid=u, splash=s, firer_card_id=f
        )
        for t, u, s, f in ((0, 7, 0, -1), (1, -1, 1500, -2), (0, 9, 0, 5))
    ]
    got = TraceSource._projectiles(SimpleNamespace(projectiles=ps), lambda i: f"card{i}")
    assert [(p.name, p.target, p.splash) for p in got] == [
        ("tower", 7, 0),
        (None, None, 1500),
        ("card5", 9, 0),
    ]


def test_a_recorded_shot_is_never_an_area() -> None:
    """A recording's shots arrive as Spells. An unaimed or arrived one used to take AREA motion,
    which the renderer now fills like a spell's ground; and the hero Musketeer's form id,
    203000014, sits above 28000000 and was read as a spell card. A spell card that has landed
    is still an area."""
    names = Names.live()
    row = {"side": 0, "x": 5000, "y": 5000, "projectile_x": -1, "projectile_y": -1}
    for cid in (-1, 26000001, 203000014):
        s = capture_spell({**row, "card_id": cid}, names)
        assert s.motion == MOTION_FLIGHT and s.name.endswith("shot"), (cid, s.name, s.motion)
    landed = capture_spell({**row, "card_id": 28000000}, names)
    assert landed.motion == MOTION_AREA and not landed.name.endswith("shot")


def test_a_stream_says_how_its_frames_were_sampled_only_when_told() -> None:
    """A replay recorded per decision never shows a shot shorter than one step, and a tower
    then looks idle. The status line says so when the publisher states its sampling, and says
    nothing at all when it does not: silence must not read as "every tick"."""
    src = StreamSource("127.0.0.1", 59996)
    try:
        for every, want in ((False, "per decision"), (True, "every tick"), (None, None)):
            f = frame_of()
            if every is not None:
                f.meta["frame_every_tick"] = every
            src.take_frame(model.encode_frame(f))
            line = src.status()
            if want is None:
                assert "per decision" not in line and "every tick" not in line, line
            else:
                assert want in line, line
    finally:
        src.close()
