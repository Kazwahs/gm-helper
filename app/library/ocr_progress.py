"""In-memory progress tracker for the OCR job - same idea as
library/progress.py (indexing) but with page-level detail too, since a
single scanned book can itself take minutes under OCR."""
import threading

_lock = threading.Lock()
_state = {
    "running": False,
    "done_books": 0,
    "total_books": 0,
    "current_title": None,
    "current_page": 0,
    "current_book_pages": 0,
    "with_text": 0,
    "failed": 0,
    "error": None,
    "finished_summary": None,
}


def start(total_books):
    with _lock:
        _state.update(
            running=True,
            done_books=0,
            total_books=total_books,
            current_title=None,
            current_page=0,
            current_book_pages=0,
            with_text=0,
            failed=0,
            error=None,
            finished_summary=None,
        )


def update(done_books=None, total_books=None, current_title=None,
           current_page=None, current_book_pages=None, with_text=None, failed=None):
    with _lock:
        if done_books is not None:
            _state["done_books"] = done_books
        if total_books is not None:
            _state["total_books"] = total_books
        _state["current_title"] = current_title
        if current_page is not None:
            _state["current_page"] = current_page
        if current_book_pages is not None:
            _state["current_book_pages"] = current_book_pages
        if with_text is not None:
            _state["with_text"] = with_text
        if failed is not None:
            _state["failed"] = failed


def finish(summary=None, error=None):
    with _lock:
        _state["running"] = False
        _state["current_title"] = None
        _state["current_page"] = 0
        _state["current_book_pages"] = 0
        _state["finished_summary"] = summary
        _state["error"] = error


def snapshot():
    with _lock:
        return dict(_state)


def is_running():
    with _lock:
        return _state["running"]
