"""Room + corridor floor-plan generator, shared by the 'dungeon' and
'interior' map types - they're the same underlying algorithm with different
color palettes and corner styling, and genre-flavored room names/features
either way. A "high-tech dungeon" or a "fantasy building interior" are both
meant to be plausible.

To avoid every generated map reading as the same "grid of rectangles
connected by L-corridors", each generation randomly picks one of three
structurally different layout archetypes:

- "warren" - the original recursive-subdivision approach: irregular rooms
  of varying size, connected by a minimum spanning tree plus a few extra
  loop connections. Reads as an organic, unplanned dungeon.
- "keep" - a regular grid of cells (occasionally merging two adjacent
  cells into one bigger room), connected along grid lines with some
  connections randomly dropped for a maze-like feel. Reads as a planned,
  architectural space - a keep, a facility, a temple complex.
- "hub" - one large central room with satellite rooms arranged around it
  in a ring, connected by straight spokes (plus occasional ring
  connections between neighbors). Reads as a rotunda, a reactor chamber,
  a wheel-shaped structure with a purpose at its center.

Each room also has a small chance of being drawn as a circular chamber
rather than a rectangle, for variety within a single map, and gets 0-4
genre-flavored prop objects (see objects.py) scaled to its size.
"""
import math

from . import content, objects, svg
from .shapes import bsp_partition, ensure_connected, minimum_spanning_tree

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
        "title": "#e8dcc0", "grid": "#3a2d18", "object": "#8a7248",
    },
    "interior": {
        "bg": "#1c2430", "room_fill": "#eef2f7", "room_stroke": "#2c3e50",
        "corridor": "#b8c4d0", "text": "#1c2430", "feature_text": "#4a5a6a",
        "title": "#eef2f7", "grid": "#28323f", "object": "#5a6a7a",
    },
}

_LAYOUT_WEIGHTS = [("warren", 0.45), ("keep", 0.3), ("hub", 0.25)]


def _choose_layout(rng):
    r = rng.random()
    acc = 0.0
    for name, weight in _LAYOUT_WEIGHTS:
        acc += weight
        if r <= acc:
            return name
    return _LAYOUT_WEIGHTS[-1][0]


def _maybe_circle(rng, w, h, chance=0.12, min_dim=3.5):
    return "circle" if (min(w, h) >= min_dim and rng.random() < chance) else "rect"


# ------------------------------------------------------------- layouts ---

def _warren_layout(rng, grid_w, grid_h, depth, min_size):
    leaves = bsp_partition(rng, 1, 1, grid_w - 2, grid_h - 2, depth, min_size)
    rooms = []
    for leaf in leaves:
        rw = leaf.w * rng.uniform(0.55, 0.82)
        rh = leaf.h * rng.uniform(0.55, 0.82)
        rw = max(rw, min(3.5, leaf.w))
        rh = max(rh, min(3.5, leaf.h))
        rx = leaf.x + rng.uniform(0, leaf.w - rw)
        ry = leaf.y + rng.uniform(0, leaf.h - rh)
        rooms.append({"x": rx, "y": ry, "w": rw, "h": rh, "shape": _maybe_circle(rng, rw, rh)})

    centers = [(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2) for r in rooms]
    edges = minimum_spanning_tree(centers)
    edge_set = {tuple(sorted(e)) for e in edges}

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
            edges.append((i, j))
            extra_budget -= 1

    return rooms, edges, "elbow"


def _keep_layout(rng, grid_w, grid_h):
    cols = rng.randint(3, 5)
    rows = rng.randint(2, 4)
    usable_w, usable_h = grid_w - 2, grid_h - 2
    cell_w, cell_h = usable_w / cols, usable_h / rows

    occupied = set()
    rooms = []
    cell_room_index = {}
    for r in range(rows):
        for c in range(cols):
            if (c, r) in occupied:
                continue
            span_w, span_h = 1, 1
            if rng.random() < 0.18 and c + 1 < cols and (c + 1, r) not in occupied:
                span_w = 2
            elif rng.random() < 0.18 and r + 1 < rows and (c, r + 1) not in occupied:
                span_h = 2
            x = 1 + c * cell_w + cell_w * 0.08
            y = 1 + r * cell_h + cell_h * 0.08
            w = cell_w * span_w * 0.84
            h = cell_h * span_h * 0.84
            idx = len(rooms)
            rooms.append({"x": x, "y": y, "w": w, "h": h, "shape": _maybe_circle(rng, w, h, chance=0.08)})
            for dc in range(span_w):
                for dr in range(span_h):
                    occupied.add((c + dc, r + dr))
                    cell_room_index[(c + dc, r + dr)] = idx

    edge_set = set()
    edges = []
    for r in range(rows):
        for c in range(cols):
            idx = cell_room_index.get((c, r))
            if idx is None:
                continue
            for dc, dr in ((1, 0), (0, 1)):
                nidx = cell_room_index.get((c + dc, r + dr))
                if nidx is not None and nidx != idx:
                    key = tuple(sorted((idx, nidx)))
                    if key not in edge_set and rng.random() < 0.78:
                        edges.append(key)
                        edge_set.add(key)

    centers = [(rm["x"] + rm["w"] / 2, rm["y"] + rm["h"] / 2) for rm in rooms]
    edges = ensure_connected(centers, edges)
    return rooms, edges, "straight"


def _hub_layout(rng, grid_w, grid_h, size_key):
    cx, cy = grid_w / 2, grid_h / 2
    span = min(grid_w, grid_h)
    hub_r = span * 0.15
    rooms = [{"x": cx - hub_r, "y": cy - hub_r, "w": hub_r * 2, "h": hub_r * 2, "shape": "circle"}]

    n_satellites = {"small": rng.randint(4, 6), "large": rng.randint(6, 9)}.get(size_key, rng.randint(5, 8))
    ring_r = span * 0.36
    angle_step = 2 * math.pi / n_satellites
    for i in range(n_satellites):
        angle = i * angle_step + rng.uniform(-0.15, 0.15)
        sx = cx + ring_r * math.cos(angle)
        sy = cy + ring_r * math.sin(angle)
        sw = ring_r * rng.uniform(0.34, 0.5)
        sh = ring_r * rng.uniform(0.3, 0.46)
        rooms.append({"x": sx - sw / 2, "y": sy - sh / 2, "w": sw, "h": sh,
                      "shape": _maybe_circle(rng, sw, sh, chance=0.1)})

    edges = [(0, i) for i in range(1, len(rooms))]
    for i in range(1, len(rooms)):
        j = i + 1 if i + 1 < len(rooms) else 1
        if j != i and rng.random() < 0.4:
            edges.append((i, j))
    return rooms, edges, "straight"


def generate(rng, genre, style, params):
    preset = _SIZE_PRESETS.get(params.get("size", "medium"), _SIZE_PRESETS["medium"])
    size_key = params.get("size", "medium")
    grid_w, grid_h, depth, min_size = preset["w"], preset["h"], preset["depth"], preset["min_size"]
    pal = _PALETTES.get(style, _PALETTES["dungeon"])

    layout = _choose_layout(rng)
    if layout == "keep":
        rooms, edges, corridor_style = _keep_layout(rng, grid_w, grid_h)
    elif layout == "hub":
        rooms, edges, corridor_style = _hub_layout(rng, grid_w, grid_h, size_key)
    else:
        rooms, edges, corridor_style = _warren_layout(rng, grid_w, grid_h, depth, min_size)

    centers = [(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2) for r in rooms]
    corner_radius = 2 if style == "dungeon" and layout == "warren" else 0

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
    grid_lines = []
    for gx in range(0, grid_w + 1, 4):
        grid_lines.append(svg.line(gx * PX, 0, gx * PX, height_px, pal["grid"], 0.5, opacity=0.5))
    for gy in range(0, grid_h + 1, 4):
        grid_lines.append(svg.line(0, gy * PX, width_px, gy * PX, pal["grid"], 0.5, opacity=0.5))
    body.append(svg.group("".join(grid_lines)))

    corridor_w = 1.1 * PX
    corridor_parts = []
    for i, j in edges:
        x1, y1 = centers[i]
        x2, y2 = centers[j]
        x1, y1, x2, y2 = x1 * PX, y1 * PX, x2 * PX, y2 * PX
        if corridor_style == "straight":
            corridor_parts.append(svg.line(x1, y1, x2, y2, pal["corridor"], corridor_w * 0.7))
        else:
            if rng.random() < 0.5:
                mx, my = x2, y1
            else:
                mx, my = x1, y2
            corridor_parts.append(svg.line(x1, y1, mx, my, pal["corridor"], corridor_w))
            corridor_parts.append(svg.line(mx, my, x2, y2, pal["corridor"], corridor_w))
    body.append(svg.group("".join(corridor_parts)))

    room_parts = []
    object_count = 0
    for room in rooms:
        x, y, w, h = room["x"] * PX, room["y"] * PX, room["w"] * PX, room["h"] * PX
        cx, cy = x + w / 2, y + h / 2
        if room["shape"] == "circle":
            radius = min(w, h) / 2
            el_parts = [svg.circle(cx, cy, radius, pal["room_fill"], pal["room_stroke"], 1.5, extra='class="map-el-fill"')]
            # A circle doesn't fill its own bounding box's top-left corner,
            # so the usual number position would land outside the room -
            # anchor it top-center of the circle instead, which is always
            # inside the fill.
            el_parts.append(svg.text(cx, cy - radius + 11, str(room["number"]), size=8, fill=pal["feature_text"],
                                      weight="bold", anchor="middle"))
        else:
            el_parts = [svg.rect(x, y, w, h, pal["room_fill"], pal["room_stroke"], 1.5, rx=corner_radius, extra='class="map-el-fill"')]
            el_parts.append(svg.text(x + 4, y + 11, str(room["number"]), size=8, fill=pal["feature_text"], weight="bold"))

        room_objects = objects.place_room_objects(rng, room, genre, pal["object"], PX)
        for obj in room_objects:
            el_parts.append(svg.map_el("object", obj["cx"], obj["cy"],
                                        objects.render_icon(obj["icon"], obj["cx"], obj["cy"], PX * 0.32, pal["object"]),
                                        fill=pal["object"]))
        object_count += len(room_objects)

        if room["label"]:
            label_y = cy if not room["feature"] else cy - 4
            el_parts.append(svg.text(cx, label_y, room["label"], size=9.5, fill=pal["text"], anchor="middle", weight="bold", extra='class="map-el-label"'))
            if room["feature"]:
                el_parts.append(svg.text(cx, cy + 9, room["feature"], size=7.5, fill=pal["feature_text"], anchor="middle", style="italic", extra='class="map-el-label"'))
        room_parts.append(svg.map_el("room", cx, cy, "".join(el_parts), fill=pal["room_fill"]))
    body.append(svg.group("".join(room_parts)))

    layout_label = {"warren": "", "keep": " (planned)", "hub": " (radial)"}[layout]
    title = f"{'Dungeon' if style == 'dungeon' else 'Building Interior'} - {content.GENRE_LABELS[genre]}{layout_label}"
    body.append(svg.text(10, height_px - 8, title, size=11, fill=pal["title"], weight="bold"))

    doc = svg.svg_doc(width_px, height_px, "".join(body))
    return {
        "svg": doc,
        "meta": {"rooms": room_count, "size": size_key, "layout": layout, "objects": object_count},
        "title_suggestion": title,
    }
