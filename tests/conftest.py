"""Shared pytest fixtures.

The app has no dependency-injection layer - modules like `database` and
`library.games` read `config.DB_PATH` / `config.GAMES_CONFIG_PATH` straight
off the config module at call time, and cache a bit of state at the module
level (a thread-local SQLite connection; a loaded-games.json cache). These
fixtures point that config at a fresh temp file per test and reset the
cached state, so every test gets a clean, isolated database / games.json
instead of touching the real gm_helper.db or app/data/games.json.
"""
import json

import pytest

from app import config, database
from app.library import games as games_module


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh, fully-migrated SQLite database for one test."""
    db_path = tmp_path / "test_gm_helper.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))

    # Drop any connection cached from a previous test (in the same thread)
    # so the next get_connection() call opens a new one against db_path.
    if hasattr(database._local, "conn"):
        try:
            database._local.conn.close()
        except Exception:
            pass
        del database._local.conn

    database.init_db()
    yield db_path

    if hasattr(database._local, "conn"):
        try:
            database._local.conn.close()
        except Exception:
            pass
        del database._local.conn


@pytest.fixture
def a_book(db):
    """Inserts one row into `books` and returns its id. Bookmarks have a
    NOT NULL foreign key to books, so bookmark tests need a real book row
    to point at."""
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO books (path, filename, title, publisher, series, game_key) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Pub/Series/book.pdf", "book.pdf", "Test Book", "Pub", "Series", "unsorted"),
        )
        return cur.lastrowid


@pytest.fixture
def games_config(tmp_path, monkeypatch):
    """A small, known games.json - independent of whatever the real
    library's app/data/games.json happens to contain."""
    games_json_path = tmp_path / "games.json"
    games_json_path.write_text(json.dumps({
        "games": [
            {"key": "dnd5e", "name": "D&D 5E", "system": "d20", "series": ["Player Handbooks"]},
            {"key": "rifts", "name": "Rifts", "system": "palladium", "series": ["Rifts Sourcebooks"]},
        ],
        "fallback_game_key": "unsorted",
        "fallback_game_name": "Unsorted / Other",
    }))
    monkeypatch.setattr(config, "GAMES_CONFIG_PATH", str(games_json_path))
    games_module.reload()
    yield
