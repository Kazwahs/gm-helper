"""Manage Games: rename / merge games and move Series folders between them."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import database
from ..library import games as games_module
from ..library import scanner
from ..web import base_ctx, templates

router = APIRouter()


@router.get("/games/manage")
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
    ctx = base_ctx(request)
    ctx.update({
        "games": games_module.get_games(),
        "counts": counts,
        "fallback_key": games_module.fallback_key(),
        "fallback_name": games_module.fallback_name(),
        "series_rows": [dict(r) for r in series_rows],
    })
    return templates.TemplateResponse(request, "games_manage.html", ctx)


@router.post("/api/games/create")
def api_create_game(name: str = Form(...), system: str = Form("")):
    try:
        key = games_module.create_game(name, system=(system or "").strip() or "generic")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "key": key})


@router.post("/api/games/rename")
def api_rename_game(key: str = Form(...), name: str = Form(...)):
    try:
        games_module.rename_game(key, name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/games/merge")
def api_merge_games(source_key: str = Form(...), target_key: str = Form(...)):
    try:
        moved_series = games_module.merge_games(source_key, target_key)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE books SET game_key = ? WHERE game_key = ?", (target_key, source_key))
    return JSONResponse({"ok": True, "moved_series": moved_series})


@router.post("/api/games/move-series")
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
