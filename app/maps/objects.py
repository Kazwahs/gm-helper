"""Small hand-drawn SVG object icons used to flesh out generated maps -
furniture/props scattered inside dungeon and building-interior rooms, and
points of interest dotted across wilderness/world maps. Each icon is a tiny
glyph built from a couple of primitive shapes (not real art, just enough to
read at map scale) and every placed object is wrapped as an editable
map_el, tagged "object", so the existing map editor's drag/recolor/relabel/
delete support works on them without any editor-side changes.

Icon geometry is genre-agnostic (a crate is a crate); what varies by genre
is which icons are thematically appropriate and what each is called, which
is what ROOM_OBJECTS and POINT_OF_INTEREST below encode. Keeping the catalog
here in Python rather than in map_content.json is deliberate: every entry
names a drawing function that must exist, so keeping name and geometry in
the same file means they can't drift out of sync the way two parallel
lists (one in JSON, one in code) eventually would.
"""
import math

from . import svg

GENRES = ["fantasy", "modern", "hightech"]


# ---------------------------------------------------------------- icons ---
# Each draws itself centered at (cx, cy) at roughly `s` px "radius", filled
# with `color`. The single shape that should be user-recolorable in the map
# editor carries the map-el-fill class; small dark accent details don't.

def _icon_crate(cx, cy, s, color):
    w = h = s * 1.7
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.line(x, y, x + w, y + h, "#00000055", 0.6))
    parts.append(svg.line(x + w, y, x, y + h, "#00000055", 0.6))
    return "".join(parts)


def _icon_barrel(cx, cy, s, color):
    w, h = s * 1.3, s * 1.9
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, rx=w * 0.3, extra='class="map-el-fill"')]
    parts.append(svg.line(x, y + h * 0.32, x + w, y + h * 0.32, "#00000055", 0.6))
    parts.append(svg.line(x, y + h * 0.68, x + w, y + h * 0.68, "#00000055", 0.6))
    return "".join(parts)


def _icon_table(cx, cy, s, color):
    w, h = s * 2.1, s * 1.3
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    for lx, ly in ((x + 1, y + h - 1), (x + w - 1, y + h - 1), (x + 1, y + 1), (x + w - 1, y + 1)):
        parts.append(svg.circle(lx, ly, 0.9, "#00000055"))
    return "".join(parts)


def _icon_chair(cx, cy, s, color):
    w = h = s * 1.2
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.line(x, y, x + w, y, "#00000070", 1.6))
    return "".join(parts)


def _icon_shelf(cx, cy, s, color):
    w, h = s * 1.5, s * 2.1
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    for frac in (0.33, 0.66):
        parts.append(svg.line(x, y + h * frac, x + w, y + h * frac, "#00000055", 0.6))
    return "".join(parts)


def _icon_chest(cx, cy, s, color):
    w, h = s * 1.9, s * 1.3
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, rx=1, extra='class="map-el-fill"')]
    parts.append(svg.line(x, y + h * 0.4, x + w, y + h * 0.4, "#00000055", 0.6))
    parts.append(svg.circle(cx, y + h * 0.4, 1.1, "#00000070"))
    return "".join(parts)


def _icon_bed(cx, cy, s, color):
    w, h = s * 1.6, s * 2.4
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, rx=1, extra='class="map-el-fill"')]
    parts.append(svg.rect(x + 1, y + 1, w - 2, h * 0.28, "#ffffff55", rx=1))
    return "".join(parts)


def _icon_brazier(cx, cy, s, color):
    parts = [svg.line(cx, cy + s, cx, cy - s * 0.2, "#3a2a1a", 1.4)]
    parts.append(svg.circle(cx, cy - s * 0.2, s * 0.75, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"'))
    parts.append(svg.circle(cx, cy - s * 0.55, s * 0.35, "#ffb347", opacity=0.9))
    return "".join(parts)


def _icon_altar(cx, cy, s, color):
    w, h = s * 2.2, s * 1.1
    x, y = cx - w / 2, cy - h / 2 + s * 0.4
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    for dx in (-w * 0.28, w * 0.28):
        parts.append(svg.circle(cx + dx, y - s * 0.35, s * 0.22, "#ffb347", opacity=0.85))
    return "".join(parts)


def _icon_pillar(cx, cy, s, color):
    parts = [svg.circle(cx, cy, s * 0.85, color, stroke="#00000066", stroke_width=0.7, extra='class="map-el-fill"')]
    parts.append(svg.circle(cx, cy, s * 0.85, "none", stroke="#00000030", stroke_width=2.2))
    return "".join(parts)


def _icon_well(cx, cy, s, color):
    parts = [svg.circle(cx, cy, s * 0.95, color, stroke="#00000066", stroke_width=0.8, extra='class="map-el-fill"')]
    parts.append(svg.circle(cx, cy, s * 0.5, "#00000055"))
    return "".join(parts)


def _icon_rubble(cx, cy, s, color):
    parts = []
    pts = [(-0.7, -0.3), (0.1, -0.7), (0.7, 0.1), (0.2, 0.7), (-0.6, 0.4)]
    for i, (dx, dy) in enumerate(pts):
        r = s * (0.32 + 0.1 * (i % 2))
        parts.append(svg.circle(cx + dx * s, cy + dy * s, r, color, stroke="#00000055", stroke_width=0.5,
                                 extra='class="map-el-fill"' if i == 0 else ""))
    return "".join(parts)


def _icon_cage(cx, cy, s, color):
    w, h = s * 1.5, s * 1.9
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, opacity=0.35, extra='class="map-el-fill"')]
    n_bars = 4
    for i in range(n_bars + 1):
        bx = x + w * i / n_bars
        parts.append(svg.line(bx, y, bx, y + h, "#00000090", 0.9))
    return "".join(parts)


def _icon_terminal(cx, cy, s, color):
    w, h = s * 1.6, s * 1.5
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.rect(x + w * 0.15, y + h * 0.15, w * 0.7, h * 0.55, "#7fe3ff", opacity=0.8))
    return "".join(parts)


def _icon_vending(cx, cy, s, color):
    w, h = s * 1.3, s * 2.6
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.rect(x + w * 0.15, y + h * 0.12, w * 0.7, h * 0.45, "#cfe8ff", opacity=0.7))
    return "".join(parts)


def _icon_planter(cx, cy, s, color):
    w, h = s * 1.4, s * 1.1
    x, y = cx - w / 2, cy - h / 2 + s * 0.3
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, rx=1, extra='class="map-el-fill"')]
    parts.append(svg.polygon([(cx, y - s * 0.9), (cx - s * 0.5, y), (cx + s * 0.5, y)], "#4f7942", opacity=0.85))
    return "".join(parts)


def _icon_server_rack(cx, cy, s, color):
    w, h = s * 1.3, s * 2.4
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    for i in range(4):
        ly = y + h * (0.15 + i * 0.22)
        dot_color = "#5fffb0" if i % 2 == 0 else "#ff5f5f"
        parts.append(svg.circle(x + w * 0.82, ly, 0.9, dot_color))
    return "".join(parts)


def _icon_reactor(cx, cy, s, color):
    parts = [svg.circle(cx, cy, s * 0.9, color, stroke="#00000066", stroke_width=0.7, extra='class="map-el-fill"')]
    for i in range(6):
        angle = i * math.pi / 3
        x2, y2 = cx + math.cos(angle) * s * 1.5, cy + math.sin(angle) * s * 1.5
        parts.append(svg.line(cx + math.cos(angle) * s * 0.9, cy + math.sin(angle) * s * 0.9, x2, y2, "#7fe3ff", 1, opacity=0.7))
    parts.append(svg.circle(cx, cy, s * 0.35, "#7fe3ff", opacity=0.9))
    return "".join(parts)


def _icon_console(cx, cy, s, color):
    w, h = s * 2, s * 1.1
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    for i in range(3):
        parts.append(svg.circle(x + w * (0.25 + i * 0.25), y + h * 0.3, 0.9, "#ffb347"))
    return "".join(parts)


def _icon_drone(cx, cy, s, color):
    pts = [(cx + s * math.cos(math.radians(a)), cy + s * math.sin(math.radians(a))) for a in (0, 60, 120, 180, 240, 300)]
    parts = [svg.polygon(pts, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    for dx, dy in ((-s, -s), (s, -s), (-s, s), (s, s)):
        parts.append(svg.line(cx, cy, cx + dx * 0.6, cy + dy * 0.6, "#00000060", 0.6))
    return "".join(parts)


def _icon_bones(cx, cy, s, color):
    w, h = s * 1.8, s * 0.5
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.5, rx=h / 2, extra='class="map-el-fill"')]
    for ex in (x, x + w):
        parts.append(svg.circle(ex, cy, h * 0.65, color, stroke="#00000066", stroke_width=0.4))
    return "".join(parts)


def _icon_fungus(cx, cy, s, color):
    parts = []
    caps = [(-0.5, 0.2, 0.5), (0.35, 0.3, 0.6), (0, -0.35, 0.4)]
    for i, (dx, dy, scale) in enumerate(caps):
        parts.append(svg.circle(cx + dx * s, cy + dy * s, s * 0.35 * scale + s * 0.15, color,
                                 stroke="#00000055", stroke_width=0.4,
                                 extra='class="map-el-fill"' if i == 0 else ""))
    return "".join(parts)


def _icon_crystal(cx, cy, s, color):
    pts = [(cx, cy - s), (cx + s * 0.55, cy - s * 0.15), (cx + s * 0.3, cy + s),
           (cx - s * 0.3, cy + s), (cx - s * 0.55, cy - s * 0.15)]
    parts = [svg.polygon(pts, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.line(cx, cy - s * 0.8, cx, cy + s * 0.5, "#ffffff70", 0.6))
    return "".join(parts)


def _icon_pool(cx, cy, s, color):
    parts = [svg.circle(cx, cy, s * 1.05, color, stroke="#00000055", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.circle(cx, cy, s * 0.6, "#ffffff30"))
    parts.append(svg.circle(cx, cy, s * 0.22, "#ffffff55"))
    return "".join(parts)


def _icon_lamppost(cx, cy, s, color):
    parts = [svg.line(cx, cy + s, cx, cy - s * 0.6, "#00000070", 1.2)]
    parts.append(svg.circle(cx, cy - s * 0.7, s * 0.42, color, stroke="#00000066", stroke_width=0.5,
                             extra='class="map-el-fill"'))
    return "".join(parts)


def _icon_fountain(cx, cy, s, color):
    parts = [svg.circle(cx, cy, s * 0.95, color, stroke="#00000066", stroke_width=0.7, extra='class="map-el-fill"')]
    parts.append(svg.circle(cx, cy, s * 0.42, "#ffffff55"))
    parts.append(svg.circle(cx, cy, s * 0.15, "#ffffff90"))
    return "".join(parts)


def _icon_stall(cx, cy, s, color):
    w, h = s * 1.6, s * 1.1
    x, y = cx - w / 2, cy - h / 2 + s * 0.3
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, extra='class="map-el-fill"')]
    parts.append(svg.polygon([(x - s * 0.15, y), (x + w + s * 0.15, y), (cx, y - s * 0.7)], "#00000055"))
    return "".join(parts)


def _icon_car(cx, cy, s, color):
    w, h = s * 2.0, s * 1.0
    x, y = cx - w / 2, cy - h / 2
    parts = [svg.rect(x, y, w, h, color, stroke="#00000066", stroke_width=0.6, rx=h * 0.3,
                       extra='class="map-el-fill"')]
    for wx in (x + w * 0.25, x + w * 0.75):
        parts.append(svg.circle(wx, y + h, h * 0.28, "#1a1a1a"))
    return "".join(parts)


_ICONS = {
    "crate": _icon_crate, "barrel": _icon_barrel, "table": _icon_table, "chair": _icon_chair,
    "shelf": _icon_shelf, "chest": _icon_chest, "bed": _icon_bed, "brazier": _icon_brazier,
    "altar": _icon_altar, "pillar": _icon_pillar, "well": _icon_well, "rubble": _icon_rubble,
    "cage": _icon_cage, "terminal": _icon_terminal, "vending": _icon_vending, "planter": _icon_planter,
    "server_rack": _icon_server_rack, "reactor": _icon_reactor, "console": _icon_console, "drone": _icon_drone,
    "bones": _icon_bones, "fungus": _icon_fungus, "crystal": _icon_crystal, "pool": _icon_pool,
    "lamppost": _icon_lamppost, "fountain": _icon_fountain, "stall": _icon_stall, "car": _icon_car,
}

# icon color per genre, keyed to each style's existing feature-text color so
# objects read as "part of the map" rather than a mismatched sticker.
_ICON_COLOR = {
    "dungeon": "#8a7248", "interior": "#5a6a7a",
}

ROOM_OBJECTS = {
    "fantasy": [
        ("crate", "a crate"), ("barrel", "a barrel"), ("table", "a table"),
        ("chair", "a stool"), ("shelf", "a bookshelf"), ("chest", "a locked chest"),
        ("bed", "a straw bed"), ("brazier", "a lit brazier"), ("altar", "a small altar"),
        ("pillar", "a stone pillar"), ("well", "a dry well"), ("rubble", "a pile of rubble"),
        ("cage", "an empty cage"),
    ],
    "modern": [
        ("crate", "a shipping crate"), ("barrel", "a rusted drum"), ("table", "a desk"),
        ("chair", "an office chair"), ("shelf", "a filing cabinet"), ("bed", "a cot"),
        ("terminal", "a computer terminal"), ("vending", "a vending machine"),
        ("planter", "a potted plant"), ("rubble", "debris"), ("cage", "a holding cell"),
    ],
    "hightech": [
        ("crate", "a supply crate"), ("table", "a workbench"), ("chair", "a console chair"),
        ("shelf", "a storage rack"), ("terminal", "a data terminal"), ("server_rack", "a server rack"),
        ("reactor", "a small reactor core"), ("console", "a control console"),
        ("drone", "an inactive drone"), ("pillar", "a support pylon"), ("rubble", "scattered debris"),
    ],
}

POINTS_OF_INTEREST = {
    "fantasy": [
        ("altar", "ancient standing stones"), ("pillar", "a crumbling watchtower"),
        ("well", "a hidden shrine"), ("rubble", "ruins, long abandoned"),
        ("brazier", "a bandit camp"), ("cage", "a monster's den"), ("chest", "a buried cache"),
    ],
    "modern": [
        ("rubble", "a plane wreck"), ("crate", "an abandoned campsite"),
        ("pillar", "an old radio tower"), ("cage", "a hunting cabin"), ("vending", "a rest stop"),
    ],
    "hightech": [
        ("rubble", "a crashed satellite"), ("pillar", "a relay station"),
        ("console", "an automated outpost"), ("drone", "a downed drone swarm"),
        ("crate", "a supply cache"),
    ],
}

# Small props scattered through cave chambers - a lighter-weight sibling of
# ROOM_OBJECTS for the underground/caves generator's more open, irregular
# spaces.
CAVE_OBJECTS = {
    "fantasy": [
        ("rubble", "loose rubble"), ("bones", "scattered bones"), ("fungus", "glowing fungus"),
        ("crystal", "a crystal cluster"), ("chest", "a hidden cache"), ("altar", "a crude altar"),
        ("pool", "a still pool"),
    ],
    "modern": [
        ("rubble", "collapsed debris"), ("crate", "abandoned supplies"), ("barrel", "a rusted drum"),
        ("bones", "old remains"), ("pool", "standing water"), ("cage", "an old cage"),
    ],
    "hightech": [
        ("rubble", "wreckage"), ("terminal", "a defunct terminal"), ("crystal", "a mineral deposit"),
        ("reactor", "a leaking coolant cell"), ("pool", "chemical runoff"), ("cage", "a containment cell"),
    ],
}

# Street furniture scattered across city blocks for the urban generator.
URBAN_PROPS = {
    "fantasy": [
        ("stall", "a market stall"), ("lamppost", "a lit lamppost"), ("fountain", "a stone fountain"),
        ("crate", "stacked goods"), ("barrel", "a barrel"), ("planter", "a flower box"),
    ],
    "modern": [
        ("car", "a parked car"), ("lamppost", "a streetlamp"), ("fountain", "a plaza fountain"),
        ("crate", "delivery crates"), ("planter", "a planter box"), ("vending", "a vending machine"),
    ],
    "hightech": [
        ("car", "a parked hovercar"), ("drone", "a delivery drone"), ("terminal", "a public terminal"),
        ("vending", "an auto-vendor"), ("console", "a transit kiosk"), ("planter", "a bio-planter"),
    ],
}


def _pool(catalog, genre):
    if genre == "mixed":
        return [item for g in GENRES for item in catalog[g]]
    return catalog.get(genre) or catalog["fantasy"]


def render_icon(icon_key, cx, cy, scale, color):
    fn = _ICONS.get(icon_key, _icon_crate)
    return fn(cx, cy, scale, color)


def place_room_objects(rng, room, genre, color, px, catalog=ROOM_OBJECTS, area_divisor=45, max_count=4):
    """Returns a list of {icon, label, cx, cy} placed within a room dict
    (x/y/w/h in grid units), scaled to pixel space via `px`. Bigger rooms
    get more objects, on a die roll rather than a hard formula so two
    similarly-sized rooms don't always end up with identical counts.

    `catalog` and `area_divisor` are overridable so other generators whose
    "rooms" are a different scale - city blocks, for instance - can reuse
    this same placement/spacing logic with their own prop list and density
    instead of dungeon/interior's tuned defaults."""
    area = room["w"] * room["h"]
    max_by_shape = max_count if room.get("shape") != "circle" else max(1, max_count // 2)
    if rng.random() < 0.15:
        count = 0  # some rooms are just bare - not every space needs dressing
    else:
        target = round(area / area_divisor) + rng.randint(-1, 1)
        count = max(0, min(target, max_by_shape))
    if count == 0:
        return []
    pool = _pool(catalog, genre)
    chosen = rng.sample(pool, min(count, len(pool)))

    x0, y0 = room["x"] * px, room["y"] * px
    w0, h0 = room["w"] * px, room["h"] * px
    pad = min(w0, h0) * 0.22
    placed = []
    attempts_per = 12
    for icon_key, label in chosen:
        for _ in range(attempts_per):
            cx = rng.uniform(x0 + pad, x0 + w0 - pad)
            cy = rng.uniform(y0 + pad, y0 + h0 - pad)
            # keep clear of the room's own label/feature text near center
            if abs(cy - (y0 + h0 / 2)) < h0 * 0.16 and w0 < px * 6:
                continue
            if all(math.hypot(cx - p["cx"], cy - p["cy"]) > pad * 1.1 for p in placed):
                placed.append({"icon": icon_key, "label": label, "cx": cx, "cy": cy})
                break
    return placed


def _place_scattered(rng, candidates, pool, count, min_dist):
    """Shared core for scattering a handful of labeled icons across a list
    of candidate grid cells, keeping them spaced apart and never doubling
    up on the same cell. `candidates`: list of (col, row, px, py)."""
    if not candidates or count <= 0:
        return []
    rng.shuffle(candidates)
    chosen_labels = rng.sample(pool, min(count, len(pool))) if count <= len(pool) else \
        [rng.choice(pool) for _ in range(count)]
    placed = []
    for (icon_key, label) in chosen_labels:
        for (col, row, px, py) in candidates:
            if any(c["col"] == col and c["row"] == row for c in placed):
                continue
            if all(math.hypot(px - p["cx"], py - p["cy"]) > min_dist for p in placed):
                placed.append({"icon": icon_key, "label": label, "col": col, "row": row, "cx": px, "cy": py})
                break
        if len(placed) >= count:
            break
    return placed


def place_points_of_interest(rng, candidates, genre, count):
    """candidates: list of (col, row, px, py) hospitable-but-unused hexes.
    Returns up to `count` {icon, label, col, row, cx, cy} with no two closer
    than a spacing floor, so markers don't cluster on top of each other."""
    min_dist = 3.0 * 17  # a few hex-radii apart (R=17 in terrain.py)
    return _place_scattered(rng, candidates, _pool(POINTS_OF_INTEREST, genre), count, min_dist)


def place_scattered_objects(rng, candidates, genre, catalog, count, min_dist):
    """General-purpose sibling of place_points_of_interest for generators
    whose grid scale isn't terrain.py's hex grid (e.g. cave floor cells) -
    same spacing/no-repeat guarantees, with the catalog and spacing floor
    both caller-supplied instead of hardcoded to hex radii."""
    return _place_scattered(rng, candidates, _pool(catalog, genre), count, min_dist)
