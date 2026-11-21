"""Builds the top navigation bar for every page: which links to show, which
one is the current page, which game you're in, and where each entry in the
game switcher should take you.

Kept in Python (not in the template) so the rules are easy to test.
"""
from urllib.parse import quote

from .library import games as games_module

GAME_COOKIE = "gm_game"

# Sections that have their own list page, in the order a "switch game" click
# should recognise them. Switching games while looking at one campaign or one
# map can't stay on that page (it belongs to the old game), so it lands on the
# section's list for the new game instead.
_SECTION_ROOTS = ("/library", "/campaigns", "/maps", "/screen")


def _valid_key(key):
    """A game key that actually exists (stale cookies and typos are ignored)."""
    if key and games_module.get_game(key):
        return key
    return None


def _under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def _section_root(path):
    for root in _SECTION_ROOTS:
        if _under(path, root):
            return root
    if path.startswith("/tools/"):
        name = path.split("/")[2] if len(path.split("/")) > 2 else ""
        if name:
            return "/tools/" + name
    return "/library"


def build_nav(path, game_key=None, remembered_key=None):
    """Everything base.html needs to draw the nav bar.

    game_key       - the game the page itself is about (from ?game= or the
                     campaign/map being viewed).
    remembered_key - the last game visited (a cookie), used only so Library and
                     Campaigns stay reachable from pages with no game of their
                     own, like Settings and Bookmarks.
    """
    page_key = _valid_key(game_key)
    key = page_key or _valid_key(remembered_key)
    game = games_module.get_game(key) if key else None
    q = f"?game={quote(key, safe='')}" if key else ""

    primary = [{"label": "Games", "href": "/", "active": path == "/"}]
    if key:
        primary.append({"label": "Library", "href": "/library" + q, "active": _under(path, "/library")})
        primary.append({"label": "Campaigns", "href": "/campaigns" + q, "active": _under(path, "/campaigns")})
    primary.append({"label": "Maps", "href": "/maps" + q, "active": _under(path, "/maps")})
    primary.append({"label": "GM Screen", "href": "/screen" + q, "active": _under(path, "/screen")})

    tools = [
        {"label": "Dice", "href": "/tools/dice" + q, "active": _under(path, "/tools/dice")},
        {"label": "Random Tables", "href": "/tools/tables" + q, "active": _under(path, "/tools/tables")},
    ]
    if key:
        tools += [
            {"label": "Initiative", "href": "/tools/initiative" + q, "active": _under(path, "/tools/initiative")},
            {"label": "NPCs", "href": "/tools/npc" + q, "active": _under(path, "/tools/npc")},
            {"label": "Encounters", "href": "/tools/encounters" + q, "active": _under(path, "/tools/encounters")},
            {"label": "One-Shot", "href": "/tools/oneshot" + q, "active": _under(path, "/tools/oneshot")},
        ]
    more = [
        {"label": "Bookmarks", "href": "/bookmarks", "active": _under(path, "/bookmarks")},
        {"label": "Manage Games", "href": "/games/manage", "active": _under(path, "/games/manage")},
        {"label": "Users", "href": "/users", "active": _under(path, "/users")},
        {"label": "Settings", "href": "/settings", "active": _under(path, "/settings")},
    ]

    root = _section_root(path)
    switcher = [
        {
            "key": g["key"],
            "name": g["name"],
            "href": f"{root}?game={quote(g['key'], safe='')}",
            "current": g["key"] == key,
        }
        for g in games_module.all_game_choices()
    ]

    return {
        "game_key": key,
        "game_name": game["name"] if game else None,
        "primary": primary,
        "tools": tools,
        "tools_active": any(i["active"] for i in tools),
        "more": more,
        "more_active": any(i["active"] for i in more),
        "games": switcher,
    }
