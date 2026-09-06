"""Shared geometry helpers: recursive spatial subdivision (used by the
room/interior generator and the urban generator) and a minimum spanning
tree over points (used to connect rooms with corridors)."""
import math


class _Leaf:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


def bsp_partition(rng, x, y, w, h, depth, min_size):
    """Recursively splits a rectangle into roughly `2**depth` leaf
    rectangles, alternating split axis based on aspect ratio (with some
    randomness) so leaves stay reasonably square. Stops early once a leaf
    would be too small to split further."""
    leaves = []

    def split(x, y, w, h, depth):
        can_split_w = w >= 2 * min_size
        can_split_h = h >= 2 * min_size
        if depth <= 0 or not (can_split_w or can_split_h):
            leaves.append(_Leaf(x, y, w, h))
            return

        if can_split_w and can_split_h:
            split_horiz = rng.random() < 0.5
        else:
            split_horiz = can_split_h

        if split_horiz:
            pos = rng.uniform(min_size, h - min_size)
            split(x, y, w, pos, depth - 1)
            split(x, y + pos, w, h - pos, depth - 1)
        else:
            pos = rng.uniform(min_size, w - min_size)
            split(x, y, pos, h, depth - 1)
            split(x + pos, y, w - pos, h, depth - 1)

    split(x, y, w, h, depth)
    return leaves


def minimum_spanning_tree(points):
    """Prim's algorithm over a small point list. Returns a list of
    (i, j) index pairs into `points` for each MST edge."""
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    in_tree[0] = True
    edges = []
    best_dist = [math.dist(points[0], points[i]) if i != 0 else 0 for i in range(n)]
    best_from = [0] * n

    for _ in range(n - 1):
        nearest, nearest_dist = -1, math.inf
        for i in range(n):
            if not in_tree[i] and best_dist[i] < nearest_dist:
                nearest, nearest_dist = i, best_dist[i]
        if nearest == -1:
            break
        in_tree[nearest] = True
        edges.append((best_from[nearest], nearest))
        for i in range(n):
            if not in_tree[i]:
                d = math.dist(points[nearest], points[i])
                if d < best_dist[i]:
                    best_dist[i] = d
                    best_from[i] = nearest
    return edges


def ensure_connected(centers, edges):
    """Given a candidate edge list over `centers` that may leave some
    points unreachable from the rest (e.g. after randomly dropping some
    candidate connections for a maze-like feel), adds the shortest possible
    edge between each pair of separate components until every point is
    reachable from any other. All existing edges are kept as-is."""
    n = len(centers)
    if n < 2:
        return list(edges)

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    result = list(edges)
    for i, j in edges:
        union(i, j)

    components = {}
    for i in range(n):
        components.setdefault(find(i), []).append(i)
    comp_list = list(components.values())

    while len(comp_list) > 1:
        best = None
        for a in range(len(comp_list)):
            for b in range(a + 1, len(comp_list)):
                for i in comp_list[a]:
                    for j in comp_list[b]:
                        d = math.dist(centers[i], centers[j])
                        if best is None or d < best[0]:
                            best = (d, i, j, a, b)
        _, i, j, a, b = best
        result.append((i, j))
        merged = comp_list[a] + comp_list[b]
        comp_list = [c for k, c in enumerate(comp_list) if k not in (a, b)] + [merged]

    return result
