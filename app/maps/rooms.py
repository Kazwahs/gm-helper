"""Room + corridor floor-plan generator, shared by the 'dungeon' and
'interior' map types - they're the same underlying algorithm (recursive
subdivision into rooms, connected by a minimum spanning tree of corridors,
with a few extra loop connections) with different color palettes and corner
styling, and genre-flavored room names/features either way. A "high-tech
dungeon" or a "fantasy building interior" are both meant to be plausible."""
import math

from . import content, svg
from .shapes import bsp_partition, minimum_spanning_tree

PX = 16  # pixels per grid unit

_SIZE_PRESETS = {
    "small":  {"w": 42, "h": 30, "depth": 3, "min_size": 7},
    "medium": {"w": 58, "h": 40, "depth": 4, "min_size": 7},
    "large":  {"w": 74, "h": 52, "depth": 5, "min_size": 7},
}

_PALETTES = {
    "dungeon": {
        "bg": "#2a2013", "room_fill": "#e8dcc0", "room_stroke": "#4a3a20",
        "corridor": "#c9b98a", "text": "#2a2013", "feature_text": "#5a4a2a",
        "title": "#e8dcc0", "grid": "#3a2d18",
    },
    "interior": {
        "bg": "#1c2430", "room_fill": "#eef2f7", "room_stroke": "#2c3e50",
        "corridor": "#b8c4d0", "text": "#1c2430", "feature_text": "#4a5a6a",
        "title": "#eef2f7", "grid": "#28323f",
    },
}


def generate(rng, genre, style, params):
    preset = _SIZE_PRESETS.get(params.get("size", "medium"), _SIZE_PRESETS["medium"])
    grid_w, grid_h, depth, min_size = preset["w"], preset["h"], preset["depth"], preset["min_size"]
    pal = _PALETTES.get(style, _PALETTES["dungeon"])
    corner_radius = 2 if style == "dungeon" else 0

    leaves = bsp_partition(rng, 1, 1, grid_w - 2, grid_h - 2, depth, min_size)
    rooms = []
    for leaf in leaves:
        rw = leaf.w * rng.uniform(0.55, 0.82)
        rh = leaf.h * rng.uniform(0.55, 0.82)
        rw = max(rw, min(3.5, leaf.w))
        rh = max(rh, min(3.5, leaf.h))
        rx = leaf.x + rng.uniform(0, leaf.w - rw)
        ry = leaf.y + rng.uniform(0, leaf.h - rh)
        rooms.append({"x": rx, "y": ry, "w": rw, "h": rh})

    centers = [(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2) for r in rooms]
    mst_edges = minimum_spanning_tree(centers)
    edge_set = {tuple(sorted(e)) for e in mst_edges}

    # A few extra connections beyond the MST so the map isn't purely
    # tree-shaped (loops make for more interesting exploration).
    extra_budget = max(1, len(rooms) // 5)
    candidates = []
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            if (i, j) not in edge_set:
                candidates.append((math.dist(centers[i], centers[j]), i, j))
    candidates.sort()
    for _, i, j in candidates[: len(rooms)]:
        if extra_budget <= 0:
            break
        if rng.random() < 0.35:
            mst_edges.append((i, j))
            extra_budget -= 1

    # Assign labels/features.
    room_count = len(rooms)
    n_labeled = round(room_count * 0.75)
    labeled_idx = set(rng.sample(range(room_count), min(n_labeled, room_count)))
    names = content.pick_many(rng, "room_names", genre, len(labeled_idx), allow_repeats=(len(labeled_idx) > 12))
    name_iter = iter(names)
    for i, room in enumerate(rooms):
        room["number"] = i + 1
        room["feature"] = None
        if i in labeled_idx:
            room["label"] = next(name_iter)
            if rng.random() < 0.28:
                room["feature"] = content.pick(rng, "room_features", genre)
        else:
            room["label"] = None

    body = []
    width_px, height_px = grid_w * PX, grid_h * PX

    body.append(svg.rect(0, 0, width_px, height_px, pal["bg"]))
    # subtle grid paper texture
    grid_lines = []
    for gx in range(0, grid_w + 1, 4):
        grid_lines.append(svg.line(gx * PX, 0, gx * PX, height_px, pal["grid"], 0.5, opacity=0.5))
    for gy in range(0, grid_h + 1, 4):
        grid_lines.append(svg.line(0, gy * PX, width_px, gy * PX, pal["grid"], 0.5, opacity=0.5))
    body.append(svg.group("".join(grid_lines)))

    corridor_w = 1.1 * PX
    corridor_parts = []
    for i, j in mst_edges:
        x1, y1 = centers[i]
        x2, y2 = centers[j]
        x1, y1, x2, y2 = x1 * PX, y1 * PX, x2 * PX, y2 * PX
        if rng.random() < 0.5:
            mx, my = x2, y1
        else:
            mx, my = x1, y2
        corridor_parts.append(svg.line(x1, y1, mx, my, pal["corridor"], corridor_w))
        corridor_parts.append(svg.line(mx, my, x2, y2, pal["corridor"], corridor_w))
    body.append(svg.group("".join(corridor_parts)))

    room_parts = []
    for room in rooms:
        x, y, w, h = room["x"] * PX, room["y"] * PX, room["w"] * PX, room["h"] * PX
        cx, cy = x + w / 2, y + h / 2
        el_parts = [svg.rect(x, y, w, h, pal["room_fill"], pal["room_stroke"], 1.5, rx=corner_radius, extra='class="map-el-fill"')]
        el_parts.append(svg.text(x + 4, y + 11, str(room["number"]), size=8, fill=pal["feature_text"], weight="bold"))
        if room["label"]:
            label_y = cy if not room["feature"] else cy - 4
            el_parts.append(svg.text(cx, label_y, room["label"], size=9.5, fill=pal["text"], anchor="middle", weight="bold", extra='class="map-el-label"'))
            if room["feature"]:
                el_parts.append(svg.text(cx, cy + 9, room["feature"], size=7.5, fill=pal["feature_text"], anchor="middle", style="italic", extra='class="map-el-label"'))
        room_parts.append(svg.map_el("room", cx, cy, "".join(el_parts), fill=pal["room_fill"]))
    body.append(svg.group("".join(room_parts)))

    title = f"{'Dungeon' if style == 'dungeon' else 'Building Interior'} - {content.GENRE_LABELS[genre]}"
    body.append(svg.text(10, height_px - 8, title, size=11, fill=pal["title"], weight="bold"))

    doc = svg.svg_doc(width_px, height_px, "".join(body))
    return {
        "svg": doc,
        "meta": {"rooms": room_count, "size": params.get("size", "medium")},
        "title_suggestion": title,
    }
