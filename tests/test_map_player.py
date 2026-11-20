"""Tests for the map player view: fog-of-war state (app/maps_store.py), its
API, the nav-less player page and the print-to-scale page."""
import math

import pytest
from fastapi.testclient import TestClient

from app import database, maps_store
from app.maps import generator

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240"><rect width="320" height="240"/></svg>'


@pytest.fixture
def map_id(db):
    return maps_store.save_map("rifts", "dungeon", "fantasy", "Crypt <of> `Doom`", SVG)


# ------------------------------------------------------------- storage
def test_a_new_map_has_fog_off_and_nothing_revealed(map_id):
    assert maps_store.get_reveal(map_id) == {"enabled": False, "areas": [], "v": 0}


def test_unknown_map(db):
    assert maps_store.get_reveal(999) is None
    for call in (lambda: maps_store.set_fog_enabled(999, True),
                 lambda: maps_store.add_reveal(999, "circle", x=1, y=1, r=5),
                 lambda: maps_store.undo_reveal(999), lambda: maps_store.clear_reveal(999)):
        with pytest.raises(ValueError):
            call()


def test_add_circle_and_rect_areas_and_version_bumps(map_id):
    maps_store.set_fog_enabled(map_id, True)
    maps_store.add_reveal(map_id, "circle", x="100.46", y=50, r=30)
    state = maps_store.add_reveal(map_id, "rect", x=10, y=20, w=80, h=60)
    assert state["enabled"] is True and state["v"] == 3
    assert state["areas"] == [
        {"shape": "circle", "x": 100.5, "y": 50.0, "r": 30.0},
        {"shape": "rect", "x": 10.0, "y": 20.0, "w": 80.0, "h": 60.0},
    ]
    assert maps_store.get_reveal(map_id) == state


def test_undo_and_clear(map_id):
    for i in range(3):
        maps_store.add_reveal(map_id, "circle", x=i, y=i, r=10)
    assert len(maps_store.undo_reveal(map_id)["areas"]) == 2
    maps_store.set_fog_enabled(map_id, True)
    cleared = maps_store.clear_reveal(map_id)
    assert cleared["areas"] == [] and cleared["enabled"] is True  # clearing keeps fog on
    assert maps_store.undo_reveal(map_id)["areas"] == []           # undo on empty is harmless


@pytest.mark.parametrize("shape,vals", [
    ("circle", {"x": "abc", "y": 1, "r": 5}),
    ("circle", {"x": 1, "y": 1, "r": 0}),
    ("circle", {"x": 1, "y": 1, "r": 10 ** 6}),
    ("circle", {"x": math.nan, "y": 1, "r": 5}),
    ("circle", {"x": math.inf, "y": 1, "r": 5}),
    ("circle", {"x": 10 ** 9, "y": 1, "r": 5}),
    ("circle", {"x": 1, "y": 1}),
    ("rect", {"x": 1, "y": 1, "w": 0, "h": 5}),
    ("rect", {"x": 1, "y": 1, "w": 5}),
    ("triangle", {"x": 1, "y": 1}),
])
def test_bad_areas_are_rejected_and_change_nothing(map_id, shape, vals):
    with pytest.raises(ValueError):
        maps_store.add_reveal(map_id, shape, **vals)
    assert maps_store.get_reveal(map_id)["areas"] == [] and maps_store.get_reveal(map_id)["v"] == 0


def test_area_count_is_capped(map_id, monkeypatch):
    monkeypatch.setattr(maps_store, "MAX_REVEAL_AREAS", 3)
    for i in range(3):
        maps_store.add_reveal(map_id, "circle", x=i, y=i, r=5)
    with pytest.raises(ValueError, match="re-fog"):
        maps_store.add_reveal(map_id, "circle", x=9, y=9, r=5)


def test_corrupt_stored_state_is_treated_as_empty(map_id):
    for junk in ("not json", "[]", '{"areas": "nope", "enabled": 1, "v": "x"}'):
        with database.get_cursor(commit=True) as cur:
            cur.execute("UPDATE maps SET reveal_json = ? WHERE id = ?", (junk, map_id))
        try:
            state = maps_store.get_reveal(map_id)
        except ValueError:
            pytest.fail(f"corrupt state {junk!r} should not raise")
        assert state["areas"] == []


def test_grid_px_by_map_type():
    assert generator.grid_px("dungeon") == 16 and generator.grid_px("urban") == 14
    assert generator.grid_px("world") == generator.DEFAULT_GRID_PX


# --------------------------------------------------------------- routes
@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_reveal_api_round_trip(client, map_id):
    assert client.get(f"/api/maps/{map_id}/reveal").json()["enabled"] is False
    assert client.post(f"/api/maps/{map_id}/reveal/enabled", data={"enabled": 1}).json()["enabled"] is True
    res = client.post(f"/api/maps/{map_id}/reveal/add", data={"shape": "circle", "x": 50, "y": 60, "r": 25})
    assert res.status_code == 200 and res.json()["areas"][0]["r"] == 25.0
    res = client.post(f"/api/maps/{map_id}/reveal/add", data={"shape": "rect", "x": 1, "y": 2, "w": 30, "h": 40})
    assert len(res.json()["areas"]) == 2
    assert len(client.post(f"/api/maps/{map_id}/reveal/undo").json()["areas"]) == 1
    assert client.post(f"/api/maps/{map_id}/reveal/clear").json()["areas"] == []


def test_reveal_api_errors(client, map_id):
    bad = client.post(f"/api/maps/{map_id}/reveal/add", data={"shape": "circle", "x": "nope", "y": 1, "r": 5})
    assert bad.status_code == 400 and "error" in bad.json()
    assert client.get("/api/maps/999/reveal").status_code == 404
    assert client.post("/api/maps/999/reveal/undo").status_code == 404
    assert client.post("/api/maps/999/reveal/add", data={"shape": "circle", "x": 1, "y": 1, "r": 5}).status_code == 404


def test_player_page_is_nav_less_and_embeds_current_fog(client, map_id):
    maps_store.set_fog_enabled(map_id, True)
    maps_store.add_reveal(map_id, "circle", x=40, y=40, r=20)
    html = client.get(f"/maps/{map_id}/player").text
    assert "<nav" not in html and "topbar" not in html          # no GM navigation for the players
    assert 'id="player-stage"' in html and "<svg" in html
    assert '"enabled": true' in html or '"enabled":true' in html  # initial fog is in the page: no flash of the full map
    assert "/api/maps/${mapId}/reveal" in html and f"const mapId = {map_id};" in html
    assert client.get("/maps/999/player").status_code == 404


def test_map_title_cannot_break_out_of_the_print_pages_script(client, map_id):
    printed = client.get(f"/maps/{map_id}/print").text
    assert "Crypt <of>" not in printed                      # never raw in markup or script
    assert "Crypt \\u003cof\\u003e `Doom`" in printed        # tojson-escaped inside the script


def test_print_page_uses_the_generators_grid_size(client, db):
    dungeon = maps_store.save_map("rifts", "dungeon", "fantasy", "D", SVG)
    world = maps_store.save_map("rifts", "world", "fantasy", "W", SVG)
    assert 'id="pxsq" value="16"' in client.get(f"/maps/{dungeon}/print").text
    page = client.get(f"/maps/{world}/print").text
    assert f'id="pxsq" value="{generator.DEFAULT_GRID_PX}"' in page and "no grid of its own" in page
    assert client.get("/maps/999/print").status_code == 404


def test_gm_page_has_fog_controls_and_links(client, map_id):
    html = client.get(f"/maps/{map_id}").text
    assert 'id="fog-enabled"' in html and f"/maps/{map_id}/player" in html and f"/maps/{map_id}/print" in html
    assert "fog-el" in html  # the editor strips the preview before saving/exporting
