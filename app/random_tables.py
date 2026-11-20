"""Random tables: the GM's own roll tables.

A table is a name, a die, and a body of text with one entry per line:

    1-2: A wandering patrol
    3: [[Strange Weather]]          <- rolls another table and splices in the result
    4-6: {2d6} silver pieces        <- {NdM} is rolled and replaced by its total

Numbered ("1-3: ...") and plain lines can't be mixed. A table of plain lines is
numbered 1..N automatically and rolled with a dN, so pasting a list of ideas
just works. Because the whole table is one text blob it edits like a note and
imports/exports trivially.

Tables belong to a game; tables with game_key "_global" are available in every
game. `[[Name]]` is looked up in the table's own game first, then in globals.
"""
import re

from . import database
from .tools import dice as dice_tool

GLOBAL_KEY = "_global"
MAX_ENTRIES = 500
MAX_BODY_CHARS = 20_000
MAX_DEPTH = 5
HISTORY_LIMIT = 50

# "3: text" or "4-6: text". The colon is required so an ordinary line that
# merely starts with a number ("10 gold coins") stays a plain entry.
_ENTRY_RE = re.compile(r"^\s*(\d+)(?:\s*[-–—]\s*(\d+))?\s*:\s*(.*\S)\s*$")
_REF_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_DICE_RE = re.compile(r"\{(\d*d\d+(?:[+-]\d+)?)\}", re.IGNORECASE)
_DIE_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.IGNORECASE)


class TableError(ValueError):
    """A problem with a table definition or a roll, worded for the GM."""


# ---------------------------------------------------------------- parsing
def parse_die(die):
    """(count, sides, modifier) for a single dice group like 'd20' or '2d6+1'."""
    m = _DIE_RE.match((die or "").strip().replace(" ", ""))
    if not m:
        raise TableError(f"'{die}' isn't a die - use something like d6, d20, d100 or 2d6.")
    count, sides, mod = int(m.group(1) or 1), int(m.group(2)), int(m.group(3) or 0)
    if not (1 <= count <= 20 and 2 <= sides <= 1000):
        raise TableError("Use between 1 and 20 dice with 2 to 1000 sides.")
    return count, sides, mod


def die_range(die):
    count, sides, mod = parse_die(die)
    return count + mod, count * sides + mod


def parse_body(body):
    """Splits a table body into entries.

    Returns (entries, numbered) where entries is [{lo, hi, text}] and numbered
    says whether the lines carried their own ranges. Plain lines get 1..N.
    """
    lines = [ln for ln in (body or "").splitlines() if ln.strip()]
    if len(lines) > MAX_ENTRIES:
        raise TableError(f"A table can have at most {MAX_ENTRIES} entries.")
    parsed = [(_ENTRY_RE.match(ln), ln) for ln in lines]
    numbered = [m for m, _ in parsed if m]
    if numbered and len(numbered) != len(lines):
        raise TableError("Some lines start with a number and a colon and some don't - "
                         "number every line (1-3: ...) or none of them.")
    if not numbered:
        return [{"lo": i, "hi": i, "text": ln.strip()} for i, ln in enumerate(lines, start=1)], False
    entries = []
    for m, _ in parsed:
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if hi < lo:
            raise TableError(f"The range {lo}-{hi} is backwards.")
        entries.append({"lo": lo, "hi": hi, "text": m.group(3).strip()})
    return entries, True


def effective_die(die, entries, numbered):
    """The die a table is actually rolled with: plain tables are always dN."""
    if not numbered:
        n = len(entries)
        # a one-line table has nothing to roll for ("d1": the only entry always comes up)
        return f"d{n}" if n else ""
    return (die or "").strip() or "d100"


def check(die, body):
    """Validates a table definition. Returns {entries, die, numbered, warnings};
    raises TableError for anything that can't be rolled at all."""
    entries, numbered = parse_body(body)
    if not entries:
        raise TableError("Add at least one entry.")
    used_die = effective_die(die, entries, numbered)
    warnings = []
    if not numbered:
        return {"entries": entries, "die": used_die, "numbered": False, "warnings": warnings}
    lo_die, hi_die = die_range(used_die)
    if numbered:
        covered = {}
        for e in entries:
            if e["hi"] > hi_die or e["lo"] < lo_die:
                raise TableError(f"The entry {e['lo']}-{e['hi']} can't be rolled with {used_die} "
                                 f"(it rolls {lo_die}-{hi_die}).")
            for n in range(e["lo"], e["hi"] + 1):
                covered[n] = covered.get(n, 0) + 1
        overlaps = sorted(n for n, c in covered.items() if c > 1)
        gaps = [n for n in range(lo_die, hi_die + 1) if n not in covered]
        if overlaps:
            warnings.append(f"Overlapping entries at {_compress(overlaps)} (the first one listed wins).")
        if gaps:
            warnings.append(f"No entry for {_compress(gaps)} - rolling it will say so.")
    return {"entries": entries, "die": used_die, "numbered": numbered, "warnings": warnings}


def _compress(numbers):
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'"""
    out, start, prev = [], None, None
    for n in numbers + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    return ", ".join(out)


# ------------------------------------------------------------------ storage
def _clean_name(name):
    name = " ".join((name or "").split())
    if not name:
        raise TableError("Give the table a name.")
    if len(name) > 80:
        raise TableError("Keep the table name under 80 characters.")
    if "[[" in name or "]]" in name:
        raise TableError("A table name can't contain [[ or ]].")
    return name


def _check_for_save(die, body):
    if len(body or "") > MAX_BODY_CHARS:
        raise TableError("That table is too long.")
    info = check(die, body)
    return info


def _name_taken(game_key, name, exclude_id=None):
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT id FROM random_tables WHERE game_key = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
            (game_key, name, exclude_id),
        ).fetchone()
    return row is not None


def get_table(table_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM random_tables WHERE id = ?", (table_id,)).fetchone()
    return dict(row) if row else None


def list_tables(game_key=None):
    """Tables for a game (plus the global ones); just the globals with no game."""
    keys = [GLOBAL_KEY] if not game_key or game_key == GLOBAL_KEY else [game_key, GLOBAL_KEY]
    marks = ",".join("?" * len(keys))
    with database.get_cursor() as cur:
        rows = cur.execute(
            f"SELECT * FROM random_tables WHERE game_key IN ({marks}) "
            "ORDER BY game_key = '_global', name COLLATE NOCASE", keys,
        ).fetchall()
    tables = []
    for r in rows:
        t = dict(r)
        try:
            info = check(t["die"], t["body"])
            t.update(entry_count=len(info["entries"]), effective_die=info["die"], warnings=info["warnings"], error="")
        except TableError as e:
            t.update(entry_count=0, effective_die=t["die"], warnings=[], error=str(e))
        tables.append(t)
    return tables


def create_table(game_key, name, die="", body=""):
    game_key = game_key or GLOBAL_KEY
    name = _clean_name(name)
    if _name_taken(game_key, name):
        raise TableError(f"There's already a table called '{name}'.")
    if (body or "").strip():
        _check_for_save(die, body)
    elif (die or "").strip():
        parse_die(die)
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO random_tables (game_key, name, die, body) VALUES (?, ?, ?, ?)",
                    (game_key, name, (die or "").strip(), body or ""))
        return cur.lastrowid


def update_table(table_id, field, value):
    table = get_table(table_id)
    if not table:
        raise TableError("That table no longer exists.")
    if field == "name":
        value = _clean_name(value)
        if _name_taken(table["game_key"], value, exclude_id=table_id):
            raise TableError(f"There's already a table called '{value}'.")
    elif field == "die":
        value = (value or "").strip()
        if value:
            parse_die(value)
        if (table["body"] or "").strip():
            _check_for_save(value, table["body"])
    elif field == "body":
        value = value or ""
        if value.strip():
            _check_for_save(table["die"], value)
    elif field == "game_key":
        value = value or GLOBAL_KEY
        if _name_taken(value, table["name"], exclude_id=table_id):
            raise TableError(f"There's already a table called '{table['name']}' there.")
    else:
        raise TableError(f"Unknown field: {field}")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE random_tables SET {field} = ? WHERE id = ?", (value, table_id))
    return get_table(table_id)


def delete_table(table_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM random_tables WHERE id = ?", (table_id,))


def _find_by_name(game_key, name):
    """A table by name: the game's own first, then the global ones."""
    for key in dict.fromkeys([game_key or GLOBAL_KEY, GLOBAL_KEY]):
        with database.get_cursor() as cur:
            row = cur.execute(
                "SELECT * FROM random_tables WHERE game_key = ? AND name = ? COLLATE NOCASE", (key, name.strip()),
            ).fetchone()
        if row:
            return dict(row)
    return None


# ------------------------------------------------------------------ rolling
def _pick(table, roll=None):
    """(roll, entry text or None) for one roll of a single table."""
    info = check(table["die"], table["body"])
    if roll is None:
        roll = 1 if info["die"] == "d1" else dice_tool.roll_group(info["die"])["total"]
    for e in info["entries"]:
        if e["lo"] <= roll <= e["hi"]:
            return roll, e["text"]
    return roll, None


def _resolve(text, game_key, stack, steps):
    """Fills in {dice} and [[table]] references inside one entry's text."""
    text = _DICE_RE.sub(lambda m: str(dice_tool.roll_group(m.group(1))["total"]), text)

    def sub_ref(m):
        name = m.group(1).strip()
        nested = _find_by_name(game_key, name)
        if nested is None:
            return f"(missing table: {name})"
        if nested["name"].lower() in stack:
            return f"(loop: {name})"
        if len(stack) >= MAX_DEPTH:
            return f"(too deep: {name})"
        try:
            roll, entry = _pick(nested)
        except TableError as e:
            return f"({name}: {e})"
        if entry is None:
            steps.append({"table": nested["name"], "roll": roll, "text": f"(no entry for {roll})"})
            return f"(no entry for {roll} in {nested['name']})"
        step = {"table": nested["name"], "roll": roll, "text": ""}
        steps.append(step)
        step["text"] = _resolve(entry, game_key, stack | {nested["name"].lower()}, steps)
        return step["text"]

    return _REF_RE.sub(sub_ref, text)


def roll_table(table_id, record=True, roll=None):
    """Rolls a table (following [[references]]). Returns {table, die, roll,
    text, steps}, where `steps` lists each nested table that was rolled. The
    result is added to the game's roll history unless record=False."""
    table = get_table(table_id)
    if not table:
        raise TableError("That table no longer exists.")
    if not (table["body"] or "").strip():
        raise TableError("That table has no entries yet.")
    info = check(table["die"], table["body"])
    rolled, entry = _pick(table, roll)
    if entry is None:
        text = f"(no entry for {rolled})"
        steps = []
    else:
        steps = []
        text = _resolve(entry, table["game_key"], {table["name"].lower()}, steps)
    result = {"table": table["name"], "die": info["die"], "roll": rolled, "text": text, "steps": steps}
    if record:
        add_history(table["game_key"], table["name"], rolled, text)
    return result


# ------------------------------------------------------------------ history
def add_history(game_key, table_name, roll, text):
    game_key = game_key or GLOBAL_KEY
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO random_roll_history (game_key, table_name, roll, text) VALUES (?, ?, ?, ?)",
                    (game_key, table_name, roll, text))
        cur.execute(
            "DELETE FROM random_roll_history WHERE game_key = ? AND id NOT IN "
            "(SELECT id FROM random_roll_history WHERE game_key = ? ORDER BY id DESC LIMIT ?)",
            (game_key, game_key, HISTORY_LIMIT),
        )


def list_history(game_key=None, limit=20):
    """Latest rolls, newest first. A game's own page shows its rolls, the
    no-game page shows the global ones."""
    key = game_key or GLOBAL_KEY
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT id, table_name, roll, text, created_at FROM random_roll_history "
            "WHERE game_key = ? ORDER BY id DESC LIMIT ?", (key, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_history(game_key=None):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM random_roll_history WHERE game_key = ?", (game_key or GLOBAL_KEY,))


# ---------------------------------------------------------------- built-ins
def builtin_tables():
    """The app's built-in encounter and loot data as ready-to-import
    (name, die, body) tables, plus one example that shows off the syntax."""
    from .tools import encounters

    out = []
    for env, lines in encounters.encounter_environments().items():
        out.append((f"Encounters: {env}", "", "\n".join(lines)))
    rarity = encounters.loot_rarity_table()
    out.append(("Loot: Item rarity", "d100",
                "\n".join(f"{e['roll']}: {e['rarity']} - {e['example']}" for e in rarity)))
    out.append(("Example: Treasure hoard", "d6",
                "1-3: {3d6} gp in mixed coin\n"
                "4-5: {2d4} gp and a trinket: [[Loot: Item rarity]]\n"
                "6: A sealed chest guarded by [[Encounters: Dungeon]]"))
    return out


def import_builtin_tables(game_key=None):
    """Adds the built-in tables to a game (or the globals) - skipping any whose
    name already exists, so it's safe to run again. Returns how many were added."""
    game_key = game_key or GLOBAL_KEY
    added = 0
    for name, die, body in builtin_tables():
        if _name_taken(game_key, name):
            continue
        create_table(game_key, name, die, body)
        added += 1
    return added
