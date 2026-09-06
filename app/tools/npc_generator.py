"""Random NPC generator.

Flavor (name/role/weapon/skill) is looked up in tiers - see the "_comment"
in app/data/npc_data.json for the exact order. The key thing this fixes:
role and gear used to be keyed only by a game's broad "system" hint
(d20/palladium/generic), which meant every Palladium-system game shared one
role list - so a Palladium Fantasy knight could roll "Juicer on the edge",
a class that only exists in Rifts. Now a game gets its own dedicated list
first (rifts, palladium_fantasy, tmnt, ...) and only falls back to the
generic system-level list when nothing more specific exists.

Combat numbers (attack/defense/damage) are generated per system, since the
actual math is genuinely different: a d20 game wants an attack bonus and
AC, Palladium wants Strike/Parry/Dodge and S.D.C. or M.D.C., Savage Worlds
wants Parry/Toughness and a trait die, and Forged in the Dark (Blades in
the Dark) doesn't roll static defenses at all - it uses action dice and
harm levels instead.
"""
import json
import random

from .. import config

with open(config.NPC_DATA_PATH, "r", encoding="utf-8") as f:
    _DATA = json.load(f)

# Some game keys don't have their own flavor list but clearly belong with
# one that does (Savage Rifts is the Rifts setting on a different rules
# engine) - route them there for names/roles/weapons without duplicating
# the whole dataset. Combat math still follows `system`, not this alias.
_ALIAS = {
    "savage_rifts": "rifts",
}

# Games whose Palladium-system flavor is unmistakably high-tech/post-apoc
# (energy weapons, computers, robots) rather than fantasy or mundane -
# routes them to the "rifts" skill bucket without needing their own copy.
_SCI_FI_SKILL_KEYS = {
    "rifts", "savage_rifts", "robotech", "splicers", "systems_failure",
    "mechanoids", "cosmic_enforcers", "heroes_unlimited", "nightbane",
    "beyond_supernatural", "dead_reign", "recon", "manhunter",
}
# Games whose flavor is period/medieval-fantasy enough to want Palladium
# Fantasy's skill list (weapon proficiencies, heraldry, lore) instead.
_FANTASY_SKILL_KEYS = {"palladium_fantasy", "valley_pharaohs"}

# Palladium-system games that use Mega-Damage (M.D.C.) rather than the
# standard Hit Points/S.D.C. scale.
_MDC_GAME_KEYS = {
    "rifts", "savage_rifts", "robotech", "splicers", "systems_failure",
    "mechanoids", "cosmic_enforcers",
}

_NO_SURNAME_GAMES = set(_DATA.get("no_surname_games", []))
_MODERN_SURNAME_GAMES = {
    "rifts", "savage_rifts", "heroes_unlimited", "nightbane", "beyond_supernatural",
    "dead_reign", "recon", "robotech", "splicers", "systems_failure", "mechanoids",
    "manhunter", "cosmic_enforcers",
}


def _tiered(section, game_key, system, fallback_key=None):
    """Look up a flavor list, trying (in order): the exact game key, a
    manual fallback/alias key, the system-level list, then 'generic'.
    Every section has a 'generic' entry so this can't come up empty."""
    lists = _DATA[section]
    game_key = _ALIAS.get(game_key, game_key)
    for key in (game_key, fallback_key, system, "generic"):
        if key and key in lists:
            return lists[key]
    return next(iter(lists.values()))


def roll_ability_scores(count=6):
    """Classic 4d6-drop-lowest. System-agnostic; callers relabel these as
    O.C.C. stats, ability scores, attributes, traits, etc."""
    scores = []
    for _ in range(count):
        rolls = sorted(random.randint(1, 6) for _ in range(4))
        scores.append(sum(rolls[1:]))
    return scores


def _roll_stats(system):
    if system == "d20":
        labels = ["Str", "Dex", "Con", "Int", "Wis", "Cha"]
        return dict(zip(labels, roll_ability_scores(6)))
    if system == "palladium":
        labels = ["IQ", "ME", "MA", "PS", "PP", "PE"]
        return dict(zip(labels, roll_ability_scores(6)))
    if system == "savage_worlds":
        labels = ["Agility", "Smarts", "Spirit", "Strength", "Vigor"]
        return dict(zip(labels, roll_ability_scores(5)))
    if system == "forged in the dark":
        # Blades' Insight/Prowess/Resolve are aggregates of its action
        # dice, not independently rolled stats - nothing sensible to fake.
        return {}
    labels = ["Score 1", "Score 2", "Score 3", "Score 4", "Score 5", "Score 6"]
    return dict(zip(labels, roll_ability_scores(6)))


def _weighted_level(max_level=8):
    """Skews toward low-to-mid level, with a long tail so an occasional
    boss-tier NPC can still show up."""
    weights = [max(1, max_level - lvl + 2) for lvl in range(1, max_level + 1)]
    return random.choices(range(1, max_level + 1), weights=weights)[0]


def _d20_combat(level, weapon):
    prof = 2 + (level - 1) // 4
    ability_mod = random.randint(-1, 4)
    attack_bonus = prof + ability_mod
    ac = 10 + random.randint(0, 4) + max(0, ability_mod)
    hp = sum(random.randint(1, 8) for _ in range(level)) + level
    dmg_bonus = max(0, ability_mod)
    damage = weapon["damage"] + (f"+{dmg_bonus}" if dmg_bonus else "")
    return {
        "Level": level,
        "Armor Class": ac,
        "Hit Points": hp,
        "Attack Bonus": f"+{attack_bonus}",
        "Damage": damage,
    }


def _palladium_combat(level, weapon, mega_damage):
    strike = random.randint(0, 3) + level // 2
    parry = random.randint(0, 3) + level // 2
    dodge = random.randint(0, 3) + level // 2
    if mega_damage:
        hp_label, hp = "M.D.C.", random.randint(20, 40) + level * random.randint(8, 15)
    else:
        hp_label, hp = "S.D.C./Hit Points", random.randint(15, 30) + level * random.randint(4, 8)
    return {
        "Level": level,
        "Strike": f"+{strike}",
        "Parry": f"+{parry}",
        "Dodge": f"+{dodge}",
        hp_label: hp,
        "Damage": weapon["damage"],
    }


def _savage_combat(weapon):
    rank = random.choices(
        ["Novice", "Seasoned", "Veteran", "Heroic", "Legendary"],
        weights=[45, 30, 15, 7, 3],
    )[0]
    return {
        "Rank": rank,
        "Parry": random.randint(4, 8),
        "Toughness": random.randint(4, 9),
        "Pace": 6,
        "Fighting/Shooting Die": random.choice(["d4", "d6", "d8", "d10", "d12"]),
        "Damage": weapon["damage"],
    }


def _fitd_combat(weapon):
    return {
        "Action rating (relevant)": random.choice(["1d", "2d", "3d"]),
        "Harm (their weapon)": weapon["damage"],
    }


def _generic_combat(level, weapon):
    return {
        "Level": level,
        "Attack Bonus": f"+{level // 2 + random.randint(0, 2)}",
        "Defense": 10 + random.randint(0, 5),
        "Hit Points": 10 + level * random.randint(3, 6),
        "Damage": weapon["damage"],
    }


def _roll_skills(game_key, system, level, count=2):
    if game_key in _FANTASY_SKILL_KEYS:
        fallback = "palladium_fantasy"
    elif game_key in _SCI_FI_SKILL_KEYS:
        fallback = "rifts"
    else:
        fallback = None
    names = _tiered("skills", game_key, system, fallback_key=fallback)
    picks = random.sample(names, min(count, len(names)))

    skills = []
    for name in picks:
        if system == "palladium":
            value = f"{min(98, 30 + level * 8 + random.randint(0, 15))}%"
        elif system == "savage_worlds":
            value = random.choice(["d4", "d6", "d8", "d10"])
        elif system == "forged in the dark":
            value = random.choice(["0d", "1d", "2d"])
        else:
            value = f"+{level // 2 + random.randint(0, 3)}"
        skills.append({"name": name, "value": value})
    return skills


def generate(game_key=None, system="generic", game_name=None):
    system = system or "generic"

    first_names = _tiered("first_names", game_key, system)
    roles = _tiered("roles", game_key, system)

    resolved_key = _ALIAS.get(game_key, game_key)
    if resolved_key in _NO_SURNAME_GAMES:
        name = random.choice(first_names)
    else:
        surnames = _DATA["surnames_modern"] if resolved_key in _MODERN_SURNAME_GAMES else _DATA["surnames"]
        name = f"{random.choice(first_names)} {random.choice(surnames)}"

    role = random.choice(roles)
    quirk = random.choice(_DATA["quirks"])
    motivation = random.choice(_DATA["motivations"])
    alignment = random.choice(_DATA["alignments"])
    stats = _roll_stats(system)

    level = _weighted_level()

    weapons_list = _tiered("weapons", game_key, system)
    num_weapons = 2 if len(weapons_list) >= 2 and random.random() < 0.3 else 1
    carried = random.sample(weapons_list, min(num_weapons, len(weapons_list)))
    primary_weapon = carried[0]

    if system == "d20":
        combat = _d20_combat(level, primary_weapon)
    elif system == "palladium":
        combat = _palladium_combat(level, primary_weapon, resolved_key in _MDC_GAME_KEYS)
    elif system == "savage_worlds":
        combat = _savage_combat(primary_weapon)
    elif system == "forged in the dark":
        combat = _fitd_combat(primary_weapon)
    else:
        combat = _generic_combat(level, primary_weapon)

    skills = _roll_skills(game_key, system, level)

    return {
        "name": name,
        "role": role,
        "quirk": quirk,
        "motivation": motivation,
        "alignment": alignment,
        "stats": stats,
        "game": game_name,
        "system": system,
        "combat": combat,
        "skills": skills,
        "weapons": carried,
    }
