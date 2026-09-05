"""Walks library_root and (re)populates the books table.

Expected layout (matches the user's own library, but nothing here requires
it beyond two levels of folders):
    <library_root>/<Publisher>/<Series>/.../<file>.pdf

Publisher = top-level folder, Series = second-level folder. Anything deeper
is just part of the book's path and doesn't affect classification. A file
sitting directly under library_root or under a Publisher folder with no
Series subfolder still gets scanned - it just has an empty Series.
"""
import os
import time

from .. import config, database
from . import games as games_module


def _relparts(root, full_path):
    rel = os.path.relpath(full_path, root)
    return rel.replace(os.sep, "/")


def scan_library(library_root, progress_cb=None):
    """Walk library_root, upsert every book file found, and drop DB rows for
    files that no longer exist. Returns a summary dict."""
    if not library_root or not os.path.isdir(library_root):
        raise FileNotFoundError(f"Library root does not exist: {library_root!r}")

    games_module.reload()

    found_paths = set()
    added = 0
    updated = 0
    unchanged = 0

    conn = database.get_connection()
    cur = conn.cursor()

    for dirpath, dirnames, filenames in os.walk(library_root):
        # skip dotfiles / hidden system folders
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in config.BOOK_EXTENSIONS:
                continue
            full_path = os.path.join(dirpath, fname)
            rel_path = _relparts(library_root, full_path)
            found_paths.add(rel_path)

            parts = rel_path.split("/")
            publisher = parts[0] if len(parts) > 1 else ""
            series = parts[1] if len(parts) > 2 else ""
            title = os.path.splitext(fname)[0]
            game_key = games_module.game_key_for_series(series)

            try:
                st = os.stat(full_path)
                size_bytes, mtime = st.st_size, st.st_mtime
            except OSError:
                size_bytes, mtime = None, None

            existing = cur.execute(
                "SELECT id, mtime, size_bytes, game_key_overridden FROM books WHERE path = ?",
                (rel_path,),
            ).fetchone()

            if existing is None:
                cur.execute(
                    """INSERT INTO books
                       (path, filename, title, publisher, series, game_key,
                        ext, size_bytes, mtime, last_scanned)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (rel_path, fname, title, publisher, series, game_key,
                     ext, size_bytes, mtime),
                )
                added += 1
            elif existing["mtime"] != mtime or existing["size_bytes"] != size_bytes:
                # A file someone manually moved to a different game keeps that
                # choice even if the underlying PDF changes - only re-derive
                # game_key here when nobody has overridden it for this book.
                # A changed file needs re-indexing (and, if it was OCR'd
                # before, re-OCRing - the old page text may no longer match).
                if existing["game_key_overridden"]:
                    cur.execute(
                        """UPDATE books SET filename=?, title=?, publisher=?, series=?,
                           ext=?, size_bytes=?, mtime=?, last_scanned=datetime('now'),
                           indexed=0, has_text=0, index_error=NULL,
                           ocr_attempted=0, text_source=NULL
                           WHERE id=?""",
                        (fname, title, publisher, series, ext,
                         size_bytes, mtime, existing["id"]),
                    )
                else:
                    cur.execute(
                        """UPDATE books SET filename=?, title=?, publisher=?, series=?,
                           game_key=?, ext=?, size_bytes=?, mtime=?, last_scanned=datetime('now'),
                           indexed=0, has_text=0, index_error=NULL,
                           ocr_attempted=0, text_source=NULL
                           WHERE id=?""",
                        (fname, title, publisher, series, game_key, ext,
                         size_bytes, mtime, existing["id"]),
                    )
                updated += 1
            else:
                cur.execute(
                    "UPDATE books SET last_scanned = datetime('now') WHERE id = ?",
                    (existing["id"],),
                )
                unchanged += 1

            if progress_cb and (added + updated + unchanged) % 200 == 0:
                progress_cb(added + updated + unchanged)

    # remove books whose files are gone
    all_paths = [r["path"] for r in cur.execute("SELECT path FROM books").fetchall()]
    removed = 0
    for p in all_paths:
        if p not in found_paths:
            cur.execute("DELETE FROM books WHERE path = ?", (p,))
            cur.execute("DELETE FROM book_pages_fts WHERE book_id = (SELECT id FROM books WHERE path = ?)", (p,))
            removed += 1

    conn.commit()

    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "total": len(found_paths),
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def game_summary():
    """Book counts per game_key, for the game-picker page."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT game_key, COUNT(*) AS n FROM books GROUP BY game_key"
        ).fetchall()
    return {r["game_key"]: r["n"] for r in rows}


def move_books_to_game(book_ids, target_key):
    """Reassigns specific books to a different game, individually - for a
    Series folder that's actually a mix (an anthology magazine, a reference
    line that spans systems, etc.) where the whole-Series move in games.py
    isn't fine-grained enough. Marks each as overridden so a later rescan
    doesn't quietly move it back."""
    book_ids = [int(b) for b in book_ids]
    if not book_ids:
        return 0
    with database.get_cursor(commit=True) as cur:
        cur.executemany(
            "UPDATE books SET game_key = ?, game_key_overridden = 1 WHERE id = ?",
            [(target_key, book_id) for book_id in book_ids],
        )
    return len(book_ids)


def reset_book_game(book_id):
    """Clears a manual override on one book, putting it back under whatever
    game its Series folder currently maps to."""
    with database.get_cursor() as cur:
        row = cur.execute("SELECT series FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown book.")
    game_key = games_module.game_key_for_series(row["series"])
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE books SET game_key = ?, game_key_overridden = 0 WHERE id = ?",
            (game_key, book_id),
        )
    return game_key
