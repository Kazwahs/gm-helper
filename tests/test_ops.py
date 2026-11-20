"""Ops features: /healthz, database backups, the optional password, the
OCR-is-optional behaviour, the home-page setup hint, and "the API key never
goes back to the browser"."""
import base64
import sqlite3
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import backup, config, database, llm, security
from app.library import ocr as ocr_module


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def _save_response_to(tmp_path, response, name="download.db"):
    path = tmp_path / name
    path.write_bytes(response.content)
    return path


# ---------------------------------------------------------------- healthz

def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_reports_503_when_database_is_unreachable(client, monkeypatch):
    def boom():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(database, "get_connection", boom)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["status"] == "error"


# ---------------------------------------------------------------- backups

def _seed_user_data(a_book):
    database.set_setting("library_root", "/some/library")
    database.set_setting("ai_provider", "openai_compatible")
    database.set_setting("ai_api_key", "sk-super-secret")
    database.set_setting("ai_verified", "1")
    database.set_setting("ai_verified_at", "2026-01-01 00:00 UTC")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE books SET indexed = 1, has_text = 1, ocr_attempted = 1, text_source = 'ocr' WHERE id = ?", (a_book,))
        cur.execute(
            "INSERT INTO book_pages_fts (content, book_id, page_number, book_title) VALUES (?, ?, ?, ?)",
            ("a dragon sleeps beneath the mountain", a_book, 3, "Test Book"),
        )
    from app import bookmarks
    bookmarks.add_bookmark(bookmarks.get_or_create_collection("Favourites"), a_book, 3, "Dragon lair")


def test_full_backup_is_a_complete_valid_sqlite_copy(client, a_book, tmp_path):
    _seed_user_data(a_book)
    r = client.get("/backup/full")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "gm_helper-backup-" in r.headers["content-disposition"]
    path = _save_response_to(tmp_path, r)

    copy = sqlite3.connect(path)
    try:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT COUNT(*) FROM book_pages_fts").fetchone()[0] == 1
        assert copy.execute("SELECT value FROM settings WHERE key = 'ai_api_key'").fetchone()[0] == "sk-super-secret"
        assert copy.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 1
    finally:
        copy.close()


def test_user_data_backup_drops_search_index_and_secrets_but_keeps_everything_else(client, a_book, tmp_path):
    _seed_user_data(a_book)
    r = client.get("/backup/user-data")
    assert r.status_code == 200
    assert "gm_helper-userdata-" in r.headers["content-disposition"]
    path = _save_response_to(tmp_path, r)

    copy = sqlite3.connect(path)
    try:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT COUNT(*) FROM book_pages_fts").fetchone()[0] == 0
        keys = {row[0] for row in copy.execute("SELECT key FROM settings")}
        assert "ai_api_key" not in keys and "ai_verified" not in keys and "ai_verified_at" not in keys
        assert "library_root" in keys and "ai_provider" in keys
        # bookmarks and the book list survive; the book is flagged for re-indexing
        assert copy.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 1
        row = copy.execute("SELECT indexed, has_text, ocr_attempted, text_source FROM books").fetchone()
        assert tuple(row) == (0, 0, 0, None)
    finally:
        copy.close()
    # raw bytes must not contain the key either (VACUUM removed freed pages)
    assert b"sk-super-secret" not in path.read_bytes()


def test_user_data_backup_leaves_the_live_database_untouched(client, a_book):
    _seed_user_data(a_book)
    client.get("/backup/user-data")
    assert database.get_setting("ai_api_key") == "sk-super-secret"
    with database.get_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM book_pages_fts").fetchone()[0] == 1
        assert cur.execute("SELECT indexed FROM books").fetchone()[0] == 1


def test_backup_temp_files_are_cleaned_up(client, a_book, tmp_path, monkeypatch):
    made = []
    real = backup.make_backup

    def spy(kind):
        path = real(kind)
        made.append(path)
        return path

    monkeypatch.setattr(backup, "make_backup", spy)
    client.get("/backup/full")
    client.get("/backup/user-data")
    import os
    assert len(made) == 2
    assert not any(os.path.exists(p) for p in made)


def test_make_backup_rejects_unknown_kind(db):
    with pytest.raises(ValueError):
        backup.make_backup("everything")


def test_backup_filenames_are_distinct_per_kind():
    from datetime import datetime
    now = datetime(2026, 3, 4, 5, 6)
    assert backup.backup_filename("full", now) == "gm_helper-backup-20260304-0506.db"
    assert backup.backup_filename("user-data", now) == "gm_helper-userdata-20260304-0506.db"


def test_settings_page_offers_both_backups(client):
    html = client.get("/settings").text
    assert 'href="/backup/user-data"' in html
    assert 'href="/backup/full"' in html
    assert "includes your saved AI API key" in html


# ---------------------------------------------------------------- password

def test_auth_disabled_when_password_unset(client, monkeypatch):
    monkeypatch.delenv(config.PASSWORD_ENV, raising=False)
    assert security.is_enabled() is False
    assert client.get("/").status_code == 200


def test_auth_disabled_when_password_blank(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "")
    assert security.is_enabled() is False
    assert client.get("/").status_code == 200


def test_auth_blocks_requests_without_credentials(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    r = client.get("/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/api/search?q=dragon").status_code == 401
    assert client.get("/static/style.css").status_code == 401
    assert client.get("/backup/full").status_code == 401
    assert client.post("/api/screen/notes/append", data={"text": "x"}).status_code == 401


def test_auth_accepts_right_credentials_default_username(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    monkeypatch.delenv(config.USER_ENV, raising=False)
    assert client.get("/", headers=_basic("gm", "hunter2")).status_code == 200


def test_auth_custom_username(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    monkeypatch.setenv(config.USER_ENV, "zak")
    assert client.get("/", headers=_basic("zak", "hunter2")).status_code == 200
    assert client.get("/", headers=_basic("gm", "hunter2")).status_code == 401


def test_auth_rejects_wrong_password_and_wrong_user(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    assert client.get("/", headers=_basic("gm", "nope")).status_code == 401
    assert client.get("/", headers=_basic("root", "hunter2")).status_code == 401


def test_auth_password_may_contain_colons(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "pa:ss:word")
    assert client.get("/", headers=_basic("gm", "pa:ss:word")).status_code == 200


def test_auth_rejects_malformed_headers(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    for bad in ("Basic", "Basic !!!notbase64!!!", "Bearer abc", "Basic " + base64.b64encode(b"nocolon").decode(),
                "Basic " + base64.b64encode(b"\xff\xfe:x").decode()):
        r = client.get("/", headers={"Authorization": bad})
        assert r.status_code == 401, bad


def test_healthz_stays_open_when_password_is_set(client, monkeypatch):
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    assert client.get("/healthz").status_code == 200


def test_auth_handles_non_http_scopes_safely(monkeypatch):
    """Lifespan scopes pass straight through; a websocket without credentials is closed."""
    import asyncio

    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    mw = security.BasicAuthMiddleware(inner)
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {}

    asyncio.run(mw({"type": "lifespan"}, receive, send))
    assert seen == ["lifespan"]

    asyncio.run(mw({"type": "websocket", "path": "/ws", "headers": []}, receive, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert seen == ["lifespan"]


# ---------------------------------------------------------------- OCR is optional

def test_ocr_reports_missing_modules(monkeypatch):
    import importlib.util as iu

    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a, **k: None if name in ("easyocr", "fitz") else real(name, *a, **k))
    missing = ocr_module.missing_modules()
    assert "easyocr" in missing
    assert ocr_module.is_available() is False
    with pytest.raises(ocr_module.OCRUnavailable) as exc:
        ocr_module.require_available()
    assert "requirements-ocr.txt" in str(exc.value)


def test_ocr_available_when_nothing_missing(monkeypatch):
    monkeypatch.setattr(ocr_module, "missing_modules", lambda: [])
    assert ocr_module.is_available() is True
    ocr_module.require_available()  # does not raise


def test_settings_page_disables_ocr_buttons_when_unavailable(client, monkeypatch):
    monkeypatch.setattr(ocr_module, "missing_modules", lambda: ["easyocr"])
    html = client.get("/settings").text
    assert 'id="ocr-unavailable"' in html
    assert "easyocr" in html
    assert 'id="ocr-test-btn" class="secondary" disabled' in html
    assert "const ocrAvailable = false;" in html


def test_settings_page_enables_ocr_buttons_when_available(client, monkeypatch):
    monkeypatch.setattr(ocr_module, "missing_modules", lambda: [])
    html = client.get("/settings").text
    assert 'id="ocr-unavailable"' not in html
    assert 'id="ocr-test-btn" class="secondary" disabled' not in html
    assert "const ocrAvailable = true;" in html


def test_api_ocr_returns_400_when_unavailable(client, monkeypatch, tmp_path):
    database.set_setting("library_root", str(tmp_path))
    monkeypatch.setattr(ocr_module, "missing_modules", lambda: ["easyocr"])
    r = client.post("/api/ocr", data={"mode": "pending"})
    assert r.status_code == 400
    assert "OCR isn't installed" in r.json()["error"]


def test_importing_the_app_does_not_load_the_ocr_libraries():
    """The slim Docker image has no numpy/pymupdf/easyocr; the app must still start."""
    code = textwrap.dedent("""
        import sys
        import app.main
        heavy = [m for m in ("numpy", "fitz", "pymupdf", "easyocr", "torch") if m in sys.modules]
        print(",".join(heavy))
    """)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            cwd=config.APP_DIR + "/..", timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------- API key never rendered

def test_settings_page_never_renders_the_saved_api_key(client):
    llm.save_config("openai_compatible", "http://localhost:11434/v1", "sk-super-secret", "llama3")
    html = client.get("/settings").text
    assert "sk-super-secret" not in html
    assert "A key is saved" in html or "key is saved" in html.lower()


# ---------------------------------------------------------------- home page setup hint

def test_home_hint_asks_for_library_folder_first(client):
    assert 'id="setup-hint"' in client.get("/").text
    assert "tell GM Helper where your books live" in client.get("/").text


def test_home_hint_asks_for_scan_when_folder_set_but_no_books(client, tmp_path):
    database.set_setting("library_root", str(tmp_path))
    html = client.get("/").text
    assert "no books have been found yet" in html


def test_home_hint_asks_for_index_when_books_exist_but_none_indexed(client, a_book, tmp_path):
    database.set_setting("library_root", str(tmp_path))
    html = client.get("/").text
    assert "none are searchable yet" in html
    assert "1 book found" in html


def test_home_hint_gone_once_something_is_indexed(client, a_book, tmp_path):
    database.set_setting("library_root", str(tmp_path))
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE books SET indexed = 1 WHERE id = ?", (a_book,))
    assert 'id="setup-hint"' not in client.get("/").text
