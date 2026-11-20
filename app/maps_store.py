"""Saved maps: persists a generated map's SVG (and the seed/params that made
it) so it redisplays instantly without regenerating, and optionally links it
to a campaign the same way a bookmark can - a map is meant to be a piece of
reference material a GM comes back to, not something recomputed each time."""
import json
import math

from . import database


def list_maps(game_key=None, campaign_id=None):
    sql = "SELECT id, game_key, campaign_id, map_type, genre, title, seed, created_at FROM maps"
    clauses, params = [], []
    if game_key:
        clauses.append("game_key = ?")
        params.append(game_key)
    if campaign_id is not None:
        clauses.append("campaign_id = ?")
        params.append(campaign_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    with database.get_cursor() as cur:
        rows = cur.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_map(map_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
    if not row:
        return None
    m = dict(row)
    try:
        m["params"] = json.loads(m["params_json"]) if m["params_json"] else {}
    except ValueError:
        m["params"] = {}
    return m


def save_map(game_key, map_type, genre, title, svg, seed=None, params=None, campaign_id=None):
    title = (title or "").strip() or f"{map_type.capitalize()} map"
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO maps (game_key, campaign_id, map_type, genre, title, seed, params_json, svg, original_svg) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (game_key, campaign_id, map_type, genre, title, seed, json.dumps(params or {}), svg, svg),
        )
        return cur.lastrowid


def rename_map(map_id, title):
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the map a title.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET title = ? WHERE id = ?", (title, map_id))


def set_campaign(map_id, campaign_id):
    """campaign_id may be None to detach the map from any campaign."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET campaign_id = ? WHERE id = ?", (campaign_id, map_id))


def delete_map(map_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM maps WHERE id = ?", (map_id,))


def update_svg(map_id, svg):
    """Persists hand-edited SVG from the map editor. Leaves original_svg
    untouched so 'Revert to Generated' can always get back to the
    as-generated version."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET svg = ? WHERE id = ?", (svg, map_id))


def revert_svg(map_id):
    """Discards any hand edits, restoring svg to the original generated
    version."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET svg = original_svg WHERE id = ?", (map_id,))


# ------------------------------------------------------------ fog of war
# The player view shows the map under a "fog" that the GM lifts one area at a
# time. State lives in maps.reveal_json as {"enabled": bool, "areas": [...],
# "v": int}; `v` bumps on every change so the player page can poll cheaply.
# Areas are either {"shape": "circle", "x", "y", "r"} or
# {"shape": "rect", "x", "y", "w", "h"}, in the SVG's own coordinates.
MAX_REVEAL_AREAS = 400
_MAX_COORD = 100_000


def _clean_reveal(raw):
    state = {"enabled": False, "areas": [], "v": 0}
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    if isinstance(data, dict):
        state["enabled"] = bool(data.get("enabled"))
        try:
            state["v"] = int(data.get("v") or 0)
        except (TypeError, ValueError):
            state["v"] = 0
        if isinstance(data.get("areas"), list):
            state["areas"] = [a for a in data["areas"] if isinstance(a, dict)][:MAX_REVEAL_AREAS]
    return state


def _num(value, name, lo, hi):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.")
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{name} is out of range.")
    return round(number, 1)


def get_reveal(map_id):
    """The fog state for a map, or None if the map doesn't exist."""
    with database.get_cursor() as cur:
        row = cur.execute("SELECT reveal_json FROM maps WHERE id = ?", (map_id,)).fetchone()
    return _clean_reveal(row["reveal_json"]) if row else None


def _update_reveal(map_id, change):
    """Read-modify-write of the fog state; `change(state)` mutates it in place."""
    with database.get_cursor(commit=True) as cur:
        row = cur.execute("SELECT reveal_json FROM maps WHERE id = ?", (map_id,)).fetchone()
        if not row:
            raise ValueError("Unknown map.")
        state = _clean_reveal(row["reveal_json"])
        change(state)
        state["v"] += 1
        cur.execute("UPDATE maps SET reveal_json = ? WHERE id = ?", (json.dumps(state), map_id))
    return state


def set_fog_enabled(map_id, enabled):
    def change(state):
        state["enabled"] = bool(enabled)
    return _update_reveal(map_id, change)


def add_reveal(map_id, shape, **vals):
    """Lifts the fog over a circle (x, y, r) or a rectangle (x, y, w, h)."""
    if shape == "circle":
        area = {"shape": "circle", "x": _num(vals.get("x"), "x", -_MAX_COORD, _MAX_COORD),
                "y": _num(vals.get("y"), "y", -_MAX_COORD, _MAX_COORD),
                "r": _num(vals.get("r"), "radius", 1, 5000)}
    elif shape == "rect":
        area = {"shape": "rect", "x": _num(vals.get("x"), "x", -_MAX_COORD, _MAX_COORD),
                "y": _num(vals.get("y"), "y", -_MAX_COORD, _MAX_COORD),
                "w": _num(vals.get("w"), "width", 1, 10 * _MAX_COORD),
                "h": _num(vals.get("h"), "height", 1, 10 * _MAX_COORD)}
    else:
        raise ValueError("shape must be 'circle' or 'rect'.")

    def change(state):
        if len(state["areas"]) >= MAX_REVEAL_AREAS:
            raise ValueError(f"That's the most revealed areas a map can hold ({MAX_REVEAL_AREAS}) - re-fog to start over.")
        state["areas"].append(area)
    return _update_reveal(map_id, change)


def undo_reveal(map_id):
    def change(state):
        if state["areas"]:
            state["areas"].pop()
    return _update_reveal(map_id, change)


def clear_reveal(map_id):
    """Re-fogs the whole map (keeps fog switched on/off as it was)."""
    def change(state):
        state["areas"] = []
    return _update_reveal(map_id, change)
