"""Map generator, saved maps, and the in-browser map editor's endpoints."""
import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import access, accounts
from .. import campaigns as campaigns_module
from .. import handouts as handouts_module
from .. import live as live_module
from .. import map_links, map_tokens, maps_store, random_tables, room_keys, token_combat, vtt_export
from ..library import games as games_module
from ..maps import MAP_TYPES, generate_map
from ..maps import generator as map_generator
from ..maps.generator import GENRES as MAP_GENRES, SIZES as MAP_SIZES, STYLES as MAP_STYLES
from ..web import base_ctx, templates

router = APIRouter()


@router.get("/maps")
def maps_page(request: Request, game: str = None, campaign_id: int = None):
    game_obj = games_module.get_game(game) if game else None
    ctx = base_ctx(request)
    ctx.update({
        "game": game_obj,
        "game_key": game,
        "maps": maps_store.list_maps(game_key=game),
        "map_types": MAP_TYPES,
        "genres": MAP_GENRES,
        "sizes": MAP_SIZES,
        "styles": MAP_STYLES,
        "all_games": games_module.all_game_choices(),
        "campaigns": campaigns_module.list_campaigns(game_key=game) if game else [],
        "preset_campaign_id": campaign_id,
    })
    return templates.TemplateResponse(request, "maps.html", ctx)


@router.post("/api/maps/generate")
def api_generate_map(map_type: str = Form(...), genre: str = Form(...), size: str = Form("medium"),
                     style: str = Form("ink"), hex_grid: str = Form("")):
    try:
        result = generate_map(map_type, genre, params={"size": size, "style": style, "hex_grid": hex_grid})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)


@router.post("/api/maps")
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


def _viewer_id(request):
    """The signed-in player looking at a player view (None for the GM or when accounts are off)."""
    user = access.current_user(request)
    return user["id"] if user and user["role"] == "player" else None


def _standalone_ctx(request, m):
    """Context for the nav-less pages (player view, print) - just theme + map."""
    ctx = base_ctx(request)
    ctx.update({"map": m, "game_key": m["game_key"]})
    return ctx


@router.get("/maps/{map_id}/player")
def map_player_page(request: Request, map_id: int):
    """A clean, nav-less view of the map for a second screen / TV. When the GM
    has fog switched on, only the areas they've revealed are visible (the page
    polls /api/maps/{id}/reveal for changes)."""
    m = maps_store.get_map(map_id)
    if not m:
        raise HTTPException(status_code=404, detail="Unknown map.")
    viewer = _viewer_id(request)
    if viewer:                          # a signed-in player never receives the GM's secret-door markers
        m = dict(m, svg=live_module.strip_gm_only(m["svg"]))
    ctx = _standalone_ctx(request, m)
    ctx["reveal"] = maps_store.get_reveal(map_id)
    ctx["live"] = live_module.live_state(map_id, viewer_id=viewer)
    ctx["can_move"] = bool(viewer)
    return templates.TemplateResponse(request, "map_player.html", ctx)


@router.get("/maps/{map_id}/print")
def map_print_page(request: Request, map_id: int):
    """Print-to-scale: tiles the map across pages so a grid square comes out
    at a chosen size (1 inch for standard miniatures)."""
    m = maps_store.get_map(map_id)
    if not m:
        raise HTTPException(status_code=404, detail="Unknown map.")
    ctx = _standalone_ctx(request, m)
    ctx["grid_px"] = map_generator.grid_px(m["map_type"])
    ctx["has_grid"] = m["map_type"] in map_generator.GRID_PX
    return templates.TemplateResponse(request, "map_print.html", ctx)


@router.get("/api/maps/{map_id}/reveal")
def api_get_reveal(map_id: int):
    state = maps_store.get_reveal(map_id)
    if state is None:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    return JSONResponse(state)


def _reveal_change(fn, *args, **kwargs):
    try:
        return JSONResponse(fn(*args, **kwargs))
    except ValueError as e:
        message = str(e)
        return JSONResponse({"error": message}, status_code=404 if message == "Unknown map." else 400)


@router.post("/api/maps/{map_id}/reveal/enabled")
def api_reveal_enabled(map_id: int, enabled: int = Form(...)):
    return _reveal_change(maps_store.set_fog_enabled, map_id, bool(enabled))


@router.post("/api/maps/{map_id}/reveal/add")
def api_reveal_add(map_id: int, shape: str = Form("circle"), x: str = Form(...), y: str = Form(...),
                   r: str = Form(""), w: str = Form(""), h: str = Form("")):
    return _reveal_change(maps_store.add_reveal, map_id, shape, x=x, y=y, r=r, w=w, h=h)


@router.post("/api/maps/{map_id}/reveal/undo")
def api_reveal_undo(map_id: int):
    return _reveal_change(maps_store.undo_reveal, map_id)


@router.post("/api/maps/{map_id}/reveal/clear")
def api_reveal_clear(map_id: int):
    return _reveal_change(maps_store.clear_reveal, map_id)


@router.get("/maps/{map_id}")
def map_detail_page(request: Request, map_id: int):
    m = maps_store.get_map(map_id)
    if not m:
        raise HTTPException(status_code=404, detail="Unknown map.")
    ctx = base_ctx(request)
    ctx.update({
        "game": games_module.get_game(m["game_key"]),
        "game_key": m["game_key"],
        "map": m,
        "campaigns": campaigns_module.list_campaigns(game_key=m["game_key"]),
        "linked_locations": campaigns_module.locations_for_map(map_id),
        "reveal": maps_store.get_reveal(map_id),
        "tokens": token_combat.decorate(map_id, map_tokens.list_tokens(map_id), m["game_key"]),
        "token_radius": live_module.token_radius(m),
        "handouts": handouts_module.list_handouts(m["game_key"]),
        "shown_handout": handouts_module.shown_on_map(map_id),
        "key_supported": m["map_type"] in room_keys.KEY_MAP_TYPES,
        "vtt_supported": vtt_export.supported(m["map_type"]),
        "key_tables": random_tables.list_tables(m["game_key"]),
        # players of the attached campaign, so a token can be handed to one of them
        "token_players": accounts.members(m["campaign_id"]) if m.get("campaign_id") else [],
    })
    return templates.TemplateResponse(request, "map_detail.html", ctx)


@router.post("/api/maps/{map_id}/rename")
def api_rename_map(map_id: int, title: str = Form(...)):
    try:
        maps_store.rename_map(map_id, title)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/api/maps/{map_id}/attach")
def api_attach_map(map_id: int, campaign_id: int = Form(None)):
    m = maps_store.get_map(map_id)
    if not m:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    if campaign_id:
        campaign = campaigns_module.get_campaign(campaign_id)
        if not campaign or campaign["game_key"] != m["game_key"]:
            return JSONResponse({"error": "That campaign doesn't belong to this game."}, status_code=400)
    maps_store.set_campaign(map_id, campaign_id)
    campaigns_module.unlink_map_from_other_campaigns(map_id, campaign_id)
    return JSONResponse({"ok": True})


@router.post("/api/maps/{map_id}/svg")
def api_update_map_svg(map_id: int, svg: str = Form(...)):
    m = maps_store.get_map(map_id)
    if not m:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    if len(svg.encode("utf-8")) > 3_000_000:
        return JSONResponse({"error": "Edited map is too large to save."}, status_code=400)
    maps_store.update_svg(map_id, svg)
    return JSONResponse({"ok": True})


@router.post("/api/maps/{map_id}/revert")
def api_revert_map_svg(map_id: int):
    m = maps_store.get_map(map_id)
    if not m:
        return JSONResponse({"error": "Unknown map."}, status_code=404)
    maps_store.revert_svg(map_id)
    return JSONResponse({"ok": True})


@router.post("/api/maps/{map_id}/delete")
def api_delete_map(map_id: int):
    maps_store.delete_map(map_id)
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ room key

def _key_map(map_id):
    m = maps_store.get_map(map_id)
    if not m:
        raise HTTPException(status_code=404, detail="Unknown map.")
    return m


def _key_payload(m):
    return {"entries": room_keys.list_entries(m["id"]), "kinds": room_keys.KIND_LABELS,
            "supported": m["map_type"] in room_keys.KEY_MAP_TYPES, "ai": room_keys.ai_available()}


def _key_error(e):
    return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status)


@router.get("/api/maps/{map_id}/key")
def api_get_key(map_id: int):
    return JSONResponse({"ok": True, **_key_payload(_key_map(map_id))})


@router.post("/api/maps/{map_id}/key/generate")
def api_generate_key(map_id: int, creature_table: str = Form(""), seed: str = Form("")):
    m = _key_map(map_id)
    roller = None
    if creature_table.strip().isdigit():
        table = random_tables.get_table(int(creature_table))
        if not table or table["game_key"] not in (m["game_key"], random_tables.GLOBAL_KEY):
            return JSONResponse({"ok": False, "error": "That random table isn't available for this game."}, status_code=400)
        table_id = table["id"]
        roller = lambda: random_tables.roll_table(table_id, record=False)["text"]
    try:
        room_keys.generate_for_map(m, seed=int(seed) if seed.strip().lstrip("-").isdigit() else None, creature_roller=roller)
    except room_keys.RoomKeyError as e:
        return _key_error(e)
    return JSONResponse({"ok": True, **_key_payload(m)})


@router.post("/api/maps/{map_id}/key/clear")
def api_clear_key(map_id: int):
    m = _key_map(map_id)
    room_keys.delete_all(m["id"])
    return JSONResponse({"ok": True, **_key_payload(m)})


@router.post("/api/maps/{map_id}/key/{room}")
def api_update_key_entry(map_id: int, room: int, title: str = Form(None), kind: str = Form(None),
                         description: str = Form(None), contents: str = Form(None), treasure: str = Form(None),
                         secrets: str = Form(None), notes: str = Form(None)):
    m = _key_map(map_id)
    try:
        entry = room_keys.update_entry(m["id"], room, title=title, kind=kind, description=description,
                                       contents=contents, treasure=treasure, secrets=secrets, notes=notes)
    except room_keys.RoomKeyError as e:
        return _key_error(e)
    return JSONResponse({"ok": True, "entry": entry})


@router.post("/api/maps/{map_id}/key/{room}/ai")
def api_ai_key_entry(map_id: int, room: int):
    m = _key_map(map_id)
    try:
        entry = room_keys.flesh_out(m, room)
    except room_keys.RoomKeyError as e:
        return _key_error(e)
    return JSONResponse({"ok": True, "entry": entry})


@router.get("/maps/{map_id}/key")
def map_key_page(request: Request, map_id: int):
    m = _key_map(map_id)
    ctx = base_ctx(request)
    ctx.update({"map": m, "game": games_module.get_game(m["game_key"]),
                "entries": room_keys.list_entries(map_id), "kinds": room_keys.KIND_LABELS})
    return templates.TemplateResponse(request, "map_key.html", ctx)


# ----------------------------------------------------------------- VTT export

@router.get("/api/maps/{map_id}/vtt")
def api_vtt_export(map_id: int, ppg: int = 70):
    m = _key_map(map_id)
    try:
        info = vtt_export.export_info(m, ppg)
    except vtt_export.VttError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status)
    return JSONResponse({"ok": True, **info})


# ---------------------------------------------------------------- linked maps

def _links_error(e):
    return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status)


@router.get("/api/maps/{map_id}/links")
def api_get_links(map_id: int):
    m = _key_map(map_id)
    return JSONResponse({"ok": True, **map_links.payload(m)})


@router.post("/api/maps/{map_id}/links/create")
def api_create_linked_map(map_id: int, kind: str = Form("detail"), map_type: str = Form(...),
                          title: str = Form(""), settlement: str = Form("")):
    m = _key_map(map_id)
    tier = "village"
    name = settlement.strip()
    if name:
        known = {s["name"]: s["tier"] for s in map_links.settlements(m["svg"])}
        if name not in known:
            return JSONResponse({"ok": False, "error": "That place isn't on this map."}, status_code=400)
        tier = known[name]
    try:
        new_id = map_links.create_linked(m, kind, map_type, title=title, settlement=name or None, tier=tier)
    except map_links.LinkError as e:
        return _links_error(e)
    return JSONResponse({"ok": True, "id": new_id, "url": f"/maps/{new_id}", **map_links.payload(m)})


@router.post("/api/maps/{map_id}/links/add")
def api_add_link(map_id: int, other_id: int = Form(...), kind: str = Form("detail"), label: str = Form("")):
    m = _key_map(map_id)
    try:
        map_links.link(m["id"], other_id, kind, label)
    except map_links.LinkError as e:
        return _links_error(e)
    return JSONResponse({"ok": True, **map_links.payload(m)})


@router.post("/api/maps/{map_id}/links/{link_id}/delete")
def api_delete_link(map_id: int, link_id: int):
    m = _key_map(map_id)
    map_links.unlink(m["id"], link_id)
    return JSONResponse({"ok": True, **map_links.payload(m)})
