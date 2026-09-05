"""Tests for the schema bootstrap + live-migration machinery in
app/database.py - the pattern the app relies on to add columns/tables to a
user's already-in-use gm_helper.db without losing data."""
import sqlite3

from app import database


EXPECTED_TABLES = {
    "settings", "books", "book_pages_fts", "npcs", "combat_sessions",
    "bookmark_collections", "bookmarks", "campaigns", "campaign_threads",
    "campaign_npcs", "campaign_locations", "campaign_factions",
    "campaign_sessions", "maps",
}


def test_init_db_creates_all_expected_tables(db):
    with database.get_cursor() as cur:
        rows = cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES <= names


def test_init_db_is_idempotent(db):
    # Calling init_db() again (as happens on every app restart) must not
    # error out or wipe anything.
    database.set_setting("library_root", "/some/path")
    database.init_db()
    assert database.get_setting("library_root") == "/some/path"


def test_default_settings_are_seeded_but_never_overwrite_a_saved_value(db):
    assert database.get_setting("library_root") == ""
    database.set_setting("library_root", "/my/books")
    database.init_db()  # simulates a second app startup
    assert database.get_setting("library_root") == "/my/books"


def test_get_setting_missing_key_returns_default(db):
    assert database.get_setting("nope", "fallback") == "fallback"
    assert database.get_setting("nope") is None


def test_set_setting_upserts(db):
    database.set_setting("k", "v1")
    assert database.get_setting("k") == "v1"
    database.set_setting("k", "v2")
    assert database.get_setting("k") == "v2"


def test_ensure_column_adds_a_missing_column_to_an_existing_table(db):
    with database.get_cursor(commit=True) as cur:
        cur.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
        cur.execute("INSERT INTO widgets (name) VALUES ('a widget')")

    database._ensure_column("widgets", "extra_field", "TEXT DEFAULT 'x'")

    with database.get_cursor() as cur:
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(widgets)").fetchall()}
        row = cur.execute("SELECT * FROM widgets").fetchone()
    assert "extra_field" in cols
    assert row["name"] == "a widget"  # pre-existing data untouched


def test_ensure_column_is_a_no_op_if_column_already_exists(db):
    with database.get_cursor(commit=True) as cur:
        cur.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, extra_field TEXT)")
        cur.execute("INSERT INTO widgets (extra_field) VALUES ('already there')")

    database._ensure_column("widgets", "extra_field", "TEXT")  # must not raise

    with database.get_cursor() as cur:
        row = cur.execute("SELECT extra_field FROM widgets").fetchone()
    assert row["extra_field"] == "already there"


def test_maps_original_svg_migration_backfills_pre_existing_rows(db, tmp_path, monkeypatch):
    """Simulates a real user's database created before the map-editing
    feature existed: a `maps` table with no `original_svg` column and a
    real row already in it. init_db() must add the column AND backfill
    original_svg = svg for that row, with zero data loss."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE maps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_key TEXT NOT NULL,
            campaign_id INTEGER,
            map_type TEXT NOT NULL,
            genre TEXT NOT NULL,
            title TEXT NOT NULL,
            seed INTEGER,
            params_json TEXT,
            svg TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO maps (game_key, map_type, genre, title, svg) VALUES (?, ?, ?, ?, ?)",
        ("unsorted", "dungeon", "fantasy", "Legacy Map", "<svg>legacy-original</svg>"),
    )
    conn.execute("CREATE TABLE campaigns (id INTEGER PRIMARY KEY, game_key TEXT, title TEXT)")
    conn.execute("INSERT INTO campaigns (id, game_key, title) VALUES (1, 'unsorted', 'Old Campaign')")
    conn.commit()
    conn.close()

    from app import config
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    if hasattr(database._local, "conn"):
        del database._local.conn

    database.init_db()
    with database.get_cursor() as cur:
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(maps)").fetchall()}
        row = cur.execute("SELECT svg, original_svg FROM maps WHERE title = 'Legacy Map'").fetchone()
        campaign = cur.execute("SELECT title FROM campaigns WHERE id = 1").fetchone()
    assert "original_svg" in cols
    assert row["svg"] == "<svg>legacy-original</svg>"
    assert row["original_svg"] == "<svg>legacy-original</svg>"
    assert campaign["title"] == "Old Campaign"  # unrelated pre-existing data untouched

    if hasattr(database._local, "conn"):
        del database._local.conn
