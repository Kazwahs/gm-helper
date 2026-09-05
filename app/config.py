"""Static, non-user-editable config: where things live on disk."""
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(APP_DIR)

# Reference data that ships with the app and is never written to at runtime.
BUNDLED_DATA_DIR = os.path.join(APP_DIR, "data")
NPC_DATA_PATH = os.path.join(BUNDLED_DATA_DIR, "npc_data.json")
ENCOUNTER_DATA_PATH = os.path.join(BUNDLED_DATA_DIR, "encounter_tables.json")
MAP_CONTENT_PATH = os.path.join(BUNDLED_DATA_DIR, "map_content.json")
# The starting games.json shipped in the image - used to seed a fresh,
# persistent copy the first time the app runs against an empty data dir.
DEFAULT_GAMES_CONFIG_PATH = os.path.join(BUNDLED_DATA_DIR, "games.json")

# Persistent, user-editable state: the SQLite DB and the games.json mapping
# the Manage Games page edits. GM_HELPER_DATA_DIR points both at a single
# mounted, persistent directory for a Docker deployment (so they survive the
# container being recreated); GM_HELPER_DB_PATH/GM_HELPER_GAMES_CONFIG_PATH
# can each be set individually to override just one. Unset, both default to
# living next to the app/project, matching a plain non-Docker checkout.
_data_dir = os.environ.get("GM_HELPER_DATA_DIR")

DB_PATH = os.environ.get("GM_HELPER_DB_PATH") or (
    os.path.join(_data_dir, "gm_helper.db") if _data_dir
    else os.path.join(PROJECT_DIR, "gm_helper.db")
)
GAMES_CONFIG_PATH = os.environ.get("GM_HELPER_GAMES_CONFIG_PATH") or (
    os.path.join(_data_dir, "games.json") if _data_dir
    else DEFAULT_GAMES_CONFIG_PATH
)

# File extensions the scanner treats as "a book".
BOOK_EXTENSIONS = {".pdf"}

DEFAULT_SETTINGS = {
    # The one thing that differs per-user: where their library lives.
    # Left empty on first run - the Settings page asks for it.
    "library_root": "",
}
