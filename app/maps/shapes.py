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
