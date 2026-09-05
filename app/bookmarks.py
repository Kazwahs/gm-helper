"""Bookmarks: user-created collections of book+page references with notes.

Bookmarks aren't tied to a game - a GM's "Session 12 prep" collection might
reference a Rifts book and a D&D book in the same list - so this lives at
the app level rather than under library/ or tools/.
"""
from . import database


def list_collections():
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM bookmark_collections ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def create_collection(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name can't be empty.")
    with database.get_cursor() as cur:
        existing = cur.execute(
            "SELECT id FROM bookmark_collections WHERE name = ?", (name,)
        ).fetchone()
    if existing:
        raise ValueError(f"A collection named {name!r} already exists.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO bookmark_collections (name) VALUES (?)", (name,))
        return cur.lastrowid


def get_or_create_collection(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name can't be empty.")
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT id FROM bookmark_collections WHERE name = ?", (name,)
        ).fetchone()
    if row:
        return row["id"]
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO bookmark_collections (name) VALUES (?)", (name,))
        return cur.lastrowid


def delete_collection(collection_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM bookmark_collections WHERE id = ?", (collection_id,))


def rename_collection(collection_id, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name can't be empty.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE bookmark_collections SET name = ? WHERE id = ?", (name, collection_id)
        )


def add_bookmark(collection_id, book_id, page_number=None, note=""):
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO bookmarks (collection_id, book_id, page_number, note) "
            "VALUES (?, ?, ?, ?)",
            (collection_id, book_id, page_number, note or ""),
        )
        return cur.lastrowid


def update_bookmark_note(bookmark_id, note):
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE bookmarks SET note = ? WHERE id = ?", (note or "", bookmark_id))


def delete_bookmark(bookmark_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))


def list_collections_with_bookmarks():
    """Collections joined with their bookmarks (and the book each references),
    for the Bookmarks page."""
    with database.get_cursor() as cur:
        collections = cur.execute(
            "SELECT * FROM bookmark_collections ORDER BY name COLLATE NOCASE"
        ).fetchall()
        bookmark_rows = cur.execute(
            """SELECT bm.*, b.title AS book_title, b.publisher, b.series, b.game_key
               FROM bookmarks bm
               JOIN books b ON b.id = bm.book_id
               ORDER BY bm.created_at DESC"""
        ).fetchall()

    by_collection = {}
    for bm in bookmark_rows:
        by_collection.setdefault(bm["collection_id"], []).append(dict(bm))

    return [
        {**dict(c), "bookmarks": by_collection.get(c["id"], [])}
        for c in collections
    ]
