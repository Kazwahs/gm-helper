"""Random encounter + loot generator, driven by data/encounter_tables.json
so GMs (or users) can edit the tables without touching code."""
import json
import random

from .. import config
from .dice import roll_group

with open(config.ENCOUNTER_DATA_PATH, "r", encoding="utf-8") as f:
    _DATA = json.load(f)


def environments():
    return list(_DATA["environments"].keys())


def generate_encounter(environment):
    options = _DATA["environments"].get(environment)
    if not options:
        raise ValueError(f"Unknown environment: {environment!r}")
    return {"environment": environment, "encounter": random.choice(options)}


def generate_loot(tier="mid", include_item=True):
    coin_expr = _DATA["loot"]["coin_by_tier"].get(tier)
    if not coin_expr:
        raise ValueError(f"Unknown loot tier: {tier!r}")

    # coin_expr looks like "2d6 x 10 in local currency" - pull the dice part out.
    dice_part = coin_expr.split(" x ")[0]
    multiplier = int(coin_expr.split(" x ")[1].split()[0])
    dice_result = roll_group(dice_part)
    coin_total = dice_result["total"] * multiplier

    result = {
        "tier": tier,
        "coin": coin_total,
        "coin_formula": coin_expr,
    }

    if include_item:
        roll = random.randint(1, 100)
        for entry in _DATA["loot"]["item_rarity_table"]:
            lo, hi = (entry["roll"].split("-") + [entry["roll"]])[:2]
            if int(lo) <= roll <= int(hi):
                result["item"] = {"rarity": entry["rarity"], "example": entry["example"], "roll": roll}
                break

    return result
