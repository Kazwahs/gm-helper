"""Dice, NPC generator, initiative tracker, encounters/loot, one-shot generator."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import campaigns as campaigns_module
from .. import llm as llm_module
from ..tools import dice as dice_tool
from ..tools import npc_generator
from ..tools import initiative as initiative_tool
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
