"""Secrets & clues, progress clocks and ties between NPCs and factions."""
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse

from .. import campaign_world, campaigns

router = APIRouter()


def _fail(error, status=400):
    return JSONResponse({"error": str(error)}, status_code=status)


def _campaign_or_404(campaign_id):
    if not campaigns.get_campaign(campaign_id):
        raise HTTPException(status_code=404, detail="Unknown campaign.")


def _wrap(fn):
    """Runs `fn`, turning a ValueError into a 400 with the message."""
    try:
        return JSONResponse(fn())
    except ValueError as e:
        return _fail(e)


@router.get("/api/campaigns/{campaign_id}/world")
def api_world(campaign_id: int):
    _campaign_or_404(campaign_id)
    return JSONResponse(campaign_world.payload(campaign_id))


# ---------------------------------------------------------------- secrets ---

@router.post("/api/campaigns/{campaign_id}/secrets")
def api_add_secret(campaign_id: int, text: str = Form(""), source: str = Form(""), session_id: str = Form("")):
    _campaign_or_404(campaign_id)
    return _wrap(lambda: {"id": campaign_world.add_secret(campaign_id, text, source, session_id or None)})


@router.post("/api/campaigns/{campaign_id}/secrets/import")
def api_import_secrets(campaign_id: int):
    _campaign_or_404(campaign_id)
    return _wrap(lambda: campaign_world.import_from_sessions(campaign_id))


@router.post("/api/campaigns/secrets/{secret_id}/update")
def api_update_secret(secret_id: int, field: str = Form(...), value: str = Form("")):
    return _wrap(lambda: (campaign_world.update_secret(secret_id, field, value), {"ok": True})[1])


@router.post("/api/campaigns/secrets/{secret_id}/reveal")
def api_reveal_secret(secret_id: int, session_id: str = Form("")):
    return _wrap(lambda: campaign_world.reveal(secret_id, session_id or None))


@router.post("/api/campaigns/secrets/{secret_id}/hide")
def api_hide_secret(secret_id: int):
    return _wrap(lambda: campaign_world.hide_again(secret_id))


@router.post("/api/campaigns/secrets/{secret_id}/delete")
def api_delete_secret(secret_id: int):
    campaign_world.delete_secret(secret_id)
    return JSONResponse({"ok": True})


# ----------------------------------------------------------------- clocks ---

@router.post("/api/campaigns/{campaign_id}/clocks")
def api_add_clock(campaign_id: int, name: str = Form(""), segments: str = Form("6"),
                  faction_id: str = Form(""), note: str = Form("")):
    _campaign_or_404(campaign_id)
    return _wrap(lambda: {"id": campaign_world.add_clock(campaign_id, name, segments, faction_id, note)})


@router.post("/api/campaigns/clocks/{clock_id}/advance")
def api_advance_clock(clock_id: int, delta: str = Form("1")):
    return _wrap(lambda: campaign_world.advance_clock(clock_id, delta))


@router.post("/api/campaigns/clocks/{clock_id}/update")
def api_update_clock(clock_id: int, field: str = Form(...), value: str = Form("")):
    return _wrap(lambda: (campaign_world.update_clock(clock_id, field, value), campaign_world.get_clock(clock_id))[1])


@router.post("/api/campaigns/clocks/{clock_id}/delete")
def api_delete_clock(clock_id: int):
    campaign_world.delete_clock(clock_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------- relationships ---

@router.post("/api/campaigns/{campaign_id}/relationships")
def api_add_relationship(campaign_id: int, a: str = Form(""), b: str = Form(""),
                         kind: str = Form("ally"), note: str = Form("")):
    _campaign_or_404(campaign_id)
    return _wrap(lambda: {"id": campaign_world.add_relationship(campaign_id, a, b, kind, note)})


@router.post("/api/campaigns/relationships/{relationship_id}/delete")
def api_delete_relationship(relationship_id: int):
    campaign_world.delete_relationship(relationship_id)
    return JSONResponse({"ok": True})
