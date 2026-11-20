"""GM Screen: reference cards, session notes scratchpad, AI recap."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import gm_screen
from .. import llm as llm_module
from ..web import base_ctx, game_ctx, templates

router = APIRouter()


def _system_for(game):
    """The game-system hint ('palladium', 'd20', ...) for a game key, or None."""
    game_obj = game_ctx(game) if game and game != gm_screen.GLOBAL_KEY else None
    return game_obj.get("system") if game_obj else None


@router.get("/screen")
def gm_screen_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    ctx["game"] = game_ctx(game)
    ctx["game_key"] = game or "_global"
    ctx["cards"] = gm_screen.list_cards(game, _system_for(game))
    ctx["notes"] = gm_screen.get_notes(game)
    return templates.TemplateResponse(request, "gm_screen.html", ctx)


@router.post("/api/screen/cards")
def api_screen_create_card(game: str = Form("_global"), title: str = Form(...), body: str = Form("")):
    try:
        return JSONResponse(gm_screen.create_card(game, title, body))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/screen/add-missing")
def api_screen_add_missing(game: str = Form("_global")):
    """Adds any of this game's system starter cards that aren't on the screen
    yet (matched by title) - never duplicates or overwrites."""
    added = gm_screen.add_missing_system_cards(game, _system_for(game))
    return JSONResponse({"added": added})


@router.post("/api/screen/pin")
def api_screen_pin_page(
    game: str = Form("_global"),
    book_id: int = Form(...),
    page_number: int = Form(None),
    note: str = Form(""),
):
    try:
        return JSONResponse(gm_screen.pin_page(game, book_id, page_number, note))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/screen/notes/append")
def api_screen_append_notes(
    game: str = Form("_global"), text: str = Form(...), heading: str = Form("")
):
    """'Send to session notes' from the encounter, loot and random-table tools."""
    try:
        body = gm_screen.append_notes(game, text, heading or None)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "notes": body})


@router.post("/api/screen/cards/{card_id}")
def api_screen_update_card(card_id: int, field: str = Form(...), value: str = Form("")):
    try:
        return JSONResponse(gm_screen.update_card_field(card_id, field, value))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/screen/cards/{card_id}/move")
def api_screen_move_card(card_id: int, direction: str = Form(...)):
    try:
        gm_screen.move_card(card_id, direction)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/screen/cards/{card_id}/delete")
def api_screen_delete_card(card_id: int):
    gm_screen.delete_card(card_id)
    return JSONResponse({"ok": True})


@router.post("/api/screen/notes")
def api_screen_save_notes(game: str = Form("_global"), body: str = Form("")):
    gm_screen.set_notes(game, body)
    return JSONResponse({"ok": True})


@router.post("/api/screen/recap")
def api_screen_recap(game: str = Form("_global")):
    if not llm_module.is_enabled():
        return JSONResponse(
            {"error": "AI features aren't enabled yet - set up and test a connection in Settings."},
            status_code=400,
        )
    notes = gm_screen.get_notes(game)
    if not notes.strip():
        return JSONResponse(
            {"error": "There are no session notes saved for this game yet - jot down where you left off first."},
            status_code=400,
        )
    game_obj = game_ctx(game if game != "_global" else None)
    game_name = game_obj["name"] if game_obj else "the campaign"
    system_prompt = (
        "You are a tabletop RPG assistant helping a Game Master recap the previous session "
        "before play resumes. Write a short, evocative 'Previously on...' recap addressed to "
        "the players, based only on the GM's own session notes below. Do not invent plot "
        "details that aren't implied by the notes. Keep it to one tight paragraph, 4-6 sentences."
    )
    user_prompt = f"Game: {game_name}\n\nGM's session notes:\n{notes}"
    try:
        # Generous budget on purpose: "thinking" models (e.g. Qwen3.5) spend
        # part of this same token budget on an internal reasoning pass before
        # writing the actual recap, so a tight limit can get cut off before
        # any real text comes back at all.
        recap = llm_module.generate(system_prompt, user_prompt, max_tokens=1600)
    except llm_module.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if not recap.strip():
        return JSONResponse(
            {"error": "The model didn't return any text - if it's a 'thinking' model, it may have used up its "
                       "whole response budget reasoning internally. Try again, or try a shorter notes entry."},
            status_code=502,
        )
    return JSONResponse({"recap": recap})
