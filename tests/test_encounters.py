"""Tests for the random encounter + loot generator (app/tools/encounters.py)."""
import pytest

from app.tools import encounters


def test_environments_come_from_the_data_file():
    envs = encounters.environments()
    assert "Dungeon" in envs and len(envs) >= 3


def test_generate_encounter_returns_one_of_that_environments_options():
    for env in encounters.environments():
        out = encounters.generate_encounter(env)
        assert out["environment"] == env
        assert out["encounter"] in encounters._DATA["environments"][env]


def test_unknown_environment_raises():
    with pytest.raises(ValueError):
        encounters.generate_encounter("The Moon")


@pytest.mark.parametrize("tier", ["low", "mid", "high"])
def test_loot_coin_is_a_multiple_of_the_tier_multiplier(tier):
    expr = encounters._DATA["loot"]["coin_by_tier"][tier]
    multiplier = int(expr.split(" x ")[1].split()[0])
    for _ in range(25):
        loot = encounters.generate_loot(tier, include_item=False)
        assert loot["coin"] % multiplier == 0 and loot["coin"] > 0
        assert "item" not in loot


def test_loot_item_roll_always_lands_on_a_rarity_row():
    for _ in range(200):
        loot = encounters.generate_loot("mid", include_item=True)
        assert 1 <= loot["item"]["roll"] <= 100
        assert loot["item"]["rarity"]


def test_unknown_loot_tier_raises():
    with pytest.raises(ValueError):
        encounters.generate_loot("legendary-ish")
