"""Cave/tunnel generator, now with three structurally distinct floor-plan
archetypes rather than always the same cellular-automata blob:

- cavern: the original cellular-automata noise-and-smooth algorithm -
  wide, organic open caverns.
- tunnels: a drunkard's-walk digger that carves a winding 1-2 wide
  passage network with occasional small chamber bulges - reads as a
  proper mine/tunnel system rather than one big open cave.
- chambers: a handful of distinct circular chambers (via the same
  minimum-spanning-tree connector rooms.py uses) joined by carved
  tunnels - reads as a series of "rooms" rather than one contiguous
  cavern.

Whichever layout runs, disconnected pockets still get pruned or tunneled
into the main area so the whole thing is reachable, and the result is
rendered as overlapping circles under an SVG 'goo' filter so the outline
comes out smooth and blobby instead of a blocky grid. A handful of small
hand-drawn object icons (bones, fungus, crystal clusters, etc.) get
scattered through open chambers alongside the existing text-only feature
markers, to flesh the map out the same way rooms.py/terrain.py do."""
import math
from collections import deque

from . import content, objects, svg
from .shapes import minimum_spanning_tree

CELL = 13

_SIZE_PRESETS = {
    "small":  {"w": 38, "h": 28},
    "medium": {"w": 52, "h": 38},
    "large":  {"w": 66, "h": 48},
}

_STYLE_BY_GENRE = {
    "fantasy":  {"bg": "#0e0a08", "floor": "#5a4a38", "text": "#e8dcc0", "object": "#c9a66b", "title": "Cave System"},
    "modern":   {"bg": "#0c0c0e", "floor": "#4a4d52", "text": "#dfe3e6", "object": "#8fa0ac", "title": "Tunnel Network"},
    "hightech": {"bg": "#060a10", "floor": "#2c4a52", "text": "#bfe8f0", "object": "#5fd6c8", "title": "Access Tunnels"},
}

_LAYOUT_WEIGHTS = [("cavern", 0.4), ("tunnels", 0.3), ("chambers", 0.3)]
_LAYOUT_LABELS = {"cavern": "", "tunnels": " (tunnels)", "chambers": " (chambers)"}


def _choose_layout(rng):
    r = rng.random()
    acc = 0.0
    for name, weight in _LAYOUT_WEIGHTS:
        acc += weight
        if r <= acc:
            return name
    return _LAYOUT_WEIGHTS[-1][0]


def _neighbor_wall_count(grid, x, y, w, h):
    count = 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h or grid[ny][nx]:
                count += 1
    return count


def _cavern_grid(rng, w, h):
    grid = [[rng.random() < 0.45 or x in (0, w - 1) or y in (0, h - 1) for x in range(w)] for y in range(h)]
    for _ in range(5):
        new_grid = [[False] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                if x in (0, w - 1) or y in (0, h - 1):
                    new_grid[y][x] = True
                    continue
                n = _neighbor_wall_count(grid, x, y, w, h)
                if n > 4:
                    new_grid[y][x] = True
                elif n < 4:
                    new_grid[y][x] = False
                else:
                    new_grid[y][x] = grid[y][x]
        grid = new_grid
    return grid


def _carve_blob(grid, x, y, radius, w, h):
    """Carves a small circular patch of floor, staying clear of the
    guaranteed-wall border. Returns how many new floor cells this added,
    so callers can track progress without re-scanning the whole grid."""
    added = 0
    for oy in range(-radius, radius + 1):
        for ox in range(-radius, radius + 1):
            if ox * ox + oy * oy > radius * radius + 1:
                continue
            nx, ny = x + ox, y + oy
            if 1 <= nx < w - 1 and 1 <= ny < h - 1 and grid[ny][nx]:
                grid[ny][nx] = False
                added += 1
    return added


def _tunnels_grid(rng, w, h):
    grid = [[True] * w for _ in range(h)]
    directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    cx, cy = w // 2, h // 2
    floor_count = _carve_blob(grid, cx, cy, 2, w, h)
    walkers = [(cx, cy, rng.choice(directions))]
    target_floor = int(w * h * rng.uniform(0.28, 0.38))
    max_steps = w * h * 4
    steps = 0
    while floor_count < target_floor and steps < max_steps:
        steps += 1
        idx = rng.randrange(len(walkers))
        x, y, d = walkers[idx]
        if rng.random() < 0.22:
            d = rng.choice(directions)
        nx, ny = x + d[0], y + d[1]
        if not (2 <= nx < w - 2 and 2 <= ny < h - 2):
            d = rng.choice(directions)
            nx, ny = x + d[0], y + d[1]
            if not (2 <= nx < w - 2 and 2 <= ny < h - 2):
                continue
        radius = 2 if rng.random() < 0.12 else 1  # occasional small chamber bulge
        floor_count += _carve_blob(grid, nx, ny, radius, w, h)
        walkers[idx] = (nx, ny, d)
        if rng.random() < 0.03 and len(walkers) < 4:
            walkers.append((nx, ny, rng.choice(directions)))
    return grid


def _chambers_grid(rng, w, h):
    grid = [[True] * w for _ in range(h)]
    n_chambers = rng.randint(5, 9)
    centers = []
    attempts = 0
    while len(centers) < n_chambers and attempts < n_chambers * 25:
        attempts += 1
        radius = rng.uniform(2.5, 5.5)
        cx = rng.uniform(radius + 2, w - radius - 2)
        cy = rng.uniform(radius + 2, h - radius - 2)
        if cx <= radius + 2 or cy <= radius + 2:
            continue
        if all(math.hypot(cx - ex, cy - ey) > (radius + er) * 0.9 for ex, ey, er in centers):
            centers.append((cx, cy, radius))
    for cx, cy, radius in centers:
        r = int(math.ceil(radius))
        _carve_blob(grid, int(cx), int(cy), r, w, h)
    if len(centers) >= 2:
        pts = [(cx, cy) for cx, cy, _ in centers]
        edges = minimum_spanning_tree(pts)
        for i, j in edges:
            _carve_line(grid, int(pts[i][0]), int(pts[i][1]), int(pts[j][0]), int(pts[j][1]), w, h)
    elif not centers:
        _carve_blob(grid, w // 2, h // 2, 4, w, h)
    return grid


def _flood_fill_regions(grid, w, h):
    seen = [[False] * w for _ in range(h)]
    regions = []
    for y in range(h):
        for x in range(w):
            if grid[y][x] or seen[y][x]:
                continue
            region = []
            q = deque([(x, y)])
            seen[y][x] = True
            while q:
                cx, cy = q.popleft()
                region.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and not grid[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            regions.append(region)
    return regions


def _carve_line(grid, x1, y1, x2, y2, w, h):
    # Simple Bresenham-ish line, carved 1-2 cells wide.
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx - dy
    x, y = x1, y1
    while True:
        for ox, oy in ((0, 0), (1, 0), (0, 1)):
            nx, ny = x + ox, y + oy
            if 0 <= nx < w and 0 <= ny < h:
                grid[ny][nx] = False
        if x == x2 and y == y2:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy


def generate(rng, genre, params):
    preset = _SIZE_PRESETS.get(params.get("size", "medium"), _SIZE_PRESETS["medium"])
    w, h = preset["w"], preset["h"]
    style_genre = genre if genre != "mixed" else rng.choice(content.GENRES)
    style = _STYLE_BY_GENRE[style_genre]

    layout = _choose_layout(rng)
    if layout == "tunnels":
        grid = _tunnels_grid(rng, w, h)
    elif layout == "chambers":
        grid = _chambers_grid(rng, w, h)
    else:
        grid = _cavern_grid(rng, w, h)

    regions = sorted(_flood_fill_regions(grid, w, h), key=len, reverse=True)
    if not regions:
        # Degenerate roll (all walls) - carve a small guaranteed room so the
        # map is never completely blank.
        cx, cy = w // 2, h // 2
        for y in range(cy - 3, cy + 3):
            for x in range(cx - 4, cx + 4):
                grid[y][x] = False
        regions = _flood_fill_regions(grid, w, h)

    main_region = regions[0]
    main_set = set(main_region)

    def centroid(region):
        xs = [p[0] for p in region]
        ys = [p[1] for p in region]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    for region in regions[1:]:
        if len(region) < 6:
            for (x, y) in region:
                grid[y][x] = True  # prune tiny noise pockets
            continue
        rcx, rcy = centroid(region)
        mcx, mcy = centroid(main_region)
        _carve_line(grid, int(rcx), int(rcy), int(mcx), int(mcy), w, h)
        main_set |= set(region)

    floor_cells = [(x, y) for y in range(h) for x in range(w) if not grid[y][x]]

    # Feature markers: sample a handful of floor cells that have plenty of
    # open neighbors (i.e. sit inside a wider chamber, not a narrow tunnel).
    def openness(x, y):
        return sum(
            1 for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
            if 0 <= x + dx < w and 0 <= y + dy < h and not grid[y + dy][x + dx]
        )

    chamber_candidates = [(x, y) for (x, y) in floor_cells if openness(x, y) > 16]
    rng.shuffle(chamber_candidates)
    features = []
    min_dist = max(w, h) / 6
    for (x, y) in chamber_candidates:
        if len(features) >= max(2, len(floor_cells) // 220):
            break
        if all((x - fx) ** 2 + (y - fy) ** 2 > min_dist ** 2 for fx, fy, _ in features):
            features.append((x, y, content.pick(rng, "cave_features", genre)))

    # Object icons: scattered on open-enough floor cells not already used by
    # a text feature marker, so the two systems don't stack on one spot.
    used_cells = {(fx, fy) for fx, fy, _ in features}
    object_candidates = [
        (x, y, (x + 0.5) * CELL, (y + 0.5) * CELL)
        for (x, y) in floor_cells
        if (x, y) not in used_cells and openness(x, y) > 6
    ]
    n_objects = max(2, len(floor_cells) // 130)
    object_min_dist = CELL * 2.4
    placed_objects = objects.place_scattered_objects(
        rng, object_candidates, genre, objects.CAVE_OBJECTS, n_objects, object_min_dist
    )

    width_px, height_px = w * CELL, h * CELL
    filter_id = "caveGoo"
    body = [svg.rect(0, 0, width_px, height_px, style["bg"])]

    circles = [
        svg.circle((x + 0.5) * CELL, (y + 0.5) * CELL, CELL * 0.72, style["floor"])
        for (x, y) in floor_cells
    ]
    body.append(svg.group("".join(circles), extra=f'filter="url(#{filter_id})"'))

    marker_parts = []
    for (x, y, feature) in features:
        px, py = (x + 0.5) * CELL, (y + 0.5) * CELL
        el_parts = [svg.circle(px, py, 3, style["text"], opacity=0.85, extra='class="map-el-fill"')]
        el_parts.append(svg.text(px + 6, py - 5, feature, size=8, fill=style["text"], style="italic", extra='class="map-el-label"'))
        marker_parts.append(svg.map_el("feature", px, py, "".join(el_parts), fill=style["text"]))
    body.append(svg.group("".join(marker_parts)))

    object_parts = []
    obj_scale = CELL * 0.55
    for obj in placed_objects:
        ocx, ocy = obj["cx"], obj["cy"]
        icon_body = objects.render_icon(obj["icon"], ocx, ocy, obj_scale, style["object"])
        label_body = svg.text(ocx, ocy + obj_scale + 9, obj["label"], size=7.5, fill=style["text"],
                               anchor="middle", extra='class="map-el-label"')
        object_parts.append(svg.map_el("object", ocx, ocy, icon_body + label_body, fill=style["object"]))
    body.append(svg.group("".join(object_parts)))

    layout_label = _LAYOUT_LABELS[layout]
    title = f"{style['title']} - {content.GENRE_LABELS[genre]}{layout_label}"
    body.append(svg.text(10, height_px - 8, title, size=11, fill=style["text"], weight="bold"))

    doc = svg.svg_doc(width_px, height_px, "".join(body), extra_defs=svg.goo_filter(filter_id, std_dev=CELL * 0.35))
    return {
        "svg": doc,
        "meta": {
            "floor_cells": len(floor_cells), "features": len(features), "layout": layout,
            "objects": len(placed_objects), "size": params.get("size", "medium"),
        },
        "title_suggestion": title,
    }
