"""Tests for the default hand-inked map renderers (app/maps/ink*.py) and the
way the app wires them in: the style option, the editor contract the ink
maps must keep (so edit / fog / tokens / print still work), secret doors,
and the generator's guarantees (connected dungeons, no blank maps)."""
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.maps import generator
from app.maps.generator import GENRES, MAP_TYPES, SIZES, generate_map

MAP_TYPE_KEYS = [m["key"] for m in MAP_TYPES]
GENRE_KEYS = [g["key"] for g in GENRES]
ROOM_TYPES = ["dungeon", "interior"]


def ink(map_type, genre="fantasy", size="small", seed=1, **extra):
    return generate_map(map_type, genre, params={"size": size, **extra}, seed=seed)


# ---------------------------------------------------------------- style option
def test_ink_is_the_default_style():
    assert generator.DEFAULT_STYLE == "ink"
    assert generate_map("dungeon", "fantasy", seed=1)["params"]["style"] == "ink"
    assert [s["key"] for s in generator.STYLES] == ["ink", "classic"]


@pytest.mark.parametrize("bad", [None, "", "watercolour", 7])
def test_unknown_style_falls_back_to_ink(bad):
    result = generate_map("dungeon", "fantasy", params={"style": bad}, seed=1)
    assert result["params"]["style"] == "ink"
    assert result["meta"]["style"] == "ink"


@pytest.mark.parametrize("map_type", MAP_TYPE_KEYS)
def test_classic_style_is_still_available_and_different(map_type):
    classic = generate_map(map_type, "fantasy", params={"size": "small", "style": "classic"}, seed=4)
    drawn = ink(map_type, seed=4)
    assert classic["params"]["style"] == "classic"
    assert classic["meta"].get("style") != "ink"
    assert classic["svg"] != drawn["svg"]


@pytest.mark.parametrize("map_type", MAP_TYPE_KEYS)
def test_ink_maps_are_deterministic(map_type):
    assert ink(map_type, seed=21)["svg"] == ink(map_type, seed=21)["svg"]
    assert ink(map_type, seed=21)["svg"] != ink(map_type, seed=22)["svg"]


def test_hex_grid_option_is_normalised_to_a_bool():
    assert generate_map("wilderness", "fantasy", params={"hex_grid": "1"}, seed=1)["params"]["hex_grid"] is True
    assert generate_map("wilderness", "fantasy", params={"hex_grid": ""}, seed=1)["params"]["hex_grid"] is False
    assert generate_map("world", "fantasy", params={"hex_grid": "false"}, seed=1)["params"]["hex_grid"] is False


# ------------------------------------------------------------------ all types
@pytest.mark.parametrize("map_type", MAP_TYPE_KEYS)
@pytest.mark.parametrize("genre", GENRE_KEYS)
def test_every_ink_map_is_well_formed_xml(map_type, genre):
    for seed in (1, 2, 3):
        svg = ink(map_type, genre, seed=seed)["svg"]
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg")


@pytest.mark.parametrize("map_type", MAP_TYPE_KEYS)
@pytest.mark.parametrize("size", SIZES)
def test_ink_maps_stay_within_a_size_budget(map_type, size):
    """The SVG is stored in SQLite, inlined in the page and cloned once per
    printed page, so it has to stay small."""
    for seed in (1, 2, 3):
        assert len(ink(map_type, size=size, seed=seed)["svg"]) < 260_000


@pytest.mark.parametrize("map_type", MAP_TYPE_KEYS)
def test_ink_maps_keep_the_editor_contract(map_type):
    svg = ink(map_type, size="medium", seed=5)["svg"]
    root = ET.fromstring(svg)
    els = [n for n in root.iter() if n.get("class") and "map-el" in n.get("class").split()]
    assert els, "an ink map with nothing selectable would leave the editor empty"
    for n in els:
        assert n.get("data-kind")
        float(n.get("data-cx")), float(n.get("data-cy"))
    # every recolourable element is a real, hit-testable shape (fill="none" can't be clicked)
    for n in root.iter():
        if n.get("class") == "map-el-fill":
            assert n.get("fill") not in (None, "none")


def test_every_ink_map_has_a_title_and_a_valid_scale_of_pixels():
    for map_type in MAP_TYPE_KEYS:
        result = ink(map_type, seed=2)
        assert result["title_suggestion"]
        m = re.match(r'<svg [^>]*width="([\d.]+)" height="([\d.]+)" viewBox="0 0 ([\d.]+) ([\d.]+)"', result["svg"])
        assert m and m.group(1) == m.group(3) and m.group(2) == m.group(4)


# ------------------------------------------------------------ dungeons & interiors
@pytest.mark.parametrize("map_type", ROOM_TYPES)
def test_room_maps_are_always_connected(map_type):
    for seed in range(40):
        for size in ("small", "medium"):
            result = ink(map_type, size=size, seed=seed)
            assert result["meta"]["connected"] is True, (map_type, size, seed)
            assert result["meta"]["rooms"] >= 3


@pytest.mark.parametrize("map_type", ROOM_TYPES)
def test_room_layouts_cover_all_archetypes(map_type):
    seen = {ink(map_type, size="medium", seed=s)["meta"]["layout"] for s in range(50)}
    assert seen == {"warren", "keep", "hub"}


@pytest.mark.parametrize("map_type", ROOM_TYPES)
def test_rooms_are_locked_editable_elements_matching_meta(map_type):
    result = ink(map_type, size="medium", seed=9)
    svg = result["svg"]
    assert svg.count('data-kind="room"') == result["meta"]["rooms"]
    # rooms sit over pen-drawn walls, so the editor must not let them be dragged or deleted
    rooms = re.findall(r'<g class="map-el"[^>]*data-kind="room"[^>]*>', svg)
    assert len(rooms) == result["meta"]["rooms"]
    assert all('data-locked="1"' in r for r in rooms)


@pytest.mark.parametrize("map_type", ROOM_TYPES)
def test_room_tint_starts_invisible_but_clickable(map_type):
    svg = ink(map_type, size="medium", seed=9)["svg"]
    tints = re.findall(r'<path class="map-el-fill"[^>]*data-tint="[^"]+"[^>]*/>', svg)
    assert tints, "the editor recolours rooms through this path"
    for t in tints:
        assert 'fill="none"' not in t
    assert any('fill-opacity="0"' in t for t in tints)


def test_secret_doors_are_marked_for_the_gm_only():
    total = 0
    for seed in range(40):
        result = ink("dungeon", size="medium", seed=seed)
        marks = len(re.findall(r'<g class="gm-only">', result["svg"]))
        assert marks == result["meta"]["secret_doors"]
        total += marks
    assert total > 0, "across 40 dungeons at least one secret door should appear"


def test_doors_come_in_several_kinds_and_stairs_appear():
    stairs = secret = 0
    for seed in range(40):
        meta = ink("dungeon", size="medium", seed=seed)["meta"]
        assert meta["doors"] >= meta["secret_doors"]
        stairs += meta["stairs"]
        secret += meta["secret_doors"]
    assert stairs > 0 and secret > 0


def test_room_objects_are_separate_draggable_elements():
    result = ink("dungeon", size="large", seed=11)
    assert result["meta"]["objects"] > 0
    assert result["svg"].count('data-kind="object"') == result["meta"]["objects"]


def test_the_ink_walls_use_one_merged_floor_with_a_grid():
    svg = ink("dungeon", size="medium", seed=3)["svg"]
    assert svg.count('<use href="#') >= 2          # pen wall + floor fill share one path
    assert "<pattern" in svg and "<clipPath" in svg


# -------------------------------------------------------------------------- caves
def test_ink_caves_are_never_blank_and_cover_all_layouts():
    seen = set()
    for seed in range(40):
        result = ink("underground", size="medium", seed=seed)
        assert result["meta"]["floor_cells"] > 0
        seen.add(result["meta"]["layout"])
    assert seen == {"cavern", "tunnels", "chambers"}


def test_cave_labels_use_the_page_halo_colour_so_they_stay_readable():
    for genre in GENRE_KEYS:
        svg = ink("underground", genre, "medium", seed=3)["svg"]
        halo = re.search(r"paint-order:stroke;stroke:(#[0-9a-fA-F]+)", svg)
        assert halo, genre
        # dark rock with a pale halo (or the reverse) - the halo is never the label colour
        labels = re.findall(r'<text[^>]*fill="(#[0-9a-fA-F]+)"[^>]*class="map-el-label"', svg)
        assert all(c.lower() != halo.group(1).lower() for c in labels)


# ---------------------------------------------------------------------- overland
@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_ink_terrain_has_no_hex_cells_and_a_legend_strip(map_type):
    result = ink(map_type, size="medium", seed=3)
    svg = result["svg"]
    assert 'data-kind="hex"' not in svg
    assert result["meta"]["hexes"] == 0
    for biome in ("Forest", "Mountains"):
        assert biome in svg or biome.lower() in svg.lower()
    spec = re.search(r'width="([\d.]+)" height="([\d.]+)"', svg)
    assert float(spec.group(1)) > float(spec.group(2)) * 0.9


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_ink_terrain_landmass_variants_all_appear(map_type):
    seen = {ink(map_type, size="small", seed=s)["meta"]["landmass"] for s in range(45)}
    assert seen == {"continent", "archipelago", "peninsula", "inland_sea"}


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_ink_terrain_is_never_all_water_and_has_rivers_or_settlements(map_type):
    rivers = towns = 0
    for seed in range(30):
        meta = ink(map_type, size="small", seed=seed)["meta"]
        rivers += meta["rivers"]
        towns += meta["settlements"]
    assert rivers > 0 and towns > 0


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_hex_overlay_is_one_cheap_pattern_and_only_when_asked(map_type):
    plain = ink(map_type, size="medium", seed=7)
    hexed = ink(map_type, size="medium", seed=7, hex_grid=True)
    assert plain["meta"]["hex_grid"] is False and hexed["meta"]["hex_grid"] is True
    assert "<pattern" not in plain["svg"] and hexed["svg"].count("<pattern") == 1
    assert len(hexed["svg"]) - len(plain["svg"]) < 2_000
    ET.fromstring(hexed["svg"])


@pytest.mark.parametrize("map_type", ["wilderness", "world"])
def test_terrain_settlements_and_points_of_interest_are_editable(map_type):
    result = ink(map_type, size="large", seed=21)
    meta, svg = result["meta"], result["svg"]
    if meta["settlements"]:
        assert 'data-kind="settlement"' in svg
    if meta["points_of_interest"]:
        assert 'data-kind="poi"' in svg


# ---------------------------------------------------------------------------- city
def test_city_layouts_cover_all_archetypes():
    seen = {ink("urban", size="medium", seed=s)["meta"]["layout"] for s in range(40)}
    assert seen == {"organic", "grid", "radial"}


def test_cities_have_blocks_and_building_footprints():
    for seed in range(30):
        for size in SIZES:
            meta = ink("urban", size=size, seed=seed)["meta"]
            assert meta["blocks"] >= 8
            assert meta["buildings"] >= 15, (size, seed)


def test_city_blocks_are_locked_but_recolourable():
    result = ink("urban", size="medium", seed=5)
    svg = result["svg"]
    blocks = re.findall(r'<g class="map-el"[^>]*data-kind="block"[^>]*>', svg)
    assert blocks and all('data-locked="1"' in b for b in blocks)
    assert 'class="map-el-fill"' in svg


def test_cities_sometimes_get_walls_and_landmarks():
    walled = landmarks = 0
    for seed in range(40):
        meta = ink("urban", "fantasy", "medium", seed=seed)["meta"]
        walled += 1 if meta["walled"] else 0
        landmarks += meta["landmarks"]
    assert walled > 0 and landmarks > 0


def test_city_legend_lives_beside_the_map_not_on_it():
    svg = ink("urban", size="medium", seed=2)["svg"]
    width = float(re.search(r'width="([\d.]+)"', svg).group(1))
    assert width > 62 * 14               # the map itself is 62 squares of 14px; the rest is the legend strip


def test_big_mixed_cities_never_run_out_of_district_colours():
    for seed in range(60):
        assert ink("urban", "mixed", "large", seed=seed)["meta"]["blocks"] > 0


# ------------------------------------------------------------------- app wiring
@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_generate_api_defaults_to_ink_and_accepts_a_style(client):
    res = client.post("/api/maps/generate", data={"map_type": "dungeon", "genre": "fantasy", "size": "small"})
    assert res.status_code == 200 and res.json()["params"]["style"] == "ink"
    res = client.post("/api/maps/generate", data={"map_type": "dungeon", "genre": "fantasy", "style": "classic"})
    assert res.json()["params"]["style"] == "classic"
    res = client.post("/api/maps/generate", data={"map_type": "dungeon", "genre": "fantasy", "style": "nonsense"})
    assert res.status_code == 200 and res.json()["params"]["style"] == "ink"


def test_generate_api_passes_the_hex_grid_option_through(client):
    res = client.post("/api/maps/generate", data={"map_type": "world", "genre": "fantasy", "size": "small", "hex_grid": "1"})
    assert res.json()["params"]["hex_grid"] is True
    res = client.post("/api/maps/generate", data={"map_type": "world", "genre": "fantasy", "size": "small"})
    assert res.json()["params"]["hex_grid"] is False


def test_the_saved_params_remember_the_style(client):
    gen = client.post("/api/maps/generate", data={"map_type": "interior", "genre": "modern", "size": "small"}).json()
    import json as _json
    saved = client.post("/api/maps", data={
        "game_key": "rifts", "map_type": gen["map_type"], "genre": gen["genre"], "title": "Ink test",
        "svg": gen["svg"], "seed": gen["seed"], "params_json": _json.dumps(gen["params"]),
    })
    assert saved.status_code == 200
    from app import maps_store
    assert maps_store.get_map(saved.json()["id"])["params"]["style"] == "ink"


def test_maps_page_offers_the_style_and_hex_options(client):
    html = client.get("/maps").text
    assert 'id="style-select"' in html and "Ink (hand-drawn)" in html and "Classic (flat colour)" in html
    assert 'id="hex-grid"' in html


def _saved_ink_dungeon(client):
    for seed in range(60):
        gen = generate_map("dungeon", "fantasy", params={"size": "medium"}, seed=seed)
        if gen["meta"]["secret_doors"]:
            break
    res = client.post("/api/maps", data={
        "game_key": "rifts", "map_type": "dungeon", "genre": "fantasy", "title": "Secret test",
        "svg": gen["svg"], "seed": gen["seed"], "params_json": "{}",
    })
    return res.json()["id"]


def test_player_page_hides_gm_only_marks(client):
    map_id = _saved_ink_dungeon(client)
    page = client.get(f"/maps/{map_id}/player")
    assert page.status_code == 200 and 'class="gm-only"' in page.text
    css = client.get("/static/style.css").text
    assert ".player-stage .gm-only" in css and "display: none" in css.split(".player-stage .gm-only")[1][:40]


def test_print_page_keeps_the_source_renderable_and_offers_a_secret_door_toggle(client):
    map_id = _saved_ink_dungeon(client)
    html = client.get(f"/maps/{map_id}/print").text
    assert 'id="source" class="source-stash"' in html and 'id="source" hidden' not in html
    assert 'id="show-gm"' in html and "body:not(.show-gm) .print-tile .gm-only" in html


def test_ink_maps_are_marked_so_the_print_page_can_drop_their_filters():
    for map_type in MAP_TYPE_KEYS:
        drawn = ink(map_type, seed=3)["svg"]
        assert drawn.startswith("<svg") and 'data-style="ink"' in drawn.split(">", 1)[0]
        assert 'class="paper-grain"' in drawn           # the filter-only layer is removable on its own
        classic = generate_map(map_type, "fantasy", params={"size": "small", "style": "classic"}, seed=3)["svg"]
        assert "data-style" not in classic.split(">", 1)[0] and "paper-grain" not in classic


def test_print_page_drops_ink_effects_unless_asked(client):
    map_id = _saved_ink_dungeon(client)
    html = client.get(f"/maps/{map_id}/print").text
    assert 'id="fx"' in html and "Pen &amp; paper effects" in html
    assert "source.dataset.style === 'ink'" in html and "dropEffects(svg)" in html
    assert "if (isInk && !$('fx').checked) dropEffects(svg);" in html


def test_editor_page_knows_about_locked_rooms_and_room_tints(client):
    map_id = _saved_ink_dungeon(client)
    html = client.get(f"/maps/{map_id}").text
    assert "dataset.locked" in html and "data-tint" in html and "Remove tint" in html
