"""What to show on the home page under "Pick up where you left off":
the campaigns you touched last (with the next session's prep), your latest
maps, and your latest bookmarks."""
from . import campaigns, database, maps_store
from .library import games as games_module

LIMIT = 4


def _game_name(key):
    game = games_module.get_game(key)
    return game["name"] if game else key


def _next_session(campaign_id):
    """The lowest-numbered session not yet played, with how much of it is prepped."""
    open_sessions = [s for s in campaigns.list_sessions(campaign_id) if (s.get("status") or "planned") != "played"]
    if not open_sessions:
        return None
    s = min(open_sessions, key=lambda x: x["session_number"])
    start = (s.get("strong_start") or "").strip()
    return {
        "number": s["session_number"],
        "title": (s.get("title") or "").strip(),
        "strong_start": start if len(start) <= 140 else start[:137].rstrip() + "...",
        "prepped": {
            "strong_start": bool(start),
            "scenes": bool((s.get("scenes") or "").strip()),
            "secrets_clues": bool((s.get("secrets_clues") or "").strip()),
        },
    }


def recent_campaigns(limit=LIMIT):
    out = []
    for c in campaigns.list_campaigns()[:limit]:
        sessions = campaigns.list_sessions(c["id"])
        out.append({
            "id": c["id"],
            "title": c["title"],
            "status": c.get("status") or "planning",
            "game_key": c["game_key"],
            "game_name": _game_name(c["game_key"]),
            "sessions_played": sum(1 for s in sessions if s.get("status") == "played"),
            "next_session": _next_session(c["id"]),
        })
    return out


def recent_maps(limit=LIMIT):
    out = []
    for m in maps_store.list_maps()[:limit]:
        out.append({"id": m["id"], "title": m["title"], "map_type": m["map_type"],
                    "game_name": _game_name(m["game_key"])})
    return out


def recent_bookmarks(limit=5):
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT bm.id, bm.page_number, bm.note, b.id AS book_id, b.title AS book_title
               FROM bookmarks bm JOIN books b ON b.id = bm.book_id
               ORDER BY bm.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def home_activity():
    data = {"campaigns": recent_campaigns(), "maps": recent_maps(), "bookmarks": recent_bookmarks()}
    data["any"] = any(data[k] for k in ("campaigns", "maps", "bookmarks"))
    return data
