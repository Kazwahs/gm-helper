"""Ink-style dungeons and building interiors.

Layout. Rooms are real vector shapes on the grid (rectangles, octagons,
circles, crosses) joined by *straight* corridors that run between the
facing sides of two rooms, so every opening is a proper doorway rather than
a line dragged between room centres. Three archetypes:

- warren: a tree grown outward from a first room (each new room is placed
  off an existing one with a corridor that is straight by construction),
  then a few extra straight links added to create loops.
- keep: a planned grid of halls and chambers, neighbours linked along the
  grid lines with some links dropped (never disconnecting the map).
- hub: a large central rotunda with chambers on all sides, spokes to the
  four axis rooms and a ring road between neighbours.

Drawing. The floor is one merged path. A thick pen stroke around it, with
the floor painted over the inner half, leaves a wall line that is open
wherever a corridor meets a room, for free. Rock around the floor is
hatched from a distance field, the floor gets a faint grid and a soft inner
shadow, and the lot is run through a slight displacement filter so lines
wobble like a pen. Doors sit on the boundary where each corridor leaves a
room: plain openings, doors, open doors, locked doors, and secret doors
(drawn as plain wall, with an 'S' marker that is hidden on the player
view). Each room is still its own editable element - its tint, number,
name and furniture - so rename/recolour/fog-reveal keep working; the walls
themselves are one drawn piece.
"""
import math

from . import content, ink, objects, svg
from .ink import f

PX = 16  # px per grid square (keep in step with generator.GRID_PX)

_SIZES = {
    "small":  {"w": 42, "h": 30, "rooms": (7, 9)},
    "medium": {"w": 58, "h": 40, "rooms": (11, 14)},
    "large":  {"w": 74, "h": 52, "rooms": (17, 22)},
}

_LAYOUT_WEIGHTS = {
    "dungeon":  [("warren", 0.50), ("keep", 0.28), ("hub", 0.22)],
    "interior": [("warren", 0.34), ("keep", 0.46), ("hub", 0.20)],
}
_LAYOUT_LABELS = {"warren": "", "keep": " (planned)", "hub": " (radial)"}

# (kind, genre-family) -> colours. `hatch` is the pen colour for the rock.
_PAL = {
    ("dungeon", "fantasy"): dict(bg="#efe5cb", paper=(0.45, 0.38, 0.27), grain=0.10, ink="#231a10", floor="#f8f1de",
                                 grid="#b9ab88", shadow="#8b7a55", label="#2a2013", halo="#f8f1decc",
                                 object="#8a7248", door="#c9a46b", locked="#9b6a3a", accent="#8a3b2a"),
    ("dungeon", "modern"):  dict(bg="#dedcd4", paper=(0.35, 0.35, 0.33), grain=0.10, ink="#1c1c1c", floor="#efeee9",
                                 grid="#b3b2aa", shadow="#77756c", label="#1e1e1e", halo="#efeee9cc",
                                 object="#6a6a66", door="#a8a79d", locked="#7d6a4a", accent="#9a2f2f"),
    ("dungeon", "hightech"): dict(bg="#0d151d", paper=(0.3, 0.6, 0.7), grain=0.06, ink="#69dff0", floor="#16232f",
                                  grid="#27414f", shadow="#000000", label="#d6f3fb", halo="#0b141cdd",
                                  object="#5fd6c8", door="#2b5668", locked="#a85a3a", accent="#ff8a5c"),
    ("interior", "fantasy"): dict(bg="#f2ead6", paper=(0.45, 0.38, 0.27), grain=0.07, ink="#3a2a1b", floor="#fbf6e8",
                                  grid="#d3c6a2", shadow="#8b7a55", label="#2a2013", halo="#fbf6e8cc",
                                  object="#8a7248", door="#b98a52", locked="#8a5a2c", accent="#8a3b2a"),
    ("interior", "modern"):  dict(bg="#e9eef3", paper=(0.4, 0.45, 0.5), grain=0.06, ink="#1d2733", floor="#fbfcfd",
                                  grid="#c3ccd6", shadow="#7f8c9b", label="#1c2430", halo="#fbfcfdcc",
                                  object="#5a6a7a", door="#9fb0c0", locked="#7d6a4a", accent="#9a2f2f"),
    ("interior", "hightech"): dict(bg="#0b1822", paper=(0.3, 0.6, 0.7), grain=0.06, ink="#6fe3f2", floor="#142533",
                                   grid="#244257", shadow="#000000", label="#d6f3fb", halo="#0b141cdd",
                                   object="#5fd6c8", door="#2b5668", locked="#a85a3a", accent="#ff8a5c"),
}
_TINTS = ["#e8c9a0", "#b9d3e6", "#cfe3b8", "#e6bcc8", "#c6d6d2", "#ddd1a8", "#d3bfe3", "#e3b9b0"]

_MAX_LINK_GAP = 9


# ---------------------------------------------------------------- rooms ---

class Room:
    __slots__ = ("idx", "x", "y", "w", "h", "shape", "cut", "arm", "label", "feature", "number", "tint", "stairs")

    def __init__(self, idx, x, y, w, h, shape="rect"):
        self.idx, self.x, self.y, self.w, self.h, self.shape = idx, x, y, w, h, shape
        self.cut = max(1, int(round(min(w, h) * 0.28))) if shape == "octagon" else 0
        self.arm = 3 if shape == "cross" else 0
        self.label = self.feature = self.tint = self.stairs = None
        self.number = idx + 1

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    def contains(self, px, py):
        """Point test in grid units."""
        if px < self.x or px > self.right or py < self.y or py > self.bottom:
            return False
        if self.shape == "rect":
            return True
        if self.shape == "circle":
            a, b = self.w / 2, self.h / 2
            return ((px - self.cx) / a) ** 2 + ((py - self.cy) / b) ** 2 <= 1.0
        if self.shape == "octagon":
            dx = min(px - self.x, self.right - px)
            dy = min(py - self.y, self.bottom - py)
            return dx + dy >= self.cut
        t = self.arm / 2  # cross
        return abs(px - self.cx) <= t or abs(py - self.cy) <= t

    def span(self, axis):
        """Range of grid coordinates along ``axis`` ('x' or 'y') where a
        straight corridor can attach to a side of this room."""
        org, size = (self.x, self.w) if axis == "x" else (self.y, self.h)
        mid = org + size / 2
        if self.shape == "rect":
            return org, org + size
        if self.shape == "circle":
            half = max(0.6, size * 0.24)
            return mid - half, mid + half
        if self.shape == "octagon":
            return org + self.cut, org + size - self.cut
        return mid - self.arm / 2, mid + self.arm / 2

    def outline(self):
        """Polygon (grid units) for polygon shapes, or None for rect/circle."""
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.shape == "octagon":
            c = self.cut
            return [(x + c, y), (x + w - c, y), (x + w, y + c), (x + w, y + h - c),
                    (x + w - c, y + h), (x + c, y + h), (x, y + h - c), (x, y + c)]
        if self.shape == "cross":
            a = (self.w - self.arm) / 2
            b = (self.h - self.arm) / 2
            return [(x + a, y), (x + w - a, y), (x + w - a, y + b), (x + w, y + b), (x + w, y + h - b),
                    (x + w - a, y + h - b), (x + w - a, y + h), (x + a, y + h), (x + a, y + h - b),
                    (x, y + h - b), (x, y + b), (x + a, y + b)]
        return None

    def path_d(self):
        poly = self.outline()
        if poly is not None:
            return ink.poly_d([(px * PX, py * PX) for px, py in poly])
        if self.shape == "circle":
            return ink.circle_d(self.cx * PX, self.cy * PX, self.w * PX / 2)
        return ink.rect_d(self.x * PX, self.y * PX, self.w * PX, self.h * PX)


def _lines_in(lo, hi, width):
    """Corridor centre lines that fit between lo and hi: cell centres for a
    one-wide corridor, whole grid lines for a two-wide one."""
    if width == 1:
        return [k + 0.5 for k in range(int(math.ceil(lo - 1e-9)), int(math.floor(hi + 1e-9)))]
    return [float(c) for c in range(int(math.ceil(lo + 1 - 1e-9)), int(math.floor(hi - 1 + 1e-9)) + 1)]


class Corridor:
    __slots__ = ("a", "b", "axis", "line", "width", "paint", "clear", "doors")

    def __init__(self, a, b, axis, line, width):
        self.a, self.b, self.axis, self.line, self.width = a, b, axis, line, width
        self.paint = self.clear = None
        self.doors = []

    def rect_cells(self):
        return self.paint


def _overlap(r1, r2):
    return r1[0] < r2[2] and r2[0] < r1[2] and r1[1] < r2[3] and r2[1] < r1[3]


def _bbox(room, pad=0.0):
    return (room.x - pad, room.y - pad, room.right + pad, room.bottom + pad)


class Plan:
    """Rooms + corridors under construction, with the collision rules."""

    def __init__(self, width, height):
        self.W, self.H = width, height
        self.rooms = []
        self.cors = []

    def add_room(self, shape, x, y, w, h):
        r = Room(len(self.rooms), x, y, w, h, shape)
        self.rooms.append(r)
        return r

    def room_fits(self, room, pad=2.0):
        if room.x < 3 or room.y < 3 or room.right > self.W - 3 or room.bottom > self.H - 3:
            return False
        bb = _bbox(room, pad)
        for other in self.rooms:
            if other is not room and _overlap(bb, _bbox(other)):
                return False
        tight = _bbox(room, 1.0)
        for c in self.cors:
            if _overlap(tight, c.paint):
                return False
        return True

    def link_candidates(self, a, b, width):
        """All straight ways to join rooms a and b: (axis, line, start, end)."""
        out = []
        for p, q in ((a, b), (b, a)):
            if p.right < q.x:  # q is east of p -> horizontal corridor
                lo = max(p.span("y")[0], q.span("y")[0])
                hi = min(p.span("y")[1], q.span("y")[1])
                if q.x - p.right <= _MAX_LINK_GAP:
                    out += [("h", ln, p, q) for ln in _lines_in(lo, hi, width)]
            if p.bottom < q.y:  # q is south of p -> vertical corridor
                lo = max(p.span("x")[0], q.span("x")[0])
                hi = min(p.span("x")[1], q.span("x")[1])
                if q.y - p.bottom <= _MAX_LINK_GAP:
                    out += [("v", ln, p, q) for ln in _lines_in(lo, hi, width)]
        return out

    def _corridor(self, axis, line, p, q, width):
        c = Corridor(p, q, axis, line, width)
        hw = width / 2
        if axis == "h":
            ext_p, ext_q = min(1.6, p.w / 2), min(1.6, q.w / 2)
            c.paint = (p.right - ext_p, line - hw, q.x + ext_q, line + hw)
            c.clear = (p.right, line - hw - 1, q.x, line + hw + 1)
        else:
            ext_p, ext_q = min(1.6, p.h / 2), min(1.6, q.h / 2)
            c.paint = (line - hw, p.bottom - ext_p, line + hw, q.y + ext_q)
            c.clear = (line - hw - 1, p.bottom, line + hw + 1, q.y)
        return c

    def corridor_ok(self, c):
        for r in self.rooms:
            if r is c.a or r is c.b:
                continue
            if _overlap(c.clear, _bbox(r)):
                return False
        for other in self.cors:
            if _overlap(c.clear, other.paint):
                return False
        return True

    def try_link(self, rng, a, b, width=1):
        cands = self.link_candidates(a, b, width)
        rng.shuffle(cands)
        for axis, line, p, q in cands:
            c = self._corridor(axis, line, p, q, width)
            if self.corridor_ok(c):
                self.cors.append(c)
                return c
        return None

    def linked(self, a, b):
        return any((c.a is a and c.b is b) or (c.a is b and c.b is a) for c in self.cors)

    def components(self):
        parent = list(range(len(self.rooms)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for c in self.cors:
            parent[find(c.a.idx)] = find(c.b.idx)
        return find


def _pick_shape(rng, kind):
    r = rng.random()
    if kind == "interior":
        table = [("rect", 0.86), ("octagon", 0.06), ("circle", 0.05), ("cross", 0.03)]
    else:
        table = [("rect", 0.60), ("octagon", 0.14), ("circle", 0.14), ("cross", 0.12)]
    acc = 0.0
    for name, wgt in table:
        acc += wgt
        if r <= acc:
            return name
    return "rect"


def _room_size(rng, shape, cap_w=14, cap_h=10):
    """Width/height honouring each shape's parity rules (so centre lines
    land on cell centres)."""
    if shape == "circle":
        d = rng.choice([5, 7, 7, 9])
        d = min(d, cap_w | 1, cap_h | 1)
        d = d if d % 2 else d - 1
        return max(d, 5), max(d, 5)
    if shape == "cross":
        n = rng.choice([7, 9])
        n = min(n, cap_w | 1, cap_h | 1)
        n = n if n % 2 else n - 1
        return max(n, 7), max(n, 7)
    if shape == "octagon":
        return min(rng.randint(6, 10), cap_w), min(rng.randint(6, 9), cap_h)
    roll = rng.random()
    if roll < 0.10:
        return min(rng.randint(10, 14), cap_w), min(rng.randint(5, 7), cap_h)  # a hall
    if roll < 0.22:
        return rng.randint(3, 4), rng.randint(3, 5)                             # a closet
    return min(rng.randint(4, 10), cap_w), min(rng.randint(4, 8), cap_h)


# ------------------------------------------------------------- layouts ---

def _warren(rng, plan, kind, target):
    w0, h0 = rng.randint(6, 9), rng.randint(6, 8)
    root = plan.add_room(rng.choice(["rect", "octagon"]), int(plan.W / 2 - w0 / 2), int(plan.H / 2 - h0 / 2), w0, h0)
    degree = {root.idx: 0}
    attempts = 0
    while len(plan.rooms) < target and attempts < target * 70:
        attempts += 1
        weights = [max(1, 4 - degree[r.idx]) for r in plan.rooms]
        parent = rng.choices(plan.rooms, weights)[0]
        d = rng.choice("ENWS")
        shape = _pick_shape(rng, kind)
        cw, ch = _room_size(rng, shape)
        gap = rng.randint(1, 3) if kind == "interior" else rng.randint(2, 6)
        cw_tmp = Room(-1, 0, 0, cw, ch, shape)
        width = 2 if rng.random() < 0.10 else 1
        if d in "EW":
            lines = _lines_in(*parent.span("y"), width)
            if not lines:
                continue
            line = rng.choice(lines)
            lo_off, hi_off = cw_tmp.span("y")
            ys = range(int(math.ceil(line + width / 2 - hi_off - 1e-9)), int(math.floor(line - width / 2 - lo_off + 1e-9)) + 1)
            if not ys:
                continue
            y = rng.choice(list(ys))
            x = parent.right + gap if d == "E" else parent.x - gap - cw
            child = Room(len(plan.rooms), x, y, cw, ch, shape)
        else:
            lines = _lines_in(*parent.span("x"), width)
            if not lines:
                continue
            line = rng.choice(lines)
            lo_off, hi_off = cw_tmp.span("x")
            xs = range(int(math.ceil(line + width / 2 - hi_off - 1e-9)), int(math.floor(line - width / 2 - lo_off + 1e-9)) + 1)
            if not xs:
                continue
            x = rng.choice(list(xs))
            y = parent.bottom + gap if d == "S" else parent.y - gap - ch
            child = Room(len(plan.rooms), x, y, cw, ch, shape)
        if not plan.room_fits(child, pad=1.0 if kind == "interior" else 2.0):
            continue
        plan.rooms.append(child)
        pair = (parent, child) if d in "ES" else (child, parent)
        axis = "h" if d in "EW" else "v"
        cor = plan._corridor(axis, line, pair[0], pair[1], width)
        if not plan.corridor_ok(cor):
            plan.rooms.pop()
            continue
        plan.cors.append(cor)
        degree[child.idx] = 1
        degree[parent.idx] += 1
    # loops: extra straight links between nearby unlinked rooms
    budget = max(1, len(plan.rooms) // 4)
    pairs = [(a, b) for i, a in enumerate(plan.rooms) for b in plan.rooms[i + 1:] if not plan.linked(a, b)]
    pairs.sort(key=lambda p: math.hypot(p[0].cx - p[1].cx, p[0].cy - p[1].cy))
    for a, b in pairs[:40]:
        if budget <= 0:
            break
        if rng.random() < 0.55 and plan.try_link(rng, a, b):
            budget -= 1


def _grid_links(rng, plan, neighbours, drop):
    """Link grid neighbours, dropping some - but never leaving the map in
    pieces."""
    rng.shuffle(neighbours)
    for a, b in neighbours:
        if rng.random() >= drop:
            plan.try_link(rng, a, b)
    find = plan.components()
    for a, b in neighbours:
        if find(a.idx) != find(b.idx) and plan.try_link(rng, a, b):
            find = plan.components()


def _keep(rng, plan, kind, size_key):
    cols = {"small": 3, "medium": rng.choice([3, 4]), "large": rng.choice([4, 5])}[size_key]
    rows = {"small": 2, "medium": 3, "large": rng.choice([3, 4])}[size_key]
    ux, uy = plan.W - 6, plan.H - 6
    cw, ch = ux / cols, uy / rows
    occupied = {}
    for r in range(rows):
        c = 0
        while c < cols:
            span = 2 if (cols >= 4 and c + 1 < cols and rng.random() < 0.16) else 1
            cell_x = 3 + c * cw
            gap_c = 2 if kind == "interior" else 3
            w_cap = int(cw * span - gap_c)
            h_cap = int(ch - gap_c)
            shape = "rect" if span == 2 else _pick_shape(rng, kind)
            w, h = _room_size(rng, shape, cap_w=max(w_cap, 5), cap_h=max(h_cap, 5))
            if span == 2:
                w, h = max(w_cap - rng.randint(0, 2), 8), max(min(h, h_cap), 4)
            w, h = min(w, w_cap), min(h, h_cap)
            w, h = max(w, 4 if shape == "rect" else 5), max(h, 3 if shape == "rect" else 5)
            if shape in ("circle", "cross"):
                w = h = min(w, h) | 1 if min(w, h) % 2 == 0 else min(w, h)
            cx = cell_x + cw * span / 2 + rng.randint(-1, 1)
            cy = 3 + r * ch + ch / 2 + rng.randint(-1, 1)
            x, y = int(round(cx - w / 2)), int(round(cy - h / 2))
            room = plan.add_room(shape, x, y, w, h)
            for k in range(span):
                occupied[(c + k, r)] = room
            c += span
    seen = set()
    neighbours = []
    for (c, r), room in occupied.items():
        for dc, dr in ((1, 0), (0, 1)):
            other = occupied.get((c + dc, r + dr))
            if other is not None and other is not room and (room.idx, other.idx) not in seen:
                seen.add((room.idx, other.idx))
                neighbours.append((room, other))
    _grid_links(rng, plan, neighbours, drop=0.22)


def _hub(rng, plan, kind, size_key):
    hub_d = {"small": 7, "medium": 11, "large": 13}[size_key]
    cx, cy = plan.W // 2, plan.H // 2
    hub_shape = rng.choice(["circle", "octagon", "circle"])
    hub = plan.add_room(hub_shape, cx - hub_d // 2, cy - hub_d // 2, hub_d, hub_d)
    sw = {"small": 6, "medium": 8, "large": 9}[size_key]
    sh = {"small": 5, "medium": 6, "large": 7}[size_key]
    gap = 3
    dx = hub_d // 2 + gap + sw // 2 + 1
    dy = hub_d // 2 + gap + sh // 2 + 1

    def make(ccx, ccy):
        shape = "rect" if rng.random() < 0.8 else "octagon"
        w, h = sw - rng.randint(0, 2), sh - rng.randint(0, 1)
        return plan.add_room(shape, int(round(ccx - w / 2)), int(round(ccy - h / 2)), w, h)

    axis = {"N": make(cx, cy - dy), "S": make(cx, cy + dy), "E": make(cx + dx, cy), "W": make(cx - dx, cy)}
    for room in axis.values():
        plan.try_link(rng, hub, room)
    corner = {}
    for key, (sx, sy) in {"NE": (1, -1), "NW": (-1, -1), "SE": (1, 1), "SW": (-1, 1)}.items():
        if rng.random() < 0.6:
            corner[key] = make(cx + sx * dx, cy + sy * dy)
    ring = [("N", "NE"), ("NE", "E"), ("E", "SE"), ("SE", "S"), ("S", "SW"), ("SW", "W"), ("W", "NW"), ("NW", "N")]
    allrooms = dict(axis, **corner)
    for a, b in ring:
        if a in allrooms and b in allrooms and rng.random() < 0.55:
            plan.try_link(rng, allrooms[a], allrooms[b])
    for key, room in corner.items():  # no orphaned corner rooms
        if not any(c.a is room or c.b is room for c in plan.cors):
            for a, b in ring:
                if key in (a, b) and a in allrooms and b in allrooms:
                    if plan.try_link(rng, allrooms[a], allrooms[b]):
                        break


def _build_plan(rng, kind, size_key):
    """Builds layouts until one is connected and big enough (the rng is
    advanced deterministically, so this is still a pure function of the
    seed)."""
    spec = _SIZES[size_key]
    names, weights = zip(*_LAYOUT_WEIGHTS[kind])
    layout = rng.choices(names, weights)[0]
    best = None
    for _ in range(10):
        plan = Plan(spec["w"], spec["h"])
        target = rng.randint(*spec["rooms"])
        if layout == "warren":
            _warren(rng, plan, kind, target)
        elif layout == "keep":
            _keep(rng, plan, kind, size_key)
        else:
            _hub(rng, plan, kind, size_key)
        find = plan.components()
        connected = len({find(r.idx) for r in plan.rooms}) == 1
        ok = connected and len(plan.rooms) >= max(4, int(spec["rooms"][0] * 0.8))
        if best is None or (connected, len(plan.rooms)) > (best[1], len(best[0].rooms)):
            best = (plan, connected)
        if ok:
            break
    return layout, best[0], best[1]


# --------------------------------------------------------------- doors ---

def _boundary(room, axis, line, direction):
    """Where the centre line of a corridor leaves ``room`` (px)."""
    if axis == "h":
        inside, outside = room.cx, (room.right + 0.5 if direction > 0 else room.x - 0.5)
        for _ in range(24):
            mid = (inside + outside) / 2
            if room.contains(mid, line):
                inside = mid
            else:
                outside = mid
        return inside * PX, line * PX
    inside, outside = room.cy, (room.bottom + 0.5 if direction > 0 else room.y - 0.5)
    for _ in range(24):
        mid = (inside + outside) / 2
        if room.contains(line, mid):
            inside = mid
        else:
            outside = mid
    return line * PX, inside * PX


def _make_doors(rng, plan, kind):
    """Decides what stands in each doorway. Returns the door list."""
    doors = []
    secret_budget = max(1, len(plan.rooms) // 7)
    for c in plan.cors:
        ends = [(c.a, +1), (c.b, -1)]
        secret_end = None
        if secret_budget > 0 and rng.random() < 0.10:
            secret_end = rng.randrange(2)
            secret_budget -= 1
        for i, (room, direction) in enumerate(ends):
            x, y = _boundary(room, c.axis, c.line, direction)
            if i == secret_end:
                dk = "secret"
            else:
                r = rng.random()
                dk = ("door" if r < 0.42 else "arch" if r < 0.74 else "swing" if r < 0.88 else "locked")
                if kind == "interior" and dk == "arch" and rng.random() < 0.5:
                    dk = "door"
            d = {"x": x, "y": y, "axis": c.axis, "width": c.width * PX, "kind": dk, "dir": direction}
            c.doors.append(d)
            doors.append(d)
    return doors


def _door_svg(d, pal, wall_w):
    x, y, w, kind, direction = d["x"], d["y"], d["width"], d["kind"], d["dir"]
    horiz = d["axis"] == "h"  # corridor runs left-right, so the door is a vertical bar
    L, T = w * 0.8, 4.6
    ink_c = pal["ink"]

    def bar(fill, stroke_w=1.1):
        if horiz:
            return (f'<rect x="{f(x - T / 2)}" y="{f(y - L / 2)}" width="{f(T)}" height="{f(L)}" rx="0.8" '
                    f'fill="{fill}" stroke="{ink_c}" stroke-width="{stroke_w}"/>')
        return (f'<rect x="{f(x - L / 2)}" y="{f(y - T / 2)}" width="{f(L)}" height="{f(T)}" rx="0.8" '
                f'fill="{fill}" stroke="{ink_c}" stroke-width="{stroke_w}"/>')

    if kind == "door":
        return bar(pal["door"])
    if kind == "locked":
        lock = f'<circle cx="{f(x)}" cy="{f(y)}" r="1.5" fill="{ink_c}"/>'
        return bar(pal["locked"]) + lock
    if kind == "swing":  # door swung open against the wall
        if horiz:
            return (f'<rect x="{f(x - 1.3)}" y="{f(y - w / 2 + 0.6)}" width="2.6" height="{f(w * 0.72)}" '
                    f'fill="{pal["door"]}" stroke="{ink_c}" stroke-width="0.9"/>')
        return (f'<rect x="{f(x - w / 2 + 0.6)}" y="{f(y - 1.3)}" width="{f(w * 0.72)}" height="2.6" '
                f'fill="{pal["door"]}" stroke="{ink_c}" stroke-width="0.9"/>')
    if kind == "arch":  # open doorway: a faint threshold line
        if horiz:
            return (f'<line x1="{f(x)}" y1="{f(y - w / 2)}" x2="{f(x)}" y2="{f(y + w / 2)}" '
                    f'stroke="{ink_c}" stroke-opacity="0.45" stroke-width="0.9" stroke-dasharray="2 2"/>')
        return (f'<line x1="{f(x - w / 2)}" y1="{f(y)}" x2="{f(x + w / 2)}" y2="{f(y)}" '
                f'stroke="{ink_c}" stroke-opacity="0.45" stroke-width="0.9" stroke-dasharray="2 2"/>')
    return ""


def _plug_d(d, wall_w):
    """A solid wall across a secret doorway (so it reads as plain wall)."""
    x, y, w, direction = d["x"], d["y"], d["width"], d["dir"]
    half = w / 2 + wall_w
    if d["axis"] == "h":
        x0 = x - 0.3 if direction > 0 else x - wall_w
        return ink.rect_d(x0, y - half, wall_w + 0.3, 2 * half)
    y0 = y - 0.3 if direction > 0 else y - wall_w
    return ink.rect_d(x - half, y0, 2 * half, wall_w + 0.3)


# ---------------------------------------------------------------- props ---

def _place_props(rng, room, genre, pal, labeled):
    """Furniture inside a room, kept off the walls and clear of the label."""
    area = room.w * room.h
    if rng.random() < 0.16 or min(room.w, room.h) < 4:
        return []
    count = max(0, min(round(area / 24) + rng.randint(-1, 1), 5 if room.shape != "circle" else 3))
    if count == 0:
        return []
    pool = objects._pool(objects.ROOM_OBJECTS, genre)
    chosen = rng.sample(pool, min(count, len(pool)))
    placed = []
    for icon_key, label in chosen:
        for _ in range(16):
            px = rng.uniform(room.x + 1.4, room.right - 1.4)
            py = rng.uniform(room.y + 1.4, room.bottom - 1.4)
            if not all(room.contains(px + dx, py + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))):
                continue
            if labeled and abs(px - room.cx) < min(room.w / 2 - 1, 3.2) and abs(py - room.cy) < 1.9:
                continue
            if all(math.hypot(px - q[0], py - q[1]) > 2.0 for q in placed):
                placed.append((px, py, icon_key, label))
                break
    return placed


def _label_lines(label, avail, size=9.5):
    """(lines, font size) that fit ``avail`` px, wrapping onto two lines
    when that lets a long name stay readable; None if it can't fit."""
    one = ink.fit_font(label, avail, size, 6.6)
    if one:
        return [label], one
    if " " in label:
        words = label.split()
        best = None
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            longest = max(a, b, key=len)
            if best is None or abs(len(a) - len(b)) < abs(len(best[0]) - len(best[1])):
                best = (a, b, longest)
        sz = ink.fit_font(best[2], avail, size, 6.6)
        if sz:
            return [best[0], best[1]], sz
    return None


def _stairs_svg(room, door_dir, pal):
    """Steps drawn across the far end of a dead-end room."""
    x0, y0, x1, y1 = room.x * PX, room.y * PX, room.right * PX, room.bottom * PX
    depth = 2.5 * PX
    ink_c = pal["ink"]
    lines = []
    inset = 0.9 * PX
    if door_dir in ("E", "W"):  # entered from the side -> steps at the opposite end
        xs = (x1 - depth, x1 - 3) if door_dir == "W" else (x0 + 3, x0 + depth)
        n = int(abs(xs[1] - xs[0]) / 4)
        for i in range(n + 1):
            xx = xs[0] + (xs[1] - xs[0]) * i / n
            lines.append(f"M{f(xx)} {f(y0 + inset)}V{f(y1 - inset)}")
    else:
        ys = (y1 - depth, y1 - 3) if door_dir == "N" else (y0 + 3, y0 + depth)
        n = int(abs(ys[1] - ys[0]) / 4)
        for i in range(n + 1):
            yy = ys[0] + (ys[1] - ys[0]) * i / n
            lines.append(f"M{f(x0 + inset)} {f(yy)}H{f(x1 - inset)}")
    return f'<path d="{"".join(lines)}" stroke="{ink_c}" stroke-width="1" fill="none" stroke-opacity="0.8"/>'


def _door_direction(room, plan):
    """Which side of a dead-end room its single corridor attaches to."""
    for c in plan.cors:
        if c.a is room:
            return "E" if c.axis == "h" else "S"
        if c.b is room:
            return "W" if c.axis == "h" else "N"
    return "W"


# ----------------------------------------------------------------- main ---

def generate(rng, genre, kind, params):
    size_key = params.get("size", "medium")
    spec = _SIZES.get(size_key, _SIZES["medium"])
    family = genre if genre != "mixed" else rng.choice(content.GENRES)
    pal = _PAL[(kind, family)]
    interior = kind == "interior"

    layout, plan, connected = _build_plan(rng, kind, size_key)
    rooms = plan.rooms
    W_px, H_px = spec["w"] * PX, spec["h"] * PX
    wall_w = 3.0 if interior else 2.3

    # names, features and stairs
    n_labeled = round(len(rooms) * 0.8)
    labeled = set(rng.sample(range(len(rooms)), min(n_labeled, len(rooms))))
    names = content.pick_many(rng, "room_names", genre, len(labeled), allow_repeats=(len(labeled) > 12))
    name_iter = iter(names)
    degree = {r.idx: 0 for r in rooms}
    for c in plan.cors:
        degree[c.a.idx] += 1
        degree[c.b.idx] += 1
    leaves = [r for r in rooms if degree[r.idx] == 1 and r.shape == "rect" and r.w >= 5 and r.h >= 5]
    rng.shuffle(leaves)
    stair_rooms = {r.idx: ("down" if i == 0 else "up") for i, r in enumerate(leaves[:2])} if not interior or rng.random() < 0.6 else {}
    for r in rooms:
        if r.idx in stair_rooms:
            r.label = "Stairs " + stair_rooms[r.idx]
            r.stairs = stair_rooms[r.idx]
            labeled.add(r.idx)
        elif r.idx in labeled:
            r.label = next(name_iter)
            if rng.random() < 0.28:
                r.feature = content.pick(rng, "room_features", genre)
        if interior and rng.random() < 0.55 and not r.stairs:
            r.tint = rng.choice(_TINTS)

    doors = _make_doors(rng, plan, kind)

    # ---- floor geometry (one merged path) ----
    floor_parts = [r.path_d() for r in rooms]
    for c in plan.cors:
        x0, y0, x1, y1 = c.paint
        floor_parts.append(ink.rect_d(x0 * PX, y0 * PX, (x1 - x0) * PX, (y1 - y0) * PX))
    floor_d = "".join(floor_parts)

    # ---- distance field for hatching ----
    raster = ink.Raster(W_px, H_px, res=4.0)
    for r in rooms:
        raster.paint(r.x * PX, r.y * PX, r.right * PX, r.bottom * PX,
                     lambda px, py, r=r: r.contains(px / PX, py / PX))
    for c in plan.cors:
        x0, y0, x1, y1 = c.paint
        raster.paint_rect(x0 * PX, y0 * PX, x1 * PX, y1 * PX)
    raster.distance()

    uid = f"i{rng.randrange(10**6)}"
    fp, fc, rough, soft, paper, grid, inner = (f"{uid}{s}" for s in ("fp", "fc", "ro", "so", "pa", "gr", "in"))
    defs = (
        ink.rough_filter(rough, scale=2.0 if not interior else 1.4)
        + ink.paper_filter(paper, pal["paper"], pal["grain"])
        + ink.blur_filter(soft, 2.4)
        + ink.inner_shadow_filter(inner, pal["shadow"], 0.55, 3.2)
        + ink.grid_pattern(grid, PX, pal["grid"], 0.85, 0.5)
        + f'<path id="{fp}" d="{floor_d}"/>'
        + f'<clipPath id="{fc}"><use href="#{fp}"/></clipPath>'
    )

    body = [f'<rect width="{W_px}" height="{H_px}" fill="{pal["bg"]}"/>',
            f'<rect class="paper-grain" width="{W_px}" height="{H_px}" filter="url(#{paper})"/>']

    if not interior:
        near = wall_w + 0.5
        hatch = raster.hatch(rng, 45, 3.0, near, 18, 0.5)
        hatch += raster.hatch(rng, 45, 6.0, near, 38, 0.55, thin=0.2, inset=1.5)
        hatch += raster.hatch(rng, -45, 5.0, near, 9, 0.4, thin=0.25, min_len=2.0)
        body.append(f'<path d="{hatch}" stroke="{pal["ink"]}" stroke-opacity="0.92" stroke-width="0.95" '
                    f'stroke-linecap="round" fill="none" filter="url(#{rough})"/>')
    else:
        # buildings have no rock to hatch: a soft shadow lifts the walls instead
        body.append(f'<use href="#{fp}" fill="none" stroke="{pal["shadow"]}" stroke-opacity="0.35" '
                    f'stroke-width="14" stroke-linejoin="round" filter="url(#{soft})"/>')

    plugs = "".join(_plug_d(d, wall_w) for d in doors if d["kind"] == "secret")
    walls = [
        f'<use href="#{fp}" fill="none" stroke="{pal["ink"]}" stroke-width="{f(wall_w * 2)}" stroke-linejoin="round"/>',
        f'<use href="#{fp}" fill="{pal["floor"]}" filter="url(#{inner})"/>',
        f'<rect width="{W_px}" height="{H_px}" fill="url(#{grid})" clip-path="url(#{fc})"/>',
    ]
    if plugs:
        walls.append(f'<path d="{plugs}" fill="{pal["ink"]}"/>')
    body.append(f'<g filter="url(#{rough})">{"".join(walls)}</g>')

    # doors, drawn crisp on top of the pen work
    door_parts = "".join(_door_svg(d, pal, wall_w) for d in doors)
    secret_marks = "".join(
        f'<g class="gm-only"><circle cx="{f(d["x"])}" cy="{f(d["y"])}" r="5" fill="{pal["floor"]}" '
        f'stroke="{pal["accent"]}" stroke-width="1"/><text x="{f(d["x"])}" y="{f(d["y"] + 2.6)}" font-size="7.5" '
        f'font-weight="bold" font-style="italic" text-anchor="middle" fill="{pal["accent"]}">S</text></g>'
        for d in doors if d["kind"] == "secret")
    body.append(f'<g>{door_parts}</g>')
    body.append(secret_marks)

    # rooms: each its own editable element (tint, number, name, furniture)
    room_parts = []
    object_count = 0
    for r in rooms:
        el = []
        tint_fill = r.tint or pal["floor"]
        el.append(f'<path class="map-el-fill" d="{r.path_d()}" fill="{tint_fill}" '
                  f'fill-opacity="{0.5 if r.tint else 0}" data-tint="{pal["floor"]}"/>')
        px_w = r.w * PX
        has_label = bool(r.label)
        if r.stairs:
            el.append(_stairs_svg(r, _door_direction(r, plan), pal))
        for (ox, oy, icon_key, olabel) in _place_props(rng, r, genre, pal, True):
            icon = objects.render_icon(icon_key, ox * PX, oy * PX, PX * 0.32, pal["object"])
            el.append(svg.map_el("object", ox * PX, oy * PX, icon, fill=pal["object"]))
            object_count += 1
        cx, cy = r.cx * PX, r.cy * PX
        lines = None
        if has_label:
            sx0, sx1 = r.span("x")
            avail = (min(sx1 - sx0, r.w) - 0.5) * PX
            lines = _label_lines(r.label, avail)
        badge_y = cy - (7 if lines else 0) - (4 if lines and len(lines[0]) == 2 else 0)
        el.append(ink.badge(cx, badge_y, r.number, pal["floor"], pal["ink"], pal["ink"]))
        if lines:
            texts, size = lines
            size = round(size, 1)
            ty = badge_y + 17
            for line in texts:
                el.append(svg.text(cx, ty, line, size=size, fill=pal["label"], anchor="middle",
                                   weight="bold", extra='class="map-el-label"'))
                ty += size + 1
            if r.feature and r.h >= 6 and len(texts) == 1:
                fsize = ink.fit_font(r.feature, avail, 7.5, 6.0)
                if fsize:
                    el.append(svg.text(cx, ty, r.feature, size=round(fsize, 1), fill=pal["label"],
                                       anchor="middle", style="italic", extra='class="map-el-label"'))
        room_parts.append(svg.map_el("room", cx, cy, "".join(el), fill=tint_fill, locked=True))
    body.append("".join(room_parts))

    # title, scale, north arrow
    layout_label = _LAYOUT_LABELS[layout]
    title = f"{'Dungeon' if not interior else 'Building Interior'} - {content.GENRE_LABELS[genre]}{layout_label}"
    unit = "5 ft" if family == "fantasy" else "1.5 m"
    body.append(svg.text(12, H_px - 10, title, size=12, fill=pal["ink"], weight="bold", extra='class="map-title"'))
    body.append(svg.text(12, H_px - 24, f"1 square = {unit}", size=8.5, fill=pal["ink"], style="italic",
                         extra='class="map-title"'))
    nx, ny = W_px - 24, 26
    body.append(f'<g class="map-title" fill="{pal["ink"]}"><path d="M{f(nx)} {f(ny - 12)}l5 16l-5 -4l-5 4z" '
                f'stroke="{pal["bg"]}" stroke-width="2.4" paint-order="stroke"/>'
                f'<text x="{f(nx)}" y="{f(ny - 15)}" font-size="9" font-weight="bold" text-anchor="middle" '
                f'stroke="{pal["bg"]}" stroke-width="2.4" paint-order="stroke">N</text></g>')

    css = f".map-title{{paint-order:stroke;stroke:{pal['bg']};stroke-width:4px;stroke-linejoin:round;}}"
    doc = svg.ink_doc(W_px, H_px, "".join(body), extra_defs=defs, halo=pal["halo"], extra_css=css)
    return {
        "svg": doc,
        "meta": {
            "rooms": len(rooms), "size": size_key, "layout": layout, "objects": object_count,
            "doors": len(doors), "secret_doors": sum(1 for d in doors if d["kind"] == "secret"),
            "stairs": len(stair_rooms), "corridors": len(plan.cors), "connected": connected, "style": "ink",
        },
        "title_suggestion": title,
    }
