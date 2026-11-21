"""The player side: a landing page, one page per campaign they belong to (what the
party has learned, their own notes, the live maps) and, where the GM allows it, a
read-only view of a game's library. Access is decided in access.py before any of
these run; the code here only has to look up what to show."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import access, accounts, database, play
from ..library import games as games_module
from ..library import search as search_module
from ..web import base_ctx, templates

router = APIRouter()


def _fail(message, status=400):
    return JSONResponse({"error": message}, status_code=status)


def _player_ctx(request):
    ctx = base_ctx(request)
    user = access.current_user(request)
    ctx["me"] = user
    ctx["has_library"] = bool(user and user["role"] == "player" and accounts.library_games(user["id"]))
    return ctx


@router.get("/play")
def play_home(request: Request):
    user = access.current_user(request)
    if user is None or user["role"] == "gm":
        return RedirectResponse("/", status_code=303)           # the GM's home is the games page
    ctx = _player_ctx(request)
    ctx.update(play.home(user["id"]))
    return templates.TemplateResponse(request, "play.html", ctx)


@router.get("/play/campaigns/{campaign_id}")
def play_campaign(request: Request, campaign_id: int):
    user = access.current_user(request)
    recap = play.recap(campaign_id)
    if recap is None:
        return _fail("Unknown campaign.", 404)
    is_player = user is not None and user["role"] == "player"
    ctx = _player_ctx(request)
    ctx.update({
        "recap": recap,
        "is_player": is_player,
        "note": accounts.get_note(campaign_id, user["id"]) if is_player else None,
        "max_note": accounts.MAX_NOTE,
        # a GM looking at this page is previewing what their players see
        "layout": "base_play.html" if is_player else "base.html",
        "game_key": None if is_player else recap["game_key"],
    })
    return templates.TemplateResponse(request, "play_campaign.html", ctx)


@router.get("/api/play/campaigns/{campaign_id}/notes")
def api_get_note(request: Request, campaign_id: int):
    user = access.current_user(request)
    if user is None or user["role"] != "player":
        return _fail("Player notes belong to players - the GM reads them from the campaign page.")
    return JSONResponse(accounts.get_note(campaign_id, user["id"]))


@router.post("/api/play/campaigns/{campaign_id}/notes")
def api_save_note(request: Request, campaign_id: int, body: str = Form("")):
    user = access.current_user(request)
    if user is None or user["role"] != "player":
        return _fail("Player notes belong to players - the GM reads them from the campaign page.")
    try:
        accounts.save_note(campaign_id, user["id"], body)
    except ValueError as e:
        return _fail(str(e))
    return JSONResponse({"ok": True, **accounts.get_note(campaign_id, user["id"])})


# ---------------------------------------------------------------- library
def _allowed_game_keys(user):
    """Games whose library this person may use (every game for a GM)."""
    if user is None or user["role"] == "gm":
        return [g["key"] for g in games_module.all_game_choices()]
    return accounts.library_games(user["id"])


@router.get("/play/library")
def play_library(request: Request, game: str = None, q: str = None, page: int = 1):
    user = access.current_user(request)
    names = {g["key"]: g["name"] for g in games_module.all_game_choices()}
    allowed = _allowed_game_keys(user)
    choices = sorted(({"key": k, "name": names.get(k, k)} for k in allowed), key=lambda g: g["name"].lower())
    found, books = None, []
    if game and game in allowed:
        if q:
            found = search_module.search_page(q, game_key=game, page=page)
        with database.get_cursor() as cur:
            books = [dict(r) for r in cur.execute(
                "SELECT id, title, series, publisher FROM books WHERE game_key = ? ORDER BY series, title LIMIT 500",
                (game,)).fetchall()]
    is_player = user is not None and user["role"] == "player"
    ctx = _player_ctx(request)
    ctx.update({
        "choices": choices, "game_key": game if game in allowed else None, "game_name": names.get(game, game or ""),
        "query": q or "", "found": found,
        "groups": search_module.group_by_book(found["results"]) if found else [], "books": books,
        "layout": "base_play.html" if is_player else "base.html",
    })
    return templates.TemplateResponse(request, "play_library.html", ctx)


@router.get("/api/play/search")
def api_play_search(q: str, game: str, limit: int = 30, offset: int = 0):
    limit = max(1, min(limit, 100))
    return JSONResponse(search_module.search(q, game_key=game, limit=limit, offset=max(0, offset)))
