"""Everything the map's player view needs in one poll: fog, visible tokens and
the handout being shown, plus one version string that changes whenever any of
them does (so the page only redraws when something actually changed)."""
import re

from . import database, handouts, map_tokens, maps_store
from .maps import generator as map_generator

_VIEWBOX = re.compile(r'<svg\b[^>]*?\bviewBox\s*=\s*"\s*([-\d.eE+]+)[\s,]+([-\d.eE+]+)[\s,]+([-\d.eE+]+)[\s,]+([-\d.eE+]+)\s*"', re.S)
_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)
_SIZE_ATTR = re.compile(r'\b(width|height)\s*=\s*"\s*([\d.]+)')


_GM_ONLY_OPEN = re.compile(r'<g\b[^>]*\bclass\s*=\s*"[^"]*\bgm-only\b[^"]*"[^>]*>', re.S)
_G_TAG = re.compile(r"<(/?)g\b[^>]*?(/?)>", re.S)


def strip_gm_only(svg):
    """The map SVG with every GM-only group (secret-door markers and the like) cut out.
    The GM's own second-screen view hides those with CSS, which is fine for a screen
    you control - but a signed-in player is sent the page itself, so for them the
    markup is removed before it leaves the server."""
    out, pos = [], 0
    svg = svg or ""
    while True:
        m = _GM_ONLY_OPEN.search(svg, pos)
        if not m:
            out.append(svg[pos:])
            break
        out.append(svg[pos:m.start()])
        if m.group(0).rstrip().endswith("/>"):
            pos = m.end()
            continue
        depth, pos = 1, len(svg)
        for tag in _G_TAG.finditer(svg, m.end()):
            if tag.group(2) == "/":          # a self-closing <g/> doesn't open anything
                continue
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                pos = tag.end()
                break
    return "".join(out)


def svg_size(svg):
    """(width, height) of a map SVG from its viewBox, else its width/height, else 1000x1000."""
    m = _VIEWBOX.search(svg or "")
    if m:
        try:
            w, h = float(m.group(3)), float(m.group(4))
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass
    tag = _SVG_TAG.search((svg or "")[:4000])
    sizes = {}
    for k, v in _SIZE_ATTR.findall(tag.group(0) if tag else ""):
        try:
            sizes[k] = float(v)
        except ValueError:
            pass
    if sizes.get("width") and sizes.get("height"):
        return sizes["width"], sizes["height"]
    return 1000.0, 1000.0


def token_radius(m):
    """Half a grid square on maps that have one (so a token sits on a square
    like a miniature), else a small fraction of the map's width."""
    if m["map_type"] in map_generator.GRID_PX:
        return round(map_generator.grid_px(m["map_type"]) * 0.42, 1)
    width, height = svg_size(m["svg"])
    return round(max(width, height) / 75, 1)


def live_state(map_id, viewer_id=None):
    """The player view's state, or None for an unknown map. Hidden tokens are
    never included. viewer_id (a signed-in player) marks the tokens that person may
    move with "mine": true - everyone else's tokens look the same as before."""
    m = maps_store.get_map(map_id)
    if not m:
        return None
    from . import token_combat  # imported here: token_combat itself needs this module

    reveal = maps_store.get_reveal(map_id)
    with database.get_cursor() as cur:
        v = cur.execute("SELECT live_v FROM maps WHERE id = ?", (map_id,)).fetchone()["live_v"]
    # tokens linked to the initiative tracker show whose turn it is and who is down
    visible = token_combat.decorate(map_id, map_tokens.list_tokens(map_id, include_hidden=False),
                                    m["game_key"], for_players=True)
    combat_v = token_combat.version_part(map_id, m["game_key"])
    return {
        "v": f"{reveal['v']}.{v}" + (f".{combat_v}" if combat_v else ""),
        "reveal": reveal,
        "tokens": [dict({k: t[k] for k in ("id", "label", "x", "y", "color", "size", "current", "down") if k in t},
                        **({"mine": True} if viewer_id and t.get("user_id") == viewer_id else {}))
                   for t in visible],
        "token_radius": token_radius(m),
        "handout": handouts.shown_on_map(map_id),
    }
