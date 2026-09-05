"""Thin SQLite wrapper. No ORM - this app is small enough that raw SQL is
clearer than fighting one, and it keeps the Docker image tiny."""
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT UNIQUE NOT NULL,   -- relative to library_root
    filename     TEXT NOT NULL,
    title        TEXT NOT NULL,
    publisher    TEXT,                   -- top-level folder
    series       TEXT,                   -- second-level folder
    game_key     TEXT,
    ext          TEXT,
    size_bytes   INTEGER,
    mtime        REAL,
    page_count   INTEGER,
    has_text     INTEGER DEFAULT 0,      -- 1 once we've confirmed extractable text
    indexed      INTEGER DEFAULT 0,      -- 1 once indexing has been attempted
    index_error  TEXT,
    last_scanned TEXT,
    game_key_overridden INTEGER DEFAULT 0, -- 1 once a person manually moved this one file
    ocr_attempted INTEGER DEFAULT 0,       -- 1 once OCR has been tried on this scanned book
    text_source  TEXT                      -- 'embedded' or 'ocr', for whichever gave us has_text=1
);
CREATE INDEX IF NOT EXISTS idx_books_game_key ON books(game_key);

CREATE VIRTUAL TABLE IF NOT EXISTS book_pages_fts USING fts5(
    content,
    book_id UNINDEXED,
    page_number UNINDEXED,
    book_title UNINDEXED,
    tokenize = 'porter'
);

CREATE TABLE IF NOT EXISTS npcs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key    TEXT,
    name        TEXT,
    summary     TEXT,
    details_json TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS combat_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key    TEXT,
    name        TEXT,
    state_json  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmark_collections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES bookmark_collections(id) ON DELETE CASCADE,
    book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    page_number   INTEGER,
    note          TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_collection ON bookmarks(collection_id);
CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks(book_id);

-- Campaign planner. A campaign belongs to one game (game_key) and gets its
-- own bookmark collection for reference materials, so "what book/page do I
-- need" reuses the same bookmarking system rather than inventing a second one.
CREATE TABLE IF NOT EXISTS campaigns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key     TEXT NOT NULL,
    title        TEXT NOT NULL,
    pitch        TEXT,     -- the premise / log-line
    tone         TEXT,     -- tone, themes, inspirations
    status       TEXT DEFAULT 'planning',  -- planning / active / paused / completed
    bookmark_collection_id INTEGER REFERENCES bookmark_collections(id) ON DELETE SET NULL,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaigns_game_key ON campaigns(game_key);

CREATE TABLE IF NOT EXISTS campaign_threads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    stakes      TEXT,      -- what happens if this goes unresolved
    status      TEXT DEFAULT 'seed',  -- seed / active / resolved / dropped
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_threads_campaign ON campaign_threads(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_npcs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT,
    disposition TEXT DEFAULT 'neutral',  -- ally / neutral / rival / villain / unknown
    motivation  TEXT,      -- what they want
    secret      TEXT,      -- what they're hiding
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_npcs_campaign ON campaign_npcs(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES campaign_locations(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_locations_campaign ON campaign_locations(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_factions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    goal        TEXT,      -- what they're after while the party's elsewhere
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_factions_campaign ON campaign_factions(campaign_id);

CREATE TABLE IF NOT EXISTS campaign_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    session_number INTEGER NOT NULL,
    title          TEXT,
    date           TEXT,
    status         TEXT DEFAULT 'planned',  -- planned / played
    strong_start   TEXT,   -- the opening hook
    scenes         TEXT,   -- potential scenes, one per line
    secrets_clues  TEXT,   -- secrets/clues that might come out this session
    prep_notes     TEXT,   -- monsters, rewards, anything else prepped
    summary        TEXT,   -- what actually happened - filled in after the fact
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_sessions_campaign ON campaign_sessions(campaign_id);

-- Generated maps. A map belongs to one game and can optionally be attached
-- to one campaign (like a material, but rendered rather than a book/page
-- reference). The rendered SVG is stored as-is so a saved map redisplays
-- instantly without ever needing to be regenerated.
CREATE TABLE IF NOT EXISTS maps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key    TEXT NOT NULL,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    map_type    TEXT NOT NULL,   -- dungeon / interior / urban / underground / wilderness / world
    genre       TEXT NOT NULL,   -- fantasy / modern / hightech / mixed
    title       TEXT NOT NULL,
    seed        INTEGER,
    params_json TEXT,
    svg         TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_maps_game_key ON maps(game_key);
CREATE INDEX IF NOT EXISTS idx_maps_campaign ON maps(campaign_id);
"""


def get_connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        db_dir = os.path.dirname(config.DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets the background indexing thread write while request threads
        # read (e.g. the Settings page polling progress, or Library search)
        # without hitting "database is locked".
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        _local.conn = conn
    return conn


@contextmanager
def get_cursor(commit=False):
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    finally:
        cur.close()


def _table_columns(table):
    with get_cursor() as cur:
        return {row["name"] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(table, column, ddl):
    """Adds a column to an already-existing table if it's not there yet.
    CREATE TABLE IF NOT EXISTS only helps on a brand-new DB - a database
    created before this column existed needs an explicit ALTER TABLE."""
    if column not in _table_columns(table):
        with get_cursor(commit=True) as cur:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()

    _ensure_column("books", "game_key_overridden", "INTEGER DEFAULT 0")
    _ensure_column("books", "ocr_attempted", "INTEGER DEFAULT 0")
    _ensure_column("books", "text_source", "TEXT")
    _ensure_column("maps", "original_svg", "TEXT")

    with get_cursor(commit=True) as cur:
        # Backfill: maps saved before hand-editing existed have no
        # original_svg yet - their current svg IS the original, so that's
        # the safe "Revert to Generated" target for them too.
        cur.execute("UPDATE maps SET original_svg = svg WHERE original_svg IS NULL")

    with get_cursor(commit=True) as cur:
        for key, value in config.DEFAULT_SETTINGS.items():
            cur.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


def get_setting(key, default=None):
    with get_cursor() as cur:
        row = cur.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
