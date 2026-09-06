"""Tests for the shared subdivision + MST geometry helpers
(app/maps/shapes.py) that the room, urban and terrain generators build on."""
import random

import pytest

from app.maps.shapes import bsp_partition, ensure_connected, minimum_spanning_tree


def _all_reachable(n, edges):
    adjacency = {i: set() for i in range(n)}
    for i, j in edges:
        adjacency[i].add(j)
        adjacency[j].add(i)
    seen = {0}
    frontier = [0]
    while frontier:
        cur = frontier.pop()
        for nxt in adjacency[cur]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen == set(range(n))


def test_bsp_partition_leaves_cover_the_whole_area_without_overlap():
    rng = random.Random(1)
    x, y, w, h = 0, 0, 40, 30
    leaves = bsp_partition(rng, x, y, w, h, depth=4, min_size=5)

    assert len(leaves) >= 2
    total_area = sum(leaf.w * leaf.h for leaf in leaves)
    assert total_area == pytest.approx(w * h, rel=1e-6)

    # Every leaf must stay fully inside the original rectangle.
    for leaf in leaves:
        assert leaf.x >= x - 1e-6
        assert leaf.y >= y - 1e-6
        assert leaf.x + leaf.w <= x + w + 1e-6
        assert leaf.y + leaf.h <= y + h + 1e-6


def test_bsp_partition_respects_min_size():
    rng = random.Random(2)
    leaves = bsp_partition(rng, 0, 0, 60, 40, depth=5, min_size=6)
    # A split only happens when both halves would still be >= min_size, so
    # no leaf should ever come out smaller than that floor.
    for leaf in leaves:
        assert leaf.w >= 6 - 1e-6
        assert leaf.h >= 6 - 1e-6


def test_bsp_partition_zero_depth_returns_single_leaf():
    rng = random.Random(3)
    leaves = bsp_partition(rng, 0, 0, 20, 20, depth=0, min_size=4)
    assert len(leaves) == 1
    assert (leaves[0].x, leaves[0].y, leaves[0].w, leaves[0].h) == (0, 0, 20, 20)


def test_bsp_partition_stops_when_too_small_to_split_further():
    rng = random.Random(4)
    # A rectangle smaller than 2*min_size in both dimensions can't be split
    # at all, however deep we ask it to recurse.
    leaves = bsp_partition(rng, 0, 0, 8, 8, depth=10, min_size=5)
    assert len(leaves) == 1


def test_mst_empty_and_single_point():
    assert minimum_spanning_tree([]) == []
    assert minimum_spanning_tree([(0, 0)]) == []


def test_mst_connects_all_points_with_n_minus_one_edges():
    points = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    edges = minimum_spanning_tree(points)
    assert len(edges) == len(points) - 1

    # Every point should be reachable from point 0 via the returned edges.
    adjacency = {i: set() for i in range(len(points))}
    for i, j in edges:
        adjacency[i].add(j)
        adjacency[j].add(i)
    seen = {0}
    frontier = [0]
    while frontier:
        cur = frontier.pop()
        for nxt in adjacency[cur]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(range(len(points)))


def test_mst_is_a_true_minimum_for_a_simple_case():
    # A square plus a center point: the MST should use the 4 short
    # center-to-corner spokes (each length ~7.07), not any of the longer
    # (length-10) sides or the length-14.1 diagonal.
    points = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    edges = minimum_spanning_tree(points)
    total_length = sum(
        ((points[i][0] - points[j][0]) ** 2 + (points[i][1] - points[j][1]) ** 2) ** 0.5
        for i, j in edges
    )
    assert total_length == pytest.approx(4 * (50 ** 0.5), rel=1e-6)


def test_ensure_connected_no_op_when_already_connected():
    centers = [(0, 0), (10, 0), (20, 0)]
    edges = [(0, 1), (1, 2)]
    result = ensure_connected(centers, edges)
    assert result == edges


def test_ensure_connected_joins_separate_components():
    # Two disconnected pairs, far apart - ensure_connected must add exactly
    # one bridging edge to make the whole set reachable from any point.
    centers = [(0, 0), (1, 0), (100, 0), (101, 0)]
    edges = [(0, 1), (2, 3)]
    result = ensure_connected(centers, edges)
    assert len(result) == 3
    assert _all_reachable(len(centers), result)
    # The original edges must be preserved untouched.
    assert (0, 1) in result and (2, 3) in result


def test_ensure_connected_picks_the_shortest_bridging_edge():
    # Three isolated points; the cheapest way to connect them is via the
    # closer pair first, not any arbitrary pairing.
    centers = [(0, 0), (1, 0), (50, 0)]
    edges = []
    result = ensure_connected(centers, edges)
    assert _all_reachable(len(centers), result)
    assert (0, 1) in result or (1, 0) in result


def test_ensure_connected_handles_trivial_inputs():
    assert ensure_connected([], []) == []
    assert ensure_connected([(0, 0)], []) == []
