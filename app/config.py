"""Static, non-user-editable config: where things live on disk."""
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(APP_DIR)

DATA_DIR = os.path.join(APP_DIR, "data")
GAMES_CONFIG_PATH = os.path.join(DATA_DIR, "games.json")
NPC_DATA_PATH = os.path.join(DATA_DIR, "npc_data.json")
ENCOUNTER_DATA_PATH = os.path.join(DATA_DIR, "encounter_tables.json")
MAP_CONTENT_PATH = os.path.join(DATA_DIR, "map_content.json")

# Where the SQLite DB lives. Overridable with GM_HELPER_DB_PATH so a Docker
# deployment can point it at a mounted volume instead of baking it into the image.
DB_PATH = os.environ.get("GM_HELPER_DB_PATH") or os.path.join(PROJECT_DIR, "gm_helper.db")

# File extensions the scanner treats as "a book".
BOOK_EXTENSIONS = {".pdf"}

DEFAULT_SETTINGS = {
    # The one thing that differs per-user: where their library lives.
    # Left empty on first run - the Settings page asks for it.
    "library_root": "",
}
