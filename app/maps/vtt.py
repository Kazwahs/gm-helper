"""Virtual tabletop export: wall and door geometry for the Universal VTT
(.dd2vtt / .uvtt) format, which Foundry VTT, Roll20 (via importers), Fantasy
Grounds and other tabletops can import to get a map with dynamic lighting set
up for them.

The inked dungeon, building and cave generators hand back their floor shape;
this module turns it into line-of-sight walls and door portals, in grid
squares. The map image is rendered by the browser (it already draws the SVG)
and added to the document there.

This has been checked against the published format description, not against a
live tabletop. See README.
"""
import math

from . import ink

FORMAT_VERSION = 0.3
DEFAULT_PIXELS_PER_GRID = 100
MIN_PPG, MAX_PPG = 32, 256
_RES = 4  # sampling cells per grid square when tracing the floor outline
_EPS = 0.8  # outline simplification tolerance, in sampling cells (0.2 of a square)


def _r(v):
    return round(float(v), 3)


# ------------------------------------------------------------- outlines ---

def trace_walls(floor, cols, rows, res=_RES, eps=_EPS):
    """Closed outlines (in grid squares) around every floor region.

    `floor` is a list of rows of booleans, `cols*res` wide and `rows*res`
    tall. Each outline is a list of (x, y) points whose last point repeats
    the first."""
    width, height = cols * res, rows * res
    edges = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    for j in range(height):
        row = floor[j]
        for i in range(width):
            if not row[i]:
                continue
            if j == 0 or not floor[j - 1][i]:
                add((i, j), (i + 1, j))
            if i == width - 1 or not row[i + 1]:
                add((i + 1, j), (i + 1, j + 1))
            if j == height - 1 or not floor[j + 1][i]:
                add((i + 1, j + 1), (i, j + 1))
            if i == 0 or not row[i - 1]:
                add((i, j + 1), (i, j))

    loops = []
    while edges:
        start = next(iter(edges))
        points = [start]
        cur = start
        while True:
            outs = edges[cur]
            nxt = outs.pop()
            if not outs:
                del edges[cur]
            cur = nxt
            if cur == start:
                break
            points.append(cur)
        points = _drop_collinear(points)
        if len(points) < 3:
            continue
        points = ink.rdp([(float(x), float(y)) for x, y in points], eps, closed=True)
        if len(points) < 3:
            continue
        loop = [(_r(x / res), _r(y / res)) for x, y in points]
        loop.append(loop[0])
        loops.append(loop)
    return loops


def _drop_collinear(points):
    out = []
    n = len(points)
    for k in range(n):
        a, b, c = points[k - 1], points[k], points[(k + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) == (b[1] - a[1]) * (c[0] - b[0]):
            continue
        out.append(b)
    return out


def _blank(cols, rows, res=_RES):
    return [[False] * (cols * res) for _ in range(rows * res)]


def rooms_geometry(rooms, corridors, doors, cols, rows, px):
    """Walls and doors for a room-and-corridor map. `rooms` have .x .y .w .h
    and .contains(x, y) in grid squares; `corridors` have .paint =
    (x0, y0, x1, y1) in squares; `doors` are dicts with x, y, width (pixels),
    axis ('h' for a corridor running left-right) and kind."""
    res = _RES
    floor = _blank(cols, rows)
    for room in rooms:
        i0, i1 = max(0, int(math.floor(room.x * res))), min(cols * res, int(math.ceil(room.right * res)))
        j0, j1 = max(0, int(math.floor(room.y * res))), min(rows * res, int(math.ceil(room.bottom * res)))
        for j in range(j0, j1):
            for i in range(i0, i1):
                if room.contains((i + 0.5) / res, (j + 0.5) / res):
                    floor[j][i] = True
    for c in corridors:
        x0, y0, x1, y1 = c.paint
        for j in range(max(0, int(math.floor(y0 * res))), min(rows * res, int(math.ceil(y1 * res)))):
            for i in range(max(0, int(math.floor(x0 * res))), min(cols * res, int(math.ceil(x1 * res)))):
                floor[j][i] = True

    walls = trace_walls(floor, cols, rows)
    portals = []
    for d in doors:
        x, y, w = d["x"] / px, d["y"] / px, d["width"] / px
        if d["axis"] == "h":  # corridor runs left-right, so the door is a vertical bar
            a, b, rotation = (x, y - w / 2), (x, y + w / 2), math.pi / 2
        else:
            a, b, rotation = (x - w / 2, y), (x + w / 2, y), 0.0
        kind = d["kind"]
        if kind == "secret":
            walls.append([(_r(a[0]), _r(a[1])), (_r(b[0]), _r(b[1]))])  # players see a wall
        elif kind in ("door", "locked", "swing"):
            portals.append({
                "position": {"x": _r(x), "y": _r(y)},
                "bounds": [{"x": _r(a[0]), "y": _r(a[1])}, {"x": _r(b[0]), "y": _r(b[1])}],
                "rotation": round(rotation, 6),
                "closed": kind != "swing",
                "freestanding": False,
            })
        # an open archway needs nothing: it is simply a gap in the walls
    return {"cols": cols, "rows": rows, "walls": walls, "portals": portals}


def cave_geometry(loops_px, cell, cols, rows):
    """Walls for a cave map from its floor outlines (pixel coordinates)."""
    walls = []
    for loop in loops_px:
        pts = [(_r(x / cell), _r(y / cell)) for x, y in loop]
        if len(pts) >= 3:
            pts.append(pts[0])
            walls.append(pts)
    return {"cols": cols, "rows": rows, "walls": walls, "portals": []}


# ------------------------------------------------------------- document ---

def clamp_ppg(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PIXELS_PER_GRID
    return max(MIN_PPG, min(MAX_PPG, value))


def uvtt_document(geometry, pixels_per_grid=DEFAULT_PIXELS_PER_GRID, image=""):
    """The Universal VTT document for a map. `image` is the base64 PNG, which
    the browser adds; everything else is here."""
    return {
        "format": FORMAT_VERSION,
        "resolution": {
            "map_origin": {"x": 0, "y": 0},
            "map_size": {"x": geometry["cols"], "y": geometry["rows"]},
            "pixels_per_grid": clamp_ppg(pixels_per_grid),
        },
        "line_of_sight": [[{"x": x, "y": y} for x, y in wall] for wall in geometry["walls"]],
        "objects_line_of_sight": [],
        "portals": geometry["portals"],
        "environment": {"baked_lighting": True, "ambient_light": "ffffffff"},
        "lights": [],
        "image": image,
    }
