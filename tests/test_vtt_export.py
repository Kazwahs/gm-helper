"""Tests for the virtual tabletop export: wall tracing (app/maps/vtt.py), the
export document builder (app/vtt_export.py) and its API."""
import json
import math

import pytest
from fastapi.testclient import TestClient

from app import maps_store, vtt_export
from app.maps import generate_map, vtt


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _floor(cols, rows, cells, res=vtt._RES):
    grid = [[False] * (cols * res) for _ in range(rows * res)]
    for (x0, y0, x1, y1) in cells:  # rectangles in squares
        for j in range(int(y0 * res), int(y1 * res)):
            for i in range(int(x0 * res), int(x1 * res)):
                grid[j][i] = True
    return grid


def _inside(pt, loop):
    x, y = pt
    n, hit = len(loop) - 1, False
    for k in range(n):
        (ax, ay), (bx, by) = loop[k], loop[k + 1]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            hit = not hit
    return hit


def _inside_any(pt, loops):
    return sum(_inside(pt, lp) for lp in loops) % 2 == 1


def _dist_to_walls(pt, walls):
    best = 1e9
    for wall in walls:
        for a, b in zip(wall, wall[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = dx * dx + dy * dy
            t = 0 if not length else max(0, min(1, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / length))
            best = min(best, math.hypot(pt[0] - (a[0] + t * dx), pt[1] - (a[1] + t * dy)))
    return best


# ------------------------------------------------------------- tracing ---
def test_a_rectangle_is_four_corners_closed():
    loops = vtt.trace_walls(_floor(10, 8, [(2, 2, 6, 5)]), 10, 8)
    assert len(loops) == 1
    loop = loops[0]
    assert loop[0] == loop[-1]
    assert sorted(set(loop)) == [(2.0, 2.0), (2.0, 5.0), (6.0, 2.0), (6.0, 5.0)]


def test_an_l_shape_has_six_corners():
    loops = vtt.trace_walls(_floor(10, 10, [(1, 1, 7, 3), (1, 3, 3, 8)]), 10, 10)
    assert len(loops) == 1 and len(set(loops[0])) == 6


def test_separate_rooms_and_a_ring_give_separate_outlines():
    two = vtt.trace_walls(_floor(20, 8, [(1, 1, 5, 5), (10, 1, 15, 5)]), 20, 8)
    assert len(two) == 2
    ring = vtt.trace_walls(_floor(12, 12, [(1, 1, 11, 3), (1, 9, 11, 11), (1, 3, 3, 9), (9, 3, 11, 9)]), 12, 12)
    assert len(ring) == 2  # the outer wall and the pillar in the middle


def test_nothing_to_trace_gives_nothing():
    assert vtt.trace_walls(_floor(5, 5, []), 5, 5) == []


def test_a_round_room_becomes_a_polygon_close_to_the_circle():
    class Round:
        x = y = 2
        w = h = 8
        right = bottom = 10
        cx = cy = 6

        def contains(self, px, py):
            return (px - 6) ** 2 + (py - 6) ** 2 <= 16

    geometry = vtt.rooms_geometry([Round()], [], [], 12, 12, 16)
    (loop,) = geometry["walls"]
    assert 8 <= len(loop) <= 40
    for x, y in loop:
        assert abs(math.hypot(x - 6, y - 6) - 4) < 0.45


# ---------------------------------------------------------- doors/portals ---
class Box:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.right, self.bottom = x + w, y + h

    def contains(self, px, py):
        return self.x <= px <= self.right and self.y <= py <= self.bottom


class Cor:
    def __init__(self, paint):
        self.paint = paint


def _two_rooms(doors):
    rooms = [Box(1, 1, 4, 4), Box(10, 1, 4, 4)]
    return vtt.rooms_geometry(rooms, [Cor((5, 2, 10, 3))], doors, 16, 8, 16)


def _door(kind, x=5 * 16, y=2.5 * 16):
    return {"x": x, "y": y, "width": 16, "axis": "h", "kind": kind}


def test_doors_become_portals_with_the_right_state():
    for kind, closed in (("door", True), ("locked", True), ("swing", False)):
        geometry = _two_rooms([_door(kind)])
        (portal,) = geometry["portals"]
        assert portal["closed"] is closed and portal["freestanding"] is False
        assert portal["position"] == {"x": 5.0, "y": 2.5}
        a, b = portal["bounds"]
        assert (a["x"], a["y"], b["x"], b["y"]) == (5.0, 2.0, 5.0, 3.0)
        assert portal["rotation"] == pytest.approx(math.pi / 2)


def test_an_archway_is_just_a_gap_and_a_secret_door_is_a_wall():
    open_geometry = _two_rooms([_door("arch")])
    assert open_geometry["portals"] == []
    base = len(open_geometry["walls"])
    secret = _two_rooms([_door("secret")])
    assert secret["portals"] == []
    assert len(secret["walls"]) == base + 1
    assert secret["walls"][-1] == [(5.0, 2.0), (5.0, 3.0)]


def test_a_door_in_a_vertical_corridor_is_a_horizontal_bar():
    geometry = vtt.rooms_geometry([Box(1, 1, 4, 4)], [], [{"x": 48, "y": 80, "width": 16, "axis": "v", "kind": "door"}], 8, 8, 16)
    a, b = geometry["portals"][0]["bounds"]
    assert (a["y"], b["y"]) == (5.0, 5.0) and (a["x"], b["x"]) == (2.5, 3.5)
    assert geometry["portals"][0]["rotation"] == 0.0


# ------------------------------------------------------------- real maps ---
@pytest.mark.parametrize("map_type", ["dungeon", "interior", "underground"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_walls_surround_the_drawn_floor(map_type, seed):
    result = generate_map(map_type, "fantasy", {"size": "medium", "style": "ink"}, seed)
    g = result["vtt"]
    walls = [w for w in g["walls"] if len(w) > 2]
    assert walls
    for w in walls:
        assert w[0] == w[-1]
        assert all(0 <= x <= g["cols"] and 0 <= y <= g["rows"] for x, y in w)
    # something that is plainly outside the dungeon is outside the outlines
    assert not _inside_any((0.05, 0.05), walls)


def test_every_room_centre_is_inside_the_walls_and_doors_sit_on_them():
    from app.maps import ink_rooms
    for seed in range(1, 6):
        result = generate_map("dungeon", "fantasy", {"size": "medium", "style": "ink"}, seed)
        g = result["vtt"]
        outlines = [w for w in g["walls"] if len(w) > 2]
        import re
        centres = re.findall(r'data-kind="room" data-cx="([\d.]+)" data-cy="([\d.]+)"', result["svg"])
        assert len(centres) == result["meta"]["rooms"]
        for cx, cy in centres:
            assert _inside_any((float(cx) / ink_rooms.PX, float(cy) / ink_rooms.PX), outlines)
        for portal in g["portals"]:
            for end in portal["bounds"]:
                assert _dist_to_walls((end["x"], end["y"]), g["walls"]) < 0.4


def test_secret_doors_in_the_map_become_extra_wall_segments():
    for seed in range(1, 40):
        result = generate_map("dungeon", "fantasy", {"size": "large", "style": "ink"}, seed)
        if result["meta"]["secret_doors"]:
            segments = [w for w in result["vtt"]["walls"] if len(w) == 2]
            assert len(segments) == result["meta"]["secret_doors"]
            return
    pytest.fail("no map with a secret door in 40 seeds")


def test_portals_count_matches_the_visible_doors():
    result = generate_map("dungeon", "fantasy", {"size": "large", "style": "ink"}, 5)
    meta = result["meta"]
    assert len(result["vtt"]["portals"]) <= meta["doors"] - meta["secret_doors"]


# -------------------------------------------------------------- document ---
def test_the_document_has_the_universal_vtt_fields():
    g = generate_map("dungeon", "fantasy", {"size": "small", "style": "ink"}, 4)["vtt"]
    doc = vtt.uvtt_document(g, 100, image="AAAA")
    assert doc["format"] == 0.3
    assert doc["resolution"] == {"map_origin": {"x": 0, "y": 0}, "map_size": {"x": 42, "y": 30}, "pixels_per_grid": 100}
    assert doc["image"] == "AAAA"
    assert doc["objects_line_of_sight"] == [] and doc["lights"] == []
    assert doc["environment"] == {"baked_lighting": True, "ambient_light": "ffffffff"}
    assert all(set(p) == {"x", "y"} for wall in doc["line_of_sight"] for p in wall)
    for portal in doc["portals"]:
        assert set(portal) == {"position", "bounds", "rotation", "closed", "freestanding"}
    assert json.loads(json.dumps(doc)) == doc


def test_pixels_per_grid_is_clamped():
    assert vtt.clamp_ppg(1) == vtt.MIN_PPG and vtt.clamp_ppg(10**6) == vtt.MAX_PPG
    assert vtt.clamp_ppg("abc") == vtt.DEFAULT_PIXELS_PER_GRID and vtt.clamp_ppg("90") == 90


# ----------------------------------------------------------- export_info ---
def _row(map_type="dungeon", style="ink", seed=7, genre="fantasy", title="Crypt of Doom!"):
    r = generate_map(map_type, genre, {"size": "small", "style": style}, seed)
    return {"map_type": map_type, "genre": genre, "seed": seed, "params": r["params"], "svg": r["svg"], "title": title}


def test_an_inked_dungeon_exports_with_walls_and_matching_image_size():
    info = vtt_export.export_info(_row(), 70)
    assert info["walls"] is True and info["filename"] == "crypt-of-doom.dd2vtt"
    assert (info["image_width"], info["image_height"]) == (42 * 70, 30 * 70)
    assert info["document"]["line_of_sight"] and info["document"]["portals"]
    assert "outline" in info["note"]
    # the image is exactly the map's own proportions
    assert info["image_width"] / info["image_height"] == pytest.approx(42 / 30)


def test_caves_and_buildings_export_with_walls():
    for map_type in ("interior", "underground"):
        assert vtt_export.export_info(_row(map_type))["walls"] is True


def test_other_cases_fall_back_to_image_and_grid_only():
    classic = vtt_export.export_info(_row(style="classic"))
    assert classic["walls"] is False and classic["document"]["line_of_sight"] == []
    assert "can't be worked out" in classic["note"]
    seedless = _row()
    seedless["seed"] = None
    assert vtt_export.export_info(seedless)["walls"] is False
    changed = _row()
    changed["svg"] = _row(seed=8)["svg"]  # a different map saved under this seed
    assert vtt_export.export_info(changed)["walls"] is False
    city = vtt_export.export_info(_row("urban"), 70)
    assert city["walls"] is False and "city" in city["note"]
    assert city["image_width"] % 70 == 0 and city["image_height"] % 70 == 0


def test_renaming_rooms_doesnt_stop_walls_being_found():
    row = _row()
    row["svg"] = row["svg"].replace('data-name="', 'data-name="Renamed ')
    assert vtt_export.export_info(row)["walls"] is True


def test_maps_without_a_square_grid_cant_be_exported():
    assert vtt_export.supported("dungeon") and vtt_export.supported("urban")
    assert not vtt_export.supported("world") and not vtt_export.supported("wilderness")
    with pytest.raises(vtt_export.VttError):
        vtt_export.export_info({"map_type": "world", "svg": "<svg/>", "title": "W"})
    with pytest.raises(vtt_export.VttError):
        vtt_export.export_info({"map_type": "urban", "svg": "<svg/>", "title": "W", "genre": "fantasy", "seed": 1})


# ------------------------------------------------------------------- API ---
def _saved(map_type="dungeon", seed=7):
    r = generate_map(map_type, "fantasy", {"size": "small", "style": "ink"}, seed)
    return maps_store.save_map("rifts", map_type, "fantasy", "Test", r["svg"], seed=seed, params=r["params"])


def test_api_returns_the_document_and_clamps_ppg(client):
    map_id = _saved()
    res = client.get(f"/api/maps/{map_id}/vtt?ppg=100").json()
    assert res["ok"] and res["walls"] and res["document"]["resolution"]["pixels_per_grid"] == 100
    assert res["image_width"] == 4200 and res["document"]["image"] == ""
    assert client.get(f"/api/maps/{map_id}/vtt?ppg=1").json()["document"]["resolution"]["pixels_per_grid"] == vtt.MIN_PPG
    assert client.get(f"/api/maps/{map_id}/vtt").json()["document"]["resolution"]["pixels_per_grid"] == 70


def test_api_errors(client):
    assert client.get("/api/maps/999/vtt").status_code == 404
    world = generate_map("world", "fantasy", {"size": "small"}, 1)
    wid = maps_store.save_map("rifts", "world", "fantasy", "W", world["svg"], seed=1)
    res = client.get(f"/api/maps/{wid}/vtt")
    assert res.status_code == 400 and not res.json()["ok"]


def test_the_export_card_shows_only_for_gridded_maps(client):
    page = client.get(f"/maps/{_saved()}").text
    assert 'id="vtt-card"' in page and "vtt_export.js" in page
    world = generate_map("world", "fantasy", {"size": "small"}, 1)
    wid = maps_store.save_map("rifts", "world", "fantasy", "W", world["svg"])
    assert 'id="vtt-card"' not in client.get(f"/maps/{wid}").text
    assert client.get("/static/vtt_export.js").status_code == 200
