"""Hex-grid regional/world terrain generator, shared by 'wilderness' and
'world' map types. A height field and an independent moisture field (each
built from a handful of random gaussian bumps) drive biome assignment;
rivers flow downhill from high ground to water, and a few settlements get
placed on hospitable land. 'world' adds a stronger edge falloff toward
ocean (so it reads as a whole landmass) and a latitude-driven tundra band;
'wilderness' is a smaller, mostly-land regional patch."""
import math

from . import content, svg

R = 17  # hex circumradius, px

_WILDERNESS_SIZES = {"small": (12, 9), "medium": (16, 12), "large": (20, 15)}
_WORLD_SIZES = {"small": (18, 13), "medium": (24, 17), "large": (30, 21)}

_BIOME_COLOR = {
    "ocean": "#3a6ea5", "coast": "#d9c48a", "plains": "#a8c47a", "forest": "#4f7942",
    "hills": "#8a8a4a", "mountains": "#8a8a8a", "desert": "#d9b96a",
    "tundra": "#dfe8ea", "swamp": "#5a6a4a",
}

# odd-q offset neighbor directions (flat-top hexes), by column parity.
_ODDQ_DIRS = [
    [(1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (0, 1)],
    [(1, 1), (1, 0), (0, -1), (-1, 0), (-1, 1), (0, 1)],
]


def _hex_center(col, row):
    x = R * 1.5 * col
    y = R * math.sqrt(3) * (row + 0.5 * (col & 1))
    return x + R * 1.6, y + R * 1.2


def _hex_corners(cx, cy):
    return [
        (cx + R * math.cos(math.radians(60 * i)), cy + R * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]


def _neighbors(col, row, cols, rows):
    parity = col & 1
    result = []
    for dq, dr in _ODDQ_DIRS[parity]:
        nc, nr = col + dq, row + dr
        if 0 <= nc < cols and 0 <= nr < rows:
            result.append((nc, nr))
    return result


def _gaussian_field(rng, cols, rows, centers_px, canvas_w, canvas_h, n_bumps, sign=1.0):
    bumps = []
    for _ in range(n_bumps):
        bx = rng.uniform(0, canvas_w)
        by = rng.uniform(0, canvas_h)
        radius = rng.uniform(0.18, 0.38) * max(canvas_w, canvas_h)
        strength = rng.uniform(0.55, 1.0)
        bumps.append((bx, by, radius, strength))

    field = {}
    max_v = 1e-6
    for col in range(cols):
        for row in range(rows):
            cx, cy = centers_px[(col, row)]
            v = 0.0
            for bx, by, radius, strength in bumps:
                d2 = (cx - bx) ** 2 + (cy - by) ** 2
                v += strength * math.exp(-d2 / (2 * radius * radius))
            field[(col, row)] = v
            max_v = max(max_v, v)
    for k in field:
        field[k] = field[k] / max_v
    return field


def _biome(h, m, lat, sea_level, use_latitude):
    if h < sea_level:
        return "ocean"
    if h < sea_level + 0.045:
        return "coast"
    if h > 0.83:
        return "mountains"
    if h > 0.68:
        return "hills"
    if use_latitude and abs(lat - 0.5) * 2 > 0.72 and h > 0.5:
        return "tundra"
    if m < 0.3:
        return "desert"
    if m < 0.56:
        return "plains"
    if m < 0.8:
        return "forest"
    return "swamp"


def generate(rng, genre, map_type, params):
    size_key = params.get("size", "medium")
    if map_type == "world":
        cols, rows = _WORLD_SIZES.get(size_key, _WORLD_SIZES["medium"])
        sea_level = 0.36
        edge_strength = 1.0
        use_latitude = True
        n_hills = max(5, (cols * rows) // 14)
    else:
        cols, rows = _WILDERNESS_SIZES.get(size_key, _WILDERNESS_SIZES["medium"])
        sea_level = 0.24
        edge_strength = 0.25
        use_latitude = False
        n_hills = max(4, (cols * rows) // 12)

    centers_px = {(c, r): _hex_center(c, r) for c in range(cols) for r in range(rows)}
    canvas_w = max(p[0] for p in centers_px.values()) + R * 2
    canvas_h = max(p[1] for p in centers_px.values()) + R * 2
    cx0, cy0 = canvas_w / 2, canvas_h / 2
    max_r = math.hypot(cx0, cy0)

    height = _gaussian_field(rng, cols, rows, centers_px, canvas_w, canvas_h, n_hills)
    moisture = _gaussian_field(rng, cols, rows, centers_px, canvas_w, canvas_h, max(3, n_hills // 2))

    for (c, r), (px, py) in centers_px.items():
        d = math.hypot(px - cx0, py - cy0) / max_r
        falloff = max(0.0, 1 - (d ** 1.6) * edge_strength)
        height[(c, r)] = height[(c, r)] * (0.35 + 0.65 * falloff) if edge_strength else height[(c, r)]

    hexes = {}
    for c in range(cols):
        for r in range(rows):
            h = height[(c, r)]
            m = moisture[(c, r)]
            lat = centers_px[(c, r)][1] / canvas_h
            hexes[(c, r)] = {"h": h, "m": m, "biome": _biome(h, m, lat, sea_level, use_latitude)}

    # Rivers: flow downhill from a few highland sources to water (or a local low point).
    sources = [k for k, v in hexes.items() if v["biome"] in ("mountains", "hills")]
    rng.shuffle(sources)
    n_rivers = min(len(sources), rng.randint(2, 4))
    rivers = []
    for src in sources[:n_rivers]:
        path = [src]
        current = src
        visited = {src}
        for _ in range(40):
            if hexes[current]["biome"] in ("ocean", "coast"):
                break
            nbrs = [n for n in _neighbors(*current, cols, rows) if n not in visited]
            if not nbrs:
                break
            nxt = min(nbrs, key=lambda n: hexes[n]["h"])
            if hexes[nxt]["h"] >= hexes[current]["h"] and hexes[current]["biome"] not in ("mountains", "hills"):
                break
            path.append(nxt)
            visited.add(nxt)
            current = nxt
            if hexes[current]["biome"] in ("ocean", "coast"):
                break
        if len(path) > 2:
            ends_in_water = hexes[path[-1]]["biome"] in ("ocean", "coast")
            rivers.append({"path": path, "lake": not ends_in_water})

    # Settlements on hospitable land, spaced apart.
    candidates = [k for k, v in hexes.items() if v["biome"] in ("plains", "forest", "hills", "coast")]
    rng.shuffle(candidates)
    n_settlements = min(len(candidates), rng.randint(3, 6))
    settlements = []
    min_dist = max_r * 0.32
    for cand in candidates:
        if len(settlements) >= n_settlements:
            break
        cpx, cpy = centers_px[cand]
        if all(math.hypot(cpx - centers_px[s][0], cpy - centers_px[s][1]) > min_dist for s, _ in settlements):
            settlements.append((cand, content.generate_name(rng, genre)))

    # ---- render ----
    body = [svg.rect(0, 0, canvas_w, canvas_h, _BIOME_COLOR["ocean"])]
    hex_parts = []
    for (c, r), data in hexes.items():
        cx, cy = centers_px[(c, r)]
        pts = _hex_corners(cx, cy)
        fill = _BIOME_COLOR[data["biome"]]
        poly = svg.polygon(pts, fill, stroke="#00000030", stroke_width=0.6, extra='class="map-el-fill"')
        hex_parts.append(svg.map_el("hex", cx, cy, poly, fill=fill))
    body.append(svg.group("".join(hex_parts)))

    river_parts = []
    for river in rivers:
        pts = [centers_px[p] for p in river["path"]]
        for i in range(len(pts) - 1):
            width = min(1.2 + i * 0.35, 4.5)
            river_parts.append(svg.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], "#2f6fa8", width))
        if river["lake"]:
            # The river ran into a local low point rather than the coast -
            # mark it as a small lake instead of just stopping mid-map.
            lx, ly = pts[-1]
            river_parts.append(svg.circle(lx, ly, R * 0.55, "#2f6fa8"))
    body.append(svg.group("".join(river_parts)))

    settlement_parts = []
    for (i, ((c, r), name)) in enumerate(settlements):
        cx, cy = centers_px[(c, r)]
        is_city = i < max(1, len(settlements) // 3)
        radius = 5 if is_city else 3.2
        el_parts = [svg.circle(cx, cy, radius, "#3a2a1a", stroke="#f4ead2", stroke_width=1.3, extra='class="map-el-fill"')]
        el_parts.append(svg.text(cx + radius + 3, cy + 3, name, size=9, fill="#241a10", weight="bold" if is_city else "normal", extra='class="map-el-label"'))
        settlement_parts.append(svg.map_el("settlement", cx, cy, "".join(el_parts), fill="#3a2a1a"))
    body.append(svg.group("".join(settlement_parts)))

    # Legend.
    biomes_present = sorted({v["biome"] for v in hexes.values()})
    legend_x, legend_y = 10, 10
    legend_h = 16 + 14 * (len(biomes_present) + 1)
    legend_parts = [svg.rect(legend_x - 6, legend_y - 8, 150, legend_h, "#f4ead2", "#7a6a4a", 1)]
    legend_parts.append(svg.text(legend_x, legend_y + 6, content.GENRE_LABELS[genre], size=9, fill="#241a10", weight="bold"))
    for i, biome in enumerate(biomes_present):
        ly = legend_y + 20 + i * 14
        label = content.biome_label(genre, biome) if genre != "mixed" else content.biome_label_mixed(rng, biome)
        legend_parts.append(svg.rect(legend_x, ly - 8, 10, 10, _BIOME_COLOR[biome], stroke="#7a6a4a", stroke_width=0.6))
        legend_parts.append(svg.text(legend_x + 16, ly, label, size=8, fill="#241a10"))
    body.append(svg.group("".join(legend_parts)))

    # Compass rose, bottom-right corner.
    rose_x, rose_y = canvas_w - 30, canvas_h - 30
    rose_parts = [svg.circle(rose_x, rose_y, 16, "#f4ead2", stroke="#7a6a4a", stroke_width=1)]
    rose_parts.append(svg.line(rose_x, rose_y - 13, rose_x, rose_y + 13, "#241a10", 1.2))
    rose_parts.append(svg.line(rose_x - 13, rose_y, rose_x + 13, rose_y, "#241a10", 1.2))
    rose_parts.append(svg.text(rose_x, rose_y - 16, "N", size=9, fill="#241a10", anchor="middle", weight="bold"))
    body.append(svg.group("".join(rose_parts)))

    realm = content.generate_name(rng, genre)
    if map_type == "world":
        title = f"The Lands of {realm} - {content.GENRE_LABELS[genre]}"
    else:
        title = f"{realm} Wilds - {content.GENRE_LABELS[genre]}"
    title_w = 16 + len(title) * 6.2
    body.append(svg.rect(6, canvas_h - 20, title_w, 18, "#241a10", opacity=0.65, rx=3))
    body.append(svg.text(12, canvas_h - 8, title, size=11, fill="#f4ead2", weight="bold"))

    doc = svg.svg_doc(canvas_w, canvas_h, "".join(body))
    return {
        "svg": doc,
        "meta": {"hexes": len(hexes), "rivers": len(rivers), "settlements": len(settlements), "size": size_key},
        "title_suggestion": title,
    }
