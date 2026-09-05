"""Loads the games.json config that maps a Series-level folder name to a
'game' the GM would pick from a dropdown. Editable per-library - nothing
else in the app hardcodes folder names.

Auto-detected series-to-game grouping is a starting guess, not always the
GM's mental model (e.g. "Savage Rifts" or "The Rifter" might feel like part
of "Rifts" to one person and not another). rename_game/merge_games/
move_series let the Manage Games page fix that up from the UI instead of
requiring a hand-edit of this file.
"""
import json
import os
import re
import shutil
import threading

from .. import config

_lock = threading.Lock()
_cache = None
_series_to_game = None


def _ensure_config_file():
    """Make sure GAMES_CONFIG_PATH exists before we try to read it.

    In a Docker deployment this points at a mounted, otherwise-empty data
    directory, so on first run there's nothing there yet - seed it from the
    default config baked into the image so the app has starting series/game
    mappings instead of crashing on a missing file.
    """
    path = config.GAMES_CONFIG_PATH
    if os.path.exists(path):
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    default_path = config.DEFAULT_GAMES_CONFIG_PATH
    if os.path.exists(default_path) and os.path.abspath(default_path) != os.path.abspath(path):
        shutil.copyfile(default_path, path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"games": [], "fallback_game_key": "unsorted", "fallback_game_name": "Unsorted / Other"},
                f,
                indent=2,
            )


def _load():
    global _cache, _series_to_game
    with _lock:
        _ensure_config_file()
        with open(config.GAMES_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        series_map = {}
        for game in data["games"]:
            for series in game["series"]:
                series_map[series.strip().lower()] = game["key"]
        _cache = data
        _series_to_game = series_map
    return data


def reload():
    return _load()


def get_config():
    if _cache is None:
        return _load()
    return _cache


def get_games():
    return get_config()["games"]


def get_game(key):
    for g in get_games():
        if g["key"] == key:
            return g
    if key == get_config().get("fallback_game_key"):
        return {
            "key": get_config().get("fallback_game_key", "unsorted"),
            "name": get_config().get("fallback_game_name", "Unsorted / Other"),
            "system": "generic",
            "series": [],
        }
    return None


def game_key_for_series(series_name):
    if _series_to_game is None:
        _load()
    if not series_name:
        return get_config().get("fallback_game_key", "unsorted")
    return _series_to_game.get(
        series_name.strip().lower(), get_config().get("fallback_game_key", "unsorted")
    )


def all_game_choices():
    """Games list plus the fallback bucket, for dropdowns."""
    games = list(get_games())
    games.append(
        {
            "key": get_config().get("fallback_game_key", "unsorted"),
            "name": get_config().get("fallback_game_name", "Unsorted / Other"),
            "system": "generic",
            "series": [],
        }
    )
    return games


def fallback_key():
    return get_config().get("fallback_game_key", "unsorted")


def fallback_name():
    return get_config().get("fallback_game_name", "Unsorted / Other")


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug or "game"


def unique_key(base_key):
    existing = {g["key"] for g in get_games()}
    if base_key not in existing and base_key != fallback_key():
        return base_key
    i = 2
    while f"{base_key}_{i}" in existing:
        i += 1
    return f"{base_key}_{i}"


def save_config(data):
    with _lock:
        with open(config.GAMES_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    reload()


def rename_game(key, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("Name can't be empty.")
    data = get_config()
    for g in data["games"]:
        if g["key"] == key:
            g["name"] = new_name
            save_config(data)
            return
    if key == data.get("fallback_game_key", "unsorted"):
        data["fallback_game_name"] = new_name
        save_config(data)
        return
    raise ValueError(f"Unknown game: {key!r}")


def create_game(name, system="generic", series=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Name can't be empty.")
    data = get_config()
    key = unique_key(slugify(name))
    data["games"].append({"key": key, "name": name, "system": system, "series": list(series or [])})
    save_config(data)
    return key


def merge_games(source_key, target_key):
    """Folds source_key's series list into target_key's and removes the
    source game. target_key may be the fallback key to dissolve a game back
    into 'Unsorted / Other' instead of merging it into another real game.
    Returns the list of series that moved, so the caller can also flip the
    game_key on any already-scanned book rows without waiting for a rescan.
    """
    if source_key == target_key:
        raise ValueError("Can't merge a game into itself.")
    data = get_config()
    games_list = data["games"]
    source = next((g for g in games_list if g["key"] == source_key), None)
    if source is None:
        raise ValueError(f"Unknown game: {source_key!r}")

    moved_series = list(source["series"])

    if target_key != data.get("fallback_game_key", "unsorted"):
        target = next((g for g in games_list if g["key"] == target_key), None)
        if target is None:
            raise ValueError(f"Unknown game: {target_key!r}")
        for s in moved_series:
            if s not in target["series"]:
                target["series"].append(s)

    games_list.remove(source)
    save_config(data)
    return moved_series


def move_series(series_name, target_key):
    """Reassigns one Series folder to a different game (or back to the
    fallback bucket if target_key is the fallback key)."""
    data = get_config()
    games_list = data["games"]
    fb_key = data.get("fallback_game_key", "unsorted")

    if target_key != fb_key and not any(g["key"] == target_key for g in games_list):
        raise ValueError(f"Unknown game: {target_key!r}")

    for g in games_list:
        if series_name in g["series"]:
            g["series"].remove(series_name)

    if target_key != fb_key:
        target = next(g for g in games_list if g["key"] == target_key)
        if series_name not in target["series"]:
            target["series"].append(series_name)

    save_config(data)
