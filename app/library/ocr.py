"""OCR for scanned (image-only) PDFs, using EasyOCR to read text off each
rendered page and PyMuPDF to do the rendering.

This only ever feeds the app's own full-text search index (book_pages_fts) -
it never rewrites, replaces, or duplicates anything in the library folder.
Your original PDFs are read-only as far as this app is concerned.

EasyOCR is slow compared to reading an embedded text layer - a few seconds
per page is typical on CPU, faster with a CUDA-capable GPU. For a library
with hundreds of scanned books this can run for hours, so callers should
always go through ocr_pending()/retry_failed() in a background thread and
drive a progress bar off progress_cb, the same pattern indexer.py uses.
"""
import os
import threading

import numpy as np
import pymupdf

from .. import database

_reader_lock = threading.Lock()
_reader = None
_LANGUAGES = ["en"]  # this library is all-English; add more here if that changes


def _get_reader():
    """EasyOCR's Reader() loads its detection/recognition models the first
    time it's constructed (downloading them on first-ever use), which is
    slow - so we build it once per process, lazily, only once OCR is
    actually requested."""
    global _reader
    if _reader is None:
        with _reader_lock:
            if _reader is None:
                import easyocr
                _reader = easyocr.Reader(_LANGUAGES)
    return _reader


def _page_to_array(page, zoom=2.0):
    """Renders one PDF page to an RGB numpy array at the given zoom (2.0 is
    roughly 144 DPI - a good balance of OCR accuracy vs. speed for printed
    book pages; push it higher for small/dense text if accuracy is poor)."""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)


def ocr_book(library_root, book_row, page_cb=None, zoom=2.0):
    """Runs OCR over every page of one book and loads the recognized text
    into book_pages_fts, the same table normal text extraction uses - so
    Library search doesn't need to know or care which source a hit came
    from. Always marks the book ocr_attempted=1 when done (success or not)
    so a plain re-run doesn't retry it forever; use retry_failed() for that.
    """
    full_path = os.path.join(library_root, book_row["path"].replace("/", os.sep))
    book_id = book_row["id"]
    reader = _get_reader()

    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM book_pages_fts WHERE book_id = ?", (book_id,))

    has_text = False
    error = None
    page_count = book_row["page_count"]

    try:
        doc = pymupdf.open(full_path)
        page_count = doc.page_count
        rows_to_insert = []
        for i in range(page_count):
            img = _page_to_array(doc[i], zoom=zoom)
            try:
                lines = reader.readtext(img, detail=0, paragraph=True)
            except Exception:
                lines = []
            text = "\n".join(lines).strip()
            if text:
                has_text = True
                rows_to_insert.append((text, book_id, i + 1, book_row["title"]))
            if page_cb:
                page_cb(i + 1, page_count)
        doc.close()

        if rows_to_insert:
            with database.get_cursor(commit=True) as cur:
                cur.executemany(
                    "INSERT INTO book_pages_fts (content, book_id, page_number, book_title) "
                    "VALUES (?, ?, ?, ?)",
                    rows_to_insert,
                )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    with database.get_cursor(commit=True) as cur:
        cur.execute(
            """UPDATE books
               SET ocr_attempted=1, has_text=?, page_count=?, text_source=?, index_error=?
               WHERE id=?""",
            (1 if has_text else 0, page_count, "ocr" if has_text else None, error, book_id),
        )

    return {"book_id": book_id, "has_text": has_text, "error": error, "page_count": page_count}


def _candidates(limit=None, retry_failed_only=False):
    if retry_failed_only:
        query = "SELECT * FROM books WHERE ocr_attempted = 1 AND has_text = 0"
    else:
        query = "SELECT * FROM books WHERE indexed = 1 AND has_text = 0 AND ocr_attempted = 0"
    if limit:
        query += f" LIMIT {int(limit)}"
    with database.get_cursor() as cur:
        return cur.execute(query).fetchall()


def count_pending():
    with database.get_cursor() as cur:
        pending = cur.execute(
            "SELECT COUNT(*) AS n FROM books WHERE indexed = 1 AND has_text = 0 AND ocr_attempted = 0"
        ).fetchone()["n"]
        failed = cur.execute(
            "SELECT COUNT(*) AS n FROM books WHERE ocr_attempted = 1 AND has_text = 0"
        ).fetchone()["n"]
        ocr_searchable = cur.execute(
            "SELECT COUNT(*) AS n FROM books WHERE text_source = 'ocr'"
        ).fetchone()["n"]
    return {"pending": pending, "failed": failed, "ocr_searchable": ocr_searchable}


def ocr_pending(library_root, limit=None, progress_cb=None):
    """OCRs every book that finished normal indexing with no text and
    hasn't had OCR tried yet."""
    rows = _candidates(limit=limit, retry_failed_only=False)
    return _run_ocr(library_root, rows, progress_cb=progress_cb)


def retry_failed(library_root, progress_cb=None):
    """Re-attempts OCR on books that were tried before but came up with no
    text (or errored) - e.g. after installing a language pack or fixing a
    setup problem."""
    rows = _candidates(retry_failed_only=True)
    return _run_ocr(library_root, rows, progress_cb=progress_cb)


def _run_ocr(library_root, rows, progress_cb=None):
    total_books = len(rows)
    done_books = 0
    with_text = 0
    failed = 0

    for row in rows:
        current_done = done_books
        if progress_cb:
            progress_cb(current_done, total_books, current_title=row["title"],
                        current_page=0, current_book_pages=row["page_count"] or 0)

        def _page_cb(p, total_p, _row=row, _done=current_done):
            if progress_cb:
                progress_cb(_done, total_books, current_title=_row["title"],
                            current_page=p, current_book_pages=total_p)

        result = ocr_book(library_root, row, page_cb=_page_cb)
        done_books += 1
        if result["error"]:
            failed += 1
        elif result["has_text"]:
            with_text += 1

    if progress_cb:
        progress_cb(done_books, total_books, current_title=None, current_page=0,
                    current_book_pages=0, with_text=with_text, failed=failed)

    return {
        "ocr_attempted": done_books,
        "with_text": with_text,
        "no_text": done_books - with_text - failed,
        "failed": failed,
    }
