"""Tiny in-memory progress tracker for the (potentially long-running)
indexing job. Indexing runs in a background thread so the browser doesn't
have to hold a request open for minutes across a big library; the Settings
page polls /api/index/progress to show a bar.

In-memory is fine here: this is a single-process, single-user local app,
and progress doesn't need to survive a restart.
"""
import threading

_lock = threading.Lock()
_state = {
    "running": False,
    "mode": None,          # "pending" or "all"
    "done": 0,
    "total": 0,
    "with_text": 0,
    "failed": 0,
    "current_title": None,
    "error": None,             # set if the job crashed outright
    "finished_summary": None,  # the dict index_pending()/reindex_all() returned
}


def start(mode, total):
    with _lock:
        _state.update(
            running=True,
            mode=mode,
            done=0,
            total=total,
            with_text=0,
            failed=0,
            current_title=None,
            error=None,
            finished_summary=None,
        )


def update(done, total, current_title=None, with_text=None, failed=None):
    with _lock:
        _state["done"] = done
        _state["total"] = total
        if current_title is not None:
            _state["current_title"] = current_title
        if with_text is not None:
            _state["with_text"] = with_text
        if failed is not None:
            _state["failed"] = failed


def finish(summary=None, error=None):
    with _lock:
        _state["running"] = False
        _state["current_title"] = None
        _state["finished_summary"] = summary
        _state["error"] = error


def snapshot():
    with _lock:
        return dict(_state)


def is_running():
    with _lock:
        return _state["running"]
