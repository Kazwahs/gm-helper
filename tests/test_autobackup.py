"""Tests for automatic backups (app/autobackup.py), its API on the ops router,
and the Settings page controls."""
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import autobackup, backup, config, database


@pytest.fixture
def folder(db, tmp_path, monkeypatch):
    path = tmp_path / "autobackups"
    monkeypatch.setenv("GM_HELPER_BACKUP_DIR", str(path))
    return path


@pytest.fixture
def client(db, games_config, folder):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _age(path, hours):
    t = time.time() - hours * 3600
    os.utime(path, (t, t))


def test_default_folder_sits_next_to_the_database(db, monkeypatch):
    monkeypatch.delenv("GM_HELPER_BACKUP_DIR", raising=False)
    assert autobackup.backup_dir() == os.path.join(os.path.dirname(config.DB_PATH), "backups")


def test_settings_default_to_on_daily_keep_fourteen(db):
    assert autobackup.get_settings() == {"enabled": True, "hours": 24, "keep": 14}


def test_settings_save_and_clamp(db):
    assert autobackup.save_settings(False, "12", "5") == {"enabled": False, "hours": 12, "keep": 5}
    assert autobackup.save_settings(True, "0", "100000") == {"enabled": True, "hours": 1, "keep": 200}
    assert autobackup.save_settings(True, "banana", "") == {"enabled": True, "hours": 24, "keep": 14}
    assert autobackup.save_settings(True, "99999", "-3")["hours"] == 24 * 30
    assert autobackup.get_settings()["keep"] == 1


def test_a_backup_is_a_real_user_data_snapshot_without_secrets(db, folder):
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO campaigns (game_key, title) VALUES ('rifts', 'The Long Night')")
    database.set_setting("ai_api_key", "sk-secret")
    name = autobackup.run_backup(datetime(2026, 11, 21, 8, 30))
    assert name == "gm_helper-auto-20261121-0830.db"
    conn = sqlite3.connect(folder / name)
    try:
        assert conn.execute("SELECT title FROM campaigns").fetchone()[0] == "The Long Night"
        assert conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'ai_api_key'").fetchone()[0] == 0
    finally:
        conn.close()


def test_old_backups_are_pruned_to_the_keep_count(db, folder):
    autobackup.save_settings(True, 24, 3)
    base = datetime(2026, 11, 1, 3, 0)
    names = []
    for i in range(6):
        names.append(autobackup.run_backup(base + timedelta(days=i)))
        _age(folder / names[-1], hours=(6 - i) * 24)   # keep file ages in the same order as their names
    left = [b["name"] for b in autobackup.list_backups()]
    assert left == names[-3:][::-1]                     # newest first, only three kept
    assert len(os.listdir(folder)) == 3


def test_foreign_files_are_never_listed_or_deleted(db, folder):
    autobackup.save_settings(True, 24, 1)
    os.makedirs(folder, exist_ok=True)
    (folder / "notes.txt").write_text("mine")
    (folder / "gm_helper-auto-notadate.db").write_text("x")
    autobackup.run_backup(datetime(2026, 1, 1, 1, 1))
    autobackup.run_backup(datetime(2026, 1, 2, 1, 1))
    assert [b["name"] for b in autobackup.list_backups()] == ["gm_helper-auto-20260102-0101.db"]
    assert (folder / "notes.txt").exists() and (folder / "gm_helper-auto-notadate.db").exists()


@pytest.mark.parametrize("bad", ["", "../x.db", "..\\x.db", "gm_helper-auto-20260101-0101.db/../../etc", "gm_helper.db", None])
def test_path_for_only_returns_our_own_backup_names(db, folder, bad):
    assert autobackup.path_for(bad) is None


def test_path_for_finds_an_existing_backup(db, folder):
    name = autobackup.run_backup(datetime(2026, 5, 5, 5, 5))
    assert autobackup.path_for(name) == str(folder / name)
    assert autobackup.path_for("gm_helper-auto-20000101-0000.db") is None


def test_due_logic(db, folder):
    assert autobackup.is_due() is True                  # nothing yet
    name = autobackup.run_backup()
    assert autobackup.is_due() is False                 # just made one
    _age(folder / name, hours=25)
    assert autobackup.is_due() is True                  # older than a day
    autobackup.save_settings(True, 72, 14)
    assert autobackup.is_due() is False                 # a three-day interval
    autobackup.save_settings(False, 24, 14)
    _age(folder / name, hours=500)
    assert autobackup.is_due() is False                 # switched off


def test_run_if_due_makes_one_then_waits(db, folder):
    first = autobackup.run_if_due()
    assert first and autobackup.run_if_due() is None
    assert autobackup.last_error() == ""


def test_a_failing_backup_is_recorded_not_raised(db, folder, monkeypatch):
    def boom(kind):
        raise OSError("disk full")
    monkeypatch.setattr(backup, "make_backup", boom)
    assert autobackup.run_if_due() is None
    assert "disk full" in autobackup.last_error()
    monkeypatch.undo()
    monkeypatch.setenv("GM_HELPER_BACKUP_DIR", str(folder))
    assert autobackup.run_if_due() is not None
    assert autobackup.last_error() == ""


def test_scheduler_runs_a_due_backup_and_stops_cleanly(db, folder):
    sched = autobackup.Scheduler(check_every=0.05, first_delay=0.01)
    sched.start()
    deadline = time.time() + 5
    while time.time() < deadline and not autobackup.list_backups():
        time.sleep(0.02)
    sched.stop()
    assert len(autobackup.list_backups()) == 1
    assert not any(t.name == "gm-helper-autobackup" and t.is_alive() for t in threading.enumerate())


def test_scheduler_start_is_idempotent(db, folder):
    sched = autobackup.Scheduler(check_every=30, first_delay=30)
    sched.start()
    sched.start()
    assert sum(1 for t in threading.enumerate() if t.name == "gm-helper-autobackup") == 1
    sched.stop()


def test_app_starts_and_stops_the_scheduler(db, games_config, folder):
    from app.main import app

    with TestClient(app):
        assert any(t.name == "gm-helper-autobackup" and t.is_alive() for t in threading.enumerate())
    assert not any(t.name == "gm-helper-autobackup" and t.is_alive() for t in threading.enumerate())


# ------------------------------------------------------------------------- API
def test_api_lists_settings_and_backups(client):
    data = client.get("/api/backups").json()
    assert data["settings"] == {"enabled": True, "hours": 24, "keep": 14}
    assert data["backups"] == [] and data["last_error"] == ""
    assert data["folder"]


def test_api_back_up_now_then_download(client):
    res = client.post("/api/backups/run")
    assert res.status_code == 200 and res.json()["ok"] is True
    name = res.json()["name"]
    assert [b["name"] for b in res.json()["backups"]] == [name]
    dl = client.get(f"/backup/auto/{name}")
    assert dl.status_code == 200 and dl.content.startswith(b"SQLite format 3")


def test_api_settings_round_trip_and_prune(client):
    for i in range(3):
        autobackup.run_backup(datetime(2026, 3, 1 + i, 4, 4))
    res = client.post("/api/backups/settings", data={"enabled": "0", "hours": "12", "keep": "1"})
    body = res.json()
    assert body["settings"] == {"enabled": False, "hours": 12, "keep": 1}
    assert len(body["backups"]) == 1


def test_api_download_refuses_unknown_or_sneaky_names(client):
    assert client.get("/backup/auto/nope.db").status_code == 404
    assert client.get("/backup/auto/..%2F..%2Fgm_helper.db").status_code == 404
    assert client.get("/backup/auto/gm_helper-auto-20000101-0000.db").status_code == 404


def test_api_run_reports_a_failed_backup(client, monkeypatch):
    monkeypatch.setattr(backup, "make_backup", lambda kind: (_ for _ in ()).throw(OSError("no space")))
    res = client.post("/api/backups/run")
    assert res.status_code == 500 and "no space" in res.json()["detail"]
    assert "no space" in client.get("/api/backups").json()["last_error"]


def test_settings_page_has_the_automatic_backup_controls(client):
    html = client.get("/settings").text
    assert 'id="autobackup-form"' in html and 'id="autobackup-run"' in html and "/api/backups" in html
