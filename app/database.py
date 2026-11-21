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

-- Prepped encounters for the initiative tracker: a named group of foes (see
-- saved_encounters.py). entries_json is a list of {name, quantity, hp, initiative, notes}.
CREATE TABLE IF NOT EXISTS saved_encounters (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key     TEXT NOT NULL,
    name         TEXT NOT NULL,
    entries_json TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_saved_encounters_game_key ON saved_encounters(game_key);

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

-- Any change to a campaign's threads, cast, locations, factions or sessions counts
-- as activity on the campaign, so the home page's "recently active" list is accurate.
CREATE TRIGGER IF NOT EXISTS trg_campaign_threads_insert_touch AFTER INSERT ON campaign_threads
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_threads_update_touch AFTER UPDATE ON campaign_threads
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_npcs_insert_touch AFTER INSERT ON campaign_npcs
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_npcs_update_touch AFTER UPDATE ON campaign_npcs
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_locations_insert_touch AFTER INSERT ON campaign_locations
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_locations_update_touch AFTER UPDATE ON campaign_locations
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_factions_insert_touch AFTER INSERT ON campaign_factions
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_factions_update_touch AFTER UPDATE ON campaign_factions
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_sessions_insert_touch AFTER INSERT ON campaign_sessions
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_sessions_update_touch AFTER UPDATE ON campaign_sessions
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;

-- Session log: timestamped one-liners kept while a session is being played. They
-- feed the recap and the session summary (see session_log.py). session_id is NULL
-- for a line that isn't tied to a particular session.
CREATE TABLE IF NOT EXISTS campaign_session_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    session_id  INTEGER REFERENCES campaign_sessions(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'note',  -- note / scene / combat / loot / clue / roll
    text        TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_session_log_campaign ON campaign_session_log(campaign_id);
CREATE INDEX IF NOT EXISTS idx_session_log_session ON campaign_session_log(session_id);
CREATE TRIGGER IF NOT EXISTS trg_campaign_session_log_insert_touch AFTER INSERT ON campaign_session_log
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_session_log_update_touch AFTER UPDATE ON campaign_session_log
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;

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

-- One row per numbered room on a dungeon/interior map: the GM's room key.
CREATE TABLE IF NOT EXISTS map_room_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id      INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    room        INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'empty',   -- empty / creatures / trap / puzzle / treasure
    description TEXT NOT NULL DEFAULT '',        -- what the players see (read aloud)
    contents    TEXT NOT NULL DEFAULT '',        -- creatures, trap or puzzle - GM only
    treasure    TEXT NOT NULL DEFAULT '',
    secrets     TEXT NOT NULL DEFAULT '',        -- secret and locked doors, hidden things
    notes       TEXT NOT NULL DEFAULT '',        -- the GM's own notes
    exits       TEXT NOT NULL DEFAULT '',
    size        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE (map_id, room)
);
CREATE INDEX IF NOT EXISTS idx_map_room_keys_map ON map_room_keys(map_id);

-- A map that leads to another: a settlement to its city, a dungeon level to the next one down.
CREATE TABLE IF NOT EXISTS map_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_map_id INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    to_map_id   INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'detail',   -- detail / below / above (from the "from" map's point of view)
    label       TEXT NOT NULL DEFAULT '',         -- e.g. the settlement this map is the city of
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE (from_map_id, to_map_id)
);
CREATE INDEX IF NOT EXISTS idx_map_links_from ON map_links(from_map_id);
CREATE INDEX IF NOT EXISTS idx_map_links_to ON map_links(to_map_id);

-- Secrets and clues the GM has prepared, and whether the party has learned them yet.
CREATE TABLE IF NOT EXISTS campaign_secrets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id         INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    text                TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT '',   -- where or how the party can find it out
    session_id          INTEGER REFERENCES campaign_sessions(id) ON DELETE SET NULL,  -- prepped for
    status              TEXT NOT NULL DEFAULT 'hidden',  -- hidden / revealed
    revealed_session_id INTEGER REFERENCES campaign_sessions(id) ON DELETE SET NULL,
    revealed_at         TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_secrets_campaign ON campaign_secrets(campaign_id);

-- Progress clocks ("the ritual is 3 of 6 done"), for a faction or on their own.
CREATE TABLE IF NOT EXISTS campaign_clocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    faction_id  INTEGER REFERENCES campaign_factions(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    segments    INTEGER NOT NULL DEFAULT 6,
    filled      INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_campaign_clocks_campaign ON campaign_clocks(campaign_id);

-- Who stands where: ties between NPCs and factions ("Marlowe owes the Guild").
CREATE TABLE IF NOT EXISTS campaign_relationships (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    a_kind      TEXT NOT NULL,   -- npc / faction
    a_id        INTEGER NOT NULL,
    b_kind      TEXT NOT NULL,
    b_id        INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'ally',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE (campaign_id, a_kind, a_id, b_kind, b_id)
);
CREATE INDEX IF NOT EXISTS idx_campaign_relationships_campaign ON campaign_relationships(campaign_id);
CREATE TRIGGER IF NOT EXISTS trg_campaign_npcs_delete_ties AFTER DELETE ON campaign_npcs
BEGIN
    DELETE FROM campaign_relationships
     WHERE (a_kind = 'npc' AND a_id = OLD.id) OR (b_kind = 'npc' AND b_id = OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_factions_delete_ties AFTER DELETE ON campaign_factions
BEGIN
    DELETE FROM campaign_relationships
     WHERE (a_kind = 'faction' AND a_id = OLD.id) OR (b_kind = 'faction' AND b_id = OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_secrets_insert_touch AFTER INSERT ON campaign_secrets
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_secrets_update_touch AFTER UPDATE ON campaign_secrets
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_clocks_insert_touch AFTER INSERT ON campaign_clocks
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_campaign_clocks_update_touch AFTER UPDATE ON campaign_clocks
BEGIN
    UPDATE campaigns SET updated_at = datetime('now') WHERE id = NEW.campaign_id;
END;

-- Handouts (text or an image) a GM can put on a map's player view, and tokens
-- dragged around on a map. See handouts.py and map_tokens.py.
CREATE TABLE IF NOT EXISTS handouts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key   TEXT NOT NULL,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text',   -- text / image
    body       TEXT,
    image      BLOB,
    mime       TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_handouts_game_key ON handouts(game_key);

CREATE TABLE IF NOT EXISTS map_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id     INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    color      TEXT NOT NULL DEFAULT '#c0863f',
    size       INTEGER NOT NULL DEFAULT 1,
    hidden     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_map_tokens_map ON map_tokens(map_id);

-- GM Screen: a per-game (or "_global") board of quick-reference cards a GM
-- keeps open during play, plus one freeform scratchpad for session-specific
-- reminders. Cards start seeded with generic starter content the first time
-- a game's screen is opened (see gm_screen.SYSTEM_CARDS) but are fully
-- editable/deletable/addable, since exactly which rules matter varies a lot
-- by game system.
CREATE TABLE IF NOT EXISTS gm_screen_cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key    TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gm_screen_cards_game_key ON gm_screen_cards(game_key);

-- Random tables: a GM's own roll tables. `body` is the whole table as text,
-- one entry per line ("1-3: text" or just "text"), so it edits like a note
-- and imports/exports trivially. game_key "_global" tables show in every game.
CREATE TABLE IF NOT EXISTS random_tables (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key   TEXT NOT NULL,
    name       TEXT NOT NULL,
    die        TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_random_tables_game_key ON random_tables(game_key);

CREATE TABLE IF NOT EXISTS random_roll_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_key   TEXT NOT NULL,
    table_name TEXT NOT NULL,
    roll       INTEGER,
    text       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_random_roll_history_game_key ON random_roll_history(game_key);

CREATE TABLE IF NOT EXISTS gm_screen_notes (
    game_key TEXT PRIMARY KEY,
    body     TEXT
);
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
    # Fog-of-war state for the player view: {"enabled", "areas", "v"} as JSON.
    _ensure_column("maps", "reveal_json", "TEXT")
    _ensure_column("maps", "shown_handout_id", "INTEGER REFERENCES handouts(id) ON DELETE SET NULL")
    _ensure_column("maps", "live_v", "INTEGER NOT NULL DEFAULT 0")
    # How a faction stands with the party (see campaign_world.py).
    _ensure_column("campaign_factions", "standing", "TEXT NOT NULL DEFAULT 'neutral'")
    # A map token can stand for a combatant in the initiative tracker (see token_combat.py).
    # The name is kept too, so a token never follows a *different* combatant after a tracker reset.
    _ensure_column("map_tokens", "combatant_id", "INTEGER")
    _ensure_column("map_tokens", "combatant_name", "TEXT")
    # A GM Screen card can be a pinned book page (opens the PDF at that page).
    _ensure_column("gm_screen_cards", "book_id", "INTEGER")
    _ensure_column("gm_screen_cards", "page_number", "INTEGER")
    # Campaign links: a location can point at a saved map; an NPC can belong
    # to a faction and live at a location. SET NULL keeps deletes tidy.
    _ensure_column("campaign_locations", "map_id", "INTEGER REFERENCES maps(id) ON DELETE SET NULL")
    _ensure_column("campaign_npcs", "faction_id", "INTEGER REFERENCES campaign_factions(id) ON DELETE SET NULL")
    _ensure_column("campaign_npcs", "location_id", "INTEGER REFERENCES campaign_locations(id) ON DELETE SET NULL")

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
