"""City-block generator with three distinct street-plan archetypes rather
than always the same recursive-subdivision layout:

- organic: the original bsp_partition trick - the gaps between
  irregular, organically-subdivided blocks ARE the streets. Reads as an
  old, unplanned city that grew block by block.
- grid: blocks arranged in a regular rectangular grid with even avenue
  spacing (with a little per-block size jitter so it doesn't look like
  graph paper) - reads as a planned, modern grid city.
- radial: two wide avenues cross at a central plaza, splitting the map
  into four quadrants that each get their own independent bsp
  subdivision - reads as a city built around a central square.

Whichever layout runs, blocks are colored/labeled by district type, and a
handful of small hand-drawn street-furniture icons (lampposts, market
stalls, parked cars, etc.) get scattered across blocks the same way
rooms.py dresses dungeon rooms."""
from collections import namedtuple

from . import content, objects, svg
from .shapes import bsp_partition

PX = 14

_SIZE_PRESETS = {
    "small":  {"w": 46, "h": 34, "depth": 4, "min_size": 6},
    "medium": {"w": 62, "h": 46, "depth": 5, "min_size": 6},
    "large":  {"w": 80, "h": 58, "depth": 6, "min_size": 6},
}

_BG_BY_GENRE = {
    "fantasy":  {"bg": "#e6dcc3", "street": "#cfc09a", "text": "#2a2013", "grid": "#d8caa5", "object": "#3a2a1a"},
    "modern":   {"bg": "#d6d6d6", "street": "#b8b8b8", "text": "#222222", "grid": "#c6c6c6", "object": "#2a2a2a"},
    "hightech": {"bg": "#101820", "street": "#1c2a36", "text": "#cfe8ff", "grid": "#182430", "object": "#7fe3ff"},
}

_BLOCK_PALETTE = ["#c9a66b", "#8fa9c4", "#a9c48f", "#c48fa0", "#8fc4bb",
                  "#c4b48f", "#b08fc4", "#c4908f", "#8f9fc4", "#a3c48f"]
_LANDMARK_COLOR = "#e0b840"

_LAYOUT_WEIGHTS = [("organic", 0.4), ("grid", 0.35), ("radial", 0.25)]
_LAYOUT_LABELS = {"organic": "", "grid": " (grid)", "radial": " (radial)"}

_Rect = namedtuple("_Rect", ["x", "y", "w", "h"])


def _choose_layout(rng):
    r = rng.random()
    acc = 0.0
    for name, weight in _LAYOUT_WEIGHTS:
        acc += weight
        if r <= acc:
            return name
    return _LAYOUT_WEIGHTS[-1][0]


def _partition_axis(rng, start, end, n):
    """Splits [start, end] into n segments with a little size jitter so a
    regular grid doesn't look perfectly mechanical, returning n+1 edges."""
    total = end - start
    weights = [rng.uniform(0.8, 1.2) for _ in range(n)]
    wsum = sum(weights)
    edges = [start]
    acc = start
    for wgt in weights:
        acc += total * wgt / wsum
        edges.append(acc)
    edges[-1] = end  # avoid float drift off the far edge
    return edges


def _grid_leaves(rng, grid_w, grid_h, min_size):
    avenue_gap = 1.6
    n_cols = max(2, int((grid_w - 2) / (min_size + avenue_gap)))
    n_rows = max(2, int((grid_h - 2) / (min_size + avenue_gap)))
    col_edges = _partition_axis(rng, 1, grid_w - 1, n_cols)
    row_edges = _partition_axis(rng, 1, grid_h - 1, n_rows)
    leaves = []
    for i in range(n_cols):
        for j in range(n_rows):
            x0, x1 = col_edges[i], col_edges[i + 1]
            y0, y1 = row_edges[j], row_edges[j + 1]
            leaves.append(_Rect(x0, y0, x1 - x0, y1 - y0))
    return leaves


def _radial_leaves(rng, grid_w, grid_h, depth, min_size):
    avenue_w = max(2, min(grid_w, grid_h) // 12)
    cx, cy = grid_w / 2, grid_h / 2
    half_av = avenue_w / 2
    quadrants = [
        (1, 1, cx - half_av - 1, cy - half_av - 1),
        (cx + half_av, 1, grid_w - 1 - (cx + half_av), cy - half_av - 1),
        (1, cy + half_av, cx - half_av - 1, grid_h - 1 - (cy + half_av)),
        (cx + half_av, cy + half_av, grid_w - 1 - (cx + half_av), grid_h - 1 - (cy + half_av)),
    ]
    leaves = []
    sub_depth = max(1, depth - 1)
    for (qx, qy, qw, qh) in quadrants:
        if qw < min_size or qh < min_size:
            continue
        leaves.extend(bsp_partition(rng, qx, qy, qw, qh, sub_depth, min_size))
    return leaves


def generate(rng, genre, params):
    preset = _SIZE_PRESETS.get(params.get("size", "medium"), _SIZE_PRESETS["medium"])
    grid_w, grid_h, depth, min_size = preset["w"], preset["h"], preset["depth"], preset["min_size"]
    bg_genre = genre if genre != "mixed" else rng.choice(content.GENRES)
    pal = _BG_BY_GENRE[bg_genre]

    layout = _choose_layout(rng)
    if layout == "grid":
        leaves = _grid_leaves(rng, grid_w, grid_h, min_size)
        margin = 0.8
    elif layout == "radial":
        leaves = _radial_leaves(rng, grid_w, grid_h, depth, min_size)
        margin = 0.5
    else:
        leaves = bsp_partition(rng, 1, 1, grid_w - 2, grid_h - 2, depth, min_size)
        margin = 0.5

    if not leaves:  # pragma: no cover - defensive fallback, layouts above
        # are tuned to always produce at least one leaf at every size preset
        leaves = bsp_partition(rng, 1, 1, grid_w - 2, grid_h - 2, depth, min_size)
        margin = 0.5

    type_colors = {}
    blocks = []
    landmark_budget = max(1, len(leaves) // 7)
    for leaf in leaves:
        district = content.pick(rng, "urban_districts", genre)
        if district not in type_colors:
            # Indexing modulo the palette length (rather than a fixed-length
            # iterator) means this never runs out, however many distinct
            # districts a big "mixed"-genre map happens to roll.
            type_colors[district] = _BLOCK_PALETTE[len(type_colors) % len(_BLOCK_PALETTE)]
        is_landmark = landmark_budget > 0 and rng.random() < 0.22
        if is_landmark:
            landmark_budget -= 1
        blocks.append({
            "x": leaf.x + margin, "y": leaf.y + margin,
            "w": max(leaf.w - 2 * margin, 1), "h": max(leaf.h - 2 * margin, 1),
            "district": district, "landmark": is_landmark,
            "name": content.generate_name(rng, genre) if is_landmark else None,
        })

    width_px, height_px = grid_w * PX, grid_h * PX
    body = [svg.rect(0, 0, width_px, height_px, pal["street"])]

    block_parts = []
    object_count = 0
    obj_scale = PX * 0.4
    for b in blocks:
        x, y, w, h = b["x"] * PX, b["y"] * PX, b["w"] * PX, b["h"] * PX
        cx, cy = x + w / 2, y + h / 2
        fill = _LANDMARK_COLOR if b["landmark"] else type_colors[b["district"]]
        el_parts = [svg.rect(x, y, w, h, fill, pal["bg"], 1, extra='class="map-el-fill"')]
        label = b["name"] or b["district"]
        # Shrink font for small blocks so labels don't overrun their block.
        size = 8.5 if min(w, h) > 40 else 6.8
        if min(w, h) > 16:
            el_parts.append(svg.text(cx, cy - 2, label, size=size, fill="#20180c",
                                      anchor="middle", weight="bold" if b["landmark"] else "normal",
                                      extra='class="map-el-label"'))
        block_parts.append(svg.map_el("block", cx, cy, "".join(el_parts), fill=fill))

        props = objects.place_room_objects(rng, b, genre, pal["object"], PX,
                                            catalog=objects.URBAN_PROPS, area_divisor=60, max_count=3)
        for prop in props:
            icon_body = objects.render_icon(prop["icon"], prop["cx"], prop["cy"], obj_scale, pal["object"])
            block_parts.append(svg.map_el("object", prop["cx"], prop["cy"], icon_body, fill=pal["object"]))
        object_count += len(props)
    body.append(svg.group("".join(block_parts)))

    # Legend.
    legend_items = list(type_colors.items())
    legend_x, legend_y = width_px - 168, 10
    legend_parts = [svg.rect(legend_x - 8, legend_y - 8, 166, 16 + 14 * (len(legend_items) + 1), pal["bg"], pal["street"], 1)]
    legend_parts.append(svg.text(legend_x, legend_y + 6, "Districts", size=9, fill=pal["text"], weight="bold"))
    for i, (dist, color) in enumerate(legend_items):
        ly = legend_y + 20 + i * 14
        legend_parts.append(svg.rect(legend_x, ly - 8, 10, 10, color, stroke=pal["text"], stroke_width=0.5))
        legend_parts.append(svg.text(legend_x + 16, ly, dist, size=8, fill=pal["text"]))
    ly = legend_y + 20 + len(legend_items) * 14
    legend_parts.append(svg.rect(legend_x, ly - 8, 10, 10, _LANDMARK_COLOR, stroke=pal["text"], stroke_width=0.5))
    legend_parts.append(svg.text(legend_x + 16, ly, "Landmark", size=8, fill=pal["text"], weight="bold"))
    body.append(svg.group("".join(legend_parts)))

    layout_label = _LAYOUT_LABELS[layout]
    title = f"City Districts - {content.GENRE_LABELS[genre]}{layout_label}"
    body.append(svg.text(10, height_px - 8, title, size=11, fill=pal["text"], weight="bold"))

    doc = svg.svg_doc(width_px, height_px, "".join(body))
    return {
        "svg": doc,
        "meta": {
            "blocks": len(blocks), "landmarks": sum(1 for b in blocks if b["landmark"]),
            "layout": layout, "objects": object_count, "size": params.get("size", "medium"),
        },
        "title_suggestion": title,
    }
