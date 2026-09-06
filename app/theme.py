"""Appearance settings: which built-in theme is active, plus the one
custom theme slot a user can tune to taste.

The whole visual system leans on CSS custom properties already defined once
in style.css's :root block (--bg, --accent, etc.), so "a theme" is just a
named set of values for those properties. Built-in themes live entirely in
style.css as `[data-theme="..."]` blocks; this module only needs to know
their names well enough to validate a choice. The "custom" theme is the one
preset whose values live in the database instead of the stylesheet, since
the user can change them - CUSTOM_KEYS lists exactly which variables are
tunable, and DEFAULT_CUSTOM_BASE seeds the customizer with Tavern's own
palette as a sensible starting point.
"""
import json
import re

from . import database

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# key -> display name, in the order they should be offered.
PRESETS = {
    "tavern": "Tavern (default)",
    "parchment": "Parchment",
    "arcane": "Arcane",
    "daylight": "Daylight",
    "custom": "Custom",
}

DEFAULT_THEME = "tavern"

# The CSS custom properties a custom theme is allowed to override, in the
# order they should appear as color pickers.
CUSTOM_KEYS = [
    "bg",
    "bg-panel",
    "bg-panel-2",
    "border",
    "text",
    "text-dim",
    "accent",
    "accent-hover",
    "good",
    "bad",
]

# Seed values for the customizer - Tavern's own palette, so "Customize"
# starts from something that already looks right rather than blank/black.
DEFAULT_CUSTOM_BASE = {
    "bg": "#1b1a17",
    "bg-panel": "#24221e",
    "bg-panel-2": "#2c2a25",
    "border": "#423f37",
    "text": "#ece6d9",
    "text-dim": "#a89d8a",
    "accent": "#c0863f",
    "accent-hover": "#d99a4e",
    "good": "#6fa96f",
    "bad": "#c25c5c",
}


def get_theme_name():
    name = database.get_setting("theme_name", DEFAULT_THEME)
    return name if name in PRESETS else DEFAULT_THEME


def set_theme_name(name):
    if name not in PRESETS:
        raise ValueError(f"Unknown theme: {name}")
    database.set_setting("theme_name", name)


def get_custom_vars():
    """The saved custom-theme palette, always returning every key in
    CUSTOM_KEYS (falling back to the Tavern-based defaults for anything
    never saved, so callers never need to handle a partial dict)."""
    raw = database.get_setting("custom_theme_vars", "")
    saved = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                saved = parsed
        except (ValueError, TypeError):
            saved = {}
    result = dict(DEFAULT_CUSTOM_BASE)
    for key in CUSTOM_KEYS:
        value = saved.get(key)
        if isinstance(value, str) and _HEX_RE.match(value):
            result[key] = value
    return result


def set_custom_vars(values):
    """values: a dict of {key: hex color}. Unknown keys are ignored; every
    value provided for a known key must be a valid hex color. Missing keys
    keep their previously-saved (or default) value."""
    if not isinstance(values, dict):
        raise ValueError("Expected a set of theme colors.")
    current = get_custom_vars()
    for key, value in values.items():
        if key not in CUSTOM_KEYS:
            continue
        if not isinstance(value, str) or not _HEX_RE.match(value):
            raise ValueError(f"'{value}' isn't a valid color for {key}.")
        current[key] = value
    database.set_setting("custom_theme_vars", json.dumps(current))
    return current
