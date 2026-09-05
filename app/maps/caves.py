"""Cellular-automata cave/tunnel generator. The classic 4-5 rule carves an
organic floor plan out of noise; disconnected pockets get pruned or
tunneled into the main cavern so the whole thing is reachable. Rendered as
overlapping circles under an SVG 'goo' filter so the outline comes out
smooth and blobby instead of a blocky grid."""
from collections import deque

from . import content, svg

CELL = 13

_SIZE_PRESETS = {
    "small":  {"w": 38, "h": 28},
    "medium": {"w": 52, "h": 38},
    "large":  {"w": 66, "h": 48},
}

_STYLE_BY_GENRE = {
    "fantasy":  {"bg": "#0e0a08", "floor": "#5a4a38", "text": "#e8dcc0", "title": "Cave System"},
    "modern":   {"bg": "#0c0c0e", "floor": "#4a4d52", "text": "#dfe3e6", "title": "Tunnel Network"},
    "hightech": {"bg": "#060a10", "floor": "#2c4a52", "text": "#bfe8f0", "title": "Access Tunnels"},
}


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

    title = f"{style['title']} - {content.GENRE_LABELS[genre]}"
    body.append(svg.text(10, height_px - 8, title, size=11, fill=style["text"], weight="bold"))

    doc = svg.svg_doc(width_px, height_px, "".join(body), extra_defs=svg.goo_filter(filter_id, std_dev=CELL * 0.35))
    return {
        "svg": doc,
        "meta": {"floor_cells": len(floor_cells), "features": len(features), "size": params.get("size", "medium")},
        "title_suggestion": title,
    }
