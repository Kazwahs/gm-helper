"""Tests for the appearance/theme settings (app/theme.py)."""
import pytest

from app import theme


def test_default_theme_is_tavern(db):
    assert theme.get_theme_name() == "tavern"


def test_set_and_get_theme_name(db):
    theme.set_theme_name("arcane")
    assert theme.get_theme_name() == "arcane"


def test_set_unknown_theme_raises(db):
    with pytest.raises(ValueError):
        theme.set_theme_name("nonexistent")
    # unchanged
    assert theme.get_theme_name() == "tavern"


def test_bad_stored_theme_name_falls_back_to_default(db):
    # Simulate a corrupted/old setting value that isn't a known preset.
    from app import database
    database.set_setting("theme_name", "some-removed-theme")
    assert theme.get_theme_name() == "tavern"


def test_custom_vars_default_to_tavern_base(db):
    vars_ = theme.get_custom_vars()
    assert vars_ == theme.DEFAULT_CUSTOM_BASE
    assert set(vars_.keys()) == set(theme.CUSTOM_KEYS)


def test_set_custom_vars_round_trip(db):
    saved = theme.set_custom_vars({"accent": "#ff0000", "bg": "#111111"})
    assert saved["accent"] == "#ff0000"
    assert saved["bg"] == "#111111"
    # untouched keys keep their default
    assert saved["good"] == theme.DEFAULT_CUSTOM_BASE["good"]

    reread = theme.get_custom_vars()
    assert reread["accent"] == "#ff0000"
    assert reread["bg"] == "#111111"


def test_set_custom_vars_partial_update_preserves_previous(db):
    theme.set_custom_vars({"accent": "#ff0000"})
    theme.set_custom_vars({"bg": "#222222"})
    result = theme.get_custom_vars()
    assert result["accent"] == "#ff0000"
    assert result["bg"] == "#222222"


def test_set_custom_vars_unknown_key_ignored(db):
    saved = theme.set_custom_vars({"not-a-real-key": "#ff0000"})
    assert "not-a-real-key" not in saved


def test_set_custom_vars_invalid_hex_raises(db):
    with pytest.raises(ValueError):
        theme.set_custom_vars({"accent": "not-a-color"})
    with pytest.raises(ValueError):
        theme.set_custom_vars({"accent": "#ff00zz"})
    with pytest.raises(ValueError):
        theme.set_custom_vars({"accent": "red"})


def test_set_custom_vars_accepts_3_and_6_digit_hex(db):
    saved = theme.set_custom_vars({"accent": "#f00"})
    assert saved["accent"] == "#f00"
    saved = theme.set_custom_vars({"accent": "#ff0000"})
    assert saved["accent"] == "#ff0000"


def test_set_custom_vars_not_a_dict_raises(db):
    with pytest.raises(ValueError):
        theme.set_custom_vars("not-a-dict")


def test_get_custom_vars_ignores_corrupted_stored_json(db):
    from app import database
    database.set_setting("custom_theme_vars", "{not valid json")
    assert theme.get_custom_vars() == theme.DEFAULT_CUSTOM_BASE


def test_all_presets_have_display_names(db):
    for key in ("tavern", "parchment", "arcane", "daylight", "custom"):
        assert key in theme.PRESETS
        assert theme.PRESETS[key]
