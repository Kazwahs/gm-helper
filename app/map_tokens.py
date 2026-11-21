"""Tokens on a map: a coloured marker per PC, monster or NPC that the GM drags
around while the player view follows. A token can be hidden (the GM sees it,
the player view never gets it) - handy for an ambush that hasn't sprung.

Positions are in the map SVG's own coordinates. Every change bumps maps.live_v
so the player page, polling /api/maps/{id}/live, redraws within a couple of seconds.
"""
import math
import re

from . import accounts, database

MAX_TOKENS = 60
MAX_LABEL = 24
DEFAULT_COLOR = "#c0863f"
MAX_SIZE = 4
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_COORD = 100_000


def _label(text):
    text = " ".join((text or "").split())[:MAX_LABEL]
    if not text:
        raise ValueError("Give the token a name.")
    return text


def _color(value):
    value = (value or "").strip() or DEFAULT_COLOR
    if not _COLOR_RE.match(value):
        raise ValueError("Pick a colour like #c0863f.")
    return value.lower()


def _size(value):
    try:
        size = 1 if value in (None, "") else int(value)
    except (TypeError, ValueError):
        raise ValueError("Size must be 1 to %d." % MAX_SIZE)
    if not 1 <= size <= MAX_SIZE:
        raise ValueError("Size must be 1 to %d." % MAX_SIZE)
    return size


def _coord(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.")
    if not math.isfinite(number) or abs(number) > _COORD:
        raise ValueError(f"{name} is out of range.")
    return round(number, 1)


def _flag(value):
    return 1 if str(value).strip().lower() in ("1", "true", "on", "yes") else 0


def _require_map(cur, map_id):
    if not cur.execute("SELECT id FROM maps WHERE id = ?", (map_id,)).fetchone():
        raise ValueError("Unknown map.")


def _bump(cur, map_id):
    cur.execute("UPDATE maps SET live_v = live_v + 1 WHERE id = ?", (map_id,))


def _row(r):
    return {"id": r["id"], "label": r["label"], "x": r["x"], "y": r["y"], "color": r["color"],
            "size": r["size"], "hidden": bool(r["hidden"]), "user_id": r["user_id"]}


def list_tokens(map_id, include_hidden=True):
    sql = "SELECT * FROM map_tokens WHERE map_id = ?" + ("" if include_hidden else " AND hidden = 0") + " ORDER BY id"
    with database.get_cursor() as cur:
        return [_row(r) for r in cur.execute(sql, (map_id,)).fetchall()]


def get_token(token_id):
    with database.get_cursor() as cur:
        r = cur.execute("SELECT * FROM map_tokens WHERE id = ?", (token_id,)).fetchone()
    return _row(r) if r else None


def add_token(map_id, label, x, y, color=DEFAULT_COLOR, size=1, hidden=False):
    label, color, size = _label(label), _color(color), _size(size)
    x, y = _coord(x, "x"), _coord(y, "y")
    with database.get_cursor() as cur:
        _require_map(cur, map_id)
        count = cur.execute("SELECT COUNT(*) AS n FROM map_tokens WHERE map_id = ?", (map_id,)).fetchone()["n"]
    if count >= MAX_TOKENS:
        raise ValueError(f"That's the most tokens a map can hold ({MAX_TOKENS}) - remove some first.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO map_tokens (map_id, label, x, y, color, size, hidden) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (map_id, label, x, y, color, size, _flag(hidden)))
        token_id = cur.lastrowid
        _bump(cur, map_id)
    return token_id


def move_token(token_id, x, y):
    x, y = _coord(x, "x"), _coord(y, "y")
    token = _owner(token_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE map_tokens SET x = ?, y = ? WHERE id = ?", (x, y, token_id))
        _bump(cur, token["map_id"])


def _owner(token_id):
    with database.get_cursor() as cur:
        r = cur.execute("SELECT id, map_id FROM map_tokens WHERE id = ?", (token_id,)).fetchone()
    if not r:
        raise ValueError("That token isn't on the map any more.")
    return r


def _player_for(map_id, value):
    """The user id a token is being handed to: blank for nobody, otherwise a player
    who belongs to the campaign this map is attached to."""
    text = str(value).strip()
    if not text:
        return None
    try:
        user_id = int(text)
    except ValueError:
        raise ValueError("Pick one of the campaign's players.")
    with database.get_cursor() as cur:
        row = cur.execute("SELECT campaign_id FROM maps WHERE id = ?", (map_id,)).fetchone()
    if not row or not row["campaign_id"]:
        raise ValueError("Attach this map to a campaign first, then pick one of its players.")
    if not accounts.is_member(user_id, row["campaign_id"]):
        raise ValueError("Pick one of the campaign's players.")
    return user_id


def update_token(token_id, field, value):
    cleaners = {"label": _label, "color": _color, "size": _size, "hidden": _flag}
    token = None
    if field == "user_id":
        token = _owner(token_id)
        value = _player_for(token["map_id"], value)
    elif field not in cleaners:
        raise ValueError("That can't be changed.")
    else:
        value = cleaners[field](value)
    token = token or _owner(token_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE map_tokens SET {field} = ? WHERE id = ?", (value, token_id))
        _bump(cur, token["map_id"])


def delete_token(token_id):
    with database.get_cursor() as cur:
        r = cur.execute("SELECT map_id FROM map_tokens WHERE id = ?", (token_id,)).fetchone()
    if not r:
        return
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM map_tokens WHERE id = ?", (token_id,))
        _bump(cur, r["map_id"])


def clear_tokens(map_id):
    with database.get_cursor(commit=True) as cur:
        _require_map(cur, map_id)
        cur.execute("DELETE FROM map_tokens WHERE map_id = ?", (map_id,))
        _bump(cur, map_id)
