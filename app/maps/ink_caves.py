"""Ink-style caves and tunnels.

The floor plan comes from the same generators as the classic style
(caves.build): a cellular-automata cavern, a drunkard's-walk tunnel network,
or round chambers joined by tunnels. What changes is the drawing. The blocky
cell grid is upsampled and softened, and its outline is traced with marching
squares and drawn as a smooth curve, so the rock face reads as natural stone
instead of a pixel map. The rock around it is hatched, the floor gets an
inner shadow and a scatter of stipple, and labels carry a halo that matches
the page (the old dark-background maps had pale labels on a pale halo).
"""
from . import caves, content, ink, objects, svg
from .caves import CELL
from .ink import f

_UP = 3  # upsample factor for the smoothed outline

_PAL = {
    "fantasy":  dict(bg="#e9dcc2", paper=(0.45, 0.38, 0.27), grain=0.11, ink="#241a10", floor="#f4ead3",
                     shadow="#7d6a48", stipple="#9d8a63", label="#2a2013", halo="#f4ead3cc", object="#8a7248",
                     marker="#8a3b2a", title="Cave System"),
    "modern":   dict(bg="#d9d7d0", paper=(0.35, 0.35, 0.33), grain=0.10, ink="#1d1d1d", floor="#ecebe6",
                     shadow="#6d6b63", stipple="#8e8c84", label="#1e1e1e", halo="#ecebe6cc", object="#6a6a66",
                     marker="#9a2f2f", title="Tunnel Network"),
    "hightech": dict(bg="#0d151d", paper=(0.3, 0.6, 0.7), grain=0.06, ink="#69dff0", floor="#15222d",
                     shadow="#000000", stipple="#2a4553", label="#d6f3fb", halo="#0b141cdd", object="#5fd6c8",
                     marker="#ff8a5c", title="Access Tunnels"),
}


def _field_from_grid(grid, w, h):
    """Floor=1/wall=0 cell grid -> smooth upsampled field (flat list) with its
    dimensions. Samples sit on cell centres, so sample (i, j) is at
    px = CELL/2 + i*CELL/_UP."""
    raw = [0.0 if grid[y][x] else 1.0 for y in range(h) for x in range(w)]
    up, uw, uh = ink.upsample(raw, w, h, _UP)
    return ink.blur_field(up, uw, uh, 2), uw, uh


def generate(rng, genre, params):
    b = caves.build(rng, genre, params)
    w, h, layout = b["w"], b["h"], b["layout"]
    floor_cells, features, placed = b["floor_cells"], b["features"], b["objects"]
    pal = _PAL[b["style_genre"]]
    W_px, H_px = w * CELL, h * CELL
    step = CELL / _UP
    off = CELL / 2

    field, uw, uh = _field_from_grid(b["grid"], w, h)
    loops = ink.contours(field, uw, uh, 0.5)
    loops = [lp for lp in loops if ink.loop_area(lp) >= 6]
    paths = []
    for lp in loops:
        pts = [(off + x * step, off + y * step) for x, y in lp]
        pts = ink.rdp(pts, 0.9, closed=True)
        paths.append(ink.smooth_closed_d(pts))
    floor_d = "".join(paths)

    # distance field for hatching, from the same smoothed shape
    raster = ink.Raster(W_px, H_px, res=step)

    def contains(px, py):
        ix = int(round((px - off) / step))
        iy = int(round((py - off) / step))
        return 0 <= ix < uw and 0 <= iy < uh and field[iy * uw + ix] >= 0.5

    raster.paint(0, 0, W_px, H_px, contains)
    raster.distance()

    uid = f"c{rng.randrange(10**6)}"
    fp, rough, paper, inner = (f"{uid}{s}" for s in ("fp", "ro", "pa", "in"))
    defs = (
        ink.rough_filter(rough, scale=2.2, freq=0.035)
        + ink.paper_filter(paper, pal["paper"], pal["grain"])
        + ink.inner_shadow_filter(inner, pal["shadow"], 0.6, 3.6)
        + f'<path id="{fp}" fill-rule="evenodd" d="{floor_d}"/>'
    )

    body = [f'<rect width="{W_px}" height="{H_px}" fill="{pal["bg"]}"/>',
            f'<rect class="paper-grain" width="{W_px}" height="{H_px}" filter="url(#{paper})"/>']

    wall_w = 2.6
    near = wall_w + 0.4
    hatch = raster.hatch(rng, 45, 3.0, near, 20, 0.5)
    hatch += raster.hatch(rng, 45, 6.0, near, 42, 0.55, thin=0.2, inset=1.5)
    hatch += raster.hatch(rng, -45, 5.0, near, 10, 0.4, thin=0.25, min_len=2.0)
    body.append(f'<path d="{hatch}" stroke="{pal["ink"]}" stroke-opacity="0.92" stroke-width="0.95" '
                f'stroke-linecap="round" fill="none" filter="url(#{rough})"/>')

    # stipple on the floor, kept clear of the walls
    dots = []
    for (cx, cy) in floor_cells:
        for _ in range(2):
            if rng.random() < 0.55:
                px = (cx + rng.random()) * CELL
                py = (cy + rng.random()) * CELL
                if raster.dist_at(px, py) == 0.0 and all(
                        contains(px + dx, py + dy) for dx, dy in ((-5, 0), (5, 0), (0, -5), (0, 5))):
                    dots.append(f"M{f(px)} {f(py)}h0")
    walls = [
        f'<use href="#{fp}" fill="none" stroke="{pal["ink"]}" stroke-width="{f(wall_w * 2)}" stroke-linejoin="round"/>',
        f'<use href="#{fp}" fill="{pal["floor"]}" filter="url(#{inner})"/>',
        f'<path d="{"".join(dots)}" stroke="{pal["stipple"]}" stroke-width="1.3" stroke-linecap="round" fill="none"/>',
    ]
    body.append(f'<g filter="url(#{rough})">{"".join(walls)}</g>')

    marker_parts = []
    for (x, y, feature) in features:
        px, py = (x + 0.5) * CELL, (y + 0.5) * CELL
        el = [svg.circle(px, py, 3.2, pal["marker"], stroke=pal["floor"], stroke_width=1, extra='class="map-el-fill"')]
        el.append(svg.text(px + 6, py - 5, feature, size=8.5, fill=pal["label"], style="italic",
                           extra='class="map-el-label"'))
        marker_parts.append(svg.map_el("feature", px, py, "".join(el), fill=pal["marker"]))
    body.append("".join(marker_parts))

    obj_scale = CELL * 0.55
    for obj in placed:
        ocx, ocy = obj["cx"], obj["cy"]
        icon = objects.render_icon(obj["icon"], ocx, ocy, obj_scale, pal["object"])
        label = svg.text(ocx, ocy + obj_scale + 9, obj["label"], size=7.8, fill=pal["label"], anchor="middle",
                         extra='class="map-el-label"')
        body.append(svg.map_el("object", ocx, ocy, icon + label, fill=pal["object"]))

    title = f"{pal['title']} - {content.GENRE_LABELS[genre]}{caves._LAYOUT_LABELS[layout]}"
    body.append(svg.text(12, H_px - 10, title, size=12, fill=pal["ink"], weight="bold", extra='class="map-title"'))
    body.append(svg.text(12, H_px - 24, "1 square = 5 ft" if b["style_genre"] == "fantasy" else "1 square = 1.5 m",
                         size=8.5, fill=pal["ink"], style="italic", extra='class="map-title"'))
    css = f".map-title{{paint-order:stroke;stroke:{pal['bg']};stroke-width:4px;stroke-linejoin:round;}}"
    doc = svg.ink_doc(W_px, H_px, "".join(body), extra_defs=defs, halo=pal["halo"], extra_css=css)
    return {
        "svg": doc,
        "meta": {
            "floor_cells": len(floor_cells), "features": len(features), "layout": layout,
            "objects": len(placed), "size": params.get("size", "medium"), "style": "ink",
        },
        "title_suggestion": title,
    }
