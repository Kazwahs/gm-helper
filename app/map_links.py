"""Linked maps: drilling down from one map into another - a settlement on the
world map into its city, a city into a building, a dungeon level into the
one below.

A link is stored once, from the map you started on, with the kind seen from
that map ("detail", "below" or "above"); the other map shows the same link from
its own side. Deleting either map removes the link.
"""
import html
import random
import re

from . import database, maps_store, room_keys
from .maps import generate_map

KINDS = ("detail", "below", "above")
_SETTLEMENT = re.compile(r"<g\b([^>]*)>")
_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
_LEVEL = re.compile(r"\s+-\s+Level (\d+)$")

# what the other map is, from this map's point of view
_OUT = {"detail": "Detail map", "below": "Level below", "above": "Level above"}
_IN = {"detail": "Part of", "below": "Level above", "above": "Level below"}
_CITY_SIZE = {"city": "large", "town": "medium", "village": "small"}


class LinkError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def settlements(svg):
    """Named settlements drawn on a world or wilderness map:
    [{name, tier}] in drawing order, each name once. Maps drawn before
    settlements recorded their names have none to offer."""
    found, seen = [], set()
    for match in _SETTLEMENT.finditer(svg or ""):
        attrs = {k: html.unescape(v) for k, v in _ATTR.findall(match.group(1))}
        if attrs.get("data-kind") != "settlement":
            continue
        name = attrs.get("data-name", "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            found.append({"name": name, "tier": attrs.get("data-tier", "village")})
    return found


def _row(r, perspective):
    d = dict(r)
    outgoing = perspective == "out"
    other = d["to_map_id"] if outgoing else d["from_map_id"]
    return {
        "id": d["id"], "kind": d["kind"], "label": d["label"],
        "role": (_OUT if outgoing else _IN)[d["kind"]],
        "map_id": other, "title": d["other_title"], "map_type": d["other_type"],
    }


def list_links(map_id):
    """{'out': [...], 'in': [...]}: maps this one leads to, and maps that lead here."""
    with database.get_cursor() as cur:
        out = cur.execute(
            "SELECT l.*, m.title AS other_title, m.map_type AS other_type FROM map_links l "
            "JOIN maps m ON m.id = l.to_map_id WHERE l.from_map_id = ? ORDER BY l.id", (map_id,)).fetchall()
        into = cur.execute(
            "SELECT l.*, m.title AS other_title, m.map_type AS other_type FROM map_links l "
            "JOIN maps m ON m.id = l.from_map_id WHERE l.to_map_id = ? ORDER BY l.id", (map_id,)).fetchall()
    return {"out": [_row(r, "out") for r in out], "in": [_row(r, "in") for r in into]}


def link(from_id, to_id, kind="detail", label=""):
    """Links two existing maps of the same game. Linking the same pair again
    updates the kind and label instead of adding a second link."""
    if kind not in KINDS:
        raise LinkError("Unknown kind of link.")
    if from_id == to_id:
        raise LinkError("A map can't link to itself.")
    a, b = maps_store.get_map(from_id), maps_store.get_map(to_id)
    if not a or not b:
        raise LinkError("That map no longer exists.", 404)
    if a["game_key"] != b["game_key"]:
        raise LinkError("Maps can only be linked within the same game.")
    label = (label or "").strip()[:120]
    with database.get_cursor(commit=True) as cur:
        # the same pair the other way round is the same relationship: replace it
        cur.execute("DELETE FROM map_links WHERE from_map_id = ? AND to_map_id = ?", (to_id, from_id))
        cur.execute(
            "INSERT INTO map_links (from_map_id, to_map_id, kind, label) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (from_map_id, to_map_id) DO UPDATE SET kind = excluded.kind, label = excluded.label",
            (from_id, to_id, kind, label))
        return cur.execute("SELECT id FROM map_links WHERE from_map_id = ? AND to_map_id = ?", (from_id, to_id)).fetchone()["id"]


def unlink(map_id, link_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM map_links WHERE id = ? AND (from_map_id = ? OR to_map_id = ?)",
                    (link_id, map_id, map_id))


def linkable_maps(m):
    """Other maps of the same game that could be linked from `m`."""
    return [x for x in maps_store.list_maps(game_key=m["game_key"]) if x["id"] != m["id"]]


def _level_title(m, kind):
    base = _LEVEL.sub("", m["title"])
    match = _LEVEL.search(m["title"])
    here = int(match.group(1)) if match else 1
    if kind == "below":
        return f"{base} - Level {here + 1}"
    return f"{base} - Level {here - 1}" if here > 1 else f"{base} - Upper level"


def create_linked(m, kind, map_type, title="", settlement=None, tier="village", seed=None):
    """Generates a new map, saves it beside `m` (same game, campaign, genre and
    style) and links it. `settlement` names the place a city or town map is
    for. Returns the new map's id."""
    if kind not in KINDS:
        raise LinkError("Unknown kind of link.")
    params = dict(m.get("params") or {})
    if settlement:
        params["name"] = settlement
        params["size"] = _CITY_SIZE.get(tier, "medium")
    else:
        params.pop("name", None)
    if map_type not in ("wilderness", "world"):
        params.pop("hex_grid", None)
    if seed is None:
        seed = random.SystemRandom().randrange(10**9)
    try:
        result = generate_map(map_type, m["genre"], params, seed)
    except ValueError as e:
        raise LinkError(str(e))
    if not (title or "").strip():
        if kind in ("below", "above") and map_type == m["map_type"]:
            title = _level_title(m, kind)
        elif settlement:
            title = result["title_suggestion"]
        else:
            title = f"{m['title']} - {result['title_suggestion']}"
    new_id = maps_store.save_map(m["game_key"], map_type, m["genre"], title, result["svg"],
                                 seed=result["seed"], params=result["params"], campaign_id=m.get("campaign_id"))
    link(m["id"], new_id, kind, settlement or "")
    return new_id


def stairs_hint(m):
    """Which rooms have stairs, for a dungeon or building: {'down': [3], 'up': [8]}."""
    hint = {"down": [], "up": []}
    for room in room_keys.parse_rooms(m["svg"]):
        if room["stairs"] in hint:
            hint[room["stairs"]].append(room["number"])
    return hint


def payload(m):
    links = list_links(m["id"])
    return {
        "links": links,
        "settlements": settlements(m["svg"]) if m["map_type"] in ("world", "wilderness") else [],
        "stairs": stairs_hint(m) if m["map_type"] in room_keys.KEY_MAP_TYPES else {"down": [], "up": []},
        "others": [{"id": x["id"], "title": x["title"], "map_type": x["map_type"]} for x in linkable_maps(m)],
        "map_type": m["map_type"],
    }
