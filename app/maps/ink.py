"""Drawing toolkit shared by the 'ink' map style.

The ink style draws maps the way a hand-inked cartographer would: floors are
one merged shape with a thick pen line around it, rock is cross-hatched,
coastlines are smooth contours, and a little paper grain sits over the top.
Everything here is plain Python that emits SVG strings - no numpy, no
imaging library - so the whole thing stays dependency-free.

The pieces:

- number/path formatting that keeps the SVG small (``f``, ``rect_d``, ...)
- ``Raster`` - a coarse floor mask with a distance-to-floor field, which the
  hatching and the soft inner shadow are driven from
- ``contours`` / ``rdp`` / ``smooth_closed_d`` - marching squares plus
  simplification and curve smoothing (caves, coastlines, lakes, biomes)
- ``noise_grid`` - cheap fractal value noise for terrain
- ``defs`` helpers - the shared SVG filters and patterns

Every function that needs randomness takes the caller's ``random.Random`` so
a map stays a pure function of its seed.
"""
import heapq
import math


# ------------------------------------------------------------ formatting ---

def f(v):
    """Compact number: one decimal at most, no trailing '.0'."""
    s = "%.1f" % v
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s == "-0" else s


def rect_d(x, y, w, h):
    """Rectangle sub-path, clockwise on screen (so overlapping shapes union
    under the default nonzero fill rule)."""
    return f"M{f(x)} {f(y)}h{f(w)}v{f(h)}h{f(-w)}z"


def circle_d(cx, cy, r):
    return (f"M{f(cx - r)} {f(cy)}a{f(r)} {f(r)} 0 1 1 {f(2 * r)} 0"
            f"a{f(r)} {f(r)} 0 1 1 {f(-2 * r)} 0z")


def ellipse_d(cx, cy, rx, ry):
    return (f"M{f(cx - rx)} {f(cy)}a{f(rx)} {f(ry)} 0 1 1 {f(2 * rx)} 0"
            f"a{f(rx)} {f(ry)} 0 1 1 {f(-2 * rx)} 0z")


def signed_area(points):
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2


def poly_d(points, clockwise=True):
    """Straight-edged closed sub-path. ``clockwise`` normalises the winding
    so several polygons can be unioned in one path."""
    pts = list(points)
    if clockwise and signed_area(pts) < 0:
        pts.reverse()
    head = f"M{f(pts[0][0])} {f(pts[0][1])}"
    return head + "L" + " ".join(f"{f(x)} {f(y)}" for x, y in pts[1:]) + "z"


def polyline_d(points):
    head = f"M{f(points[0][0])} {f(points[0][1])}"
    return head + "L" + " ".join(f"{f(x)} {f(y)}" for x, y in points[1:])


def smooth_closed_d(points):
    """Closed quadratic B-spline through the midpoints of a polygon: the
    result is a smooth curve that hugs the polygon without needing any
    extra points (about one short command per vertex)."""
    n = len(points)
    if n < 3:
        return poly_d(points, clockwise=False)
    mids = [((points[i][0] + points[(i + 1) % n][0]) / 2,
             (points[i][1] + points[(i + 1) % n][1]) / 2) for i in range(n)]
    out = [f"M{f(mids[-1][0])} {f(mids[-1][1])}"]
    for i in range(n):
        out.append(f"Q{f(points[i][0])} {f(points[i][1])} {f(mids[i][0])} {f(mids[i][1])}")
    return "".join(out) + "z"


def smooth_open_d(points):
    """Open quadratic B-spline: starts and ends exactly on the end points."""
    n = len(points)
    if n < 3:
        return polyline_d(points) if n >= 2 else ""
    out = [f"M{f(points[0][0])} {f(points[0][1])}"]
    for i in range(1, n - 1):
        mx = (points[i][0] + points[i + 1][0]) / 2
        my = (points[i][1] + points[i + 1][1]) / 2
        out.append(f"Q{f(points[i][0])} {f(points[i][1])} {f(mx)} {f(my)}")
    out.append(f"L{f(points[-1][0])} {f(points[-1][1])}")
    return "".join(out)


# ------------------------------------------------------------- simplify ---

def _pt_seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def rdp(points, eps, closed=False):
    """Douglas-Peucker simplification (iterative, so big loops are safe)."""
    pts = list(points)
    if len(pts) < 4:
        return pts
    if closed:
        # split a loop at its two farthest-apart anchor points
        i0 = 0
        i1 = max(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]))
        a = _rdp_open(pts[i0:i1 + 1], eps)
        b = _rdp_open(pts[i1:] + [pts[0]], eps)
        merged = a[:-1] + b[:-1]
        return merged if len(merged) >= 3 else pts
    return _rdp_open(pts, eps)


def _rdp_open(pts, eps):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        best, idx = -1.0, -1
        for i in range(lo + 1, hi):
            d = _pt_seg_dist(pts[i], pts[lo], pts[hi])
            if d > best:
                best, idx = d, i
        if best > eps:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [p for p, k in zip(pts, keep) if k]


# ------------------------------------------------------ marching squares ---

# edge ids are ('h', x, y): the edge from sample (x, y) to (x+1, y), and
# ('v', x, y): the edge from (x, y) to (x, y+1)
def contours(field, w, h, level):
    """Closed iso-lines of ``field`` (flat list, w*h samples, row-major) at
    ``level``. Returns loops of (x, y) in sample coordinates, with edge
    crossings interpolated linearly. The field is treated as 'below level'
    outside its bounds, so every loop closes."""
    def val(x, y):
        if 0 <= x < w and 0 <= y < h:
            return field[y * w + x]
        return level - 1.0

    def crossing(edge):
        kind, x, y = edge
        if kind == "h":
            a, b = val(x, y), val(x + 1, y)
            t = (level - a) / (b - a) if b != a else 0.5
            return (x + t, float(y))
        a, b = val(x, y), val(x, y + 1)
        t = (level - a) / (b - a) if b != a else 0.5
        return (float(x), y + t)

    adj = {}

    def link(e1, e2):
        adj.setdefault(e1, []).append(e2)
        adj.setdefault(e2, []).append(e1)

    for y in range(-1, h):
        for x in range(-1, w):
            tl, tr = val(x, y) >= level, val(x + 1, y) >= level
            bl, br = val(x, y + 1) >= level, val(x + 1, y + 1) >= level
            case = (tl << 3) | (tr << 2) | (br << 1) | bl
            if case in (0, 15):
                continue
            top, right = ("h", x, y), ("v", x + 1, y)
            bottom, left = ("h", x, y + 1), ("v", x, y)
            if case == 1 or case == 14:
                link(left, bottom)
            elif case == 2 or case == 13:
                link(bottom, right)
            elif case == 3 or case == 12:
                link(left, right)
            elif case == 4 or case == 11:
                link(top, right)
            elif case == 6 or case == 9:
                link(top, bottom)
            elif case == 7 or case == 8:
                link(top, left)
            else:  # saddles (5, 10): decide by the cell centre
                centre = (val(x, y) + val(x + 1, y) + val(x, y + 1) + val(x + 1, y + 1)) / 4 >= level
                if (case == 5) == centre:
                    link(top, left)
                    link(bottom, right)
                else:
                    link(top, right)
                    link(left, bottom)

    seen = set()
    loops = []
    for start in adj:
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        prev, cur = None, start
        while True:
            nxts = [n for n in adj[cur] if n != prev]
            nxt = nxts[0] if nxts else None
            if nxt is None or nxt in seen:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        if len(loop) >= 3:
            loops.append([crossing(e) for e in loop])
    return loops


def loop_area(loop):
    return abs(signed_area(loop))


# ---------------------------------------------------------- field helpers ---

def blur_field(field, w, h, passes=1):
    """Separable 3-tap box blur, edge-clamped. Returns a new list."""
    cur = list(field)
    for _ in range(passes):
        tmp = cur[:]
        for y in range(h):
            row = y * w
            for x in range(w):
                a = cur[row + (x - 1 if x > 0 else 0)]
                c = cur[row + (x + 1 if x < w - 1 else w - 1)]
                tmp[row + x] = (a + cur[row + x] + c) / 3
        for y in range(h):
            up = (y - 1 if y > 0 else 0) * w
            dn = (y + 1 if y < h - 1 else h - 1) * w
            row = y * w
            for x in range(w):
                cur[row + x] = (tmp[up + x] + tmp[row + x] + tmp[dn + x]) / 3
    return cur


def upsample(field, w, h, k):
    """Bilinear k-times upsample. Returns (new_field, new_w, new_h)."""
    nw, nh = (w - 1) * k + 1, (h - 1) * k + 1
    out = [0.0] * (nw * nh)
    for y in range(nh):
        fy = y / k
        y0 = min(int(fy), h - 2)
        ty = fy - y0
        for x in range(nw):
            fx = x / k
            x0 = min(int(fx), w - 2)
            tx = fx - x0
            a = field[y0 * w + x0]
            b = field[y0 * w + x0 + 1]
            c = field[(y0 + 1) * w + x0]
            d = field[(y0 + 1) * w + x0 + 1]
            out[y * nw + x] = (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty
    return out, nw, nh


def noise_grid(rng, w, h, base_cells=4.0, octaves=4, persistence=0.5, lacunarity=2.0):
    """Fractal value noise over a w*h grid, normalised to 0..1. ``base_cells``
    is how many lattice cells span the longer side at the first octave."""
    total = [0.0] * (w * h)
    amp, norm = 1.0, 0.0
    longest = max(w, h)
    cells = base_cells
    for _ in range(octaves):
        nx = int(cells * w / longest) + 2
        ny = int(cells * h / longest) + 2
        lattice = [[rng.random() for _ in range(nx + 1)] for _ in range(ny + 1)]
        xs = []
        for x in range(w):
            fx = x / longest * cells
            ix = int(fx)
            t = fx - ix
            xs.append((ix, t * t * (3 - 2 * t)))
        for y in range(h):
            fy = y / longest * cells
            iy = int(fy)
            t = fy - iy
            ty = t * t * (3 - 2 * t)
            r0, r1 = lattice[iy], lattice[iy + 1]
            row = y * w
            for x in range(w):
                ix, tx = xs[x]
                a = r0[ix] + (r0[ix + 1] - r0[ix]) * tx
                b = r1[ix] + (r1[ix + 1] - r1[ix]) * tx
                total[row + x] += (a + (b - a) * ty) * amp
        norm += amp
        amp *= persistence
        cells *= lacunarity
    return [v / norm for v in total]


def normalise(field):
    lo, hi = min(field), max(field)
    span = (hi - lo) or 1.0
    return [(v - lo) / span for v in field]


# ----------------------------------------------------------------- raster ---

class Raster:
    """A coarse mask over the map (``res`` px per sample) plus a distance-to-
    floor field in px. Paint floor shapes in, call ``distance()`` once, then
    ask ``dist_at`` / ``hatch`` / ``inside``."""

    def __init__(self, width_px, height_px, res=4.0):
        self.res = float(res)
        self.w = int(math.ceil(width_px / res))
        self.h = int(math.ceil(height_px / res))
        self.width_px, self.height_px = width_px, height_px
        self.mask = bytearray(self.w * self.h)
        self.dist = None

    def paint(self, x0, y0, x1, y1, contains):
        """Mark every sample whose centre (px) is inside ``contains`` within
        the given px bounding box."""
        res = self.res
        ix0, ix1 = max(0, int(x0 / res) - 1), min(self.w - 1, int(x1 / res) + 1)
        iy0, iy1 = max(0, int(y0 / res) - 1), min(self.h - 1, int(y1 / res) + 1)
        for iy in range(iy0, iy1 + 1):
            py = (iy + 0.5) * res
            row = iy * self.w
            for ix in range(ix0, ix1 + 1):
                if contains((ix + 0.5) * res, py):
                    self.mask[row + ix] = 1

    def paint_rect(self, x0, y0, x1, y1):
        res = self.res
        ix0, ix1 = max(0, int(math.floor(x0 / res + 0.5))), min(self.w, int(math.floor(x1 / res + 0.5)))
        iy0, iy1 = max(0, int(math.floor(y0 / res + 0.5))), min(self.h, int(math.floor(y1 / res + 0.5)))
        for iy in range(iy0, iy1):
            row = iy * self.w
            for ix in range(ix0, ix1):
                self.mask[row + ix] = 1

    def inside(self, x, y):
        ix, iy = int(x / self.res), int(y / self.res)
        return 0 <= ix < self.w and 0 <= iy < self.h and self.mask[iy * self.w + ix] == 1

    def distance(self):
        """Chamfer distance (px) from each sample to the nearest floor sample."""
        w, h, res = self.w, self.h, self.res
        inf = 1e9
        d = [0.0 if m else inf for m in self.mask]
        diag = 1.4142 * res
        for y in range(h):
            row = y * w
            for x in range(w):
                i = row + x
                v = d[i]
                if v == 0.0:
                    continue
                if x > 0 and d[i - 1] + res < v:
                    v = d[i - 1] + res
                if y > 0:
                    if d[i - w] + res < v:
                        v = d[i - w] + res
                    if x > 0 and d[i - w - 1] + diag < v:
                        v = d[i - w - 1] + diag
                    if x < w - 1 and d[i - w + 1] + diag < v:
                        v = d[i - w + 1] + diag
                d[i] = v
        for y in range(h - 1, -1, -1):
            row = y * w
            for x in range(w - 1, -1, -1):
                i = row + x
                v = d[i]
                if v == 0.0:
                    continue
                if x < w - 1 and d[i + 1] + res < v:
                    v = d[i + 1] + res
                if y < h - 1:
                    if d[i + w] + res < v:
                        v = d[i + w] + res
                    if x < w - 1 and d[i + w + 1] + diag < v:
                        v = d[i + w + 1] + diag
                    if x > 0 and d[i + w - 1] + diag < v:
                        v = d[i + w - 1] + diag
                d[i] = v
        self.dist = d
        return d

    def dist_at(self, x, y):
        ix, iy = int(x / self.res), int(y / self.res)
        if 0 <= ix < self.w and 0 <= iy < self.h:
            return self.dist[iy * self.w + ix]
        return 1e9

    def hatch(self, rng, angle=45.0, spacing=4.0, near=2.0, far=16.0, far_jitter=0.45,
              min_len=2.5, inset=0.6, thin=0.0):
        """Parallel pen strokes over the rock around the floor, as one path's
        ``d``. Strokes run from ``near`` to ``far`` px away from any floor;
        each line stops at its own random distance inside that band so the
        edge of the hatching looks drawn rather than computed. ``thin`` drops
        that fraction of lines at random (a sparser second pass)."""
        res = self.res
        W, H = self.w * res, self.h * res
        dist, iw = self.dist, self.w
        th = math.radians(angle)
        ux, uy = math.cos(th), -math.sin(th)
        nx, ny = math.sin(th), math.cos(th)
        corners = ((0, 0), (W, 0), (0, H), (W, H))
        ns = [x * nx + y * ny for x, y in corners]
        k0, k1 = int(math.floor(min(ns) / spacing)), int(math.ceil(max(ns) / spacing))
        step = res * 0.75
        parts = []
        for k in range(k0, k1 + 1):
            if thin and rng.random() < thin:
                continue
            n0 = k * spacing + rng.uniform(-0.5, 0.5)
            # clip the line to the map rectangle
            bx, by = n0 * nx, n0 * ny
            t_lo, t_hi = -1e9, 1e9
            for p, q, lim in ((bx, ux, W), (by, uy, H)):
                if abs(q) < 1e-9:
                    if p < 0 or p > lim:
                        t_lo, t_hi = 1.0, 0.0
                    continue
                ta, tb = (0 - p) / q, (lim - p) / q
                if ta > tb:
                    ta, tb = tb, ta
                t_lo, t_hi = max(t_lo, ta), min(t_hi, tb)
            if t_lo >= t_hi:
                continue
            limit = far * (1 - far_jitter * rng.random())
            t = t_lo
            run = None
            last = None
            while t <= t_hi:
                x, y = bx + t * ux, by + t * uy
                ix, iy = int(x / res), int(y / res)
                if 0 <= ix < self.w and 0 <= iy < self.h:
                    d = dist[iy * iw + ix]
                    ok = near <= d <= limit
                else:
                    ok = False
                if ok:
                    if run is None:
                        run = t
                    last = t
                elif run is not None:
                    if last - run >= min_len:
                        parts.append(_stroke(bx, by, ux, uy, run + inset, last - inset))
                    run = None
                t += step
            if run is not None and last - run >= min_len:
                parts.append(_stroke(bx, by, ux, uy, run + inset, last - inset))
        return "".join(parts)


def _stroke(bx, by, ux, uy, t0, t1):
    x0, y0 = bx + t0 * ux, by + t0 * uy
    return f"M{f(x0)} {f(y0)}l{f((t1 - t0) * ux)} {f((t1 - t0) * uy)}"


# --------------------------------------------------------------- A* roads ---

def grid_path(w, h, start, goal, cost):
    """A* over a w*h grid (8-connected). ``cost(ix, iy)`` is the cost of
    entering a sample, or None if it can't be entered. Returns a list of
    (ix, iy) or None."""
    def hfn(p):
        return math.hypot(p[0] - goal[0], p[1] - goal[1])

    open_heap = [(hfn(start), 0.0, start)]
    best = {start: 0.0}
    came = {}
    while open_heap:
        _, g, cur = heapq.heappop(open_heap)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            return path[::-1]
        if g > best.get(cur, 1e18):
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = cur[0] + dx, cur[1] + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            c = cost(nx, ny)
            if c is None:
                continue
            ng = g + c * (1.4142 if dx and dy else 1.0)
            if ng < best.get((nx, ny), 1e18):
                best[(nx, ny)] = ng
                came[(nx, ny)] = cur
                heapq.heappush(open_heap, (ng + hfn((nx, ny)), ng, (nx, ny)))
    return None


# ------------------------------------------------------------- svg pieces ---

def rough_filter(fid, scale=2.2, freq=0.04, seed=3):
    return (f'<filter id="{fid}" x="-2%" y="-2%" width="104%" height="104%">'
            f'<feTurbulence type="fractalNoise" baseFrequency="{freq}" numOctaves="2" seed="{seed}" result="n"/>'
            f'<feDisplacementMap in="SourceGraphic" in2="n" scale="{scale}"/></filter>')


def paper_filter(fid, rgb=(0.45, 0.38, 0.27), strength=0.10, seed=7):
    r, g, b = rgb
    return (f'<filter id="{fid}"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" '
            f'seed="{seed}" result="t"/><feColorMatrix type="matrix" '
            f'values="0 0 0 0 {r}  0 0 0 0 {g}  0 0 0 0 {b}  0 0 0 {strength} 0"/></filter>')


def blur_filter(fid, std=2.2):
    return f'<filter id="{fid}" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="{std}"/></filter>'


def inner_shadow_filter(fid, color, opacity=0.5, std=3.0):
    """Soft shadow cast inward from the edge of a shape (the floor under a
    wall), computed from the shape's alpha so overlapping sub-paths in a
    union don't leave stray inner edges."""
    return (f'<filter id="{fid}" x="-5%" y="-5%" width="110%" height="110%">'
            f'<feFlood flood-color="{color}" flood-opacity="{opacity}" result="c"/>'
            f'<feComposite in="c" in2="SourceAlpha" operator="out" result="rock"/>'
            f'<feGaussianBlur in="rock" stdDeviation="{std}" result="blur"/>'
            f'<feComposite in="blur" in2="SourceAlpha" operator="in" result="shadow"/>'
            f'<feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="shadow"/></feMerge></filter>')


def grid_pattern(pid, cell, color, opacity=0.8, width=0.5):
    return (f'<pattern id="{pid}" width="{cell}" height="{cell}" patternUnits="userSpaceOnUse">'
            f'<path d="M0 0H{cell}M0 0V{cell}" stroke="{color}" stroke-width="{width}" '
            f'stroke-opacity="{opacity}" fill="none"/></pattern>')


def badge(cx, cy, text, fill, stroke, text_color, r=7.5, size=9.5):
    """Round numbered marker (room numbers, keyed locations)."""
    ty = cy + size * 0.36
    return (f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
            f'<text x="{f(cx)}" y="{f(ty)}" font-size="{size}" font-weight="bold" text-anchor="middle" '
            f'fill="{text_color}">{text}</text>')


def fit_font(text, max_width_px, size=9.5, min_size=6.5):
    """Largest font size (<= size) at which ``text`` fits ``max_width_px``,
    or None if it would have to go below ``min_size``."""
    if not text:
        return None
    est = len(text) * 0.54
    fit = max_width_px / est
    if fit >= size:
        return size
    return fit if fit >= min_size else None
