"""Tests for the shared object/icon placement helpers (app/maps/objects.py)
used to dress dungeon/interior rooms and drop points of interest on
wilderness/world maps."""
import math
import random

import pytest

from app.maps import objects


def test_pool_mixed_combines_all_genres():
    fantasy_only = objects._pool(objects.ROOM_OBJECTS, "fantasy")
    mixed = objects._pool(objects.ROOM_OBJECTS, "mixed")
    assert len(mixed) == sum(len(objects.ROOM_OBJECTS[g]) for g in objects.GENRES)
    for item in fantasy_only:
        assert item in mixed


def test_pool_unknown_genre_falls_back_to_fantasy():
    assert objects._pool(objects.ROOM_OBJECTS, "not-a-genre") == objects.ROOM_OBJECTS["fantasy"]


def test_render_icon_known_keys_produce_a_recolorable_shape():
    for icon_key in objects._ICONS:
        svg_fragment = objects.render_icon(icon_key, 10, 10, 5, "#abcdef")
        assert 'class="map-el-fill"' in svg_fragment
        assert "#abcdef" in svg_fragment


def test_render_icon_unknown_key_falls_back_to_crate():
    assert objects.render_icon("not-a-real-icon", 0, 0, 5, "#000") == objects._icon_crate(0, 0, 5, "#000")


def test_place_room_objects_stays_within_room_bounds():
    rng = random.Random(1)
    room = {"x": 0, "y": 0, "w": 10, "h": 8, "shape": "rect"}
    px = 20
    placed = objects.place_room_objects(rng, room, "fantasy", "#8a7248", px)
    for obj in placed:
        assert 0 <= obj["cx"] <= room["w"] * px
        assert 0 <= obj["cy"] <= room["h"] * px


def test_place_room_objects_respects_circle_cap():
    # Circular rooms cap at 2 objects regardless of area, since a circle's
    # usable interior is smaller than a rect of the same bounding box.
    rng = random.Random(2)
    big_circle_room = {"x": 0, "y": 0, "w": 20, "h": 20, "shape": "circle"}
    for seed in range(30):
        placed = objects.place_room_objects(random.Random(seed), big_circle_room, "fantasy", "#8a7248", 20)
        assert len(placed) <= 2


def test_place_room_objects_never_exceeds_rect_cap():
    big_room = {"x": 0, "y": 0, "w": 30, "h": 30, "shape": "rect"}
    for seed in range(30):
        placed = objects.place_room_objects(random.Random(seed), big_room, "fantasy", "#8a7248", 20)
        assert len(placed) <= 4


def test_place_room_objects_are_spaced_apart():
    rng = random.Random(4)
    room = {"x": 0, "y": 0, "w": 15, "h": 15, "shape": "rect"}
    px = 20
    placed = objects.place_room_objects(rng, room, "modern", "#5a6a7a", px)
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"]) > 0


def test_place_room_objects_some_rooms_come_up_bare():
    # With enough seeds, at least some should roll the "no objects" branch.
    room = {"x": 0, "y": 0, "w": 6, "h": 6, "shape": "rect"}
    counts = [len(objects.place_room_objects(random.Random(seed), room, "fantasy", "#8a7248", 20))
              for seed in range(60)]
    assert any(c == 0 for c in counts)
    assert any(c > 0 for c in counts)


def test_place_points_of_interest_empty_inputs():
    assert objects.place_points_of_interest(random.Random(1), [], "fantasy", 3) == []
    assert objects.place_points_of_interest(random.Random(1), [(0, 0, 0, 0)], "fantasy", 0) == []


def test_place_points_of_interest_respects_count_and_spacing():
    rng = random.Random(5)
    candidates = [(c, r, c * 60.0, r * 60.0) for c in range(10) for r in range(10)]
    placed = objects.place_points_of_interest(rng, candidates, "hightech", 4)
    assert len(placed) <= 4
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"]) > 3.0 * 17 - 1e-6


def test_place_points_of_interest_never_reuses_a_hex():
    rng = random.Random(6)
    candidates = [(c, r, c * 60.0, r * 60.0) for c in range(6) for r in range(6)]
    placed = objects.place_points_of_interest(rng, candidates, "fantasy", 8)
    seen = {(p["col"], p["row"]) for p in placed}
    assert len(seen) == len(placed)


def test_cave_and_urban_catalogs_only_reference_real_icons():
    for catalog in (objects.CAVE_OBJECTS, objects.URBAN_PROPS):
        for genre_items in catalog.values():
            for icon_key, label in genre_items:
                assert icon_key in objects._ICONS
                assert isinstance(label, str) and label


def test_pool_mixed_combines_all_genres_for_new_catalogs():
    for catalog in (objects.CAVE_OBJECTS, objects.URBAN_PROPS):
        mixed = objects._pool(catalog, "mixed")
        assert len(mixed) == sum(len(catalog[g]) for g in objects.GENRES)


def test_place_scattered_objects_respects_custom_min_dist():
    rng = random.Random(7)
    candidates = [(c, r, c * 20.0, r * 20.0) for c in range(10) for r in range(10)]
    placed = objects.place_scattered_objects(rng, candidates, "fantasy", objects.CAVE_OBJECTS, 5, min_dist=30.0)
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            assert math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"]) > 30.0 - 1e-6


def test_place_room_objects_supports_a_custom_catalog_and_density():
    # Urban blocks reuse place_room_objects with URBAN_PROPS and a looser
    # area divisor - confirm the override actually takes effect instead of
    # silently falling back to ROOM_OBJECTS.
    rng = random.Random(8)
    room = {"x": 0, "y": 0, "w": 12, "h": 12}
    placed = objects.place_room_objects(rng, room, "modern", "#2a2a2a", 14,
                                         catalog=objects.URBAN_PROPS, area_divisor=60, max_count=3)
    urban_icon_keys = {icon for icon, _ in objects._pool(objects.URBAN_PROPS, "modern")}
    for obj in placed:
        assert obj["icon"] in urban_icon_keys
        assert len(placed) <= 3
