"""Handouts: a letter, a rumour, a map fragment or a picture the GM can put on
the players' screen (the map's player view) and take down again.

Handouts belong to a game, so one can be shown on any of that game's maps. Which
handout a map is showing is stored on the map (maps.shown_handout_id). Showing,
hiding or editing a shown handout bumps maps.live_v so the player page, which
polls /api/maps/{id}/live, picks it up within a couple of seconds.

Images are checked by their first bytes (PNG, JPEG, GIF or WebP only - never SVG,
which can carry script) and capped in size. Nothing here is access-controlled
beyond the app's own password: anyone who can open the player view can see the
handout currently shown.
"""
from . import database

MAX_TITLE = 80
MAX_BODY = 6000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PER_GAME = 100


def _title(text):
    text = " ".join((text or "").split())[:MAX_TITLE]
    if not text:
        raise ValueError("Give the handout a title.")
    return text


def sniff_image(data):
    """The mime type of PNG/JPEG/GIF/WebP bytes, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _count(game_key):
    with database.get_cursor() as cur:
        return cur.execute("SELECT COUNT(*) AS n FROM handouts WHERE game_key = ?", (game_key,)).fetchone()["n"]


def _check_room(game_key):
    if _count(game_key) >= MAX_PER_GAME:
        raise ValueError(f"That's the most handouts a game can hold ({MAX_PER_GAME}) - delete some first.")


def create_text(game_key, title, body):
    title = _title(title)
    body = (body or "").replace("\r\n", "\n").strip()
    if not body:
        raise ValueError("Write the handout's text.")
    if len(body) > MAX_BODY:
        raise ValueError(f"That's too long for a handout ({MAX_BODY} characters at most).")
    _check_room(game_key)
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO handouts (game_key, title, kind, body) VALUES (?, ?, 'text', ?)",
                    (game_key, title, body))
        return cur.lastrowid


def create_image(game_key, title, data):
    if not data:
        raise ValueError("Choose an image file.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"That image is too big (the limit is {MAX_IMAGE_BYTES // (1024 * 1024)} MB).")
    mime = sniff_image(data)
    if not mime:
        raise ValueError("That doesn't look like a PNG, JPEG, GIF or WebP image.")
    title = _title(title)
    _check_room(game_key)
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO handouts (game_key, title, kind, image, mime) VALUES (?, ?, 'image', ?, ?)",
                    (game_key, title, data, mime))
        return cur.lastrowid


def _public(row):
    return {"id": row["id"], "game_key": row["game_key"], "title": row["title"], "kind": row["kind"],
            "body": row["body"] or "", "image_url": f"/handouts/{row['id']}/image" if row["kind"] == "image" else None}


def get_handout(handout_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT id, game_key, title, kind, body FROM handouts WHERE id = ?", (handout_id,)).fetchone()
    return _public(row) if row else None


def get_image(handout_id):
    """(bytes, mime) or None."""
    with database.get_cursor() as cur:
        row = cur.execute("SELECT image, mime FROM handouts WHERE id = ? AND kind = 'image'", (handout_id,)).fetchone()
    return (bytes(row["image"]), row["mime"]) if row and row["image"] else None


def list_handouts(game_key):
    """Newest first, each with the ids of the maps currently showing it."""
    with database.get_cursor() as cur:
        rows = cur.execute("SELECT id, game_key, title, kind, body FROM handouts WHERE game_key = ? "
                           "ORDER BY id DESC", (game_key,)).fetchall()
        shown = cur.execute("SELECT id, shown_handout_id FROM maps WHERE shown_handout_id IS NOT NULL").fetchall()
    on_maps = {}
    for s in shown:
        on_maps.setdefault(s["shown_handout_id"], []).append(s["id"])
    out = []
    for r in rows:
        item = _public(r)
        item["shown_on"] = on_maps.get(r["id"], [])
        out.append(item)
    return out


def _bump_maps_showing(cur, handout_id):
    cur.execute("UPDATE maps SET live_v = live_v + 1 WHERE shown_handout_id = ?", (handout_id,))


def update_handout(handout_id, field, value):
    current = get_handout(handout_id)
    if not current:
        raise ValueError("That handout doesn't exist any more.")
    if field == "title":
        value = _title(value)
    elif field == "body" and current["kind"] == "text":
        value = (value or "").replace("\r\n", "\n").strip()
        if not value:
            raise ValueError("Write the handout's text.")
        if len(value) > MAX_BODY:
            raise ValueError(f"That's too long for a handout ({MAX_BODY} characters at most).")
    else:
        raise ValueError("That can't be changed.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE handouts SET {field} = ? WHERE id = ?", (value, handout_id))
        _bump_maps_showing(cur, handout_id)


def delete_handout(handout_id):
    with database.get_cursor(commit=True) as cur:
        _bump_maps_showing(cur, handout_id)
        cur.execute("UPDATE maps SET shown_handout_id = NULL WHERE shown_handout_id = ?", (handout_id,))
        cur.execute("DELETE FROM handouts WHERE id = ?", (handout_id,))


def show_on_map(map_id, handout_id):
    with database.get_cursor() as cur:
        m = cur.execute("SELECT game_key FROM maps WHERE id = ?", (map_id,)).fetchone()
        h = cur.execute("SELECT game_key FROM handouts WHERE id = ?", (handout_id,)).fetchone()
    if not m:
        raise ValueError("Unknown map.")
    if not h or h["game_key"] != m["game_key"]:
        raise ValueError("That handout doesn't exist any more.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET shown_handout_id = ?, live_v = live_v + 1 WHERE id = ?", (handout_id, map_id))


def hide_on_map(map_id):
    with database.get_cursor() as cur:
        if not cur.execute("SELECT id FROM maps WHERE id = ?", (map_id,)).fetchone():
            raise ValueError("Unknown map.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET shown_handout_id = NULL, live_v = live_v + 1 WHERE id = ?", (map_id,))


def shown_on_map(map_id):
    """The handout this map's player view is showing (public fields), or None."""
    with database.get_cursor() as cur:
        row = cur.execute("SELECT shown_handout_id FROM maps WHERE id = ?", (map_id,)).fetchone()
    if not row or not row["shown_handout_id"]:
        return None
    return get_handout(row["shown_handout_id"])
