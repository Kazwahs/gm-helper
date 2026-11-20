"""Campaign planner pages and their JSON endpoints."""
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import bookmarks as bookmarks_module
from .. import campaign_ai, campaign_export
from .. import campaigns as campaigns_module
from .. import database, maps_store
from ..library import games as games_module
from ..web import base_ctx, templates

router = APIRouter()


@router.get("/campaigns")
def campaigns_page(request: Request, game: str = None):
    game_obj = games_module.get_game(game) if game else None
    all_games = games_module.all_game_choices()
    ctx = base_ctx(request)
    ctx.update({
        "game": game_obj,
        "game_key": game,
        "campaigns": campaigns_module.list_campaigns(game_key=game),
        "all_games": all_games,
        "game_names": {g["key"]: g["name"] for g in all_games},
    })
    return templates.TemplateResponse(request, "campaigns.html", ctx)


@router.post("/api/campaigns")
def api_create_campaign(game_key: str = Form(...), title: str = Form(...),
                         pitch: str = Form(""), tone: str = Form("")):
    if game_key != games_module.fallback_key() and games_module.get_game(game_key) is None:
        return JSONResponse({"error": f"Unknown game: {game_key}"}, status_code=400)
    try:
        campaign_id = campaigns_module.create_campaign(game_key, title, pitch, tone)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": campaign_id})


@router.get("/campaigns/{campaign_id}")
def campaign_detail_page(request: Request, campaign_id: int):
    detail = campaigns_module.get_campaign_detail(campaign_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Unknown campaign.")
    game_key = detail["campaign"]["game_key"]
    ctx = base_ctx(request)
    ctx.update(detail)
    ctx.update({
        "game": games_module.get_game(game_key),
        "game_key": game_key,
        "books": campaigns_module.books_for_game(game_key),
        "attached_maps": maps_store.list_maps(campaign_id=campaign_id),
        # maps a location can link to: this campaign's, plus unattached ones for the game
        "linkable_maps": [m for m in maps_store.list_maps(game_key=game_key)
                          if m["campaign_id"] in (None, campaign_id)],
    })
    return templates.TemplateResponse(request, "campaign_detail.html", ctx)


@router.get("/campaigns/{campaign_id}/export.md")
def campaign_export_markdown(campaign_id: int, player_safe: int = 0):
    """Download the campaign as Markdown (full GM copy, or a player-safe handout)."""
    campaign = campaigns_module.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Unknown campaign.")
    game_obj = games_module.get_game(campaign["game_key"])
    text = campaign_export.to_markdown(campaign_id, player_safe=bool(player_safe),
                                       game_name=game_obj["name"] if game_obj else None)
    name = campaign_export.slugify(campaign["title"]) + ("-handout" if player_safe else "") + ".md"
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/api/campaigns/{campaign_id}/recap")
def api_campaign_recap(campaign_id: int):
    try:
        return JSONResponse(campaign_ai.generate_recap(campaign_id))
    except campaign_ai.CampaignAIError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)


@router.post("/api/campaigns/{campaign_id}/prep-suggestions")
def api_campaign_prep_suggestions(campaign_id: int):
    try:
        return JSONResponse(campaign_ai.suggest_prep(campaign_id))
    except campaign_ai.CampaignAIError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)


@router.post("/api/campaigns/{campaign_id}/update")
def api_update_campaign(campaign_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_campaign_field(campaign_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/delete")
def api_delete_campaign(campaign_id: int):
    campaigns_module.delete_campaign(campaign_id)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/threads")
def api_add_thread(campaign_id: int, title: str = Form(...), description: str = Form(""),
                    stakes: str = Form(""), status: str = Form("seed")):
    try:
        thread_id = campaigns_module.add_thread(campaign_id, title, description, stakes, status)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": thread_id})


@router.post("/api/campaigns/threads/{thread_id}/update")
def api_update_thread(thread_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_thread_field(thread_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/threads/{thread_id}/delete")
def api_delete_thread(thread_id: int):
    campaigns_module.delete_thread(thread_id)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/npcs")
def api_add_campaign_npc(campaign_id: int, name: str = Form(...), role: str = Form(""),
                          disposition: str = Form("neutral"), motivation: str = Form(""),
                          secret: str = Form(""), notes: str = Form(""),
                          faction_id: str = Form(""), location_id: str = Form("")):
    try:
        npc_id = campaigns_module.add_npc(campaign_id, name, role, disposition, motivation, secret, notes,
                                          faction_id, location_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": npc_id})


@router.post("/api/campaigns/npcs/{npc_id}/update")
def api_update_campaign_npc(npc_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_npc_field(npc_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/npcs/{npc_id}/delete")
def api_delete_campaign_npc(npc_id: int):
    campaigns_module.delete_npc(npc_id)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/locations")
def api_add_location(campaign_id: int, name: str = Form(...), description: str = Form(""),
                      parent_id: str = Form(""), map_id: str = Form("")):
    try:
        location_id = campaigns_module.add_location(campaign_id, name, description, parent_id, map_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": location_id})


@router.post("/api/campaigns/locations/{location_id}/update")
def api_update_location(location_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_location_field(location_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/locations/{location_id}/delete")
def api_delete_location(location_id: int):
    campaigns_module.delete_location(location_id)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/factions")
def api_add_faction(campaign_id: int, name: str = Form(...), goal: str = Form(""), notes: str = Form("")):
    try:
        faction_id = campaigns_module.add_faction(campaign_id, name, goal, notes)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"id": faction_id})


@router.post("/api/campaigns/factions/{faction_id}/update")
def api_update_faction(faction_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_faction_field(faction_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/factions/{faction_id}/delete")
def api_delete_faction(faction_id: int):
    campaigns_module.delete_faction(faction_id)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/sessions")
def api_add_session(campaign_id: int, title: str = Form(""), date: str = Form("")):
    session_id = campaigns_module.add_session(campaign_id, title, date)
    return JSONResponse({"id": session_id})


@router.post("/api/campaigns/sessions/{session_id}/update")
def api_update_session(session_id: int, field: str = Form(...), value: str = Form("")):
    try:
        campaigns_module.update_session_field(session_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/sessions/{session_id}/delete")
def api_delete_session(session_id: int):
    campaigns_module.delete_session(session_id)
    return JSONResponse({"ok": True})


@router.post("/api/campaigns/{campaign_id}/materials")
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


@router.post("/api/campaigns/materials/{bookmark_id}/delete")
def api_delete_material(bookmark_id: int):
    bookmarks_module.delete_bookmark(bookmark_id)
    return JSONResponse({"ok": True})
