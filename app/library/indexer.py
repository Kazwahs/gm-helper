"""Extracts text from PDFs (via pypdf) and loads it into the FTS5 table.

Many older/scanned RPG PDFs are image-only with no embedded text layer.
Those get indexed=1, has_text=0 so the UI can say "not searchable (scanned
image)" instead of silently returning zero hits forever without
explanation - and so library/ocr.py knows which books are OCR candidates.
"""
import os

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .. import database


def index_book(library_root, book_row):
    """Extract text for one book row (a sqlite3.Row from `books`) and store
    it in book_pages_fts. Always marks the book as indexed=1 when done,
    even on failure, so re-scans don't retry it forever."""
    full_path = os.path.join(library_root, book_row["path"].replace("/", os.sep))
    book_id = book_row["id"]

    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM book_pages_fts WHERE book_id = ?", (book_id,))

    error = None
    page_count = None
    has_text = False

    try:
        reader = PdfReader(full_path, strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        page_count = len(reader.pages)
        rows_to_insert = []
        for i, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = text.strip()
            if text:
                has_text = True
                rows_to_insert.append((text, book_id, i + 1, book_row["title"]))
        if rows_to_insert:
            with database.get_cursor(commit=True) as cur:
                cur.executemany(
                    "INSERT INTO book_pages_fts (content, book_id, page_number, book_title) "
                    "VALUES (?, ?, ?, ?)",
                    rows_to_insert,
                )
    except (PdfReadError, OSError, ValueError) as e:
        error = str(e)
    except Exception as e:  # keep indexing the rest of the library no matter what
        error = f"{type(e).__name__}: {e}"

    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE books SET indexed=1, has_text=?, page_count=?, index_error=?, "
            "text_source=? WHERE id=?",
            (1 if has_text else 0, page_count, error, "embedded" if has_text else None, book_id),
        )

    return {"book_id": book_id, "has_text": has_text, "error": error, "page_count": page_count}


def index_pending(library_root, limit=None, progress_cb=None):
    """Index every book row not yet indexed. Returns a summary dict.

    progress_cb, if given, is called after every book as
    progress_cb(done, total, current_title=..., with_text=..., failed=...)
    so a caller can drive a progress bar.
    """
    with database.get_cursor() as cur:
        query = "SELECT * FROM books WHERE indexed = 0"
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = cur.execute(query).fetchall()

    total = len(rows)
    done = 0
    with_text = 0
    failed = 0
    for row in rows:
        result = index_book(library_root, row)
        done += 1
        if result["error"]:
            failed += 1
        elif result["has_text"]:
            with_text += 1
        if progress_cb:
            progress_cb(done, total, current_title=row["title"], with_text=with_text, failed=failed)

    return {"indexed": done, "with_text": with_text, "no_text": done - with_text - failed, "failed": failed}


def reindex_all(library_root, progress_cb=None):
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE books SET indexed = 0, has_text = 0, index_error = NULL, "
            "ocr_attempted = 0, text_source = NULL"
        )
    return index_pending(library_root, progress_cb=progress_cb)
