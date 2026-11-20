"""Operational endpoints: health check and database backups."""
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from .. import backup, database

router = APIRouter()


@router.get("/healthz")
def healthz():
    """Liveness + database check (used by Docker's HEALTHCHECK; never needs a login)."""
    try:
        with database.get_cursor() as cur:
            cur.execute("SELECT 1").fetchone()
    except Exception as e:  # any failure to open/query the DB means unhealthy
        return JSONResponse({"status": "error", "detail": type(e).__name__}, status_code=503)
    return {"status": "ok"}


def _backup_response(kind):
    path = backup.make_backup(kind)
    return FileResponse(
        path,
        media_type="application/vnd.sqlite3",
        filename=backup.backup_filename(kind),
        background=BackgroundTask(backup.remove_file, path),
    )


@router.get("/backup/full")
def backup_full():
    """A complete copy of the database (search index and AI key included)."""
    return _backup_response("full")


@router.get("/backup/user-data")
def backup_user_data():
    """Everything you created, minus the search index and the AI key."""
    return _backup_response("user-data")
