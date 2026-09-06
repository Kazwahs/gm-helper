import json
import os
import threading

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import bookmarks as bookmarks_module
from . import campaigns as campaigns_module
from . import config, database
from . import gm_screen
from . import maps_store
from .library import games as games_module
from .library import scanner, indexer, search as search_module
from .library import progress as index_progress
from .library import ocr as ocr_module
from .library import ocr_progress
from .maps import MAP_TYPES, generate_map
from .maps.generator import GENRES as MAP_GENRES, SIZES as MAP_SIZES
from .tools import dice as dice_tool
from .tools import npc_generator
from .tools import initiative as initiative_tool
from .tools import encounters as encounters_tool

app = FastAPI(title="GM Helper")

app.mount("/static", StaticFiles(directory=os.path.join(config.APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(config.APP_DIR, "templates"))


@app.on_event("startup")
def startup():
    database.init_db()
    games_module.reload()


def _library_root():
    return database.get_setting("library_root", "")


def _base_ctx(request):
    return {"request": request, "library_root": _library_root()}


# ---------------------------------------------------------------- dashboard
@app.get("/")
def index(request: Request):
    root = _library_root()
    counts = scanner.game_summary() if root else {}
    games = games_module.all_game_choices()
    for g in games:
        g["count"] = counts.get(g["key"], 0)
    ctx = _base_ctx(request)
    ctx.update({"games": games, "needs_setup": not root})
    return templates.TemplateResponse(request, "index.html", ctx)


# ----------------------------------------------------------------- settings
@app.get("/settings")
def settings_page(request: Request, scan_result: str = None):
    ctx = _base_ctx(request)
    ctx["scan_result"] = scan_result
    ctx["ocr_counts"] = ocr_module.count_pending() if _library_root() else {"pending": 0, "failed": 0, "ocr_searchable": 0}
    return templates.TemplateResponse(request, "settings.html", ctx)


@app.post("/settings/library-root")
def set_library_root(library_root: str = Form(...)):
    database.set_setting("library_root", library_root.strip())
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/api/scan")
def api_scan():
    root = _library_root()
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


@app.post("/api/index")
def api_index(mode: str = Form("pending")):
    root = _library_root()
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


@app.get("/api/index/progress")
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


@app.post("/api/ocr")
def api_ocr(mode: str = Form("pending"), limit: int = Form(None)):
    root = _library_root()
    if not root:
        return JSONResponse({"error": "Set your library folder on the Settings page first."}, status_code=400)
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


@app.get("/api/ocr/progress")
def api_ocr_progress():
    return JSONResponse(ocr_progress.snapshot())


# ------------------------------------------------------------- manage games
@app.get("/games/manage")
def games_manage_page(request: Request):
    games_module.reload()
    counts = scanner.game_summary()
    with database.get_cursor() as cur:
        series_rows = cur.execute(
            """SELECT publisher, series, game_key, COUNT(*) AS n
               FROM books
               GROUP BY publisher, series, game_key
               ORDER BY publisher, series"""
        ).fetchall()
    ctx = _base_ctx(request)
    ctx.update({
        "games": games_module.get_games(),
        "counts": counts,
        "fallback_key": games_module.fallback_key(),
        "fallback_name": games_module.fallback_name(),
        "series_rows": [dict(r) for r in series_rows],
    })
    return templates.TemplateResponse(request, "games_manage.html", ctx)


@app.post("/api/games/rename")
def api_rename_game(key: str = Form(...), name: str = Form(...)):
    try:
        games_module.rename_game(key, name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/games/merge")
def api_merge_games(source_key: str = Form(...), target_key: str = Form(...)):
    try:
        moved_series = games_module.merge_games(source_key, target_key)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE books SET game_key = ? WHERE game_key = ?", (target_key, source_key))
    return JSONResponse({"ok": True, "moved_series": moved_series})


@app.post("/api/games/move-series")
def api_move_series(publisher: str = Form(...), series: str = Form(...),
                     target_key: str = Form(...), new_game_name: str = Form(None)):
    try:
        if target_key == "__new__":
            if not new_game_name or not new_game_name.strip():
                return JSONResponse({"error": "Name the new game first."}, status_code=400)
            target_key = games_module.create_game(new_game_name)
        games_module.move_series(series, target_key)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE books SET game_key = ? WHERE publisher = ? AND series = ?",
            (target_key, publisher, series),
        )
    return JSONResponse({"ok": True, "new_game_key": target_key})


# ------------------------------------------------------------------ library
@app.get("/library")
def library_page(request: Request, game: str = None, q: str = None):
    game_obj = games_module.get_game(game) if game else None
    results = []
    if q:
        results = search_module.search(q, game_key=game)
    stats = search_module.search_stats(game_key=game)

    with database.get_cursor() as cur:
        sql = "SELECT * FROM books"
        params = []
        if game:
            sql += " WHERE game_key = ?"
            params.append(game)
        sql += " ORDER BY series, title LIMIT 500"
        books = [dict(r) for r in cur.execute(sql, params).fetchall()]

    all_games = games_module.all_game_choices()
    game_names = {g["key"]: g["name"] for g in all_games}

    ctx = _base_ctx(request)
    ctx.update({
        "game": game_obj,
        "game_key": game,
        "query": q or "",
        "results": results,
        "books": books,
        "stats": stats,
        "collections": bookmarks_module.list_collections(),
        "all_games": all_games,
        "game_names": game_names,
    })
    return templates.TemplateResponse(request, "library.html", ctx)


@app.get("/api/search")
def api_search(q: str, game: str = None):
    return JSONResponse(search_module.search(q, game_key=game))


@app.post("/api/books/move")
def api_move_books(book_ids: str = Form(...), target_key: str = Form(...)):
    ids = [int(x) for x in book_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return JSONResponse({"error": "No books selected."}, status_code=400)
    if target_key != games_module.fallback_key() and games_module.get_game(target_key) is None:
        return JSONResponse({"error": f"Unknown game: {target_key}"}, status_code=400)
    moved = scanner.move_books_to_game(ids, target_key)
    return JSONResponse({"ok": True, "moved": moved})


@app.post("/api/books/{book_id}/reset-game")
def api_reset_book_game(book_id: int):
    try:
        new_key = scanner.reset_book_game(book_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "game_key": new_key})


@app.get("/books/{book_id}/file")
def serve_book_file(book_id: int):
    """Streams a book's PDF straight from the library folder so it opens in
    the browser's own PDF viewer (Range requests included, so big scans
    don't have to fully download before the first page shows)."""
    root = _library_root()
    if not root:
        raise HTTPException(status_code=404, detail="Library folder isn't set yet.")

    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown book.")

    root_norm = os.path.normpath(root)
    full_path = os.path.normpath(os.path.join(root_norm, row["path"].replace("/", os.sep)))
    # Belt-and-suspenders: the path always comes from a scan of real files on
    # disk, never from user input, but double-check it still resolves inside
    # library_root in case that setting changed since the last scan.
    if full_path != root_norm and not full_path.startswith(root_norm + os.sep):
        raise HTTPException(status_code=400, detail="Book path is outside the library folder.")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File is missing on disk - try rescanning the library.")

    return FileResponse(
        full_path,
        media_type="application/pdf",
        filename=row["filename"],
        content_disposition_type="inline",
    )


# --------------------------------------------------------------- bookmarks
@app.get("/bookmarks")
def bookmarks_page(request: Request):
    ctx = _base_ctx(request)
    ctx["collections"] = bookmarks_module.list_collections_with_bookmarks()
    return templates.TemplateResponse(request, "bookmarks.html", ctx)


@app.get("/api/bookmarks/collections")
def api_list_collections():
    return JSONResponse(bookmarks_module.list_collections())


@app.post("/api/bookmarks/collections")
def api_create_collection(name: str = Form(...)):
    try:
        collection_id = bookmarks_module.create_collection(name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": collection_id, "name": name.strip()})


@app.post("/api/bookmarks/collections/{collection_id}/rename")
def api_rename_collection(collection_id: int, name: str = Form(...)):
    try:
        bookmarks_module.rename_collection(collection_id, name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/bookmarks/collections/{collection_id}/delete")
def api_delete_collection(collection_id: int):
    bookmarks_module.delete_collection(collection_id)
    return JSONResponse({"ok": True})


@app.post("/api/bookmarks/add")
def api_add_bookmark(book_id: int = Form(...), page_number: int = Form(None),
                      note: str = Form(""), collection_id: int = Form(None),
                      new_collection_name: str = Form(None)):
    if not collection_id and not new_collection_name:
        return JSONResponse({"error": "Choose a collection or name a new one."}, status_code=400)
    with database.get_cursor() as cur:
        book_exists = cur.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if collection_id:
            collection_exists = cur.execute(
                "SELECT 1 FROM bookmark_collections WHERE id = ?", (collection_id,)
            ).fetchone()
        else:
            collection_exists = True
    if not book_exists:
        return JSONResponse({"error": "That book no longer exists - try rescanning the library."}, status_code=400)
    if not collection_exists:
        return JSONResponse({"error": "That collection no longer exists - refresh the page."}, status_code=400)
    try:
        if not collection_id:
            collection_id = bookmarks_module.get_or_create_collection(new_collection_name)
        bookmark_id = bookmarks_module.add_bookmark(collection_id, book_id, page_number, note)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": bookmark_id, "collection_id": collection_id})


@app.post("/api/bookmarks/{bookmark_id}/note")
def api_update_bookmark_note(bookmark_id: int, note: str = Form("")):
    bookmarks_module.update_bookmark_note(bookmark_id, note)
    return JSONResponse({"ok": True})


@app.post("/api/bookmarks/{bookmark_id}/delete")
def api_delete_bookmark(bookmark_id: int):
    bookmarks_module.delete_bookmark(bookmark_id)
    return JSONResponse({"ok": True})


# -------------------------------------------------------------- campaigns
@app.get("/campaigns")
def campaigns_page(request: Request, game: str = None):
    game_obj = games_module.get_game(game) if game else None
    all_games = games_module.all_game_choices()
    ctx = _base_ctx(request)
    ctx.update({
        "game": game_obj,
        "game_key": game,
        "campaigns": campaigns_module.list_campaigns(game_key=game),
        "all_games": all_games,
        "game_names": {g["key"]: g["name"] for g in all_games},
    })
    return templates.TemplateResponse(request, "campaigns.html", ctx)


@app.post("/api/campaigns")
def api_create_campaign(game_key: str = Form(...), title: str = Form(...),
                         pitch: str = Form(""), tone: str = Form("")):
    if game_key != games_module.fallback_key() and games_module.get_game(game_key) is None:
        return JSONResponse({"error": f"Unknown game: {game_key}"}, status_code=400)
    try:
        campaign_id = campaigns_module.create_campaign(game_key, title, pitch, tone)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": campaign_id})


@app.get("/campaigns/{campaign_id}")
def campaign_detail_page(request: Request, campaign_id: int):
    detail = campaigns_module.get_campaign_detail(campaign_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Unknown campaign.")
    game_key = detail["campaign"]["game_key"]
    ctx = _base_ctx(request)
    ctx.update(detail)
    ctx.update({
        "game": games_module.get_game(game_key),
        "game_key": game_key,
        "books": campaigns_module.books_for_game(game_key),
        "attached_maps": maps_store.list_maps(campaign_id=campaign_id),
    })
    return templates.TemplateResponse(request, "campaign_detail.html", ctx)


@app.post("/api/campaigns/{campaign_id}/update")
def api_update_campaign(campaign_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_campaign_field(campaign_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/delete")
def api_delete_campaign(campaign_id: int):
    campaigns_module.delete_campaign(campaign_id)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/threads")
def api_add_thread(campaign_id: int, title: str = Form(...), description: str = Form(""),
                    stakes: str = Form(""), status: str = Form("seed")):
    try:
        thread_id = campaigns_module.add_thread(campaign_id, title, description, stakes, status)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": thread_id})


@app.post("/api/campaigns/threads/{thread_id}/update")
def api_update_thread(thread_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_thread_field(thread_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/threads/{thread_id}/delete")
def api_delete_thread(thread_id: int):
    campaigns_module.delete_thread(thread_id)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/npcs")
def api_add_campaign_npc(campaign_id: int, name: str = Form(...), role: str = Form(""),
                          disposition: str = Form("neutral"), motivation: str = Form(""),
                          secret: str = Form(""), notes: str = Form("")):
    try:
        npc_id = campaigns_module.add_npc(campaign_id, name, role, disposition, motivation, secret, notes)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": npc_id})


@app.post("/api/campaigns/npcs/{npc_id}/update")
def api_update_campaign_npc(npc_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_npc_field(npc_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/npcs/{npc_id}/delete")
def api_delete_campaign_npc(npc_id: int):
    campaigns_module.delete_npc(npc_id)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/locations")
def api_add_location(campaign_id: int, name: str = Form(...), description: str = Form(""),
                      parent_id: int = Form(None)):
    try:
        location_id = campaigns_module.add_location(campaign_id, name, description, parent_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": location_id})


@app.post("/api/campaigns/locations/{location_id}/update")
def api_update_location(location_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_location_field(location_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/locations/{location_id}/delete")
def api_delete_location(location_id: int):
    campaigns_module.delete_location(location_id)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/factions")
def api_add_faction(campaign_id: int, name: str = Form(...), goal: str = Form(""), notes: str = Form("")):
    try:
        faction_id = campaigns_module.add_faction(campaign_id, name, goal, notes)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": faction_id})


@app.post("/api/campaigns/factions/{faction_id}/update")
def api_update_faction(faction_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_faction_field(faction_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/factions/{faction_id}/delete")
def api_delete_faction(faction_id: int):
    campaigns_module.delete_faction(faction_id)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/sessions")
def api_add_session(campaign_id: int, title: str = Form(""), date: str = Form("")):
    session_id = campaigns_module.add_session(campaign_id, title, date)
    return JSONResponse({"id": session_id})


@app.post("/api/campaigns/sessions/{session_id}/update")
def api_update_session(session_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_session_field(session_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/sessions/{session_id}/delete")
def api_delete_session(session_id: int):
    campaigns_module.delete_session(session_id)
    return JSONResponse({"ok": True})


@app.post("/api/campaigns/{campaign_id}/materials")
def api_add_material(campaign_id: int, book_id: int = Form(...), page_number: int = Form(None),
                      note: str = Form("")):
    with database.get_cursor() as cur:
        book_exists = cur.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book_exists:
        return JSONResponse({"error": "That book no longer exists - try rescanning the library."}, status_code=400)
    try:
        bookmark_id = campaigns_module.add_material(campaign_id, book_id, page_number, note)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": bookmark_id})


@app.post("/api/campaigns/materials/{bookmark_id}/delete")
def api_delete_material(bookmark_id: int):
    bookmarks_module.delete_bookmark(bookmark_id)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------- maps
@app.get("/maps")
def maps_page(request: Request, game: str = None, campaign_id: int = None):
    game_obj = games_module.get_game(game) if game else None
    ctx = _base_ctx(request)
    ctx.update({
        "game": game_obj,
        "game_key": game,
        "maps": maps_store.list_maps(game_key=game),
        "map_types": MAP_TYPES,
        "genres": MAP_GENRES,
        "sizes": MAP_SIZES,
        "all_games": games_module.all_game_choices(),
        "campaigns": campaigns_module.list_campaigns(game_key=game) if game else [],
        "preset_campaign_id": campaign_id,
    })
    return templates.TemplateResponse(request, "maps.html", ctx)


@app.post("/api/maps/generate")
def api_generate_map(map_type: str = Form(...), genre: str = Form(...), size: str = Form("medium")):
    try:
        result = generate_map(map_type, genre, params={"size": size})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


@app.post("/api/maps")
def api_save_map(game_key: str = Form(...), map_type: str = Form(...), genre: str = Form(...),
                  title: str = Form(...), svg: str = Form(...), seed: int = Form(None),
                  params_json: str = Form("{}"), campaign_id: int = Form(None)):
    if game_key != games_module.fallback_key() and games_module.get_game(game_key) is None:
        return JSONResponse({"error": f"Unknown game: {game_key}"}, status_code=400)
    try:
        params = json.loads(params_json) if params_json else {}
    except ValueError:
        params = {}
    if campaign_id:
        campaign = campaigns_module.get_campaign(campaign_id)
        if not campaign or campaign["game_key"] != game_key:
            return JSONResponse({"error": "That campaign doesn't belong to this game."}, status_code=400)
    map_id = maps_store.save_map(game_key, map_type, genre, title, svg, seed=seed,
                                  params=params, campaign_id=campaign_id)
    return JSONResponse({"id": map_id})


@app.get("/maps/{map_id}")
def map_detail_page(request: Request, map_id: int):
    m = maps_store.get_map(map_id)
    if not m:
        raise HTTPException(status_code=404, detail="Unknown map.")
    ctx = _base_ctx(request)
    ctx.update({
        "game": games_module.get_game(m["game_key"]),
        "game_key": m["game_key"],
        "map": m,
        "campaigns": campaigns_module.list_campaigns(game_key=m["game_key"]),
    })
    return templates.TemplateResponse(request, "map_detail.html", ctx)


@app.post("/api/maps/{map_id}/rename")
def api_rename_map(map_id: int, title: str = Form(...)):
    try:
        maps_store.rename_map(map_id, title)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/maps/{map_id}/attach")
def api_attach_map(map_id: int, campaign_id: int = Form(None)):
    m = maps_store.get_map(map_id)
    if not m:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    if campaign_id:
        campaign = campaigns_module.get_campaign(campaign_id)
        if not campaign or campaign["game_key"] != m["game_key"]:
            return JSONResponse({"error": "That campaign doesn't belong to this game."}, status_code=400)
    maps_store.set_campaign(map_id, campaign_id)
    return JSONResponse({"ok": True})


@app.post("/api/maps/{map_id}/svg")
def api_update_map_svg(map_id: int, svg: str = Form(...)):
    m = maps_store.get_map(map_id)
    if not m:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    if len(svg.encode("utf-8")) > 3_000_000:
        return JSONResponse({"error": "Edited map is too large to save."}, status_code=400)
    maps_store.update_svg(map_id, svg)
    return JSONResponse({"ok": True})


@app.post("/api/maps/{map_id}/revert")
def api_revert_map_svg(map_id: int):
    m = maps_store.get_map(map_id)
    if not m:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    maps_store.revert_svg(map_id)
    return JSONResponse({"ok": True})


@app.post("/api/maps/{map_id}/delete")
def api_delete_map(map_id: int):
    maps_store.delete_map(map_id)
    return JSONResponse({"ok": True})


# -------------------------------------------------------------------- tools
def _game_ctx(game_key):
    game_obj = games_module.get_game(game_key) if game_key else None
    return game_obj


@app.get("/tools/dice")
def dice_page(request: Request, game: str = None):
    ctx = _base_ctx(request)
    ctx["game"] = _game_ctx(game)
    return templates.TemplateResponse(request, "tools_dice.html", ctx)


@app.post("/api/dice/roll")
def api_dice_roll(expr: str = Form(...)):
    try:
        return JSONResponse(dice_tool.roll(expr))
    except dice_tool.DiceError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/screen")
def gm_screen_page(request: Request, game: str = None):
    ctx = _base_ctx(request)
    ctx["game"] = _game_ctx(game)
    ctx["game_key"] = game or "_global"
    ctx["cards"] = gm_screen.list_cards(game)
    ctx["notes"] = gm_screen.get_notes(game)
    return templates.TemplateResponse(request, "gm_screen.html", ctx)


@app.post("/api/screen/cards")
def api_screen_create_card(game: str = Form("_global"), title: str = Form(...), body: str = Form("")):
    try:
        return JSONResponse(gm_screen.create_card(game, title, body))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/screen/cards/{card_id}")
def api_screen_update_card(card_id: int, field: str = Form(...), value: str = Form("")):
    try:
        return JSONResponse(gm_screen.update_card_field(card_id, field, value))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/screen/cards/{card_id}/move")
def api_screen_move_card(card_id: int, direction: str = Form(...)):
    try:
        gm_screen.move_card(card_id, direction)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@app.post("/api/screen/cards/{card_id}/delete")
def api_screen_delete_card(card_id: int):
    gm_screen.delete_card(card_id)
    return JSONResponse({"ok": True})


@app.post("/api/screen/notes")
def api_screen_save_notes(game: str = Form("_global"), body: str = Form("")):
    gm_screen.set_notes(game, body)
    return JSONResponse({"ok": True})


@app.get("/tools/npc")
def npc_page(request: Request, game: str = None):
    ctx = _base_ctx(request)
    ctx["game"] = _game_ctx(game)
    ctx["campaigns"] = campaigns_module.list_campaigns(game_key=game) if game else []
    return templates.TemplateResponse(request, "tools_npc.html", ctx)


@app.post("/api/npc/generate")
def api_npc_generate(game: str = Form(None)):
    game_obj = _game_ctx(game)
    system = game_obj["system"] if game_obj else "generic"
    name = game_obj["name"] if game_obj else None
    return JSONResponse(npc_generator.generate(system=system, game_name=name))


@app.get("/tools/initiative")
def initiative_page(request: Request, game: str = None):
    ctx = _base_ctx(request)
    ctx["game"] = _game_ctx(game)
    ctx["game_key"] = game or "_global"
    return templates.TemplateResponse(request, "tools_initiative.html", ctx)


@app.get("/api/initiative")
def api_initiative_get(game: str = "_global"):
    return JSONResponse(initiative_tool.get_state(game))


@app.post("/api/initiative/add")
def api_initiative_add(game: str = Form("_global"), name: str = Form(...),
                        initiative: int = Form(...), hp: int = Form(None),
                        is_pc: bool = Form(False), notes: str = Form("")):
    return JSONResponse(initiative_tool.add_combatant(game, name, initiative, hp, is_pc, notes))


@app.post("/api/initiative/remove")
def api_initiative_remove(game: str = Form("_global"), combatant_id: int = Form(...)):
    return JSONResponse(initiative_tool.remove_combatant(game, combatant_id))


@app.post("/api/initiative/hp")
def api_initiative_hp(game: str = Form("_global"), combatant_id: int = Form(...), delta: int = Form(...)):
    return JSONResponse(initiative_tool.update_hp(game, combatant_id, delta))


@app.post("/api/initiative/next")
def api_initiative_next(game: str = Form("_global")):
    return JSONResponse(initiative_tool.next_turn(game))


@app.post("/api/initiative/reset")
def api_initiative_reset(game: str = Form("_global")):
    return JSONResponse(initiative_tool.reset(game))


@app.get("/tools/encounters")
def encounters_page(request: Request, game: str = None):
    ctx = _base_ctx(request)
    ctx["game"] = _game_ctx(game)
    ctx["environments"] = encounters_tool.environments()
    return templates.TemplateResponse(request, "tools_encounters.html", ctx)


@app.post("/api/encounters/generate")
def api_encounters_generate(environment: str = Form(...)):
    try:
        return JSONResponse(encounters_tool.generate_encounter(environment))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/loot/generate")
def api_loot_generate(tier: str = Form("mid"), include_item: bool = Form(True)):
    try:
        return JSONResponse(encounters_tool.generate_loot(tier, include_item))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
