"""Live table state for a map: the player view's poll, tokens, and handouts."""
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from .. import handouts, live, map_tokens

router = APIRouter()


def _fail(message, status=400):
    return JSONResponse({"error": message}, status_code=status)


def _call(fn, *args, **kwargs):
    """Runs fn and turns a ValueError into a 400 (404 for 'Unknown map.')."""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        message = str(e)
        return _fail(message, 404 if message == "Unknown map." else 400)


@router.get("/api/maps/{map_id}/live")
def api_live(map_id: int):
    state = live.live_state(map_id)
    if state is None:
        return _fail("Unknown map.", 404)
    return JSONResponse(state, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ tokens
@router.get("/api/maps/{map_id}/tokens")
def api_list_tokens(map_id: int):
    """The GM's list - hidden tokens included."""
    return JSONResponse({"tokens": map_tokens.list_tokens(map_id)})


@router.post("/api/maps/{map_id}/tokens")
def api_add_token(map_id: int, label: str = Form(""), x: str = Form(...), y: str = Form(...),
                  color: str = Form(""), size: str = Form("1"), hidden: str = Form("0")):
    result = _call(lambda: map_tokens.add_token(map_id, label, x, y, color, size, hidden))
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse({"token": map_tokens.get_token(result)})


@router.post("/api/maps/{map_id}/tokens/clear")
def api_clear_tokens(map_id: int):
    result = _call(map_tokens.clear_tokens, map_id)
    return result if isinstance(result, JSONResponse) else JSONResponse({"ok": True})


@router.post("/api/map-tokens/{token_id}/move")
def api_move_token(token_id: int, x: str = Form(...), y: str = Form(...)):
    result = _call(map_tokens.move_token, token_id, x, y)
    return result if isinstance(result, JSONResponse) else JSONResponse({"token": map_tokens.get_token(token_id)})


@router.post("/api/map-tokens/{token_id}/update")
def api_update_token(token_id: int, field: str = Form(...), value: str = Form("")):
    result = _call(map_tokens.update_token, token_id, field, value)
    return result if isinstance(result, JSONResponse) else JSONResponse({"token": map_tokens.get_token(token_id)})


@router.post("/api/map-tokens/{token_id}/delete")
def api_delete_token(token_id: int):
    map_tokens.delete_token(token_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- handouts
@router.get("/api/handouts")
def api_list_handouts(game: str):
    return JSONResponse({"handouts": handouts.list_handouts(game)})


@router.post("/api/handouts")
def api_create_text_handout(game: str = Form(...), title: str = Form(""), body: str = Form("")):
    result = _call(handouts.create_text, game, title, body)
    return result if isinstance(result, JSONResponse) else JSONResponse({"handout": handouts.get_handout(result)})


@router.post("/api/handouts/image")
async def api_create_image_handout(game: str = Form(...), title: str = Form(""), file: UploadFile = File(...)):
    data = await file.read(handouts.MAX_IMAGE_BYTES + 1)   # never buffer more than the limit allows
    name = title or (file.filename or "").rsplit(".", 1)[0]
    result = _call(handouts.create_image, game, name, data)
    return result if isinstance(result, JSONResponse) else JSONResponse({"handout": handouts.get_handout(result)})


@router.post("/api/handouts/{handout_id}/update")
def api_update_handout(handout_id: int, field: str = Form(...), value: str = Form("")):
    result = _call(handouts.update_handout, handout_id, field, value)
    return result if isinstance(result, JSONResponse) else JSONResponse({"handout": handouts.get_handout(handout_id)})


@router.post("/api/handouts/{handout_id}/delete")
def api_delete_handout(handout_id: int):
    handouts.delete_handout(handout_id)
    return JSONResponse({"ok": True})


@router.get("/handouts/{handout_id}/image")
def handout_image(handout_id: int):
    found = handouts.get_image(handout_id)
    if not found:
        return _fail("Unknown handout.", 404)
    data, mime = found
    return Response(data, media_type=mime, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
        "Cache-Control": "private, max-age=300",
    })


@router.post("/api/maps/{map_id}/handout/show")
def api_show_handout(map_id: int, handout_id: int = Form(...)):
    result = _call(handouts.show_on_map, map_id, handout_id)
    return result if isinstance(result, JSONResponse) else JSONResponse({"handout": handouts.shown_on_map(map_id)})


@router.post("/api/maps/{map_id}/handout/hide")
def api_hide_handout(map_id: int):
    result = _call(handouts.hide_on_map, map_id)
    return result if isinstance(result, JSONResponse) else JSONResponse({"handout": None})
