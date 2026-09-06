"""Tests for the random NPC generator (app/tools/npc_generator.py) - mainly
that game-specific flavor stays out of the wrong games (the reported bug:
Rifts classes like Juicer leaking into Palladium Fantasy) and that each
system produces its own sensible combat/skill shape."""
import random

import pytest

from app.tools import npc_generator as npc


def _all_roles(game_key, system, n=200):
    return {npc.generate(game_key=game_key, system=system, game_name="x")["role"] for _ in range(n)}


def test_palladium_fantasy_never_gets_rifts_only_roles(db):
    roles = _all_roles("palladium_fantasy", "palladium")
    rifts_only = set(npc._DATA["roles"]["rifts"]) - set(npc._DATA["roles"]["palladium_fantasy"])
    assert roles.issubset(set(npc._DATA["roles"]["palladium_fantasy"]))
    assert not (roles & rifts_only)
    assert "Juicer on the edge" not in roles


def test_rifts_and_palladium_fantasy_role_lists_are_distinct(db):
    rifts_roles = set(npc._DATA["roles"]["rifts"])
    fantasy_roles = set(npc._DATA["roles"]["palladium_fantasy"])
    assert rifts_roles.isdisjoint(fantasy_roles)


def test_unmapped_palladium_game_falls_back_to_neutral_list(db):
    # A palladium-system game with no dedicated role list of its own should
    # get the neutral system-level list, not something else's dedicated one.
    roles = _all_roles("some_future_palladium_game", "palladium")
    assert roles.issubset(set(npc._DATA["roles"]["palladium"]))


def test_savage_rifts_aliases_rifts_flavor(db):
    names_seen = set()
    for _ in range(100):
        result = npc.generate(game_key="savage_rifts", system="savage_worlds", game_name="Savage Rifts")
        names_seen.add(result["role"])
    assert names_seen.issubset(set(npc._DATA["roles"]["rifts"]))


def test_generic_system_uses_generic_lists(db):
    roles = _all_roles(None, "generic")
    assert roles.issubset(set(npc._DATA["roles"]["generic"]))


@pytest.mark.parametrize("game_key,system,expected_keys", [
    (None, "generic", {"Level", "Attack Bonus", "Defense", "Hit Points", "Damage"}),
    (None, "d20", {"Level", "Armor Class", "Hit Points", "Attack Bonus", "Damage"}),
])
def test_combat_block_has_expected_keys_for_system(db, game_key, system, expected_keys):
    result = npc.generate(game_key=game_key, system=system, game_name="x")
    assert set(result["combat"].keys()) == expected_keys


def test_palladium_combat_uses_mdc_for_rifts(db):
    result = npc.generate(game_key="rifts", system="palladium", game_name="Rifts")
    assert "M.D.C." in result["combat"]
    assert "S.D.C./Hit Points" not in result["combat"]


def test_palladium_combat_uses_sdc_for_palladium_fantasy(db):
    result = npc.generate(game_key="palladium_fantasy", system="palladium", game_name="Palladium Fantasy")
    assert "S.D.C./Hit Points" in result["combat"]
    assert "M.D.C." not in result["combat"]


def test_savage_worlds_combat_shape(db):
    result = npc.generate(game_key="savage_rifts", system="savage_worlds", game_name="Savage Rifts")
    combat = result["combat"]
    assert set(combat.keys()) == {"Rank", "Parry", "Toughness", "Pace", "Fighting/Shooting Die", "Damage"}
    assert combat["Rank"] in ["Novice", "Seasoned", "Veteran", "Heroic", "Legendary"]


def test_forged_in_the_dark_combat_shape_and_no_ability_scores(db):
    result = npc.generate(game_key="blades_in_the_dark", system="forged in the dark", game_name="Blades in the Dark")
    assert set(result["combat"].keys()) == {"Action rating (relevant)", "Harm (their weapon)"}
    assert result["stats"] == {}


def test_savage_worlds_ability_scores_use_five_traits(db):
    result = npc.generate(game_key="savage_rifts", system="savage_worlds", game_name="Savage Rifts")
    assert set(result["stats"].keys()) == {"Agility", "Smarts", "Spirit", "Strength", "Vigor"}


def test_weapons_carried_are_nonempty_and_shaped(db):
    result = npc.generate(game_key="palladium_fantasy", system="palladium", game_name="Palladium Fantasy")
    assert 1 <= len(result["weapons"]) <= 2
    for w in result["weapons"]:
        assert "name" in w and "damage" in w


def test_weapons_use_setting_appropriate_list(db):
    fantasy_weapon_names = {w["name"] for w in npc._DATA["weapons"]["palladium_fantasy"]}
    rifts_weapon_names = {w["name"] for w in npc._DATA["weapons"]["rifts"]}
    assert fantasy_weapon_names.isdisjoint(rifts_weapon_names)
    for _ in range(30):
        result = npc.generate(game_key="palladium_fantasy", system="palladium", game_name="x")
        for w in result["weapons"]:
            assert w["name"] in fantasy_weapon_names


def test_skills_are_nonempty_and_shaped(db):
    result = npc.generate(game_key="rifts", system="palladium", game_name="Rifts")
    assert len(result["skills"]) >= 1
    for s in result["skills"]:
        assert "name" in s and "value" in s


def test_palladium_skill_values_are_percentages(db):
    result = npc.generate(game_key="rifts", system="palladium", game_name="Rifts")
    for s in result["skills"]:
        assert s["value"].endswith("%")


def test_fantasy_skill_bucket_excludes_sci_fi_skills(db):
    sci_fi_only = set(npc._DATA["skills"]["rifts"]) - set(npc._DATA["skills"]["palladium_fantasy"])
    seen = set()
    for _ in range(100):
        result = npc.generate(game_key="palladium_fantasy", system="palladium", game_name="x")
        seen.update(s["name"] for s in result["skills"])
    assert not (seen & sci_fi_only)


def test_no_surname_games_use_single_name(db):
    for _ in range(30):
        result = npc.generate(game_key="tmnt", system="palladium", game_name="TMNT")
        assert " " not in result["name"]
    for _ in range(30):
        result = npc.generate(game_key="valley_pharaohs", system="palladium", game_name="Valley of the Pharaohs")
        assert " " not in result["name"]


def test_other_games_use_first_and_surname(db):
    result = npc.generate(game_key="palladium_fantasy", system="palladium", game_name="Palladium Fantasy")
    assert " " in result["name"]


def test_level_is_within_expected_range(db):
    for _ in range(50):
        result = npc.generate(game_key=None, system="d20", game_name="x")
        assert 1 <= result["combat"]["Level"] <= 8


def test_roll_ability_scores_default_count(db):
    scores = npc.roll_ability_scores()
    assert len(scores) == 6
    assert all(3 <= s <= 18 for s in scores)


def test_roll_ability_scores_custom_count(db):
    scores = npc.roll_ability_scores(5)
    assert len(scores) == 5


def test_generate_includes_game_and_system_passthrough(db):
    result = npc.generate(game_key="dnd5e", system="d20", game_name="D&D 5th Edition")
    assert result["game"] == "D&D 5th Edition"
    assert result["system"] == "d20"


def test_generate_defaults_system_to_generic_when_falsy(db):
    result = npc.generate(game_key=None, system=None, game_name=None)
    assert result["system"] == "generic"
