"""Ink-style city maps: real streets and individual building footprints.

Streets are the gaps between blocks, laid out one of three ways:

- organic: a recursive subdivision with wide avenues first and narrower
  lanes below, then a gentle smooth warp so nothing is dead straight.
- grid: a planned grid with every third street an avenue.
- radial: concentric ring roads and radial spokes around a central plaza.

Each block is split into lots and each lot gets a footprint (some L-shaped,
some left as yards), so the map reads as a town rather than coloured
boxes. A few blocks become parks (trees and a pond), a plaza (paving,
fountain, market stalls) or a named landmark (one big building around a
courtyard). Blocks keep a faint district tint, and the legend lives in its
own strip beside the map. Each block is still one editable element.
"""
import math

from . import content, ink, objects, svg
from .ink import f

PX = 14
STRIP = 184

_SIZES = {
    "small":  {"w": 46, "h": 34},
    "medium": {"w": 62, "h": 46},
    "large":  {"w": 80, "h": 58},
}
_LAYOUT_WEIGHTS = [("organic", 0.4), ("grid", 0.35), ("radial", 0.25)]
_LAYOUT_LABELS = {"organic": "", "grid": " (grid)", "radial": " (radial)"}

_PAL = {
    "fantasy":  dict(bg="#e9dfc4", street="#cbbb94", block="#f3ead2", roofs=["#c9b48c", "#b8a27a", "#d3c29b"],
                     ink="#2a2013", label="#20180c", halo="#efe5c9cc", park="#a9c48f", paving="#e2d6b5",
                     water="#a9c9d6", tree="#5f8a4f", wall="#4a3b2a", landmark="#d9b45a", object="#3a2a1a",
                     strip="#efe3c3", grain=(0.45, 0.38, 0.27), dark=False),
    "modern":   dict(bg="#e3e3de", street="#bcbcb6", block="#f2f1ed", roofs=["#b9b9b3", "#a9a9a3", "#cbcbc5"],
                     ink="#222222", label="#1c1c1c", halo="#efeeeacc", park="#a8c79a", paving="#dcdcd6",
                     water="#b3cfdd", tree="#5f9050", wall="#555555", landmark="#d9b45a", object="#2a2a2a",
                     strip="#ece9e4", grain=(0.4, 0.4, 0.38), dark=False),
    "hightech": dict(bg="#0c1620", street="#0e1923", block="#1b2b37", roofs=["#2c4455", "#35526a", "#274052"],
                     ink="#69dff0", label="#d6f3fb", halo="#0b141cdd", park="#244a3c", paving="#223442",
                     water="#12384d", tree="#3f7f62", wall="#69dff0", landmark="#ff8a5c", object="#7fe3ff",
                     strip="#0d1a23", grain=(0.3, 0.6, 0.7), dark=True),
}
_TINTS = ["#c9a66b", "#8fa9c4", "#a9c48f", "#c48fa0", "#8fc4bb", "#c4b48f", "#b08fc4", "#c4908f", "#8f9fc4", "#a3c48f"]


# ------------------------------------------------------------- geometry ---

class Warp:
    """A gentle smooth distortion of the plane (cell units)."""

    def __init__(self, rng, amount):
        self.a = amount
        self.p = [rng.uniform(0, 6.28) for _ in range(4)]
        self.f = [rng.uniform(0.09, 0.15), rng.uniform(0.07, 0.12)]

    def __call__(self, x, y):
        if not self.a:
            return x, y
        dx = self.a * (math.sin(y * self.f[0] + self.p[0]) + 0.5 * math.sin(x * self.f[1] * 1.7 + self.p[1]))
        dy = self.a * (math.sin(x * self.f[1] + self.p[2]) + 0.5 * math.sin(y * self.f[0] * 1.9 + self.p[3]))
        return x + dx, y + dy


def _to_px(pt):
    return pt[0] * PX, pt[1] * PX


def _rect_outline(x, y, w, h, step=2.5):
    """Perimeter points of a rectangle, subdivided so a warp can bend it."""
    pts = []
    nx, ny = max(1, int(w / step)), max(1, int(h / step))
    for i in range(nx):
        pts.append((x + w * i / nx, y))
    for i in range(ny):
        pts.append((x + w, y + h * i / ny))
    for i in range(nx):
        pts.append((x + w - w * i / nx, y + h))
    for i in range(ny):
        pts.append((x, y + h - h * i / ny))
    return pts


class Block:
    __slots__ = ("outline", "lots", "kind", "district", "name", "cx", "cy", "tint", "bbox", "area")

    def __init__(self, outline, lots, cx, cy, area):
        self.outline, self.lots, self.cx, self.cy, self.area = outline, lots, cx, cy, area
        self.kind = "district"
        self.district = self.name = self.tint = None
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))


def _rect_lots(rng, x, y, w, h, warp, target=(2.3, 3.5)):
    """Lot footprints (lists of cell-space points) filling a rectangle."""
    lots = []

    def split(x, y, w, h):
        tw, th = rng.uniform(*target), rng.uniform(*target)
        if w > tw * 1.9 and (w >= h or h <= th * 1.9):
            at = rng.uniform(0.38, 0.62) * w
            split(x, y, at, h)
            split(x + at, y, w - at, h)
        elif h > th * 1.9:
            at = rng.uniform(0.38, 0.62) * h
            split(x, y, w, at)
            split(x, y + at, w, h - at)
        else:
            lots.append((x, y, w, h))
    split(x, y, w, h)
    out = []
    gap = 0.17
    for lx, ly, lw, lh in lots:
        if lw < 1.4 or lh < 1.4 or rng.random() < 0.07:
            continue
        x0, y0, x1, y1 = lx + gap, ly + gap, lx + lw - gap, ly + lh - gap
        if lw > 2.9 and lh > 2.9 and rng.random() < 0.2:  # L-shaped
            cx, cy = x0 + (x1 - x0) * rng.uniform(0.45, 0.6), y0 + (y1 - y0) * rng.uniform(0.45, 0.6)
            corner = rng.choice(range(4))
            if corner == 0:
                poly = [(cx, y0), (x1, y0), (x1, y1), (x0, y1), (x0, cy), (cx, cy)]
            elif corner == 1:
                poly = [(x0, y0), (cx, y0), (cx, cy), (x1, cy), (x1, y1), (x0, y1)]
            elif corner == 2:
                poly = [(x0, y0), (x1, y0), (x1, cy), (cx, cy), (cx, y1), (x0, y1)]
            else:
                poly = [(x0, y0), (x1, y0), (x1, y1), (cx, y1), (cx, cy), (x0, cy)]
        else:
            poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        out.append([warp(px, py) for px, py in poly])
    return out


# ------------------------------------------------------------- layouts ---

def _layout_organic(rng, W, H, warp):
    blocks = []
    x0, y0, x1, y1 = 2.5, 2.5, W - 2.5, H - 2.5
    max_depth = 6

    def split(x, y, w, h, d):
        too_small = w < 9.5 and h < 9.5
        if d >= max_depth or too_small or (d >= 2 and rng.random() < 0.12):
            blocks.append((x, y, w, h))
            return
        street = {0: 2.4, 1: 1.8, 2: 1.4}.get(d, 1.1)
        vertical = w > h * 1.1 if abs(w - h) > 2 else rng.random() < 0.5
        if w < 9.5:
            vertical = False
        if h < 9.5:
            vertical = True
        if vertical:
            at = rng.uniform(0.36, 0.64) * w
            split(x, y, at - street / 2, h, d + 1)
            split(x + at + street / 2, y, w - at - street / 2, h, d + 1)
        else:
            at = rng.uniform(0.36, 0.64) * h
            split(x, y, w, at - street / 2, d + 1)
            split(x, y + at + street / 2, w, h - at - street / 2, d + 1)
    split(x0, y0, x1 - x0, y1 - y0, 0)
    return _rect_blocks(rng, blocks, warp, 0.5)


def _layout_grid(rng, W, H, warp):
    x0, y0, x1, y1 = 2.5, 2.5, W - 2.5, H - 2.5
    cols = max(3, int((x1 - x0) / rng.uniform(7.5, 9.5)))
    rows = max(2, int((y1 - y0) / rng.uniform(7, 9)))

    def edges(a, b, n):
        ws = [rng.uniform(0.85, 1.15) for _ in range(n)]
        tot = sum(ws)
        out, acc = [a], a
        for w_ in ws:
            acc += (b - a) * w_ / tot
            out.append(acc)
        out[-1] = b
        return out
    ce, re_ = edges(x0, x1, cols), edges(y0, y1, rows)
    blocks = []
    for j in range(rows):
        for i in range(cols):
            gx0 = 0.55 if i % 3 else 0.95
            gx1 = 0.55 if (i + 1) % 3 else 0.95
            gy0 = 0.55 if j % 3 else 0.95
            gy1 = 0.55 if (j + 1) % 3 else 0.95
            xa, xb = ce[i] + (0 if i == 0 else gx0), ce[i + 1] - (0 if i == cols - 1 else gx1)
            ya, yb = re_[j] + (0 if j == 0 else gy0), re_[j + 1] - (0 if j == rows - 1 else gy1)
            blocks.append((xa, ya, xb - xa, yb - ya))
    return _rect_blocks(rng, blocks, warp, 0.0)


def _rect_blocks(rng, rects, warp, _unused):
    out = []
    for (x, y, w, h) in rects:
        if w < 4 or h < 4:
            continue
        pad = 0.45
        outline = [warp(px, py) for px, py in _rect_outline(x, y, w, h)]
        lots = _rect_lots(rng, x + pad, y + pad, w - 2 * pad, h - 2 * pad, warp)
        out.append(Block(outline, lots, *warp(x + w / 2, y + h / 2), w * h))
    return out


def _layout_radial(rng, W, H):
    cx, cy = W / 2, H / 2
    R = min(W, H) / 2 - 2.0
    sx = max(1.0, (W / 2 - 2.0) / R)  # stretch sideways so the town fills a wide map
    r_plaza = 3.8
    inner = r_plaza + 1.4
    n_rings = max(2, int(round((R - inner) / rng.uniform(7.0, 8.2))))
    rings = [inner + (R - inner) * i / n_rings for i in range(n_rings + 1)]
    blocks = []
    street_r = 1.3
    for k in range(len(rings) - 1):
        r0, r1 = rings[k] + street_r / 2, rings[k + 1] - street_r / 2
        if r1 - r0 < 3:
            continue
        mid = (r0 + r1) / 2
        n_sec = max(8, int(round(2 * math.pi * mid * sx / rng.uniform(6.5, 8.0) / 4)) * 4)
        for s in range(n_sec):
            a0, a1 = 2 * math.pi * s / n_sec, 2 * math.pi * (s + 1) / n_sec
            gap = (1.9 if s % (n_sec // 4) == 0 else 1.25) / 2
            ra0, ra1 = a0 + gap / r0 * 1.0, a1 - gap / r0 * 1.0
            if ra1 - ra0 < 0.12:
                continue
            def pt(r, a):
                return cx + sx * r * math.cos(a), cy + r * math.sin(a)
            steps = max(3, int((ra1 - ra0) * r1 / 2.5))
            outline = [pt(r1, ra0 + (ra1 - ra0) * i / steps) for i in range(steps + 1)]
            outline += [pt(r0, ra1 - (ra1 - ra0) * i / steps) for i in range(steps + 1)]
            # lots on a (r, theta) lattice
            lots = []
            pad = 0.45
            lr0, lr1 = r0 + pad, r1 - pad
            n_r = max(1, int(round((lr1 - lr0) / rng.uniform(2.6, 3.4))))
            for ir in range(n_r):
                rr0, rr1 = lr0 + (lr1 - lr0) * ir / n_r, lr0 + (lr1 - lr0) * (ir + 1) / n_r
                mid_r = (rr0 + rr1) / 2
                n_t = max(1, int(round((ra1 - ra0) * mid_r * sx / rng.uniform(2.6, 3.4))))
                for it in range(n_t):
                    if rng.random() < 0.07:
                        continue
                    ta0 = ra0 + (ra1 - ra0) * it / n_t + 0.17 / mid_r
                    ta1 = ra0 + (ra1 - ra0) * (it + 1) / n_t - 0.17 / mid_r
                    lots.append([pt(rr0 + 0.17, ta0), pt(rr0 + 0.17, ta1), pt(rr1 - 0.17, ta1), pt(rr1 - 0.17, ta0)])
            mx, my = pt((r0 + r1) / 2, (a0 + a1) / 2)
            blocks.append(Block(outline, lots, mx, my, (r1 - r0) * (ra1 - ra0) * (r0 + r1) / 2))
    return blocks, (cx, cy, r_plaza, R, sx)


# ----------------------------------------------------------------- main ---

def _poly_d(points):
    return ink.poly_d([_to_px(p) for p in points], clockwise=False)


def generate(rng, genre, params):
    size_key = params.get("size", "medium")
    spec = _SIZES.get(size_key, _SIZES["medium"])
    W, H = spec["w"], spec["h"]
    family = genre if genre != "mixed" else rng.choice(content.GENRES)
    pal = _PAL[family]

    layout = rng.choices([n for n, _ in _LAYOUT_WEIGHTS], [w for _, w in _LAYOUT_WEIGHTS])[0]
    plaza_info = None
    if layout == "radial":
        blocks, plaza_info = _layout_radial(rng, W, H)
        warp = Warp(rng, 0)
    elif layout == "grid":
        warp = Warp(rng, 0)
        blocks = _layout_grid(rng, W, H, warp)
    else:
        warp = Warp(rng, rng.uniform(0.8, 1.3))
        blocks = _layout_organic(rng, W, H, warp)
    if not blocks:  # pragma: no cover - defensive, layouts always yield blocks
        blocks = _layout_grid(rng, W, H, Warp(rng, 0))

    # assign what each block is
    centre = (W / 2, H / 2)
    order = sorted(range(len(blocks)), key=lambda i: math.hypot(blocks[i].cx - centre[0], blocks[i].cy - centre[1]))
    plaza_idx = None
    if layout != "radial" and len(blocks) > 3:
        fits = [i for i in order if 30 <= blocks[i].area <= 110]
        plaza_idx = fits[0] if fits else min(order, key=lambda i: abs(blocks[i].area - 60))
    type_tints = {}
    landmark_budget = max(1, len(blocks) // 7)
    park_budget = max(1, len(blocks) // 8)
    for i, b in enumerate(blocks):
        if i == plaza_idx:
            b.kind = "plaza"
            b.lots = []
        elif park_budget > 0 and rng.random() < 0.13 and b.area > 30:
            b.kind = "park"
            b.lots = []
            park_budget -= 1
        elif landmark_budget > 0 and rng.random() < 0.2 and b.lots:
            b.kind = "landmark"
            b.name = content.generate_name(rng, genre)
            landmark_budget -= 1
        b.district = content.pick(rng, "urban_districts", genre)
        if b.district not in type_tints:
            type_tints[b.district] = _TINTS[len(type_tints) % len(_TINTS)]
        b.tint = type_tints[b.district]

    walled = family == "fantasy" and rng.random() < 0.5 or family == "modern" and rng.random() < 0.1
    W_px, H_px = W * PX, H * PX
    uid = f"u{rng.randrange(10**6)}"
    shadow, paper = f"{uid}sh", f"{uid}pa"
    defs = (f'<filter id="{shadow}" x="-5%" y="-5%" width="115%" height="115%"><feDropShadow dx="1.4" dy="1.6" '
            f'stdDeviation="0.7" flood-color="#000" flood-opacity="0.32"/></filter>'
            + ink.paper_filter(paper, pal["grain"], 0.09 if not pal["dark"] else 0.05))
    body = [f'<rect width="{W_px}" height="{H_px}" fill="{pal["bg"]}"/>']

    # outskirts: scattered trees outside the built-up area
    trees = []
    for _ in range(int(W * H / 14)):
        x, y = rng.uniform(0.4, W - 0.4), rng.uniform(0.4, H - 0.4)
        inside = any(b.bbox[0] - 0.8 < x < b.bbox[2] + 0.8 and b.bbox[1] - 0.8 < y < b.bbox[3] + 0.8 for b in blocks)
        if plaza_info:
            cx, cy, _, R, sx = plaza_info
            inside = math.hypot((x - cx) / sx, y - cy) < R + 1.2
        edge = min(x, y, W - x, H - y) < 2.4
        if not inside and edge:
            r = rng.uniform(3.2, 5.5)
            trees.append(ink.circle_d(x * PX, y * PX, r))
    if trees:
        body.append(f'<path d="{"".join(trees)}" fill="{pal["tree"]}" fill-opacity="0.75" stroke="{pal["ink"]}" '
                    f'stroke-width="0.7" stroke-opacity="0.6"/>')
    if plaza_info:
        cx, cy, rp, R, sx = plaza_info
        body.append(f'<ellipse cx="{f(cx * PX)}" cy="{f(cy * PX)}" rx="{f((R + 0.7) * sx * PX)}" ry="{f((R + 0.7) * PX)}" fill="{pal["street"]}" '
                    f'stroke="{pal["ink"]}" stroke-opacity="0.5" stroke-width="1"/>')
    else:
        xs = [b.bbox for b in blocks]
        x0, y0 = min(b[0] for b in xs) - 0.9, min(b[1] for b in xs) - 0.9
        x1, y1 = max(b[2] for b in xs) + 0.9, max(b[3] for b in xs) + 0.9
        body.append(f'<path d="{_poly_d([warp(px, py) for px, py in _rect_outline(x0, y0, x1 - x0, y1 - y0)])}" '
                    f'fill="{pal["street"]}" stroke="{pal["ink"]}" stroke-opacity="0.5" stroke-width="1"/>')
    if plaza_info:  # central plaza
        cx, cy, rp, R, sx = plaza_info
        el = [f'<ellipse class="map-el-fill" cx="{f(cx * PX)}" cy="{f(cy * PX)}" rx="{f(rp * sx * PX)}" ry="{f(rp * PX)}" fill="{pal["paving"]}" '
              f'stroke="{pal["ink"]}" stroke-width="1"/>']
        el.append(objects.render_icon("fountain", cx * PX, cy * PX, PX * 0.9, pal["object"]))
        plaza_el = svg.map_el("block", cx * PX, cy * PX, "".join(el), fill=pal["paving"], locked=True)
    else:
        plaza_el = ""

    block_parts = [plaza_el]
    n_objects = n_buildings = n_parks = n_plazas = n_landmarks = 0
    n_plazas += 1 if plaza_info else 0
    for b in blocks:
        el = []
        outline_d = _poly_d(b.outline)
        base_fill = {"plaza": pal["paving"], "park": pal["park"]}.get(b.kind, pal["block"])
        tinted = b.kind in ("district", "landmark")
        if tinted:
            el.append(f'<path d="{outline_d}" fill="{pal["block"]}" stroke="{pal["ink"]}" stroke-opacity="0.55" '
                      f'stroke-width="0.9" stroke-linejoin="round"/>')
        el.append(f'<path class="map-el-fill" d="{outline_d}" fill="{b.tint if tinted else base_fill}" '
                  f'fill-opacity="{0.4 if tinted else 1}" '
                  + ('' if tinted else f'stroke="{pal["ink"]}" stroke-opacity="0.55" stroke-width="0.9" ')
                  + 'stroke-linejoin="round"/>')
        label = None
        if b.kind == "district" or b.kind == "landmark":
            groups = [[], [], []]
            lots = b.lots
            landmark_lot = None
            if b.kind == "landmark" and lots:
                landmark_lot = max(range(len(lots)), key=lambda k: _poly_area(lots[k]))
            for k, lot in enumerate(lots):
                if k == landmark_lot:
                    continue
                groups[rng.randrange(3)].append(_poly_d(lot))
                n_buildings += 1
            bg = "".join(
                f'<path d="{"".join(g)}" fill="{pal["roofs"][gi]}" stroke="{pal["ink"]}" stroke-width="0.8" '
                f'stroke-opacity="0.85" stroke-linejoin="round"/>' for gi, g in enumerate(groups) if g)
            el.append(f'<g filter="url(#{shadow})">{bg}</g>')
            if landmark_lot is not None:
                lot = lots[landmark_lot]
                el.append(f'<path d="{_poly_d(lot)}" fill="{pal["landmark"]}" stroke="{pal["ink"]}" stroke-width="1.3" '
                          f'stroke-linejoin="round" filter="url(#{shadow})"/>')
                n_landmarks += 1
                n_buildings += 1
                lcx = sum(p[0] for p in lot) / len(lot)
                lcy = sum(p[1] for p in lot) / len(lot)
                label = (b.name, lcx, lcy, True)
        elif b.kind == "park":
            n_parks += 1
            x0, y0, x1, y1 = b.bbox
            tr = []
            for _ in range(max(4, int(b.area / 9))):
                tx, ty = rng.uniform(x0 + 0.8, x1 - 0.8), rng.uniform(y0 + 0.8, y1 - 0.8)
                if _inside(b.outline, tx, ty):
                    tr.append(ink.circle_d(tx * PX, ty * PX, rng.uniform(2.6, 4.4)))
            el.append(f'<path d="{"".join(tr)}" fill="{pal["tree"]}" stroke="{pal["ink"]}" stroke-width="0.7"/>')
            pcx, pcy = (x0 + x1) / 2, (y0 + y1) / 2
            el.append(f'<ellipse cx="{f(pcx * PX)}" cy="{f(pcy * PX)}" rx="{f(min(x1 - x0, 14) * PX * 0.14)}" '
                      f'ry="{f(min(y1 - y0, 14) * PX * 0.1)}" fill="{pal["water"]}" stroke="{pal["ink"]}" stroke-width="0.8"/>')
            if (x1 - x0) >= 8 and (y1 - y0) >= 6:
                label = ("Park", pcx, pcy + 3.2, False)
        else:  # plaza block
            n_plazas += 1
            x0, y0, x1, y1 = b.bbox
            pcx, pcy = (x0 + x1) / 2, (y0 + y1) / 2
            el.append(objects.render_icon("fountain", pcx * PX, pcy * PX, PX * 0.85, pal["object"]))
            for (sx, sy) in ((x0 + 1.6, y0 + 1.6), (x1 - 1.6, y0 + 1.6), (x0 + 1.6, y1 - 1.6), (x1 - 1.6, y1 - 1.6)):
                icon = objects.render_icon("stall", sx * PX, sy * PX, PX * 0.45, pal["object"])
                el.append(svg.map_el("object", sx * PX, sy * PX, icon, fill=pal["object"]))
                n_objects += 1
            label = ("Plaza", pcx, pcy + 2.6, False)
        if label:
            text, lx, ly, bold = label
            el.append(svg.text(lx * PX, ly * PX + (-3 if bold else 0), text, size=9 if bold else 8, fill=pal["label"],
                               anchor="middle", weight="bold" if bold else "normal", style="normal" if bold else "italic",
                               extra='class="map-el-label"'))
        block_parts.append(svg.map_el("block", b.cx * PX, b.cy * PX, "".join(el),
                                      fill=b.tint if tinted else base_fill, locked=True))
    body.append("".join(block_parts))

    # city wall
    if walled:
        if plaza_info:
            cx, cy, rp, R, sx = plaza_info
            ring = [(cx + (R + 0.7) * sx * math.cos(a / 48 * 2 * math.pi), cy + (R + 0.7) * math.sin(a / 48 * 2 * math.pi))
                    for a in range(48)]
        else:
            xs = [b.bbox for b in blocks]
            x0, y0 = min(b[0] for b in xs) - 0.9, min(b[1] for b in xs) - 0.9
            x1, y1 = max(b[2] for b in xs) + 0.9, max(b[3] for b in xs) + 0.9
            ring = [warp(px, py) for px, py in _rect_outline(x0, y0, x1 - x0, y1 - y0, 2.0)]
        pts = [_to_px(p) for p in ring]
        n = len(pts)
        gates = sorted(rng.sample(range(n), 4))
        gate_set = {g for g in gates}
        segs, cur = [], []
        for i in range(n + 1):
            p = pts[i % n]
            if i % n in gate_set and cur:
                cur.append(p)
                segs.append(cur)
                cur = [p]
            else:
                cur.append(p)
        segs.append(cur)
        wall_d = "".join(ink.polyline_d(s) for s in segs if len(s) > 1)
        body.append(f'<path d="{wall_d}" fill="none" stroke="{pal["ink"]}" stroke-width="5.2" stroke-linecap="butt"/>'
                    f'<path d="{wall_d}" fill="none" stroke="{pal["wall"]}" stroke-width="3.4" stroke-linecap="butt" '
                    f'stroke-dasharray="7 1.2" stroke-opacity="0.95"/>')
        towers = [pts[i] for i in range(0, n, max(1, n // 12))] + [pts[g] for g in gates]
        body.append("".join(svg.circle(x, y, 4.8 if (x, y) in {pts[g] for g in gates} else 3.8, pal["wall"],
                                       stroke=pal["ink"], stroke_width=1.2) for x, y in towers))

    body.append(f'<rect class="paper-grain" width="{W_px}" height="{H_px}" filter="url(#{paper})" pointer-events="none"/>')

    # legend strip beside the map
    layout_label = _LAYOUT_LABELS[layout]
    title = f"City Districts - {content.GENRE_LABELS[genre]}{layout_label}"
    sx = W_px
    strip = [f'<rect x="{sx}" width="{STRIP}" height="{H_px}" fill="{pal["strip"]}"/>',
             f'<path d="M{sx} 0V{H_px}" stroke="{pal["ink"]}" stroke-width="2"/>',
             svg.text(sx + 14, 32, "City", size=15, fill=pal["ink"], weight="bold"),
             svg.text(sx + 14, 47, content.GENRE_LABELS[genre] + layout_label + (", walled" if walled else ""),
                      size=9, fill=pal["ink"], style="italic")]
    ly = 76
    for dist, tint in list(type_tints.items())[:14]:
        strip.append(f'<rect x="{sx + 14}" y="{ly - 10}" width="18" height="13" fill="{tint}" fill-opacity="0.5" '
                     f'stroke="{pal["ink"]}" stroke-width="0.8" rx="2"/>')
        strip.append(svg.text(sx + 40, ly + 1, dist, size=9.5, fill=pal["ink"]))
        ly += 19
    ly += 6
    for lbl, fill in (("Landmark", pal["landmark"]), ("Park", pal["park"]), ("Plaza", pal["paving"])):
        strip.append(f'<rect x="{sx + 14}" y="{ly - 10}" width="18" height="13" fill="{fill}" stroke="{pal["ink"]}" '
                     f'stroke-width="0.8" rx="2"/>')
        strip.append(svg.text(sx + 40, ly + 1, lbl, size=9.5, fill=pal["ink"]))
        ly += 19
    unit = ("10 ft", "3 m")[family != "fantasy"]
    strip.append(svg.text(sx + 14, H_px - 22, f"1 square = {unit}", size=9, fill=pal["ink"], style="italic"))
    body.append("".join(strip))

    doc = svg.ink_doc(W_px + STRIP, H_px, "".join(body), extra_defs=defs, halo=pal["halo"])
    return {
        "svg": doc,
        "meta": {
            "blocks": len(blocks) + (1 if plaza_info else 0), "landmarks": n_landmarks, "layout": layout,
            "objects": n_objects, "size": size_key, "buildings": n_buildings, "parks": n_parks,
            "plazas": n_plazas, "walled": bool(walled), "style": "ink",
        },
        "title_suggestion": title,
    }


def _poly_area(points):
    return abs(ink.signed_area(points))


def _inside(poly, x, y):
    """Even-odd point-in-polygon, cell space."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside
