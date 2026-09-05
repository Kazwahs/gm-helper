"""Initiative / combat tracker. State for one encounter is a JSON blob in
combat_sessions.state_json:
    {
      "round": 1,
      "turn_index": 0,
      "combatants": [
         {"id": 1, "name": "...", "initiative": 18, "hp": 30, "max_hp": 30,
          "is_pc": true, "notes": "", "conditions": []}
      ]
    }
One active session per game_key is kept for simplicity (a single-user local
GM tool doesn't need more than that at a time); "reset" just clears it.
"""
import json

from .. import database

_EMPTY_STATE = {"round": 1, "turn_index": 0, "combatants": []}


def _get_or_create_session(game_key):
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT * FROM combat_sessions WHERE game_key = ? ORDER BY id DESC LIMIT 1",
            (game_key,),
        ).fetchone()
    if row:
        return row["id"], json.loads(row["state_json"])

    state = dict(_EMPTY_STATE)
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO combat_sessions (game_key, name, state_json) VALUES (?, ?, ?)",
            (game_key, "Encounter", json.dumps(state)),
        )
        session_id = cur.lastrowid
    return session_id, state


def _save(session_id, state):
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE combat_sessions SET state_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(state), session_id),
        )


def get_state(game_key):
    _, state = _get_or_create_session(game_key)
    return _sorted_view(state)


def _sorted_view(state):
    combatants = sorted(state["combatants"], key=lambda c: c["initiative"], reverse=True)
    current_id = None
    if combatants and 0 <= state["turn_index"] < len(combatants):
        current_id = combatants[state["turn_index"]]["id"]
    return {
        "round": state["round"],
        "current_id": current_id,
        "combatants": combatants,
    }


def add_combatant(game_key, name, initiative, hp=None, is_pc=False, notes=""):
    session_id, state = _get_or_create_session(game_key)
    next_id = (max((c["id"] for c in state["combatants"]), default=0)) + 1
    state["combatants"].append(
        {
            "id": next_id,
            "name": name,
            "initiative": initiative,
            "hp": hp,
            "max_hp": hp,
            "is_pc": is_pc,
            "notes": notes,
            "conditions": [],
        }
    )
    _save(session_id, state)
    return _sorted_view(state)


def remove_combatant(game_key, combatant_id):
    session_id, state = _get_or_create_session(game_key)
    state["combatants"] = [c for c in state["combatants"] if c["id"] != combatant_id]
    if state["turn_index"] >= len(state["combatants"]):
        state["turn_index"] = 0
    _save(session_id, state)
    return _sorted_view(state)


def update_hp(game_key, combatant_id, delta):
    session_id, state = _get_or_create_session(game_key)
    for c in state["combatants"]:
        if c["id"] == combatant_id and c["hp"] is not None:
            c["hp"] = c["hp"] + delta
    _save(session_id, state)
    return _sorted_view(state)


def next_turn(game_key):
    session_id, state = _get_or_create_session(game_key)
    if state["combatants"]:
        state["turn_index"] += 1
        if state["turn_index"] >= len(state["combatants"]):
            state["turn_index"] = 0
            state["round"] += 1
    _save(session_id, state)
    return _sorted_view(state)


def reset(game_key):
    session_id, state = _get_or_create_session(game_key)
    state = dict(_EMPTY_STATE)
    _save(session_id, state)
    return _sorted_view(state)
