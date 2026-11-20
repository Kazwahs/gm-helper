"""Random tables: the page and its JSON endpoints."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import random_tables as tables_module
from ..web import base_ctx, game_ctx, templates

router = APIRouter()


def _table_json(table_id):
    t = tables_module.get_table(table_id)
    info = tables_module.check(t["die"], t["body"]) if (t["body"] or "").strip() else None
    return {
        "id": t["id"], "name": t["name"], "die": t["die"], "game_key": t["game_key"],
        "effective_die": info["die"] if info else t["die"],
        "entry_count": len(info["entries"]) if info else 0,
        "warnings": info["warnings"] if info else [],
    }


def _fail(e, status=400):
    return JSONResponse({"error": str(e)}, status_code=status)


@router.get("/tools/tables")
def tables_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    ctx["game"] = game_ctx(game)
    ctx["game_key"] = game
    ctx["tables"] = tables_module.list_tables(game)
    ctx["history"] = tables_module.list_history(game)
    ctx["global_key"] = tables_module.GLOBAL_KEY
    return templates.TemplateResponse(request, "tools_tables.html", ctx)


@router.post("/api/tables")
def api_create_table(game: str = Form(""), name: str = Form(""), die: str = Form(""),
                     body: str = Form(""), everywhere: int = Form(0)):
    key = tables_module.GLOBAL_KEY if (everywhere or not game) else game
    try:
        table_id = tables_module.create_table(key, name, die, body)
    except tables_module.TableError as e:
        return _fail(e)
    return JSONResponse({"id": table_id})


@router.post("/api/tables/import-builtin")
def api_import_builtin(game: str = Form("")):
    return JSONResponse({"added": tables_module.import_builtin_tables(game or None)})


@router.post("/api/tables/history/clear")
def api_clear_history(game: str = Form("")):
    tables_module.clear_history(game or None)
    return JSONResponse({"ok": True})


@router.get("/api/tables/history")
def api_history(game: str = None):
    return JSONResponse(tables_module.list_history(game))


@router.post("/api/tables/{table_id}/update")
def api_update_table(table_id: int, field: str = Form(...), value: str = Form("")):
    try:
        tables_module.update_table(table_id, field, value)
    except tables_module.TableError as e:
        return _fail(e)
    return JSONResponse(_table_json(table_id))


@router.post("/api/tables/{table_id}/delete")
def api_delete_table(table_id: int):
    tables_module.delete_table(table_id)
    return JSONResponse({"ok": True})


@router.post("/api/tables/{table_id}/roll")
def api_roll_table(table_id: int):
    try:
        return JSONResponse(tables_module.roll_table(table_id))
    except tables_module.TableError as e:
        return _fail(e, 404 if "no longer exists" in str(e) else 400)
