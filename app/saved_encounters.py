"""Saved encounters: a prepped group of foes ("Goblin ambush: 4 goblins and a
boss") kept per game, ready to drop into the initiative tracker in one click.

An encounter is stored as a list of entries, each {name, quantity, hp,
initiative, notes} (plus `attacks` and `mdc` when given), and edited as plain
text, one group per line:

    4x Goblin (hp 7, init 1d20+2)
    Goblin Boss (hp 21, init 1d20+1, notes: carries the key)
    Wolf x2 (hp 11)
    Dragon Hatchling (hp 150, mdc, attacks 3)

`attacks` (per round) and `mdc` (the hp are mega-damage capacity) are only
used by the difficulty guide in encounter_guide.py; they are left off the entry
unless the line says them.

`init` is a number or a dice roll (a bare "+2" means 1d20+2); leave it out and
each one rolls 1d20. PCs are not part of a saved encounter - they stay on the
board between fights - so loading only ever adds foes.
"""
import json
import re

from . import database
from .tools import initiative as initiative_tool

DEFAULT_INITIATIVE = "1d20"
MAX_NAME = 80
MAX_NOTES = 200
MAX_ENTRIES = 40
MAX_BODY = 4000
MAX_ATTACKS = 20

_QTY_LEAD = re.compile(r"^(\d+)\s*[x×]\s*(.+)$", re.IGNORECASE)
_QTY_TRAIL = re.compile(r"^(.+?)\s+[x×]\s*(\d+)$", re.IGNORECASE)
_TRAILING_NUMBER = re.compile(r"^(.*\S)\s+\d+$")


def _collapse(text, limit):
    return " ".join((text or "").split())[:limit]


def _clean_initiative(raw, line_no=None):
    """Normalises an init value and checks it can actually be rolled."""
    where = f"Line {line_no}: " if line_no else ""
    text = (raw or "").strip().replace(" ", "")
    if not text:
        return DEFAULT_INITIATIVE
    if re.fullmatch(r"[+-]\d+", text):          # "+2" -> 1d20+2
        text = DEFAULT_INITIATIVE + text
    try:
        initiative_tool._roll_initiative(text)
    except ValueError:
        raise ValueError(f"{where}can't read the initiative {raw.strip()!r} - use a number or dice like 1d20+2.")
    return text


def _split_options(inner):
    """'hp 7, init 1d20+2, notes: a, b' -> ['hp 7', 'init 1d20+2', 'notes: a, b'];
    everything after 'notes:' belongs to the note, commas included."""
    notes = None
    m = re.search(r"(?:^|,)\s*notes?\s*[:=]\s*", inner, re.IGNORECASE)
    if m:
        notes, inner = inner[m.end():], inner[:m.start()]
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    if notes is not None:
        parts.append("notes: " + notes.strip())
    return parts


def parse_line(line, line_no=None):
    where = f"Line {line_no}: " if line_no else ""
    text = line.strip()
    inner = ""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", text)
    if m:
        text, inner = m.group(1).strip(), m.group(2)
    quantity = 1
    lead, trail = _QTY_LEAD.match(text), _QTY_TRAIL.match(text)
    if lead:
        quantity, text = int(lead.group(1)), lead.group(2).strip()
    elif trail:
        text, quantity = trail.group(1).strip(), int(trail.group(2))
    name = _collapse(text, MAX_NAME)
    if not name:
        raise ValueError(f"{where}every line needs a name, like 4x Goblin (hp 7).")
    if not 1 <= quantity <= initiative_tool.MAX_QUANTITY:
        raise ValueError(f"{where}how many must be between 1 and {initiative_tool.MAX_QUANTITY}.")

    entry = {"name": name, "quantity": quantity, "hp": None, "initiative": DEFAULT_INITIATIVE, "notes": ""}
    for part in _split_options(inner):
        low = part.lower()
        if low.startswith("notes:"):
            entry["notes"] = _collapse(part[6:], MAX_NOTES)
        elif m2 := re.fullmatch(r"hp\s*[:=]?\s*(\d+)", low):
            entry["hp"] = int(m2.group(1))
        elif m3 := re.fullmatch(r"init(?:iative)?\s*[:=]?\s*(.+)", low):
            entry["initiative"] = _clean_initiative(m3.group(1), line_no)
        elif re.fullmatch(r"m\.?d\.?c\.?", low):
            entry["mdc"] = True
        elif m4 := re.fullmatch(r"attacks?(?:\s+per\s+round)?\s*[:=]?\s*(\d+)", low):
            entry["attacks"] = int(m4.group(1))
            if not 1 <= entry["attacks"] <= MAX_ATTACKS:
                raise ValueError(f"{where}attacks per round must be between 1 and {MAX_ATTACKS}.")
        else:
            raise ValueError(f"{where}don't understand {part!r} - use hp N, init 1d20+2, attacks N, mdc or notes: ...")
    if entry["hp"] is not None and not 1 <= entry["hp"] <= 100000:
        raise ValueError(f"{where}hp must be a positive number.")
    return entry


def parse_body(body):
    """Text -> list of entries. Blank lines and '#' comment lines are skipped."""
    body = (body or "")[:MAX_BODY * 4]
    if len(body) > MAX_BODY:
        raise ValueError("That's too long for one encounter - split it into two.")
    entries = []
    for number, line in enumerate(body.splitlines(), start=1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        entries.append(parse_line(line, number))
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"An encounter can have up to {MAX_ENTRIES} groups.")
    return entries


def format_entry(e):
    opts = []
    if e.get("hp") is not None:
        opts.append(f"hp {e['hp']}")
    if (e.get("initiative") or DEFAULT_INITIATIVE) != DEFAULT_INITIATIVE:
        opts.append(f"init {e['initiative']}")
    if e.get("mdc"):
        opts.append("mdc")
    if e.get("attacks"):
        opts.append(f"attacks {e['attacks']}")
    if e.get("notes"):
        opts.append(f"notes: {e['notes']}")
    text = (f"{e['quantity']}x " if e.get("quantity", 1) > 1 else "") + e["name"]
    return text + (f" ({', '.join(opts)})" if opts else "")


def format_body(entries):
    return "\n".join(format_entry(e) for e in entries)


def summary(entries):
    """'4x Goblin, Goblin Boss' - for a list row."""
    return ", ".join((f"{e['quantity']}x " if e.get("quantity", 1) > 1 else "") + e["name"] for e in entries)


def _clean_name(name):
    name = _collapse(name, MAX_NAME)
    if not name:
        raise ValueError("Give the encounter a name.")
    return name


def _row_to_dict(row):
    entries = json.loads(row["entries_json"] or "[]")
    return {
        "id": row["id"], "game_key": row["game_key"], "name": row["name"], "entries": entries,
        "body": format_body(entries), "summary": summary(entries),
        "foes": sum(e["quantity"] for e in entries),
    }


def get_encounter(encounter_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM saved_encounters WHERE id = ?", (encounter_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_encounters(game_key):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM saved_encounters WHERE game_key = ? ORDER BY name COLLATE NOCASE, id", (game_key,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _name_taken(game_key, name, exclude_id=None):
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT id FROM saved_encounters WHERE game_key = ? AND name = ? COLLATE NOCASE AND id != ?",
            (game_key, name, exclude_id or 0),
        ).fetchone()
    return row is not None


def create_encounter(game_key, name, body=""):
    name = _clean_name(name)
    if _name_taken(game_key, name):
        raise ValueError(f"There's already an encounter called {name!r} for this game.")
    entries = parse_body(body)
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO saved_encounters (game_key, name, entries_json) VALUES (?, ?, ?)",
            (game_key, name, json.dumps(entries)),
        )
        return cur.lastrowid


def update_encounter(encounter_id, field, value):
    current = get_encounter(encounter_id)
    if not current:
        raise ValueError("That encounter doesn't exist any more.")
    if field == "name":
        value = _clean_name(value)
        if _name_taken(current["game_key"], value, exclude_id=encounter_id):
            raise ValueError(f"There's already an encounter called {value!r} for this game.")
        sql, param = "UPDATE saved_encounters SET name = ?, updated_at = datetime('now') WHERE id = ?", value
    elif field == "body":
        param = json.dumps(parse_body(value))
        sql = "UPDATE saved_encounters SET entries_json = ?, updated_at = datetime('now') WHERE id = ?"
    else:
        raise ValueError("That field can't be changed.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(sql, (param, encounter_id))


def delete_encounter(encounter_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM saved_encounters WHERE id = ?", (encounter_id,))


def append_entry(encounter_id, line):
    """Adds one parsed line (e.g. from a stat block) to an existing encounter."""
    current = get_encounter(encounter_id)
    if not current:
        raise ValueError("That encounter doesn't exist any more.")
    entries = current["entries"] + [parse_line(line)]
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"An encounter can have up to {MAX_ENTRIES} groups.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE saved_encounters SET entries_json = ?, updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(entries), encounter_id))


def append_foe(encounter_id, name, quantity=1, hp=None, initiative=""):
    """Adds one group (e.g. a creature picked from a book's stat block)."""
    current = get_encounter(encounter_id)
    if not current:
        raise ValueError("That encounter doesn't exist any more.")
    name = _collapse(name, MAX_NAME)
    if not name:
        raise ValueError("Give the creature a name.")
    quantity = 1 if quantity in (None, "") else int(quantity)
    if not 1 <= quantity <= initiative_tool.MAX_QUANTITY:
        raise ValueError(f"How many must be between 1 and {initiative_tool.MAX_QUANTITY}.")
    if hp in (None, ""):
        hp = None
    else:
        hp = int(hp)
        if not 1 <= hp <= 100000:
            raise ValueError("HP must be a positive number.")
    entries = current["entries"] + [{
        "name": name, "quantity": quantity, "hp": hp,
        "initiative": _clean_initiative(initiative), "notes": "",
    }]
    if len(entries) > MAX_ENTRIES:
        raise ValueError(f"An encounter can have up to {MAX_ENTRIES} groups.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE saved_encounters SET entries_json = ?, updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(entries), encounter_id))


def entries_from_board(game_key):
    """The foes currently on the initiative board, grouped back into entries:
    'Goblin 1..3' with the same max HP become one '3x Goblin' line."""
    groups = {}
    for c in initiative_tool.get_state(game_key)["combatants"]:
        if c.get("is_pc"):
            continue
        base = c["name"]
        m = _TRAILING_NUMBER.match(base)
        if m:
            base = m.group(1)
        key = (base.lower(), c.get("max_hp"))
        g = groups.setdefault(key, {"name": _collapse(base, MAX_NAME), "quantity": 0, "hp": c.get("max_hp"),
                                    "initiative": DEFAULT_INITIATIVE, "notes": _collapse(c.get("notes"), MAX_NOTES)})
        g["quantity"] += 1
    entries = sorted(groups.values(), key=lambda g: (g["name"].lower(), g["hp"] or 0))
    for e in entries:
        e["quantity"] = min(e["quantity"], initiative_tool.MAX_QUANTITY)
    return entries[:MAX_ENTRIES]


def save_board_foes(game_key, name):
    entries = entries_from_board(game_key)
    if not entries:
        raise ValueError("There are no foes on the board to save - add some first.")
    name = _clean_name(name)
    if _name_taken(game_key, name):
        raise ValueError(f"There's already an encounter called {name!r} for this game.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO saved_encounters (game_key, name, entries_json) VALUES (?, ?, ?)",
            (game_key, name, json.dumps(entries)),
        )
        return cur.lastrowid


def load_into_tracker(encounter_id, game_key):
    """Adds every group to the game's initiative board (each one rolling its
    own initiative) and returns {'state': board, 'added': number of foes}."""
    enc = get_encounter(encounter_id)
    if not enc or enc["game_key"] != game_key:
        raise ValueError("That encounter doesn't exist any more.")
    if not enc["entries"]:
        raise ValueError("That encounter is empty - add some foes to it first.")
    state, added = None, 0
    for e in enc["entries"]:
        notes = e.get("notes") or ""
        if e.get("mdc") and "m.d.c" not in notes.lower() and "mdc" not in notes.lower():
            notes = ("M.D.C. " + notes).strip()       # so the board shows what the hp number means
        state = initiative_tool.add_combatant(
            game_key, e["name"], e.get("initiative") or DEFAULT_INITIATIVE, hp=e.get("hp"),
            is_pc=False, notes=notes, quantity=e.get("quantity", 1),
        )
        added += e.get("quantity", 1)
    return {"state": state, "added": added}
