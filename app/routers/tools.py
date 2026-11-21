"""Dice, NPC generator, initiative tracker, encounters/loot, one-shot generator."""
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import campaigns as campaigns_module
from .. import llm as llm_module
from ..tools import dice as dice_tool
from ..tools import npc_generator
from ..tools import initiative as initiative_tool
from .. import encounter_guide, saved_encounters
from ..tools import encounters as encounters_tool
from ..web import base_ctx, game_ctx, templates

router = APIRouter()


@router.get("/tools/dice")
def dice_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    ctx["game"] = game_ctx(game)
    return templates.TemplateResponse(request, "tools_dice.html", ctx)


@router.post("/api/dice/roll")
def api_dice_roll(expr: str = Form(...)):
    try:
        return JSONResponse(dice_tool.roll(expr))
    except dice_tool.DiceError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/tools/oneshot")
def oneshot_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    ctx["game"] = game_ctx(game)
    ctx["game_key"] = game or "_global"
    return templates.TemplateResponse(request, "tools_oneshot.html", ctx)


@router.post("/api/oneshot/generate")
def api_oneshot_generate(game: str = Form("_global"), party_size: int = Form(4),
                          party_level: str = Form(""), tone: str = Form("")):
    if not llm_module.is_enabled():
        return JSONResponse(
            {"error": "AI features aren't enabled yet - set up and test a connection in Settings."},
            status_code=400,
        )
    game_obj = game_ctx(game if game != "_global" else None)
    game_name = game_obj["name"] if game_obj else "a tabletop RPG"
    system_prompt = (
        "You are an experienced tabletop RPG Game Master writing a complete, ready-to-run "
        "one-shot adventure seed. Reply in exactly this plain-text layout - no markdown, no "
        "asterisks, no headers other than the labels shown:\n\n"
        "TITLE: <a punchy adventure title>\n\n"
        "HOOK: <one paragraph on how the party gets pulled in>\n\n"
        "KEY NPCS:\n- <name> - <one line: role and what they want>\n"
        "(2 to 4 NPCs, one per line)\n\n"
        "SCENES:\n1. <scene>\n2. <scene>\n3. <scene>\n"
        "(3 to 5 scenes/beats in order)\n\n"
        "CLIMAX: <one paragraph describing the final confrontation or challenge>\n\n"
        "TWIST: <one line - an optional complication or reveal>\n\n"
        "Keep it genuinely runnable in a single 3-4 hour session, grounded in the conventions of "
        "the specified game system. Describe narrative content only - do not invent rules "
        "mechanics, stat blocks, or dice math."
    )
    party_desc = f"{party_size} players"
    if party_level.strip():
        party_desc += f", {party_level.strip()}"
    user_prompt = f"Game: {game_name}\nParty: {party_desc}"
    if tone.strip():
        user_prompt += f"\nDesired tone/theme: {tone.strip()}"
    try:
        oneshot = llm_module.generate(system_prompt, user_prompt, max_tokens=2000)
    except llm_module.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if not oneshot.strip():
        return JSONResponse({"error": "The model didn't return any text. Try again."}, status_code=502)
    return JSONResponse({"oneshot": oneshot})


@router.get("/tools/npc")
def npc_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    ctx["game"] = game_ctx(game)
    ctx["campaigns"] = campaigns_module.list_campaigns(game_key=game) if game else []
    return templates.TemplateResponse(request, "tools_npc.html", ctx)


@router.post("/api/npc/generate")
def api_npc_generate(game: str = Form(None)):
    game_obj = game_ctx(game)
    system = game_obj["system"] if game_obj else "generic"
    name = game_obj["name"] if game_obj else None
    key = game_obj["key"] if game_obj else None
    return JSONResponse(npc_generator.generate(game_key=key, system=system, game_name=name))


@router.post("/api/npc/dialogue")
def api_npc_dialogue(name: str = Form(...), role: str = Form(""), quirk: str = Form(""),
                      motivation: str = Form(""), alignment: str = Form(""), game: str = Form(None)):
    if not llm_module.is_enabled():
        return JSONResponse(
            {"error": "AI features aren't enabled yet - set up and test a connection in Settings."},
            status_code=400,
        )
    game_obj = game_ctx(game if game != "_global" else None)
    game_name = game_obj["name"] if game_obj else "a tabletop RPG"
    system_prompt = (
        "You are helping a Game Master voice a tabletop RPG NPC at the table. Write exactly 4 "
        "short, distinct lines of in-character dialogue for the NPC described below - just what "
        "they would actually say out loud. One line per line, no numbering, no quotation marks, "
        "no stage directions or narration, no preamble - only the spoken lines themselves."
    )
    user_prompt = (
        f"Game: {game_name}\nName: {name}\nRole: {role}\nAlignment: {alignment}\n"
        f"Quirk: {quirk}\nMotivation: {motivation}"
    )
    try:
        reply = llm_module.generate(system_prompt, user_prompt, max_tokens=500)
    except llm_module.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    lines = npc_generator.parse_dialogue_lines(reply)
    if not lines:
        return JSONResponse({"error": "The model didn't return any usable dialogue. Try again."}, status_code=502)
    return JSONResponse({"lines": lines})


@router.get("/tools/initiative")
def initiative_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    game_obj = game_ctx(game)
    ctx["game"] = game_obj
    ctx["game_key"] = game or "_global"
    ctx["condition_suggestions"] = initiative_tool.suggested_conditions(
        game_obj["system"] if game_obj else "generic"
    )
    return templates.TemplateResponse(request, "tools_initiative.html", ctx)


def _initiative_call(fn, *args):
    try:
        return JSONResponse(fn(*args))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/initiative")
def api_initiative_get(game: str = "_global"):
    return JSONResponse(initiative_tool.get_state(game))


@router.post("/api/initiative/add")
def api_initiative_add(game: str = Form("_global"), name: str = Form(...),
                        initiative: str = Form(...), hp: int = Form(None),
                        is_pc: bool = Form(False), notes: str = Form(""),
                        quantity: int = Form(1)):
    return _initiative_call(initiative_tool.add_combatant, game, name, initiative, hp, is_pc, notes, quantity)


@router.post("/api/initiative/set")
def api_initiative_set(game: str = Form("_global"), combatant_id: int = Form(...),
                        initiative: str = Form(...)):
    return _initiative_call(initiative_tool.set_initiative, game, combatant_id, initiative)


@router.post("/api/initiative/remove")
def api_initiative_remove(game: str = Form("_global"), combatant_id: int = Form(...)):
    return _initiative_call(initiative_tool.remove_combatant, game, combatant_id)


@router.post("/api/initiative/hp")
def api_initiative_hp(game: str = Form("_global"), combatant_id: int = Form(...), delta: int = Form(...)):
    return _initiative_call(initiative_tool.update_hp, game, combatant_id, delta)


@router.post("/api/initiative/condition")
def api_initiative_condition(game: str = Form("_global"), combatant_id: int = Form(...),
                              name: str = Form(...), rounds: str = Form("")):
    return _initiative_call(initiative_tool.add_condition, game, combatant_id, name, rounds)


@router.post("/api/initiative/condition/remove")
def api_initiative_condition_remove(game: str = Form("_global"), combatant_id: int = Form(...),
                                     name: str = Form(...)):
    return _initiative_call(initiative_tool.remove_condition, game, combatant_id, name)


@router.post("/api/initiative/next")
def api_initiative_next(game: str = Form("_global")):
    return _initiative_call(initiative_tool.next_turn, game)


@router.post("/api/initiative/clear-foes")
def api_initiative_clear_foes(game: str = Form("_global")):
    return _initiative_call(initiative_tool.clear_foes, game)


@router.post("/api/initiative/reset")
def api_initiative_reset(game: str = Form("_global")):
    return _initiative_call(initiative_tool.reset, game)


@router.get("/tools/encounters")
def encounters_page(request: Request, game: str = None):
    ctx = base_ctx(request)
    ctx["game"] = game_ctx(game)
    ctx["environments"] = encounters_tool.environments()
    ctx["guide_game"] = game or "_global"
    return templates.TemplateResponse(request, "tools_encounters.html", ctx)


@router.post("/api/encounters/generate")
def api_encounters_generate(environment: str = Form(...)):
    try:
        return JSONResponse(encounters_tool.generate_encounter(environment))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/loot/generate")
def api_loot_generate(tier: str = Form("mid"), include_item: bool = Form(True)):
    try:
        return JSONResponse(encounters_tool.generate_loot(tier, include_item))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ------------------------------------------------- encounter builder guide
def _guide_error(e):
    return JSONResponse({"error": str(e)}, status_code=400)


def _party_from_form(game, raw):
    """The party typed on the page (a JSON list), else the one saved for this game."""
    if raw is None or not raw.strip():
        return encounter_guide.get_party(game)
    try:
        return json.loads(raw)
    except ValueError:
        raise encounter_guide.GuideError("The party list couldn't be read.")


@router.get("/api/guide")
def api_guide(game: str = "_global"):
    """Everything the builder needs on load: its choices, this game's profile and saved party."""
    try:
        return JSONResponse({
            "options": encounter_guide.options(),
            "profile": encounter_guide.profile_for(game),
            "party": encounter_guide.get_party(game),
            "encounters": [{"id": e["id"], "name": e["name"], "body": e["body"]} for e in saved_encounters.list_encounters(game)],
        })
    except encounter_guide.GuideError as e:
        return _guide_error(e)


@router.post("/api/guide/party")
def api_guide_party_save(game: str = Form("_global"), party: str = Form("[]")):
    try:
        return JSONResponse({"party": encounter_guide.set_party(game, _party_from_form(game, party or "[]"))})
    except encounter_guide.GuideError as e:
        return _guide_error(e)


@router.get("/api/guide/party/board")
def api_guide_party_board(game: str = "_global"):
    """The PCs on the initiative board with hit points, as party members (not saved)."""
    try:
        return JSONResponse({"party": encounter_guide.party_from_board(game)})
    except encounter_guide.GuideError as e:
        return _guide_error(e)


@router.post("/api/guide/assess")
def api_guide_assess(game: str = Form("_global"), body: str = Form(""), party: str = Form(""),
                     foe_hits: str = Form("")):
    try:
        result = encounter_guide.assess_text(game, body, _party_from_form(game, party), foe_hits or None)
    except encounter_guide.GuideError as e:
        return _guide_error(e)
    return JSONResponse(result)


# ------------------------------------------------------- saved encounters
@router.get("/api/initiative/encounters")
def api_saved_encounters(game: str = "_global"):
    return JSONResponse({"encounters": saved_encounters.list_encounters(game)})


@router.post("/api/initiative/encounters")
def api_saved_encounter_create(game: str = Form("_global"), name: str = Form(""), body: str = Form(""),
                                from_board: int = Form(0)):
    try:
        if from_board:
            enc_id = saved_encounters.save_board_foes(game, name)
        else:
            enc_id = saved_encounters.create_encounter(game, name, body)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"encounter": saved_encounters.get_encounter(enc_id)})


@router.post("/api/initiative/encounters/{encounter_id}/update")
def api_saved_encounter_update(encounter_id: int, field: str = Form(...), value: str = Form("")):
    try:
        saved_encounters.update_encounter(encounter_id, field, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"encounter": saved_encounters.get_encounter(encounter_id)})


@router.post("/api/initiative/encounters/{encounter_id}/delete")
def api_saved_encounter_delete(encounter_id: int):
    saved_encounters.delete_encounter(encounter_id)
    return JSONResponse({"ok": True})


@router.post("/api/initiative/encounters/{encounter_id}/load")
def api_saved_encounter_load(encounter_id: int, game: str = Form("_global")):
    try:
        return JSONResponse(saved_encounters.load_into_tracker(encounter_id, game))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/initiative/encounters/{encounter_id}/add-foe")
def api_saved_encounter_add_foe(encounter_id: int, name: str = Form(""), quantity: str = Form("1"),
                                 hp: str = Form(""), initiative: str = Form("")):
    try:
        saved_encounters.append_foe(encounter_id, name, quantity, hp, initiative)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"encounter": saved_encounters.get_encounter(encounter_id)})
