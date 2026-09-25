"""Two tables from the engine's card data, so the viewer draws what the engine DOES.

GENERATED from RoyaleSim's data/derived/cards-15.535.29.json (vintage 15.535.29). Nothing here is
a guess from a name: each value is read from the numbers the engine runs.
``tests/test_status_effects.py`` re-derives both tables from that file when it is on disk,
restating the rules independently, and names any entry that has drifted.

BUFF_KINDS: what each buff does to a unit, as status kinds, for ``Unit.status``.

    speed -100          HELD: the engine stops the unit (status.rs ``compose`` gives 0).
                        "freeze" when the buff is a Freeze (its name says freeze, not zap),
                        "stun" for every other hold: Zap, the Electro Wizard, snares, Stun.
                        The engine holds them all the same way; the game shows them differently,
                        and inside one engine state the name is the only thing that tells them
                        apart.
    damage/s > 0        poison  (damage over time: Poison, Earthquake, Tornado)
    heal/s > 0          heal
    speed or hit < 0    slow    (not for a hold, which is already stopped)
    speed or hit > 100  rage    (a positive multiplier counts only ABOVE 100: 100 is the
                                 identity and 0 is a blank column, not a speed)
    attract > 0         pull    (Tornado)

A buff can do several: Poison is ("poison", "slow"), because it also slows by 15 %. A buff that
does none of these is left out, and draws as "other".

SPELL_RADIUS_MILLI: each spell card's radius in millitiles (1000 = one tile), keyed by its name
lower-cased with only letters and digits kept. The area effect's radius where it has one, else
the spell's, else, for a roll, its half-width across the direction, else its projectile's
splash. The viewer converts with the frame's units per tile.
"""

from __future__ import annotations

VINTAGE = "15.535.29"

BUFF_KINDS: dict[str, tuple[str, ...]] = {
    "archerqueenrapid": ("slow", "rage"),
    "babydragon_ev1_wind_buff_negative": ("slow",),
    "babydragon_ev1_wind_buff_positive": ("rage",),
    "barbarian_evo_rage": ("rage",),
    "batsev1_heal": ("heal",),
    "battlehealerall": ("heal",),
    "battlehealerself": ("heal",),
    "battlehealerspawnbuff": ("heal",),
    "berserk_rage": ("rage",),
    "bolasnare": ("slow",),
    "buff_goblinstein_doctor": ("stun",),
    "cannon_ev1_barrage_damage_buff": ("poison",),
    "clone": ("stun",),
    "continuefreeze": ("freeze",),
    "darkelixirbuff": ("rage",),
    "earthquake": ("poison", "slow"),
    "electro_dragon_hit_buff": ("stun",),
    "electrogiantzapfreeze": ("stun",),
    "event_freeze": ("freeze",),
    "event_lovebuff": ("heal",),
    "fireballprojectile": ("slow",),
    "firecrackerfireworks_ev1": ("poison", "slow"),
    "freeze": ("freeze",),
    "goblincursedamage": ("poison", "slow"),
    "goldenknightcharge": ("rage",),
    "heal": ("heal",),
    "healmodeheal": ("heal",),
    "healspiritbuff": ("heal",),
    "hunter_ev1_bear_trap_snare_large": ("stun",),
    "hunter_ev1_bear_trap_snare_medium": ("stun",),
    "hunter_ev1_bear_trap_snare_no_effect": ("stun",),
    "hunter_ev1_bear_trap_snare_small": ("stun",),
    "icewizardcold": ("slow",),
    "icewizardslowdown": ("slow",),
    "littleprincelvl1": ("rage",),
    "littleprincelvlmax": ("rage",),
    "minionhorde_ev1_ghostbuff": ("slow",),
    "mysterybuff_heal": ("heal",),
    "mysterybuff_poison": ("poison", "slow"),
    "mysterybuff_zapfreeze": ("stun",),
    "neutral_rage": ("rage",),
    "notinusebuff7": ("rage",),
    "notinusebuff8": ("rage",),
    "notinusebuff9": ("rage",),
    "pekkaev1_healmax": ("heal",),
    "pekkaev1_healmed": ("heal",),
    "pekkaev1_healmin": ("heal",),
    "poison": ("poison", "slow"),
    "poisonmodepoison": ("poison",),
    "princeragebuff1": ("rage",),
    "princeragebuff2": ("rage",),
    "princeragebuff3": ("rage",),
    "rage": ("rage",),
    "ragemoderage": ("rage",),
    "regent": ("heal",),
    "ronin_reflect_stun_buff": ("stun",),
    "snowball_spell_ev1_after_release": ("slow",),
    "snowball_spell_ev1_hit": ("poison", "slow"),
    "snowball_spell_ev1_incapacitate_target": ("stun",),
    "stun": ("stun",),
    "superarchertornado": ("poison", "pull"),
    "superhogjumpcharge": ("rage",),
    "superminipekkapancakesheal": ("heal",),
    "superrage": ("rage",),
    "tesla_ev1_withdamage": ("stun", "poison"),
    "tornado": ("poison", "pull"),
    "valkyrie_minitornado_ev1": ("poison", "pull"),
    "vines_trap_snare_base": ("stun", "poison"),
    "vines_trap_snare_large": ("stun", "poison"),
    "vines_trap_snare_medium": ("stun", "poison"),
    "vines_trap_snare_no_effect": ("stun",),
    "vines_trap_snare_small": ("stun", "poison"),
    "vines_trap_snare_xlarge": ("stun", "poison"),
    "vines_trap_snare_xxlarge": ("stun", "poison"),
    "warmup": ("heal",),
    "witch_ev1_heal_buff": ("heal",),
    "zap_ev1_withdamage": ("stun", "poison"),
    "zapfreeze": ("stun",),
}

SPELL_RADIUS_MILLI: dict[str, int] = {
    "arrows": 3500,
    "barblog": 1300,
    "clone": 3000,
    "darkmagic": 2500,
    "earthquake": 3500,
    "fireball": 2500,
    "freeze": 3000,
    "globalclone": 30000,
    "goblinbarrel": 1500,
    "goblincurse": 3000,
    "graveyard": 4000,
    "lightning": 3500,
    "log": 1950,
    "poison": 3500,
    "rage": 3000,
    "rocket": 2000,
    "royaldelivery": 3000,
    "snowball": 2500,
    "tornado": 5500,
    "vines": 2500,
    "warmspell": 4000,
    "zap": 2500,
}
