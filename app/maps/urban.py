"""City-block generator: the same recursive-subdivision trick as rooms.py,
but the gaps between blocks ARE the streets (no corridor-carving needed),
and blocks are colored/labeled by district type instead of being rooms."""
from . import content, svg
from .shapes import bsp_partition

PX = 14

_SIZE_PRESETS = {
    "small":  {"w": 46, "h": 34, "depth": 4, "min_size": 6},
    "medium": {"w": 62, "h": 46, "depth": 5, "min_size": 6},
    "large":  {"w": 80, "h": 58, "depth": 6, "min_size": 6},
}

_BG_BY_GENRE = {
    "fantasy":  {"bg": "#e6dcc3", "street": "#cfc09a", "text": "#2a2013", "grid": "#d8caa5"},
    "modern":   {"bg": "#d6d6d6", "street": "#b8b8b8", "text": "#222222", "grid": "#c6c6c6"},
    "hightech": {"bg": "#101820", "street": "#1c2a36", "text": "#cfe8ff", "grid": "#182430"},
}

_BLOCK_PALETTE = ["#c9a66b", "#8fa9c4", "#a9c48f", "#c48fa0", "#8fc4bb",
                  "#c4b48f", "#b08fc4", "#c4908f", "#8f9fc4", "#a3c48f"]
_LANDMARK_COLOR = "#e0b840"


def generate(rng, genre, params):
    preset = _SIZE_PRESETS.get(params.get("size", "medium"), _SIZE_PRESETS["medium"])
    grid_w, grid_h, depth, min_size = preset["w"], preset["h"], preset["depth"], preset["min_size"]
    bg_genre = genre if genre != "mixed" else rng.choice(content.GENRES)
    pal = _BG_BY_GENRE[bg_genre]

    leaves = bsp_partition(rng, 1, 1, grid_w - 2, grid_h - 2, depth, min_size)
    margin = 0.5

    type_colors = {}
    color_cycle = iter(_BLOCK_PALETTE * 3)
    blocks = []
    landmark_budget = max(1, len(leaves) // 7)
    for leaf in leaves:
        district = content.pick(rng, "urban_districts", genre)
        if district not in type_colors:
            type_colors[district] = next(color_cycle)
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

    title = f"City Districts - {content.GENRE_LABELS[genre]}"
    body.append(svg.text(10, height_px - 8, title, size=11, fill=pal["text"], weight="bold"))

    doc = svg.svg_doc(width_px, height_px, "".join(body))
    return {
        "svg": doc,
        "meta": {"blocks": len(blocks), "landmarks": sum(1 for b in blocks if b["landmark"]), "size": params.get("size", "medium")},
        "title_suggestion": title,
    }
