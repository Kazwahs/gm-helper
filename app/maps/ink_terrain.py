"""Ink-style wilderness and world maps.

Terrain comes from layered value noise rather than a hex grid. An elevation
field is shaped into one of four landmasses (continent, archipelago,
peninsula, inland sea) and the sea level is chosen by quantile, so a map can
never come out landless. Coastlines are marching-squares contours of that
field, smoothed into curves, with shallow-water bands and ripple lines
around them. A moisture field (boosted near the sea) and a temperature field
(latitude on world maps, a random climate on wilderness maps) decide the
biomes; ridged noise decides where mountains and hills go.

Rivers follow real drainage: depressions are flooded with a priority-flood
pass so every river runs downhill to the sea (or into a lake where the land
holds water), and flow accumulation sets how wide each stretch is. Towns are
sited near water on hospitable land, joined by roads found with A* that
avoid steep ground, water and mountains. The map is drawn as ink symbols
(peaks, hills, trees, reeds, dunes) over softly tinted biomes, with the
legend in its own strip beside the map instead of on top of it. An optional
hex overlay is a single tiny repeating pattern.
"""
import heapq
import math

from . import content, ink, objects, svg
from .ink import f

STRIP = 196  # width of the legend strip, px

_SIZES = {
    "world":      {"small": (760, 540), "medium": (1000, 700), "large": (1280, 900)},
    "wilderness": {"small": (640, 480), "medium": (880, 620), "large": (1120, 780)},
}
_RES = 8.0

_LANDMASS_WEIGHTS = [("continent", 0.4), ("archipelago", 0.25), ("peninsula", 0.2), ("inland_sea", 0.15)]
_LANDMASS_LABELS = {"continent": "", "archipelago": " (archipelago)", "peninsula": " (peninsula)",
                    "inland_sea": " (inland sea)"}
_LAND_FRACTION = {
    "world":      {"continent": 0.42, "archipelago": 0.27, "peninsula": 0.36, "inland_sea": 0.55},
    "wilderness": {"continent": 0.70, "archipelago": 0.50, "peninsula": 0.62, "inland_sea": 0.76},
}

_PAL = {
    "fantasy": dict(sea="#aac9d6", shelf1="#bbd5df", shelf2="#cde1e7", ripple="#7ea3b3", land="#f1e6c8",
                    forest="#a9c28f", desert="#ead397", swamp="#a2b48b", tundra="#e9eff1", hills="#dfd0a5",
                    mountains="#d3c7aa", river="#6f9fc0", ink="#2c2218", road="#7a5a34", strip="#efe3c3",
                    label="#2c2218", halo="#f1e6c8cc", marker="#3a2a1a", poi="#6a3520", tree="#5f8a4f",
                    tree_ink="#27401f", peak="#f4ecd6", peak_shade="#b9aa88", grain=(0.45, 0.38, 0.27),
                    sea_label="#4a7488", dark=False),
    "modern":  dict(sea="#bcd6e3", shelf1="#cde1ea", shelf2="#dcebf1", ripple="#8fb2c3", land="#eef0e4",
                    forest="#a6c493", desert="#e8d9a4", swamp="#a9bfa0", tundra="#eef3f5", hills="#d9dcc0",
                    mountains="#cfcfc4", river="#6aa0c6", ink="#26323a", road="#8a6a4a", strip="#eef0e6",
                    label="#26323a", halo="#eef0e4cc", marker="#26323a", poi="#7a3a2a", tree="#5f9050",
                    tree_ink="#2c4a26", peak="#f2f2ea", peak_shade="#aeb2a8", grain=(0.4, 0.42, 0.4),
                    sea_label="#4f7f97", dark=False),
    "hightech": dict(sea="#0b1c27", shelf1="#10293a", shelf2="#15364a", ripple="#2e6178", land="#1e3138",
                     forest="#27503f", desert="#4e4a30", swamp="#2e473b", tundra="#41535b", hills="#2a4249",
                     mountains="#34494f", river="#3fa6c8", ink="#69dff0", road="#e0a85a", strip="#0d1a23",
                     label="#d6f3fb", halo="#0b1c27dd", marker="#d6f3fb", poi="#ff8a5c", tree="#3f7f62",
                     tree_ink="#0d2018", peak="#4a6670", peak_shade="#22343b", grain=(0.3, 0.6, 0.7),
                     sea_label="#5fb7cf", dark=True),
}


def _quantile(values, q):
    s = sorted(values)
    return s[max(0, min(len(s) - 1, int(q * len(s))))]


def _smoothstep(a, b, v):
    t = max(0.0, min(1.0, (v - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _choose_landmass(rng):
    r, acc = rng.random(), 0.0
    for name, wgt in _LANDMASS_WEIGHTS:
        acc += wgt
        if r <= acc:
            return name
    return _LANDMASS_WEIGHTS[-1][0]


def _elevation(rng, gw, gh, map_type, landmass):
    world = map_type == "world"
    if landmass == "archipelago":
        n = ink.normalise(ink.noise_grid(rng, gw, gh, 6.5 if world else 7.5, 5, 0.55))
    else:
        n = ink.normalise(ink.noise_grid(rng, gw, gh, 3.2 if world else 4.2, 5, 0.52))
    direction = rng.choice([(0, -1), (0, 1), (-1, 0), (1, 0)])
    dip = rng.uniform(0.2, 0.3)
    out = [0.0] * (gw * gh)
    for y in range(gh):
        ny = (y + 0.5) / gh * 2 - 1
        for x in range(gw):
            nx = (x + 0.5) / gw * 2 - 1
            d = min(1.0, math.hypot(nx, ny))
            edge = _smoothstep(1.0, 0.7, max(abs(nx), abs(ny)))
            v = n[y * gw + x]
            if landmass == "continent":
                e = 0.62 * v + 0.55 * (1 - d ** 1.3)
                e *= edge if world else 0.55 + 0.45 * edge
            elif landmass == "archipelago":
                e = 0.85 * v + 0.2 * (1 - d)
                e *= edge if world else 0.7 + 0.3 * edge
            elif landmass == "peninsula":
                a = nx * direction[0] + ny * direction[1]
                lateral = abs(nx * direction[1] + ny * direction[0])
                ramp = max(0.0, min(1.0, (a + 1) / 2)) ** 1.4
                e = (0.55 * v + 0.65 * ramp) * (1 - 0.55 * lateral ** 2)
            else:  # inland_sea
                e = 0.6 * v + 0.3 - 0.75 * math.exp(-(d * d) / (2 * dip * dip))
                e *= 0.85 + 0.15 * edge if not world else 0.55 + 0.45 * edge
            out[y * gw + x] = e
    return out


def _neighbors8(i, gw, gh):
    x, y = i % gw, i // gw
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < gw and 0 <= ny < gh:
            yield ny * gw + nx


def _drainage(elev, land, gw, gh, moisture):
    """Priority-flood: returns (filled elevation, flow-to index, accumulation).
    Every land sample drains to the sea or the map edge."""
    n = gw * gh
    filled = [0.0] * n
    parent = [-1] * n
    closed = bytearray(n)
    heap = []
    for i in range(n):
        x, y = i % gw, i // gw
        if not land[i] or x in (0, gw - 1) or y in (0, gh - 1):
            closed[i] = 1
            filled[i] = elev[i]
            heap.append((elev[i], i))
    heapq.heapify(heap)
    while heap:
        fe, i = heapq.heappop(heap)
        for j in _neighbors8(i, gw, gh):
            if closed[j]:
                continue
            closed[j] = 1
            parent[j] = i
            filled[j] = max(elev[j], fe + 1e-5)
            heapq.heappush(heap, (filled[j], j))
    acc = [0.0] * n
    order = sorted((i for i in range(n) if land[i]), key=lambda i: -filled[i])
    for i in order:
        acc[i] += 1.0 + moisture[i]
        p = parent[i]
        if p >= 0 and land[p]:
            acc[p] += acc[i]
    return filled, parent, acc


def _distance_to(mask, gw, gh):
    r = ink.Raster(gw * _RES, gh * _RES, _RES)
    r.mask = bytearray(mask)
    return r.distance()


def _biome_paths(field, gw, gh, level=0.5, eps=0.7, min_area=5):
    """Smooth closed outlines of a 0/1 membership field, in px."""
    blurred = ink.blur_field(field, gw, gh, 2)
    out = []
    for lp in ink.contours(blurred, gw, gh, level):
        if ink.loop_area(lp) < min_area:
            continue
        pts = [((x + 0.5) * _RES, (y + 0.5) * _RES) for x, y in lp]
        out.append(ink.smooth_closed_d(ink.rdp(pts, eps * _RES * 0.5, closed=True)))
    return "".join(out)


def _symbols(pal):
    ink_c, tree, tree_ink = pal["ink"], pal["tree"], pal["tree_ink"]
    peak, shade = pal["peak"], pal["peak_shade"]

    def mountain(w, h, extra=""):
        return (f'<path d="M{-w} 0L{f(-w * 0.1)} {-h}L{w} 0z" fill="{peak}" stroke="{ink_c}" stroke-width="0.9" stroke-linejoin="round"/>'
                f'<path d="M{f(-w * 0.1)} {-h}L{w} 0L{f(w * 0.15)} 0z" fill="{shade}" stroke="{ink_c}" stroke-width="0.7" stroke-linejoin="round"/>'
                f'{extra}')

    return {
        "m1": mountain(8, 12),
        "m2": mountain(10, 16, f'<path d="M-9 0L-6 -7L-3 0" fill="{peak}" stroke="{ink_c}" stroke-width="0.8" stroke-linejoin="round"/>'),
        "m3": mountain(12, 21, f'<path d="M7 0L10 -9L13 0" fill="{shade}" stroke="{ink_c}" stroke-width="0.8" stroke-linejoin="round"/>'),
        "hill": f'<path d="M-7 0Q-3.5 -7 0 -6.5Q3.5 -7 7 0" fill="{peak}" stroke="{ink_c}" stroke-width="0.9" stroke-linecap="round"/>'
                f'<path d="M2 -5.2Q5 -3 5.5 -1" fill="none" stroke="{ink_c}" stroke-width="0.6"/>',
        "pine": f'<path d="M0 -11L3.6 -5.5H1.7L5 0H-5L-1.7 -5.5H-3.6z" fill="{tree}" stroke="{tree_ink}" stroke-width="0.7" stroke-linejoin="round"/>',
        "oak": f'<path d="M0 0V-3" stroke="{tree_ink}" stroke-width="1.2"/>'
               f'<circle cx="0" cy="-6" r="4.4" fill="{tree}" stroke="{tree_ink}" stroke-width="0.7"/>',
        "reed": f'<path d="M-3 0V-5M0 0V-7M3 0V-4M-7 1.5H7" fill="none" stroke="{tree_ink}" stroke-width="0.9" stroke-linecap="round"/>',
        "dune": f'<path d="M-6 0Q-2 -4.5 1 -1.5Q3 0 6 -1" fill="none" stroke="{ink_c}" stroke-width="0.8" stroke-linecap="round" stroke-opacity="0.7"/>',
        "ice": f'<path d="M-4 0L0 -4L4 0M-1.5 -2.5H1.5" fill="none" stroke="{ink_c}" stroke-width="0.7" stroke-opacity="0.6"/>',
    }


def _scatter(rng, W, H, spacing, accept, jitter=0.3):
    pts = []
    row = 0
    y = spacing * 0.6
    while y < H:
        x = spacing * 0.5 + (spacing * 0.5 if row % 2 else 0)
        while x < W:
            px = x + rng.uniform(-jitter, jitter) * spacing
            py = y + rng.uniform(-jitter, jitter) * spacing
            if accept(px, py):
                pts.append((px, py))
            x += spacing
        y += spacing * 0.82
        row += 1
    pts.sort(key=lambda p: p[1])
    return pts


def _centroid_label_spot(mask, gw, gh):
    """The sample deepest inside the largest connected blob of ``mask``."""
    seen = bytearray(gw * gh)
    best = []
    for s in range(gw * gh):
        if mask[s] and not seen[s]:
            comp, stack = [], [s]
            seen[s] = 1
            while stack:
                i = stack.pop()
                comp.append(i)
                x, y = i % gw, i // gw
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < gw and 0 <= ny < gh:
                        j = ny * gw + nx
                        if mask[j] and not seen[j]:
                            seen[j] = 1
                            stack.append(j)
            if len(comp) > len(best):
                best = comp
    if len(best) < 25:
        return None
    sub = bytearray(gw * gh)
    for i in best:
        sub[i] = 1
    inv = bytearray(1 - v for v in sub)
    r = ink.Raster(gw * _RES, gh * _RES, _RES)
    r.mask = inv
    dist = r.distance()
    i = max(best, key=lambda k: dist[k])
    return ((i % gw + 0.5) * _RES, (i // gw + 0.5) * _RES, len(best))


def generate(rng, genre, map_type, params):
    size_key = params.get("size", "medium")
    family = genre if genre != "mixed" else rng.choice(content.GENRES)
    pal = _PAL[family]
    map_w, map_h = _SIZES[map_type][size_key]
    gw, gh = int(map_w / _RES), int(map_h / _RES)
    n = gw * gh
    world = map_type == "world"
    hex_grid = bool(params.get("hex_grid"))

    landmass = _choose_landmass(rng)
    elev = _elevation(rng, gw, gh, map_type, landmass)
    sea = _quantile(elev, 1 - _LAND_FRACTION[map_type][landmass])
    land = bytearray(1 if e >= sea else 0 for e in elev)
    top = (max(elev) - sea) or 1.0
    rel = [max(0.0, (e - sea) / top) for e in elev]

    # ridged noise -> mountains and hills
    ridge_n = ink.normalise(ink.noise_grid(rng, gw, gh, 5, 4, 0.55))
    hs = [0.55 * rel[i] + 0.6 * (1 - abs(2 * ridge_n[i] - 1)) ** 1.5 * min(1.0, rel[i] * 2.5) for i in range(n)]
    land_hs = [hs[i] for i in range(n) if land[i]]
    mount_thr = _quantile(land_hs, 0.93)
    hill_thr = _quantile(land_hs, 0.80)

    # moisture (boosted near the sea) and temperature
    dist_ocean = _distance_to([0 if land[i] else 1 for i in range(n)], gw, gh)
    m_noise = ink.normalise(ink.noise_grid(rng, gw, gh, 3.0, 4, 0.55))
    moist = [0.62 * m_noise[i] + 0.38 * math.exp(-dist_ocean[i] / 110.0) for i in range(n)]
    climate = rng.choice([0.38, 0.6, 0.6, 0.78])
    t_noise = ink.normalise(ink.noise_grid(rng, gw, gh, 2.5, 3, 0.5))

    biome = [None] * n
    for i in range(n):
        if not land[i]:
            continue
        y = i // gw
        if world:
            temp = 1 - abs((y + 0.5) / gh - 0.5) * 1.9 + (t_noise[i] - 0.5) * 0.25
        else:
            temp = climate + (t_noise[i] - 0.5) * 0.35
        temp -= rel[i] * 0.2
        if hs[i] >= mount_thr:
            biome[i] = "mountains"
        elif hs[i] >= hill_thr:
            biome[i] = "hills"
        elif temp < 0.27:
            biome[i] = "tundra"
        elif moist[i] < 0.26 and temp > 0.4:
            biome[i] = "desert"
        elif moist[i] > 0.76 and rel[i] < 0.3:
            biome[i] = "swamp"
        elif moist[i] > 0.5:
            biome[i] = "forest"
        else:
            biome[i] = "plains"

    # rivers
    filled, parent, acc = _drainage(elev, land, gw, gh, moist)
    land_cells = [i for i in range(n) if land[i]]
    acc_sorted = sorted(acc[i] for i in land_cells)
    thr = max(28.0, acc_sorted[int(len(acc_sorted) * 0.972)])
    is_river = bytearray(1 if (land[i] and acc[i] >= thr) else 0 for i in range(n))
    has_up = bytearray(n)
    for i in range(n):
        if is_river[i] and parent[i] >= 0 and is_river[parent[i]]:
            has_up[parent[i]] = 1
    river_runs = []   # (points, bucket)
    visited = bytearray(n)
    max_acc = max(acc[i] for i in land_cells) or 1.0
    heads = [i for i in range(n) if is_river[i] and not has_up[i]]
    heads.sort(key=lambda i: -acc[i])
    heads = heads[:14]
    for h0 in heads:
        path = []
        i = h0
        while i >= 0:
            path.append(i)
            if visited[i] or not land[i]:
                break
            visited[i] = 1
            i = parent[i]
            if i >= 0 and not is_river[i] and land[i]:
                path.append(i)
                break
        if len(path) < 4:
            continue
        runs, cur = [], [path[0]]
        bucket = lambda k: min(3, int(math.sqrt(acc[k] / max_acc) * 4.2))
        cb = bucket(path[0])
        for k in path[1:]:
            b = bucket(k) if land[k] else cb
            cur.append(k)
            if b != cb:
                runs.append((cur, cb))
                cur, cb = [k], b
        runs.append((cur, cb))
        for cells, b in runs:
            pts = [((c % gw + 0.5) * _RES, (c // gw + 0.5) * _RES) for c in cells]
            river_runs.append((pts, b))
    river_cells = bytearray(n)
    for i in range(n):
        if is_river[i] and visited[i]:
            river_cells[i] = 1
    lake_field = [1.0 if (land[i] and filled[i] - elev[i] > 0.014) else 0.0 for i in range(n)]

    # settlements: near water on hospitable land, spread out
    near_river = bytearray(n)
    for i in range(n):
        if river_cells[i]:
            for j in _neighbors8(i, gw, gh):
                near_river[j] = 1
            near_river[i] = 1
    scored = []
    for i in land_cells:
        b = biome[i]
        if b in ("mountains", "tundra", "desert") or lake_field[i]:
            continue
        sc = {"plains": 1.0, "forest": 0.7, "hills": 0.55, "swamp": 0.2}.get(b, 0.3)
        if near_river[i]:
            sc += 1.1
        if dist_ocean[i] < 3 * _RES:
            sc += 0.7
        sc += rng.uniform(0, 0.4)
        scored.append((sc, i))
    scored.sort(reverse=True)
    n_set = {"small": rng.randint(4, 6), "medium": rng.randint(6, 9), "large": rng.randint(9, 13)}[size_key]
    min_d = math.hypot(map_w, map_h) * 0.12
    towns = []
    for sc, i in scored:
        if len(towns) >= n_set:
            break
        px, py = (i % gw + 0.5) * _RES, (i // gw + 0.5) * _RES
        if px < 30 or py < 30 or px > map_w - 40 or py > map_h - 30:
            continue
        if all(math.hypot(px - q[0], py - q[1]) > min_d for q in towns):
            towns.append((px, py, i))
    towns = [(px, py, i, content.generate_name(rng, genre)) for px, py, i in towns]

    # roads: minimum spanning tree between towns, routed with A*
    road_cells = set()
    road_paths = []
    if len(towns) >= 2:
        from .shapes import minimum_spanning_tree
        edges = minimum_spanning_tree([(t[0], t[1]) for t in towns])
        penalty = {"mountains": 7.0, "hills": 1.8, "forest": 1.4, "swamp": 3.0, "tundra": 1.6, "desert": 1.3}

        def cost(ix, iy):
            j = iy * gw + ix
            if not land[j] or lake_field[j]:
                return None
            c = 1.0 + penalty.get(biome[j], 0.0)
            if river_cells[j]:
                c += 2.5
            if (j in road_cells):
                c *= 0.35
            return c

        for a, b in edges:
            ia, ib = towns[a][2], towns[b][2]
            path = ink.grid_path(gw, gh, (ia % gw, ia // gw), (ib % gw, ib // gw), cost)
            if path:
                pts = [((x + 0.5) * _RES, (y + 0.5) * _RES) for x, y in path]
                road_paths.append(ink.smooth_open_d(ink.rdp(pts, 3.0)))
                road_cells.update(y * gw + x for x, y in path)

    # points of interest (kept off towns, rivers and roads)
    taken = {t[2] for t in towns} | {i for i in range(n) if river_cells[i]} | road_cells
    poi_candidates = [
        (i % gw, i // gw, (i % gw + 0.5) * _RES, (i // gw + 0.5) * _RES)
        for i in land_cells if i not in taken and not lake_field[i]
        and 30 < (i % gw + 0.5) * _RES < map_w - 30 and 30 < (i // gw + 0.5) * _RES < map_h - 40
    ]
    n_poi = max(2, round(map_w * map_h / 140000)) + rng.randint(0, 2)
    pois = objects._place_scattered(rng, poi_candidates, objects._pool(objects.POINTS_OF_INTEREST, genre), n_poi, 95.0)

    uid = f"t{rng.randrange(10**6)}"
    ids = {k: f"{uid}{k}" for k in ("land", "clip", "rough", "paper", "hex")}
    # ---- coast, shelf bands ----
    sea_field = ink.blur_field([e - sea for e in elev], gw, gh, 1)

    def outline(level, eps=0.8, min_area=6):
        loops = ink.contours(sea_field, gw, gh, level)
        out = []
        for lp in loops:
            if ink.loop_area(lp) < min_area:
                continue
            pts = [((x + 0.5) * _RES, (y + 0.5) * _RES) for x, y in lp]
            out.append(ink.smooth_closed_d(ink.rdp(pts, eps * 2.2, closed=True)))
        return "".join(out)

    land_d = outline(0.0)
    shelf1_d = outline(-0.075, 1.2, 14)
    shelf2_d = outline(-0.035, 1.0, 10)
    ripple_d = outline(-0.05, 1.2, 14)

    symbols = _symbols(pal)
    defs = "".join(f'<g id="{uid}{k}">{v}</g>' for k, v in symbols.items())
    defs += (f'<path id="{ids["land"]}" fill-rule="evenodd" d="{land_d}"/>'
             f'<clipPath id="{ids["clip"]}"><use href="#{ids["land"]}" clip-rule="evenodd"/></clipPath>'
             + ink.rough_filter(ids["rough"], 2.2, 0.03, 5)
             + ink.paper_filter(ids["paper"], pal["grain"], 0.09 if not pal["dark"] else 0.05))
    if hex_grid:
        hexr = 26.0
        hexh = math.sqrt(3) * hexr
        defs += (f'<pattern id="{ids["hex"]}" width="{f(3 * hexr)}" height="{f(hexh)}" patternUnits="userSpaceOnUse">'
                 f'<path d="M0 {f(hexh / 2)}L{f(hexr / 2)} 0H{f(1.5 * hexr)}L{f(2 * hexr)} {f(hexh / 2)}L{f(1.5 * hexr)} {f(hexh)}'
                 f'H{f(hexr / 2)}zM{f(2 * hexr)} {f(hexh / 2)}H{f(3 * hexr)}" fill="none" stroke="{pal["ink"]}" '
                 f'stroke-width="0.7" stroke-opacity="0.35"/></pattern>')

    W_px, H_px = map_w + STRIP, map_h
    body = [f'<rect width="{map_w}" height="{map_h}" fill="{pal["sea"]}"/>']
    body.append(f'<path d="{shelf1_d}" fill="{pal["shelf1"]}" fill-rule="evenodd"/>')
    body.append(f'<path d="{shelf2_d}" fill="{pal["shelf2"]}" fill-rule="evenodd"/>')
    body.append(f'<path d="{ripple_d}" fill="none" stroke="{pal["ripple"]}" stroke-width="0.8" '
                f'stroke-opacity="0.6" stroke-dasharray="5 4"/>')

    land_layer = [f'<use href="#{ids["land"]}" fill="{pal["land"]}"/>']
    tints = {}
    for b in ("hills", "mountains", "forest", "desert", "swamp", "tundra"):
        fld = [1.0 if biome[i] == b else 0.0 for i in range(n)]
        d = _biome_paths(fld, gw, gh)
        if d:
            tints[b] = d
            op = 0.55 if b in ("forest", "desert", "swamp", "tundra") else 0.5
            land_layer.append(f'<path d="{d}" fill="{pal[b]}" fill-opacity="{op}" fill-rule="evenodd"/>')
    # lakes and rivers
    lake_d = _biome_paths(lake_field, gw, gh, 0.5, 0.6, 3)
    if lake_d:
        land_layer.append(f'<path d="{lake_d}" fill="{pal["river"]}" stroke="{pal["ink"]}" stroke-width="0.9" '
                          f'stroke-opacity="0.7" fill-rule="evenodd"/>')
    widths = [1.1, 1.8, 2.7, 3.7]
    for b in range(4):
        d = "".join(ink.smooth_open_d(pts) for pts, bk in river_runs if bk == b and len(pts) >= 2)
        if d:
            land_layer.append(f'<path d="{d}" fill="none" stroke="{pal["river"]}" stroke-width="{widths[b]}" '
                              f'stroke-linecap="round" stroke-linejoin="round"/>')
    body.append(f'<g clip-path="url(#{ids["clip"]})">{"".join(land_layer)}</g>')
    body.append(f'<use href="#{ids["land"]}" fill="none" stroke="{pal["ink"]}" stroke-width="1.7" '
                f'stroke-linejoin="round" filter="url(#{ids["rough"]})"/>')
    if road_paths:
        body.append(f'<path d="{"".join(road_paths)}" fill="none" stroke="{pal["road"]}" stroke-width="1.7" '
                    f'stroke-dasharray="5 3.5" stroke-linecap="round" stroke-linejoin="round"/>')

    # ---- symbols ----
    blocked = set()
    for i in taken:
        blocked.add(i)
    town_px = [(t[0], t[1]) for t in towns]
    poi_px = [(p["cx"], p["cy"]) for p in pois]

    def cell(px, py):
        ix, iy = int(px / _RES), int(py / _RES)
        return iy * gw + ix if 0 <= ix < gw and 0 <= iy < gh else -1

    def clear(px, py, i, margin=18):
        if i < 0 or not land[i] or lake_field[i] or river_cells[i] or i in road_cells:
            return False
        if any(abs(px - tx) < margin and abs(py - ty) < margin for tx, ty in town_px):
            return False
        return not any(abs(px - tx) < margin and abs(py - ty) < margin for tx, ty in poi_px)

    sym = []
    # mountains and hills
    for px, py in _scatter(rng, map_w, map_h, 20, lambda x, y: biome[max(cell(x, y), 0)] == "mountains"
                           and clear(x, y, cell(x, y), 14) and dist_ocean[cell(x, y)] > 18):
        i = cell(px, py)
        k = "m3" if hs[i] > mount_thr * 1.12 else rng.choice(["m1", "m2"])
        sym.append(f'<use href="#{uid}{k}" x="{f(px)}" y="{f(py + 7)}"/>')
    for px, py in _scatter(rng, map_w, map_h, 19, lambda x, y: biome[max(cell(x, y), 0)] == "hills"
                           and clear(x, y, cell(x, y), 12) and dist_ocean[cell(x, y)] > 14, 0.35):
        sym.append(f'<use href="#{uid}hill" x="{f(px)}" y="{f(py + 3)}"/>')
    # forests
    for px, py in _scatter(rng, map_w, map_h, 13, lambda x, y: biome[max(cell(x, y), 0)] == "forest"
                           and clear(x, y, cell(x, y), 10) and dist_ocean[cell(x, y)] > 12 and rng.random() < 0.8):
        i = cell(px, py)
        cold = (not world and climate < 0.5) or (world and abs((i // gw + 0.5) / gh - 0.5) > 0.27)
        k = "pine" if (cold or moist[i] < 0.62) and rng.random() < 0.8 else "oak"
        sym.append(f'<use href="#{uid}{k}" x="{f(px)}" y="{f(py + 4)}"/>')
    for px, py in _scatter(rng, map_w, map_h, 17, lambda x, y: biome[max(cell(x, y), 0)] == "swamp"
                           and clear(x, y, cell(x, y), 10) and dist_ocean[cell(x, y)] > 12):
        sym.append(f'<use href="#{uid}reed" x="{f(px)}" y="{f(py + 2)}"/>')
    for px, py in _scatter(rng, map_w, map_h, 22, lambda x, y: biome[max(cell(x, y), 0)] == "desert"
                           and clear(x, y, cell(x, y), 10) and dist_ocean[cell(x, y)] > 12, 0.4):
        sym.append(f'<use href="#{uid}dune" x="{f(px)}" y="{f(py)}"/>')
    for px, py in _scatter(rng, map_w, map_h, 24, lambda x, y: biome[max(cell(x, y), 0)] == "tundra"
                           and clear(x, y, cell(x, y), 10) and dist_ocean[cell(x, y)] > 12, 0.4):
        sym.append(f'<use href="#{uid}ice" x="{f(px)}" y="{f(py)}"/>')
    body.append("".join(sym))

    if hex_grid:
        body.append(f'<rect width="{map_w}" height="{map_h}" fill="url(#{ids["hex"]})" pointer-events="none"/>')
    body.append(f'<rect class="paper-grain" width="{map_w}" height="{map_h}" filter="url(#{ids["paper"]})" pointer-events="none"/>')

    # ---- editable pieces: region names, towns, points of interest ----
    el_parts = []
    spots = []
    mount_spot = _centroid_label_spot(bytearray(1 if biome[i] == "mountains" else 0 for i in range(n)), gw, gh)
    forest_spot = _centroid_label_spot(bytearray(1 if biome[i] == "forest" else 0 for i in range(n)), gw, gh)
    desert_spot = _centroid_label_spot(bytearray(1 if biome[i] == "desert" else 0 for i in range(n)), gw, gh)
    sea_spot = _centroid_label_spot(bytearray(0 if land[i] else 1 for i in range(n)), gw, gh)
    suffix = {"fantasy": ("Mountains", "Forest", "Desert", "Sea"), "modern": ("Mountains", "National Forest", "Desert", "Ocean"),
              "hightech": ("Range", "Reserve", "Wastes", "Sea")}[family]
    for spot, word, color, sz in ((mount_spot, suffix[0], pal["label"], 12), (forest_spot, suffix[1], pal["label"], 11),
                                  (desert_spot, suffix[2], pal["label"], 11), (sea_spot, suffix[3], pal["sea_label"], 14)):
        if spot and 40 < spot[0] < map_w - 40 and spot[2] > 40:
            name = f"{content.generate_name(rng, genre)} {word}"
            spots.append((spot[0], spot[1], name, color, sz))
    for (x, y, name, color, sz) in spots:
        txt = svg.text(x, y, name, size=sz, fill=color, anchor="middle", style="italic", weight="bold",
                       extra='class="map-el-label" letter-spacing="2" opacity="0.9"')
        el_parts.append(svg.map_el("region", x, y, txt, fill=color))

    for k, (px, py, i, name) in enumerate(towns):
        is_city = k < max(1, len(towns) // 4)
        is_town = not is_city and k < max(2, len(towns) // 2)
        r = 5.6 if is_city else (4.0 if is_town else 2.9)
        parts = []
        if is_city:
            parts.append(svg.circle(px, py, r + 2.6, "none", stroke=pal["marker"], stroke_width=1.1))
        parts.append(svg.circle(px, py, r, pal["marker"], stroke=pal["land"], stroke_width=1.2, extra='class="map-el-fill"'))
        left = px > map_w - 110
        parts.append(svg.text(px + (-(r + 5) if left else r + 5), py + 3.4, name, size=11 if is_city else 9.5,
                              fill=pal["label"], anchor="end" if left else "start",
                              weight="bold" if is_city else "normal", extra='class="map-el-label"'))
        el_parts.append(svg.map_el("settlement", px, py, "".join(parts), fill=pal["marker"]))
    poi_scale = 8.5
    for p in pois:
        icon = objects.render_icon(p["icon"], p["cx"], p["cy"], poi_scale, pal["poi"])
        label = svg.text(p["cx"], p["cy"] + poi_scale + 10, p["label"], size=8.5, fill=pal["label"], anchor="middle",
                         extra='class="map-el-label"')
        el_parts.append(svg.map_el("poi", p["cx"], p["cy"], icon + label, fill=pal["poi"]))
    body.append("".join(el_parts))

    # ---- legend strip ----
    realm = content.generate_name(rng, genre)
    landmass_label = _LANDMASS_LABELS[landmass]
    title = (f"The Lands of {realm}" if world else f"{realm} Wilds") + f" - {content.GENRE_LABELS[genre]}{landmass_label}"
    sx = map_w
    strip = [f'<rect x="{sx}" width="{STRIP}" height="{H_px}" fill="{pal["strip"]}"/>',
             f'<path d="M{sx} 0V{H_px}" stroke="{pal["ink"]}" stroke-width="2"/>']
    heading = f"{realm}" if not world else f"Realm of {realm}"
    strip.append(svg.text(sx + 16, 34, heading, size=15, fill=pal["ink"], weight="bold"))
    strip.append(svg.text(sx + 16, 50, content.GENRE_LABELS[genre] + landmass_label, size=9, fill=pal["ink"], style="italic"))
    ly = 82
    present = [b for b in ("plains", "forest", "hills", "mountains", "desert", "swamp", "tundra") if b in set(biome)]
    for b in present:
        label = content.biome_label(genre, b) if genre != "mixed" else content.biome_label_mixed(rng, b)
        fill = pal["land"] if b == "plains" else pal[b]
        strip.append(f'<rect x="{sx + 16}" y="{ly - 10}" width="22" height="14" fill="{fill}" stroke="{pal["ink"]}" '
                     f'stroke-width="0.8" rx="2"/>')
        sym_key = {"forest": "pine", "hills": "hill", "mountains": "m1", "desert": "dune", "swamp": "reed", "tundra": "ice"}.get(b)
        if sym_key:
            sc = 0.8 if sym_key != "m1" else 0.65
            strip.append(f'<use href="#{uid}{sym_key}" transform="translate({sx + 27} {ly + 1}) scale({sc})"/>')
        strip.append(svg.text(sx + 46, ly + 1, label, size=10, fill=pal["ink"]))
        ly += 22
    ly += 4
    strip.append(f'<path d="M{sx + 16} {ly - 3}h22" stroke="{pal["river"]}" stroke-width="2.4" stroke-linecap="round"/>')
    strip.append(svg.text(sx + 46, ly + 1, "River", size=10, fill=pal["ink"]))
    ly += 20
    if road_paths:
        strip.append(f'<path d="M{sx + 16} {ly - 3}h22" stroke="{pal["road"]}" stroke-width="1.7" stroke-dasharray="5 3.5"/>')
        strip.append(svg.text(sx + 46, ly + 1, "Road", size=10, fill=pal["ink"]))
        ly += 20
    for lbl, r in (("City", 5.6), ("Town", 4.0), ("Village", 2.9)):
        strip.append(svg.circle(sx + 27, ly - 3, r, pal["marker"], stroke=pal["strip"], stroke_width=1))
        strip.append(svg.text(sx + 46, ly + 1, lbl, size=10, fill=pal["ink"]))
        ly += 20
    # scale bar and compass
    miles = 200 if world else 25
    bar = 100.0
    by = H_px - 70
    strip.append(f'<path d="M{sx + 16} {by}h{bar}M{sx + 16} {by - 4}v8M{sx + 16 + bar} {by - 4}v8" stroke="{pal["ink"]}" stroke-width="1.4" fill="none"/>')
    strip.append(svg.text(sx + 16, by + 16, f"{miles} mi", size=9, fill=pal["ink"]))
    cx, cy = sx + STRIP - 44, H_px - 44
    strip.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="none" stroke="{pal["ink"]}" stroke-width="1"/>'
                 f'<path d="M{cx} {cy - 22}L{cx + 5} {cy}L{cx} {cy + 22}L{cx - 5} {cy}z" fill="{pal["ink"]}"/>'
                 f'<path d="M{cx - 22} {cy}L{cx} {cy - 4}L{cx + 22} {cy}L{cx} {cy + 4}z" fill="{pal["ink"]}" fill-opacity="0.55"/>')
    strip.append(svg.text(cx, cy - 27, "N", size=10, fill=pal["ink"], anchor="middle", weight="bold"))
    body.append("".join(strip))

    css = f".map-title{{paint-order:stroke;stroke:{pal['land']};stroke-width:4px}}"
    doc = svg.ink_doc(W_px, H_px, "".join(body), extra_defs=defs, halo=pal["halo"], extra_css=css,
                      view_box=f"0 0 {W_px} {H_px}")
    return {
        "svg": doc,
        "meta": {
            "hexes": 0, "rivers": len(heads), "settlements": len(towns), "landmass": landmass,
            "points_of_interest": len(pois), "size": size_key, "roads": len(road_paths),
            "hex_grid": hex_grid, "style": "ink",
        },
        "title_suggestion": title,
    }
