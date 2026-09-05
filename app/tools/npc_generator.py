"""Random NPC generator. Uses the current game's `system` hint (from
games.json) to pick flavor-appropriate name/role lists, but always falls
back to the generic list so it works for any game, including the
'Unsorted / Other' bucket."""
import json
import random

from .. import config

with open(config.NPC_DATA_PATH, "r", encoding="utf-8") as f:
    _DATA = json.load(f)


def _pick_list(bucket, system):
    lists = _DATA[bucket]
    return lists.get(system) or lists.get("generic") or next(iter(lists.values()))


def roll_ability_scores():
    """Classic 4d6-drop-lowest, six scores. System-agnostic; the UI can
    relabel these as O.C.C. stats, ability scores, attributes, etc."""
    scores = []
    for _ in range(6):
        rolls = sorted(random.randint(1, 6) for _ in range(4))
        scores.append(sum(rolls[1:]))
    return scores


def generate(system="generic", game_name=None):
    system = system or "generic"
    first_names = _pick_list("first_names", system)
    roles = _pick_list("roles", system)

    name = f"{random.choice(first_names)} {random.choice(_DATA['surnames'])}"
    role = random.choice(roles)
    quirk = random.choice(_DATA["quirks"])
    motivation = random.choice(_DATA["motivations"])
    alignment = random.choice(_DATA["alignments"])
    scores = roll_ability_scores()

    stat_labels = (
        ["Str", "Dex", "Con", "Int", "Wis", "Cha"]
        if system == "d20"
        else ["IQ", "ME", "MA", "PS", "PP", "PE", "PB", "Spd"][:6]
        if system == "palladium"
        else ["Score 1", "Score 2", "Score 3", "Score 4", "Score 5", "Score 6"]
    )
    stats = dict(zip(stat_labels, scores))

    return {
        "name": name,
        "role": role,
        "quirk": quirk,
        "motivation": motivation,
        "alignment": alignment,
        "stats": stats,
        "game": game_name,
        "system": system,
    }
