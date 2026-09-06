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


def test_underground_layouts_produce_all_three_archetypes():
    """caves.py picks one of three structurally distinct floor plans
    (cavern/tunnels/chambers) per generation - across enough seeds, all
    three should actually get selected."""
    layouts_seen = set()
    for seed in range(60):
        result = generate_map("underground", "fantasy", params={"size": "medium"}, seed=seed)
        layouts_seen.add(result["meta"]["layout"])
    assert layouts_seen == {"cavern", "tunnels", "chambers"}


def test_underground_layouts_never_produce_a_blank_map():
    for seed in range(60):
        result = generate_map("underground", "fantasy", params={"size": "small"}, seed=seed)
        assert result["meta"]["floor_cells"] > 0


def test_underground_objects_are_wrapped_as_editable_when_present():
    result = generate_map("underground", "fantasy", params={"size": "large"}, seed=4)
    if result["meta"]["objects"] > 0:
        assert 'data-kind="object"' in result["svg"]


def test_urban_layouts_produce_all_three_archetypes():
    """urban.py picks one of three structurally distinct street plans
    (organic/grid/radial) per generation - across enough seeds, all three
    should actually get selected."""
    layouts_seen = set()
    for seed in range(60):
        result = generate_map("urban", "fantasy", params={"size": "medium"}, seed=seed)
        layouts_seen.add(result["meta"]["layout"])
    assert layouts_seen == {"organic", "grid", "radial"}


def test_urban_layouts_never_produce_a_blank_map():
    for seed in range(60):
        result = generate_map("urban", "fantasy", params={"size": "small"}, seed=seed)
        assert result["meta"]["blocks"] > 0


def test_urban_district_color_cycling_never_runs_out():
    """Regression guard: a big 'mixed'-genre map can roll many more
    distinct districts than the base color palette has entries. Block
    colors must cycle back around instead of raising StopIteration."""
    for seed in range(120):
        result = generate_map("urban", "mixed", params={"size": "large"}, seed=seed)
        assert result["meta"]["blocks"] > 0


def test_urban_objects_are_wrapped_as_editable_when_present():
    result = generate_map("urban", "fantasy", params={"size": "large"}, seed=1)
    if result["meta"]["objects"] > 0:
        assert 'data-kind="object"' in result["svg"]


@pytest.mark.parametrize("map_type", ["dungeon", "interior"])
def test_room_layouts_produce_all_three_archetypes(map_type):
    """rooms.py picks one of three structurally distinct layouts (warren/
    keep/hub) per generation - across enough seeds, all three should
    actually get selected rather than one dominating or a typo silently
    excluding one."""
    layouts_seen = set()
    for seed in range(60):
        result = generate_map(map_type, "fantasy", params={"size": "medium"}, seed=seed)
        layouts_seen.add(result["meta"]["layout"])
    assert layouts_seen == {"warren", "keep", "hub"}


@pytest.mark.parametrize("map_type", ["dungeon", "interior"])
def test_room_layouts_stay_valid_and_connected(map_type):
    """Whichever layout gets picked, the room count in meta must still
    match the rendered rooms, and the map must still be well-formed."""
    for seed in range(40):
        result = generate_map(map_type, "fantasy", params={"size": "medium"}, seed=seed)
        ET.fromstring(result["svg"])
        assert result["svg"].count('data-kind="room"') == result["meta"]["rooms"]


@pytest.mark.parametrize("map_type", ["dungeon", "interior"])
def test_room_objects_are_wrapped_as_editable_when_present(map_type):
    result = generate_map(map_type, "fantasy", params={"size": "large"}, seed=11)
    if result["meta"]["objects"] > 0:
        assert 'data-kind="object"' in result["svg"]


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_terrain_landmasses_produce_all_four_variants(map_type):
    landmasses_seen = set()
    for seed in range(80):
        result = generate_map(map_type, "fantasy", params={"size": "medium"}, seed=seed)
        landmasses_seen.add(result["meta"]["landmass"])
    assert landmasses_seen == {"continent", "archipelago", "peninsula", "inland_sea"}


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_terrain_never_produces_a_completely_landless_map(map_type):
    """Regression guard for the landmass templates: none of the four should
    ever be able to suppress land down to nothing (or, for inland_sea,
    flood so much of the interior that no land is left)."""
    for seed in range(60):
        result = generate_map(map_type, "fantasy", params={"size": "small"}, seed=seed)
        assert "ocean" not in result["svg"] or 'data-kind="hex"' in result["svg"]
        # At least one non-ocean biome swatch must appear in the legend.
        assert any(biome in result["svg"] for biome in
                   ("Plains", "Forest", "Hills", "Mountains", "Desert", "Swamp", "Coast"))


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_terrain_points_of_interest_are_wrapped_as_editable_when_present(map_type):
    result = generate_map(map_type, "fantasy", params={"size": "large"}, seed=21)
    if result["meta"]["points_of_interest"] > 0:
        assert 'data-kind="poi"' in result["svg"]
