"""Everything the map's player view needs in one poll: fog, visible tokens and
the handout being shown, plus one version string that changes whenever any of
them does (so the page only redraws when something actually changed)."""
import re

from . import database, handouts, map_tokens, maps_store
from .maps import generator as map_generator

_VIEWBOX = re.compile(r'<svg\b[^>]*?\bviewBox\s*=\s*"\s*([-\d.eE+]+)[\s,]+([-\d.eE+]+)[\s,]+([-\d.eE+]+)[\s,]+([-\d.eE+]+)\s*"', re.S)
_SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)
_SIZE_ATTR = re.compile(r'\b(width|height)\s*=\s*"\s*([\d.]+)')


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


def live_state(map_id):
    """The player view's state, or None for an unknown map. Hidden tokens are
    never included."""
    m = maps_store.get_map(map_id)
    if not m:
        return None
    reveal = maps_store.get_reveal(map_id)
    with database.get_cursor() as cur:
        v = cur.execute("SELECT live_v FROM maps WHERE id = ?", (map_id,)).fetchone()["live_v"]
    return {
        "v": f"{reveal['v']}.{v}",
        "reveal": reveal,
        "tokens": [{k: t[k] for k in ("id", "label", "x", "y", "color", "size")}
                   for t in map_tokens.list_tokens(map_id, include_hidden=False)],
        "token_radius": token_radius(m),
        "handout": handouts.shown_on_map(map_id),
    }
