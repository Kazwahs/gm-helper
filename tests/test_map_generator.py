"""Tests for the map generator dispatcher and the six concrete generators
(app/maps/generator.py + rooms/urban/caves/terrain.py). No database
involved - generate_map() is a pure function of (map_type, genre, params,
seed)."""
import xml.etree.ElementTree as ET

import pytest

from app.maps.generator import GENRES, MAP_TYPES, SIZES, generate_map

MAP_TYPE_KEYS = [m["key"] for m in MAP_TYPES]
GENRE_KEYS = [g["key"] for g in GENRES]


def test_unknown_map_type_raises():
    with pytest.raises(ValueError):
        generate_map("not-a-real-type", "fantasy")


def test_unknown_genre_raises():
    with pytest.raises(ValueError):
        generate_map("dungeon", "not-a-real-genre")


def test_invalid_size_falls_back_to_medium_instead_of_raising():
    result = generate_map("dungeon", "fantasy", params={"size": "enormous"}, seed=1)
    assert result["params"]["size"] == "medium"


def test_result_shape_and_seed_echoed_back():
    result = generate_map("dungeon", "fantasy", seed=42)
    assert result["seed"] == 42
    assert result["map_type"] == "dungeon"
    assert result["genre"] == "fantasy"
    assert isinstance(result["svg"], str) and result["svg"].startswith("<svg")
    assert "meta" in result and isinstance(result["meta"], dict)
    assert "title_suggestion" in result


def test_no_seed_still_produces_a_seed_for_reproducibility():
    result = generate_map("dungeon", "fantasy")
    assert isinstance(result["seed"], int)


def test_same_seed_is_byte_identical():
    a = generate_map("wilderness", "mixed", params={"size": "small"}, seed=777)
    b = generate_map("wilderness", "mixed", params={"size": "small"}, seed=777)
    assert a["svg"] == b["svg"]


def test_different_seeds_usually_differ():
    a = generate_map("urban", "fantasy", params={"size": "small"}, seed=1)
    b = generate_map("urban", "fantasy", params={"size": "small"}, seed=2)
    assert a["svg"] != b["svg"]


@pytest.mark.parametrize("map_type", MAP_TYPE_KEYS)
@pytest.mark.parametrize("genre", GENRE_KEYS)
def test_every_type_and_genre_combination_produces_valid_looking_svg(map_type, genre):
    result = generate_map(map_type, genre, params={"size": "small"}, seed=5)
    svg = result["svg"]
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    ET.fromstring(svg)  # raises ParseError if the SVG isn't well-formed XML


@pytest.mark.parametrize("size", SIZES)
def test_every_size_preset_works_for_each_generator_family(size):
    for map_type in MAP_TYPE_KEYS:
        result = generate_map(map_type, "fantasy", params={"size": size}, seed=1)
        assert result["meta"]["size"] == size


@pytest.mark.parametrize("map_type", ["dungeon", "interior", "urban", "underground"])
def test_stylized_types_wrap_generated_elements_as_editable(map_type):
    """These four generators are expected to tag their generated pieces with
    the map-el/map-el-fill/map-el-label classes the in-app map editor relies
    on for select/drag/relabel/recolor/delete."""
    result = generate_map(map_type, "fantasy", params={"size": "small"}, seed=3)
    svg = result["svg"]
    assert 'class="map-el"' in svg
    assert 'class="map-el-fill"' in svg


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_terrain_types_wrap_hexes_and_settlements_as_editable(map_type):
    result = generate_map(map_type, "fantasy", params={"size": "small"}, seed=3)
    svg = result["svg"]
    assert 'data-kind="hex"' in svg
    # A "small" map may occasionally roll zero settlements - only assert the
    # settlement wrapper class exists on maps that actually got some.
    if result["meta"]["settlements"] > 0:
        assert 'data-kind="settlement"' in svg


def test_dungeon_room_count_matches_meta():
    result = generate_map("dungeon", "fantasy", params={"size": "medium"}, seed=9)
    room_count = result["meta"]["rooms"]
    assert result["svg"].count('data-kind="room"') == room_count


def test_underground_never_produces_a_completely_blank_map():
    """Regression guard for the cellular-automata noise roll: on an
    all-walls result, generate() carves a small guaranteed room instead of
    returning a map with zero floor cells."""
    for seed in range(25):
        result = generate_map("underground", "fantasy", params={"size": "small"}, seed=seed)
        assert result["meta"]["floor_cells"] > 0
