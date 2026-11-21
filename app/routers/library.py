"""Library: browse, full-text search, open a book's PDF, move books between games."""
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import bookmarks as bookmarks_module
from .. import database
from ..library import games as games_module
from ..library import ask as ask_module
from ..library import scanner, search as search_module, statblock as statblock_module
from ..web import base_ctx, library_root, templates

router = APIRouter()


@router.get("/library")
def library_page(request: Request, game: str = None, q: str = None, page: int = 1):
    game_obj = games_module.get_game(game) if game else None
    found = search_module.search_page(q, game_key=game, page=page) if q else None
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

    ctx = base_ctx(request)
    ctx.update({
        "game": game_obj,
        "game_key": game,
        "query": q or "",
        "found": found,
        "groups": search_module.group_by_book(found["results"]) if found else [],
        "books": books,
        "stats": stats,
        "collections": bookmarks_module.list_collections(),
        "all_games": all_games,
        "game_names": game_names,
    })
    return templates.TemplateResponse(request, "library.html", ctx)


@router.get("/api/search")
def api_search(q: str, game: str = None, limit: int = 50, offset: int = 0):
    limit = max(1, min(limit, 200))
    return JSONResponse(search_module.search(q, game_key=game, limit=limit, offset=max(0, offset)))


@router.post("/api/library/ask")
def api_library_ask(question: str = Form(...), game: str = Form("")):
    """Answer a question from the library's own pages, with page citations."""
    game_obj = games_module.get_game(game) if game else None
    try:
        return JSONResponse(ask_module.ask(question, game_key=game or None,
                                           game_name=game_obj["name"] if game_obj else None))
    except ask_module.AskError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)


@router.post("/api/library/statblock")
def api_library_statblock(book_id: int = Form(...), page_number: int = Form(...), game: str = Form("")):
    """Creatures found on one book page, ready to review and add to the tracker."""
    game_obj = games_module.get_game(game) if game else None
    try:
        return JSONResponse(statblock_module.extract(
            book_id, page_number, game_name=game_obj["name"] if game_obj else None))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@router.post("/api/books/move")
def api_move_books(book_ids: str = Form(...), target_key: str = Form(...)):
    ids = [int(x) for x in book_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return JSONResponse({"error": "No books selected."}, status_code=400)
    if target_key != games_module.fallback_key() and games_module.get_game(target_key) is None:
        return JSONResponse({"error": f"Unknown game: {target_key}"}, status_code=400)
    moved = scanner.move_books_to_game(ids, target_key)
    return JSONResponse({"ok": True, "moved": moved})


@router.post("/api/books/{book_id}/reset-game")
def api_reset_book_game(book_id: int):
    try:
        new_key = scanner.reset_book_game(book_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "game_key": new_key})


@router.get("/books/{book_id}/file")
def serve_book_file(book_id: int):
    """Streams a book's PDF straight from the library folder so it opens in
    the browser's own PDF viewer (Range requests included, so big scans
    don't have to fully download before the first page shows)."""
    root = library_root()
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
