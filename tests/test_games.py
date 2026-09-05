"""Tests for the games.json Series->game mapping (app/library/games.py) -
the logic behind the Manage Games page's rename/merge/move actions."""
import pytest

from app.library import games as games_module


def test_get_game_known_key(games_config):
    game = games_module.get_game("dnd5e")
    assert game["name"] == "D&D 5E"


def test_get_game_fallback_key_returns_synthetic_entry(games_config):
    game = games_module.get_game("unsorted")
    assert game["name"] == "Unsorted / Other"


def test_get_game_unknown_key_returns_none(games_config):
    assert games_module.get_game("not-a-real-game") is None


def test_game_key_for_series_known_and_unknown(games_config):
    assert games_module.game_key_for_series("Player Handbooks") == "dnd5e"
    assert games_module.game_key_for_series("Some Unmapped Series") == "unsorted"
    assert games_module.game_key_for_series(None) == "unsorted"


def test_game_key_for_series_is_case_and_whitespace_insensitive(games_config):
    assert games_module.game_key_for_series("  player handbooks  ") == "dnd5e"


def test_all_game_choices_includes_fallback_bucket(games_config):
    keys = {g["key"] for g in games_module.all_game_choices()}
    assert {"dnd5e", "rifts", "unsorted"} <= keys


def test_slugify():
    assert games_module.slugify("Dungeons & Dragons 5E") == "dungeons_dragons_5e"
    assert games_module.slugify("   ") == "game"


def test_unique_key_avoids_collisions(games_config):
    assert games_module.unique_key("dnd5e") == "dnd5e_2"
    assert games_module.unique_key("brand_new") == "brand_new"


def test_unique_key_avoids_the_fallback_key_too(games_config):
    assert games_module.unique_key("unsorted") == "unsorted_2"


def test_create_game_persists_and_returns_a_unique_key(games_config):
    key = games_module.create_game("Blades in the Dark", system="generic")
    game = games_module.get_game(key)
    assert game["name"] == "Blades in the Dark"
    assert game["series"] == []


def test_create_game_blank_name_raises(games_config):
    with pytest.raises(ValueError):
        games_module.create_game("   ")


def test_rename_game(games_config):
    games_module.rename_game("dnd5e", "D&D Fifth Edition")
    assert games_module.get_game("dnd5e")["name"] == "D&D Fifth Edition"


def test_rename_fallback_game(games_config):
    games_module.rename_game("unsorted", "Misc")
    assert games_module.get_game("unsorted")["name"] == "Misc"


def test_rename_unknown_game_raises(games_config):
    with pytest.raises(ValueError):
        games_module.rename_game("not-a-real-game", "New Name")


def test_merge_games_folds_series_into_target(games_config):
    moved = games_module.merge_games("rifts", "dnd5e")
    assert moved == ["Rifts Sourcebooks"]
    assert games_module.get_game("rifts") is None
    assert "Rifts Sourcebooks" in games_module.get_game("dnd5e")["series"]


def test_merge_games_into_fallback_dissolves_the_source(games_config):
    games_module.merge_games("rifts", "unsorted")
    assert games_module.get_game("rifts") is None
    assert games_module.game_key_for_series("Rifts Sourcebooks") == "unsorted"


def test_merge_games_into_itself_raises(games_config):
    with pytest.raises(ValueError):
        games_module.merge_games("rifts", "rifts")


def test_merge_games_unknown_source_raises(games_config):
    with pytest.raises(ValueError):
        games_module.merge_games("not-a-real-game", "dnd5e")


def test_move_series_to_another_game(games_config):
    games_module.move_series("Rifts Sourcebooks", "dnd5e")
    assert games_module.game_key_for_series("Rifts Sourcebooks") == "dnd5e"
    assert "Rifts Sourcebooks" not in games_module.get_game("rifts")["series"]


def test_move_series_back_to_fallback(games_config):
    games_module.move_series("Rifts Sourcebooks", "unsorted")
    assert games_module.game_key_for_series("Rifts Sourcebooks") == "unsorted"


def test_move_series_unknown_target_raises(games_config):
    with pytest.raises(ValueError):
        games_module.move_series("Rifts Sourcebooks", "not-a-real-game")
