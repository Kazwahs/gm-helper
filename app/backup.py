"""Backups of GM Helper's SQLite database.

Two flavours, both plain SQLite files made with SQLite's online-backup API
(so they're consistent even while the app is running in WAL mode):

* full       - an exact copy of everything, search index included. Biggest
               (the search index of a large library is hundreds of MB) and
               it includes your saved AI API key.
* user-data  - everything you made - campaigns, maps, bookmarks, GM Screen,
               random tables, settings - WITHOUT the book search index and
               WITHOUT the AI API key. Typically a few hundred KB. The book
               list is kept (bookmarks point at books) but flagged as not yet
               indexed, so after restoring, "Index books" on the Settings
               page rebuilds search.

To restore either one: stop GM Helper, replace gm_helper.db in your data
folder with the backup file (renamed), delete any gm_helper.db-wal /
gm_helper.db-shm next to it, and start it again.
"""
import os
import sqlite3
import tempfile
from datetime import datetime

from . import database

SECRET_SETTINGS = ("ai_api_key", "ai_verified", "ai_verified_at")


def backup_filename(kind, now=None):
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"gm_helper-{'userdata' if kind == 'user-data' else 'backup'}-{stamp}.db"


def make_backup(kind):
    """Writes a backup to a new temp file and returns its path. The caller
    deletes it once it has been sent."""
    if kind not in ("full", "user-data"):
        raise ValueError("kind must be 'full' or 'user-data'")
    fd, path = tempfile.mkstemp(prefix="gm_helper-backup-", suffix=".db")
    os.close(fd)
    try:
        source = database.get_connection()
        dest = sqlite3.connect(path)
        try:
            source.backup(dest)
            if kind == "user-data":
                _strip_rebuildable_and_secret_data(dest)
        finally:
            dest.close()
    except Exception:
        _remove(path)
        raise
    return path


def _strip_rebuildable_and_secret_data(conn):
    conn.execute("DELETE FROM book_pages_fts")
    conn.execute(
        "UPDATE books SET indexed = 0, has_text = 0, index_error = NULL, ocr_attempted = 0, text_source = NULL"
    )
    marks = ",".join("?" * len(SECRET_SETTINGS))
    conn.execute(f"DELETE FROM settings WHERE key IN ({marks})", SECRET_SETTINGS)
    conn.commit()
    conn.execute("VACUUM")


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def remove_file(path):
    _remove(path)
