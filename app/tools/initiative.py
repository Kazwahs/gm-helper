"""Initiative / combat tracker. State for one encounter is a JSON blob in
combat_sessions.state_json:
    {
      "round": 1,
      "turn_id": 3,          # id of whose turn it is (None until "Next turn" is first pressed)
      "next_id": 5,          # monotonic id counter, so ids are never reused after a removal
      "combatants": [
         {"id": 1, "name": "...", "initiative": 18, "hp": 30, "max_hp": 30,
          "is_pc": true, "notes": "", "conditions": [{"name": "Stunned", "rounds": 2}]}
      ]
    }
One active session per game_key is kept for simplicity (a single-user local
GM tool doesn't need more than that at a time).

Whose turn it is is tracked by combatant *id*, never by position in the sorted
list. The earlier version stored a list index, which meant adding someone
mid-round (or removing someone earlier in the order) silently moved the turn
to a different person.

Ordering: highest initiative first; on a tie, PCs go before NPCs/monsters (a
common table convention), then whoever was added first.
"""
import json

from .. import database
from . import dice as dice_tool

MAX_QUANTITY = 30

# Quick-pick suggestions for the condition box, keyed by a game's `system`
# hint. Free text is always allowed - these are just a datalist.
_CONDITION_SUGGESTIONS = {
    "d20": [
        "Blinded", "Charmed", "Deafened", "Frightened", "Grappled", "Incapacitated",
        "Invisible", "Paralyzed", "Petrified", "Poisoned", "Prone", "Restrained",
        "Stunned", "Unconscious", "Concentrating", "Bleeding",
    ],
    "palladium": [
        "Stunned", "Bleeding", "Knocked down", "Entangled", "Blinded", "Paralyzed",
        "Unconscious", "In a coma", "Held", "Possessed", "Insane",
    ],
    "savage_worlds": [
        "Shaken", "Distracted", "Vulnerable", "Stunned", "Entangled", "Bound",
        "Incapacitated", "Bleeding Out", "Fatigued", "Exhausted",
    ],
    "forged_in_the_dark": [
        "Harm 1", "Harm 2", "Harm 3", "Stressed", "Trauma", "Desperate", "Wanted",
    ],
    "generic": [
        "Stunned", "Prone", "Blinded", "Poisoned", "Bleeding", "Frightened",
        "Held", "Unconscious",
    ],
}


def normalize_system(system):
    return (system or "generic").strip().lower().replace(" ", "_").replace("-", "_")


def suggested_conditions(system):
    return _CONDITION_SUGGESTIONS.get(normalize_system(system), _CONDITION_SUGGESTIONS["generic"])


def _empty_state():
    # A function (not a module-level dict) on purpose: a shared dict whose
    # "combatants" list is only shallow-copied leaks one game's combatants
    # into every new game's tracker.
    return {"round": 1, "turn_id": None, "next_id": 1, "combatants": []}


def _normalize(state):
    """Fills in anything an older saved state (which used `turn_index`) lacks."""
    state.setdefault("round", 1)
    state.setdefault("combatants", [])
    for c in state["combatants"]:
        c.setdefault("notes", "")
        c.setdefault("is_pc", False)
        c.setdefault("max_hp", c.get("hp"))
        # old saves stored conditions as a (never-populated) list of strings
        c["conditions"] = [
            {"name": x, "rounds": None} if isinstance(x, str) else x
            for x in c.get("conditions", [])
        ]
    state.setdefault("next_id", max((c["id"] for c in state["combatants"]), default=0) + 1)
    if "turn_id" not in state:
        legacy_index = state.pop("turn_index", None)
        ordered = _ordered(state)
        if legacy_index is not None and 0 <= legacy_index < len(ordered) and legacy_index > 0:
            state["turn_id"] = ordered[legacy_index]["id"]
        else:
            state["turn_id"] = None
    state.pop("turn_index", None)
    return state


def _load(game_key, create):
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT * FROM combat_sessions WHERE game_key = ? ORDER BY id DESC LIMIT 1",
            (game_key,),
        ).fetchone()
    if row:
        return row["id"], _normalize(json.loads(row["state_json"]))

    state = _empty_state()
    if not create:
        return None, state
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO combat_sessions (game_key, name, state_json) VALUES (?, ?, ?)",
            (game_key, "Encounter", json.dumps(state)),
        )
        return cur.lastrowid, state


def _save(session_id, state):
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE combat_sessions SET state_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(state), session_id),
        )


def _ordered(state):
    return sorted(
        state["combatants"],
        key=lambda c: (-c["initiative"], 0 if c.get("is_pc") else 1, c["id"]),
    )


def _current(state, ordered=None):
    """The combatant whose turn it is. Before 'Next turn' has ever been
    pressed this is simply whoever is currently first in the order."""
    ordered = ordered if ordered is not None else _ordered(state)
    if not ordered:
        return None
    for c in ordered:
        if c["id"] == state.get("turn_id"):
            return c
    return ordered[0]


def _view(state):
    ordered = _ordered(state)
    current = _current(state, ordered)
    combatants = []
    for c in ordered:
        item = dict(c)
        item["down"] = c["hp"] is not None and c["hp"] <= 0
        combatants.append(item)
    return {
        "round": state["round"],
        "current_id": current["id"] if current else None,
        "combatants": combatants,
    }


def get_state(game_key):
    # Read-only: merely opening the tracker must not write a row.
    _, state = _load(game_key, create=False)
    return _view(state)


def _roll_initiative(value):
    """Accepts a plain number or a dice expression like '1d20+3'."""
    text = str(value).strip()
    if not text:
        raise ValueError("Enter an initiative number or a dice roll like 1d20+3.")
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return dice_tool.roll(text)["grand_total"]
    except dice_tool.DiceError:
        raise ValueError(
            f"Couldn't read initiative {text!r} - use a number or dice like 1d20+3."
        )


def _find(state, combatant_id):
    for c in state["combatants"]:
        if c["id"] == combatant_id:
            return c
    return None


def add_combatant(game_key, name, initiative, hp=None, is_pc=False, notes="", quantity=1):
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the combatant a name.")
    quantity = 1 if quantity is None else int(quantity)
    if quantity < 1 or quantity > MAX_QUANTITY:
        raise ValueError(f"Quantity must be between 1 and {MAX_QUANTITY}.")

    session_id, state = _load(game_key, create=True)
    existing = {c["name"].lower() for c in state["combatants"]}
    for n in range(1, quantity + 1):
        # "Goblin" x3 -> "Goblin 1", "Goblin 2", "Goblin 3" (skipping any
        # numbered name that's already on the board).
        label = name if quantity == 1 else f"{name} {n}"
        if quantity > 1:
            suffix = n
            while label.lower() in existing:
                suffix += 1
                label = f"{name} {suffix}"
        existing.add(label.lower())
        state["combatants"].append(
            {
                "id": state["next_id"],
                "name": label,
                # each one rolls on its own when a dice expression is given
                "initiative": _roll_initiative(initiative),
                "hp": hp,
                "max_hp": hp,
                "is_pc": bool(is_pc),
                "notes": notes or "",
                "conditions": [],
            }
        )
        state["next_id"] += 1
    _save(session_id, state)
    return _view(state)


def set_initiative(game_key, combatant_id, value):
    session_id, state = _load(game_key, create=True)
    c = _find(state, combatant_id)
    if c is None:
        raise ValueError("That combatant isn't on the board any more.")
    c["initiative"] = _roll_initiative(value)
    _save(session_id, state)
    return _view(state)


def remove_combatant(game_key, combatant_id):
    session_id, state = _load(game_key, create=True)
    ordered = _ordered(state)
    current = _current(state, ordered)
    target = _find(state, combatant_id)
    if target is None:
        return _view(state)

    if current is not None and current["id"] == combatant_id:
        # Their turn just ended by leaving: pass it to the next in order,
        # starting a new round if they were last.
        idx = next(i for i, c in enumerate(ordered) if c["id"] == combatant_id)
        remaining = [c for c in ordered if c["id"] != combatant_id]
        if remaining:
            if idx < len(remaining):
                state["turn_id"] = remaining[idx]["id"]
            else:
                state["turn_id"] = remaining[0]["id"]
                state["round"] += 1
        else:
            state["turn_id"] = None
    state["combatants"] = [c for c in state["combatants"] if c["id"] != combatant_id]
    _save(session_id, state)
    return _view(state)


def update_hp(game_key, combatant_id, delta):
    """Damage is a negative delta, healing a positive one. Healing stops at
    max HP; damage may take HP below zero (some systems track that)."""
    delta = int(delta)
    session_id, state = _load(game_key, create=True)
    c = _find(state, combatant_id)
    if c is not None and c["hp"] is not None:
        new_hp = c["hp"] + delta
        if delta > 0 and c.get("max_hp") is not None:
            new_hp = min(new_hp, max(c["max_hp"], c["hp"]))
        c["hp"] = new_hp
        _save(session_id, state)
    return _view(state)


def add_condition(game_key, combatant_id, name, rounds=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Name the condition.")
    if rounds in ("", None):
        rounds = None
    else:
        rounds = int(rounds)
        if rounds < 1:
            raise ValueError("Duration must be at least 1 round (or leave it blank).")
    session_id, state = _load(game_key, create=True)
    c = _find(state, combatant_id)
    if c is None:
        raise ValueError("That combatant isn't on the board any more.")
    # Re-applying the same condition refreshes it rather than stacking copies.
    c["conditions"] = [x for x in c["conditions"] if x["name"].lower() != name.lower()]
    c["conditions"].append({"name": name, "rounds": rounds})
    _save(session_id, state)
    return _view(state)


def remove_condition(game_key, combatant_id, name):
    session_id, state = _load(game_key, create=True)
    c = _find(state, combatant_id)
    if c is not None:
        c["conditions"] = [x for x in c["conditions"] if x["name"].lower() != (name or "").lower()]
        _save(session_id, state)
    return _view(state)


def next_turn(game_key):
    session_id, state = _load(game_key, create=True)
    ordered = _ordered(state)
    if not ordered:
        return _view(state)

    current = _current(state, ordered)
    # Timed conditions tick down at the end of the affected creature's turn.
    kept = []
    for cond in current["conditions"]:
        if cond["rounds"] is None:
            kept.append(cond)
        elif cond["rounds"] > 1:
            kept.append({"name": cond["name"], "rounds": cond["rounds"] - 1})
    current["conditions"] = kept

    idx = next(i for i, c in enumerate(ordered) if c["id"] == current["id"])
    if idx + 1 >= len(ordered):
        state["turn_id"] = ordered[0]["id"]
        state["round"] += 1
    else:
        state["turn_id"] = ordered[idx + 1]["id"]
    _save(session_id, state)
    return _view(state)


def clear_foes(game_key):
    """End of a fight: drop every non-PC but keep the party on the board, and
    start fresh at round 1."""
    session_id, state = _load(game_key, create=True)
    state["combatants"] = [c for c in state["combatants"] if c.get("is_pc")]
    state["round"] = 1
    state["turn_id"] = None
    _save(session_id, state)
    return _view(state)


def reset(game_key):
    session_id, state = _load(game_key, create=True)
    fresh = _empty_state()
    fresh["next_id"] = state["next_id"]  # ids keep climbing; no reuse after a reset
    _save(session_id, fresh)
    return _view(fresh)
