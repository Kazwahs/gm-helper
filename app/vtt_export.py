"""Builds the Universal VTT (.dd2vtt) document for a saved map.

Inked dungeons, buildings and caves get real walls and doors, worked out by
regenerating the map from its saved seed (the generators are deterministic)
and checking that it matches the saved drawing. Anything else that has a
square grid (city maps, classic-style maps, hand-edited maps that no longer
match) is exported as an image on the right grid with no walls, and says so.
"""
import json
import re

from . import room_keys
from .maps import generate_map
from .maps.generator import grid_px
from .maps import vtt

WALLED_TYPES = ("dungeon", "interior", "underground")
GRID_TYPES = ("dungeon", "interior", "underground", "urban")
_VIEWBOX = re.compile(r'viewBox="\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*"')


class VttError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def supported(map_type):
    return map_type in GRID_TYPES


def _size(svg):
    m = _VIEWBOX.search(svg or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def _room_centres(svg):
    out = []
    for match in room_keys._TAG.finditer(svg or ""):
        attrs = dict(room_keys._ATTR.findall(match.group(1)))
        if attrs.get("data-kind") == "room":
            out.append((attrs.get("data-cx"), attrs.get("data-cy")))
    return sorted(out)


def _matches(saved, fresh, map_type):
    if _size(saved) != _size(fresh):
        return False
    if map_type in ("dungeon", "interior"):
        return _room_centres(saved) == _room_centres(fresh)
    return True


def _walled_geometry(m):
    """Walls and doors by regenerating the map, or None if that isn't possible
    or the saved drawing no longer matches."""
    if m["map_type"] not in WALLED_TYPES or m.get("seed") is None:
        return None
    params = dict(m.get("params") or {})
    if params.get("style", "ink") != "ink":
        return None
    try:
        fresh = generate_map(m["map_type"], m["genre"], params, m["seed"])
    except Exception:  # an old map whose settings no longer generate
        return None
    geometry = fresh.get("vtt")
    if not geometry or not _matches(m["svg"], fresh["svg"], m["map_type"]):
        return None
    return geometry


def export_info(m, pixels_per_grid=vtt.DEFAULT_PIXELS_PER_GRID):
    """Everything the browser needs: the document (without its image), the
    pixel size the image must be rendered at, and a note on what is included."""
    if not supported(m["map_type"]):
        raise VttError("This kind of map has no square grid to export.")
    ppg = vtt.clamp_ppg(pixels_per_grid)
    geometry = _walled_geometry(m)
    walls = geometry is not None
    if geometry is None:
        size = _size(m["svg"])
        if not size:
            raise VttError("Couldn't read this map's size.")
        square = grid_px(m["map_type"])
        geometry = {"cols": max(1, round(size[0] / square)), "rows": max(1, round(size[1] / square)),
                    "walls": [], "portals": []}
    doc = vtt.uvtt_document(geometry, ppg)
    if walls:
        doors = len(geometry["portals"])
        note = (f"{len(geometry['walls'])} wall outlines and {doors} door{'s' if doors != 1 else ''}."
                + (" Secret doors are walls for the players." if doors else ""))
    elif m["map_type"] in WALLED_TYPES:
        note = ("Image and grid only: this map's walls can't be worked out (it is a classic-style map, "
                "was saved without its seed, or has been changed since it was generated).")
    else:
        note = "Image and grid only: city maps have no wall data."
    slug = re.sub(r"[^A-Za-z0-9]+", "-", m["title"]).strip("-").lower() or "map"
    return {
        "document": doc,
        "walls": walls,
        "note": note,
        "filename": f"{slug}.dd2vtt",
        "image_width": geometry["cols"] * ppg,
        "image_height": geometry["rows"] * ppg,
    }
