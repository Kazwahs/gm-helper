"""Room keys: the numbered list of what is in each room of a dungeon or
interior map.

The map generators stamp every room's facts (number, name, size, exits) onto
the room's SVG element as data attributes, so the key can be built from a
saved map without regenerating it. Entries are stored per map, are fully
editable, and can optionally be fleshed out by the configured AI model.

The generated text is deliberately system-neutral: creatures are described
rather than statted and treasure is described rather than priced, so the GM
drops in whatever their game uses.
"""
import html
import json
import random
import re
from collections import deque

from . import config, database, llm

KINDS = ("empty", "creatures", "trap", "puzzle", "treasure")
KIND_LABELS = {"empty": "Empty", "creatures": "Creatures", "trap": "Trap",
               "puzzle": "Puzzle or hazard", "treasure": "Treasure"}
TEXT_FIELDS = ("title", "kind", "description", "contents", "treasure", "secrets", "notes")
MAX_TEXT = 4000
KEY_MAP_TYPES = ("dungeon", "interior")

with open(config.ROOM_KEY_CONTENT_PATH, "r", encoding="utf-8") as _f:
    _BANK = json.load(_f)

_GENRES = ("fantasy", "modern", "hightech")
_TIER_LABEL = {"light": "Light", "moderate": "Moderate", "tough": "Tough", "boss": "Boss"}


class RoomKeyError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------- reading maps --

_TAG = re.compile(r"<g\b([^>]*)>")
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')


def _split_exits(text):
    out = []
    for part in (text or "").split(","):
        num, _, kind = part.partition(":")
        if num.strip().isdigit():
            out.append((int(num), kind.strip() or "door"))
    return out


def parse_rooms(svg):
    """The rooms drawn on a map, in number order: [{number, name, feature,
    shape, size, stairs, exits: [(room, door kind)]}].

    Maps saved before the generators recorded these facts have room elements
    with no data attributes; those are numbered in drawing order (which is the
    numbering the generators use) and come back with no name or exits."""
    rooms = []
    for match in _TAG.finditer(svg or ""):
        attrs = {k: html.unescape(v) for k, v in _ATTR.findall(match.group(1))}
        if attrs.get("data-kind") != "room":
            continue
        raw_number = attrs.get("data-room", "")
        number = int(raw_number) if raw_number.isdigit() else len(rooms) + 1
        rooms.append({
            "number": number,
            "name": attrs.get("data-name", "").strip(),
            "feature": attrs.get("data-feature", "").strip(),
            "shape": attrs.get("data-shape", "rect"),
            "size": attrs.get("data-size", ""),
            "stairs": attrs.get("data-stairs", ""),
            "exits": _split_exits(attrs.get("data-exits", "")),
        })
    rooms.sort(key=lambda r: r["number"])
    return rooms


def exits_text(exits):
    """'door to 2, secret door to 5' - in room order, each room once per kind."""
    words = _BANK["door_words"]
    parts = []
    for num, kind in sorted(exits):
        word = words.get(kind, "a door")
        parts.append(f"{word} to {num}")
    return ", ".join(parts)


def _depths(rooms):
    """How many doors away from the lowest-numbered room each room is."""
    if not rooms:
        return {}
    graph = {r["number"]: {n for n, _ in r["exits"]} for r in rooms}
    for number, neighbours in list(graph.items()):
        for n in neighbours:
            graph.setdefault(n, set()).add(number)
    start = rooms[0]["number"]
    depth = {start: 0}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for n in sorted(graph.get(cur, ())):
            if n not in depth:
                depth[n] = depth[cur] + 1
                queue.append(n)
    far = max(depth.values())
    for r in rooms:
        depth.setdefault(r["number"], far)
    return depth


def _area(room):
    m = re.match(r"^(\d+)x(\d+)$", room.get("size") or "")
    return int(m.group(1)) * int(m.group(2)) if m else 0


# --------------------------------------------------------------- generating --

def _bank(name, genre):
    table = _BANK[name]
    return table.get(genre) or table["fantasy"]


def _weighted(rng, weights):
    total = sum(weights.values())
    roll = rng.uniform(0, total)
    for key, weight in weights.items():
        roll -= weight
        if roll <= 0:
            return key
    return next(iter(weights))


def generate(rooms, genre, seed, creature_roller=None):
    """A key entry for every room: [{room, title, kind, description, contents,
    treasure, secrets, exits, size}]. The same rooms, genre and seed always
    give the same key. `creature_roller`, if given, is called for each room
    that holds creatures and should return a line of text (the GM's own random
    table); if it fails or returns nothing, the built-in creatures are used."""
    rng = random.Random(f"room-key:{seed}")
    depth = _depths(rooms)
    far = max(depth.values()) if depth else 0
    start = rooms[0]["number"] if rooms else None

    boss = None
    candidates = [r for r in rooms if not r["stairs"] and r["number"] != start] or [r for r in rooms if r["number"] != start]
    if candidates:
        boss = max(candidates, key=lambda r: (depth[r["number"]], _area(r), -r["number"]))["number"]

    entries = []
    for room in rooms:
        g = rng.choice(_GENRES) if genre == "mixed" else genre
        number = room["number"]
        fraction = depth[number] / far if far else 0
        tier = "light" if fraction < 0.34 else "moderate" if fraction < 0.67 else "tough"

        if number == boss:
            kind = "creatures"
            tier = "boss"
        elif room["stairs"]:
            kind = "empty"
        elif number == start:
            kind = _weighted(rng, {"empty": 5, "creatures": 2, "trap": 1})
            tier = "light"
        else:
            kind = _weighted(rng, {"creatures": 40, "empty": 22, "trap": 13, "puzzle": 12, "treasure": 13})

        description = rng.choice(_bank("looks", g))
        if room["feature"]:
            description += f" Of note: {room['feature']}."
        if room["stairs"]:
            description += " " + _BANK["stairs"].get(room["stairs"], "")

        contents = treasure = ""
        if kind == "creatures":
            contents = ""
            if creature_roller:
                try:
                    contents = (creature_roller() or "").strip()
                except Exception:  # a missing or empty table must not break the key
                    contents = ""
            if not contents:
                contents = rng.choice(_bank("creatures", g)[tier])
            contents = f"{_TIER_LABEL[tier]} encounter: {contents}"
            if tier == "boss":
                treasure = rng.choice(_bank("treasure", g)["hoard"])
            elif rng.random() < {"light": 0.35, "moderate": 0.55, "tough": 0.75}[tier]:
                treasure = rng.choice(_bank("treasure", g)["minor" if tier == "light" else "fair"])
        elif kind == "trap":
            contents = rng.choice(_bank("traps", g))
            if rng.random() < 0.3:
                treasure = rng.choice(_bank("treasure", g)["minor"])
        elif kind == "puzzle":
            contents = rng.choice(_bank("puzzles", g))
            if rng.random() < 0.45:
                treasure = rng.choice(_bank("treasure", g)["fair" if tier != "light" else "minor"])
        elif kind == "treasure":
            treasure = rng.choice(_bank("treasure", g)["fair" if tier != "light" else "minor"])
            if rng.random() < 0.35:
                contents = "Trapped: " + rng.choice(_bank("traps", g))
        else:  # empty
            contents = rng.choice(_bank("empty", g))
            if not room["stairs"] and rng.random() < 0.12:
                treasure = "Hidden, found by a careful search: " + rng.choice(_bank("treasure", g)["minor"])

        secrets = []
        for num, door in sorted(room["exits"]):
            if door == "secret":
                secrets.append(f"Secret door to room {num}: found only on a careful search of the wall.")
            elif door == "locked":
                secrets.append(f"Locked door to room {num}: pick it, force it, or find the key.")

        entries.append({
            "room": number,
            "title": room["name"] or f"Room {number}",
            "kind": kind,
            "description": description,
            "contents": contents,
            "treasure": treasure,
            "secrets": "\n".join(secrets),
            "exits": exits_text(room["exits"]),
            "size": room["size"],
        })
    return entries


# ------------------------------------------------------------------ storage --

def list_entries(map_id):
    with database.get_cursor() as cur:
        rows = cur.execute("SELECT * FROM map_room_keys WHERE map_id = ? ORDER BY room", (map_id,)).fetchall()
    return [dict(r) for r in rows]


def get_entry(map_id, room):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM map_room_keys WHERE map_id = ? AND room = ?", (map_id, room)).fetchone()
    return dict(row) if row else None


def _clean(value, limit=MAX_TEXT):
    return (value or "").strip()[:limit]


def replace_all(map_id, entries):
    """Throws away the map's key and stores these entries instead."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM map_room_keys WHERE map_id = ?", (map_id,))
        for e in entries:
            cur.execute(
                "INSERT INTO map_room_keys (map_id, room, title, kind, description, contents, treasure, "
                "secrets, notes, exits, size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (map_id, e["room"], _clean(e.get("title"), 200), e.get("kind") if e.get("kind") in KINDS else "empty",
                 _clean(e.get("description")), _clean(e.get("contents")), _clean(e.get("treasure")),
                 _clean(e.get("secrets")), _clean(e.get("notes")), _clean(e.get("exits"), 500),
                 _clean(e.get("size"), 40)),
            )


def update_entry(map_id, room, **fields):
    """Changes the named text fields of one entry. Unknown fields are ignored."""
    entry = get_entry(map_id, room)
    if not entry:
        raise RoomKeyError("That room has no key entry yet.", 404)
    sets, values = [], []
    for name, value in fields.items():
        if name not in TEXT_FIELDS or value is None:
            continue
        if name == "kind":
            if value not in KINDS:
                raise RoomKeyError("Unknown room type.")
            clean = value
        elif name == "title":
            clean = _clean(value, 200) or f"Room {room}"
        else:
            clean = _clean(value)
        sets.append(f"{name} = ?")
        values.append(clean)
    if sets:
        with database.get_cursor(commit=True) as cur:
            cur.execute(f"UPDATE map_room_keys SET {', '.join(sets)}, updated_at = datetime('now') "
                        "WHERE map_id = ? AND room = ?", (*values, map_id, room))
    return get_entry(map_id, room)


def delete_all(map_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM map_room_keys WHERE map_id = ?", (map_id,))


def generate_for_map(m, seed=None, creature_roller=None):
    """Builds and stores the key for a saved map row (from maps_store.get_map)."""
    if m["map_type"] not in KEY_MAP_TYPES:
        raise RoomKeyError("Room keys are for dungeon and building maps.")
    rooms = parse_rooms(m["svg"])
    if not rooms:
        raise RoomKeyError("There are no numbered rooms on this map.")
    if seed is None:
        seed = random.SystemRandom().randrange(10**9)
    entries = generate(rooms, m["genre"], f"{m['id']}:{seed}", creature_roller)
    replace_all(m["id"], entries)
    return list_entries(m["id"])


# ----------------------------------------------------------------------- AI --

_SYSTEM = (
    "You help a game master write the room key for a tabletop RPG dungeon or building. "
    "Write vivid but brief text. Never invent game statistics, dice or rules numbers; the GM "
    "will use their own system. Reply with exactly two labelled sections and nothing else:\n"
    "READ ALOUD: two or three sentences describing what the players see, hear and smell on "
    "entering, without revealing anything hidden.\n"
    "GM NOTES: two to four short sentences on how the room plays: what the occupants want, how "
    "a trap or puzzle works, what a clever party can do."
)


def ai_available():
    return llm.is_enabled()


def parse_ai_reply(text):
    """Splits the model's reply into (read_aloud, gm_notes). Tolerates a model
    that ignores the labels by treating the whole reply as the read-aloud text."""
    text = (text or "").strip()
    m = re.search(r"READ ALOUD:\s*(.*?)\s*(?:GM NOTES:\s*(.*))?$", text, re.S | re.I)
    if not m:
        return text, ""
    return (m.group(1) or "").strip(), (m.group(2) or "").strip()


def flesh_out(m, room):
    """Asks the configured model to expand one entry. Updates the entry's read-aloud
    text, and adds the model's GM notes after any notes the GM already wrote."""
    if not llm.is_enabled():
        raise RoomKeyError("AI features aren't enabled yet - set up and test a connection in Settings.")
    entry = get_entry(m["id"], room)
    if not entry:
        raise RoomKeyError("That room has no key entry yet.", 404)
    lines = [f"Map: {m['title']} ({m['genre']} {m['map_type']})",
             f"Room {entry['room']}: {entry['title']}"]
    for label, field in (("Type", "kind"), ("What is here", "contents"), ("Treasure", "treasure"),
                         ("Hidden or locked", "secrets"), ("Starting description", "description")):
        if (entry[field] or "").strip():
            lines.append(f"{label}: {entry[field]}")
    if entry["exits"]:
        lines.append(f"Exits: {entry['exits']}")
    try:
        reply = llm.generate(_SYSTEM, "\n".join(lines), max_tokens=500)
    except llm.LLMError as e:
        raise RoomKeyError(str(e), 502)
    read_aloud, notes = parse_ai_reply(reply)
    if not read_aloud:
        raise RoomKeyError("The model sent back nothing usable. Try again.", 502)
    new_notes = entry["notes"]
    if notes:
        new_notes = (entry["notes"].rstrip() + "\n\n" if entry["notes"].strip() else "") + "AI notes: " + notes
    return update_entry(m["id"], room, description=read_aloud, notes=new_notes)
