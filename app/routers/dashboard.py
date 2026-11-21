"""Home page: the game picker."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import jump, recent
from ..library import games as games_module
from ..library import scanner
from ..web import base_ctx, library_root, templates

router = APIRouter()


@router.get("/")
def index(request: Request):
    root = library_root()
    counts = scanner.game_summary() if root else {}
    games = games_module.all_game_choices()
    for g in games:
        g["count"] = counts.get(g["key"], 0)
    ctx = base_ctx(request)
    status = scanner.library_status() if root else {"books": 0, "indexed": 0}
    if not root:
        setup_hint = "set_folder"
    elif status["books"] == 0:
        setup_hint = "scan"
    elif status["indexed"] == 0:
        setup_hint = "index"
    else:
        setup_hint = None
    ctx.update({"games": games, "needs_setup": not root, "setup_hint": setup_hint, "library_status": status,
                "activity": recent.home_activity()})
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/api/jump")
def api_jump(q: str = "", game: str = None):
    """Results for the Ctrl+K quick-jump box."""
    return JSONResponse({"results": jump.search(q, game_key=game)})
