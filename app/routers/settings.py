"""Settings page plus everything it drives: theme, library folder, scan/index/OCR jobs, AI connection."""
import threading

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import database
from .. import llm as llm_module
from .. import theme as theme_module
from ..library import scanner, indexer
from ..library import progress as index_progress
from ..library import ocr as ocr_module
from ..library import ocr_progress
from ..web import base_ctx, library_root, templates

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request, scan_result: str = None):
    ctx = base_ctx(request)
    ctx["scan_result"] = scan_result
    ctx["ocr_available"] = ocr_module.is_available()
    ctx["ocr_missing"] = ocr_module.missing_modules()
    ctx["ocr_counts"] = ocr_module.count_pending() if library_root() else {"pending": 0, "failed": 0, "ocr_searchable": 0}
    ctx["theme_presets"] = theme_module.PRESETS
    ctx["custom_keys"] = theme_module.CUSTOM_KEYS
    # Always available (not just when "custom" is active) so the customize
    # panel has something sensible to start from either way.
    ctx["custom_theme_vars"] = theme_module.get_custom_vars()
    ctx["ai_providers"] = llm_module.PROVIDERS
    ctx["ai_config"] = llm_module.get_public_config()
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/api/settings/ai")
def save_ai_settings(provider: str = Form(...), base_url: str = Form(""),
                      api_key: str = Form(""), model: str = Form(""), clear_key: int = Form(0)):
    try:
        llm_module.save_config(provider, base_url, api_key, model, clear_key=bool(clear_key))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/settings/ai/test")
def test_ai_settings():
    result = llm_module.test_connection()
    if not result["ok"]:
        return JSONResponse({"error": result["error"]}, status_code=400)
    return JSONResponse({"ok": True, "reply": result["reply"], "verified_at": llm_module.get_config()["verified_at"]})


@router.post("/settings/library-root")
def set_library_root(library_root: str = Form(...)):
    database.set_setting("library_root", library_root.strip())
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/api/settings/theme")
def set_theme(name: str = Form(...)):
    try:
        theme_module.set_theme_name(name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "theme_name": name})


@router.post("/api/settings/custom-theme")
async def set_custom_theme(request: Request):
    form = await request.form()
    values = dict(form.items())
    try:
        saved = theme_module.set_custom_vars(values)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "custom_theme_vars": saved})


@router.post("/api/scan")
def api_scan():
    root = library_root()
    if not root:
        return JSONResponse({"error": "Set your library folder on the Settings page first."}, status_code=400)
    try:
        result = scanner.scan_library(root)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


def _run_indexing_job(root, mode):
    def progress_cb(done, total, current_title=None, with_text=None, failed=None):
        index_progress.update(done, total, current_title=current_title, with_text=with_text, failed=failed)

    try:
        if mode == "all":
            summary = indexer.reindex_all(root, progress_cb=progress_cb)
        else:
            summary = indexer.index_pending(root, progress_cb=progress_cb)
        index_progress.finish(summary=summary)
    except Exception as e:  # keep the UI informed instead of hanging at "running"
        index_progress.finish(error=f"{type(e).__name__}: {e}")


@router.post("/api/index")
def api_index(mode: str = Form("pending")):
    root = library_root()
    if not root:
        return JSONResponse({"error": "Set your library folder on the Settings page first."}, status_code=400)
    if index_progress.is_running():
        return JSONResponse({"error": "Indexing is already running."}, status_code=409)

    with database.get_cursor() as cur:
        if mode == "all":
            total = cur.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"]
        else:
            total = cur.execute("SELECT COUNT(*) AS n FROM books WHERE indexed = 0").fetchone()["n"]

    index_progress.start(mode, total)
    thread = threading.Thread(target=_run_indexing_job, args=(root, mode), daemon=True)
    thread.start()
    return JSONResponse({"started": True, "mode": mode, "total": total})


@router.get("/api/index/progress")
def api_index_progress():
    return JSONResponse(index_progress.snapshot())


def _run_ocr_job(root, mode, limit):
    def progress_cb(done, total, current_title=None, current_page=None,
                     current_book_pages=None, with_text=None, failed=None):
        ocr_progress.update(done, total, current_title=current_title, current_page=current_page,
                             current_book_pages=current_book_pages, with_text=with_text, failed=failed)

    try:
        if mode == "retry_failed":
            summary = ocr_module.retry_failed(root, progress_cb=progress_cb)
        else:
            summary = ocr_module.ocr_pending(root, limit=limit, progress_cb=progress_cb)
        ocr_progress.finish(summary=summary)
    except Exception as e:
        ocr_progress.finish(error=f"{type(e).__name__}: {e}")


@router.post("/api/ocr")
def api_ocr(mode: str = Form("pending"), limit: int = Form(None)):
    root = library_root()
    if not root:
        return JSONResponse({"error": "Set your library folder on the Settings page first."}, status_code=400)
    try:
        ocr_module.require_available()
    except ocr_module.OCRUnavailable as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if ocr_progress.is_running():
        return JSONResponse({"error": "OCR is already running."}, status_code=409)

    counts = ocr_module.count_pending()
    if mode == "retry_failed":
        total = counts["failed"]
    else:
        total = min(counts["pending"], limit) if limit else counts["pending"]

    if total == 0:
        return JSONResponse({"error": "No scanned books are waiting for OCR right now."}, status_code=400)

    ocr_progress.start(total)
    thread = threading.Thread(target=_run_ocr_job, args=(root, mode, limit), daemon=True)
    thread.start()
    return JSONResponse({"started": True, "mode": mode, "total": total})


@router.get("/api/ocr/progress")
def api_ocr_progress():
    return JSONResponse(ocr_progress.snapshot())
