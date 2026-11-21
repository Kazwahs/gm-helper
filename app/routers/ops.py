"""Operational endpoints: health check and database backups."""
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from .. import autobackup, backup, database

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


def _auto_status():
    return {
        "settings": autobackup.get_settings(),
        "folder": autobackup.backup_dir(),
        "backups": [{"name": b["name"], "size": b["size"], "modified": b["modified"]} for b in autobackup.list_backups()],
        "last_error": autobackup.last_error(),
    }


@router.get("/api/backups")
def api_auto_backups():
    """Automatic-backup settings, the folder they go to, and the copies on disk."""
    return _auto_status()


@router.post("/api/backups/settings")
def api_auto_backup_settings(enabled: str = Form("1"), hours: str = Form(""), keep: str = Form("")):
    autobackup.save_settings(enabled not in ("", "0", "false", "off"), hours, keep)
    autobackup.prune()
    return _auto_status()


@router.post("/api/backups/run")
def api_auto_backup_run():
    """Back up right now (in addition to the schedule)."""
    try:
        name = autobackup.run_backup()
    except Exception as e:
        autobackup.record_run(f"{type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Backup failed: {type(e).__name__}: {e}")
    autobackup.record_run(None)
    return {"ok": True, "name": name, **_auto_status()}


@router.get("/backup/auto/{name}")
def backup_auto_download(name: str):
    path = autobackup.path_for(name)
    if not path:
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(path, media_type="application/vnd.sqlite3", filename=name)
