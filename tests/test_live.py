"""Handouts and tokens on a map's player view: storage, the live poll, the GM
endpoints, uploads, and the pages that carry them."""
import base64
import re

import pytest
from fastapi.testclient import TestClient

from app import database, handouts, live, map_tokens, maps_store

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600">'
       '<rect width="800" height="600"/></svg>')
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 30


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def map_id(db, games_config):
    return maps_store.save_map("rifts", "dungeon", "fantasy", "Crypt", SVG)


def _live_v(map_id):
    return live.live_state(map_id)["v"]


# ---------------------------------------------------------------- handouts: storage

@pytest.mark.parametrize("data, mime", [
    (PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp"),
])
def test_sniff_accepts_the_four_image_types(data, mime):
    assert handouts.sniff_image(data) == mime


@pytest.mark.parametrize("data", [
    b"", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"<html></html>",
    b"MZ\x90\x00", b"RIFF\x00\x00\x00\x00WAVEfmt ",
])
def test_sniff_rejects_everything_else(data):
    assert handouts.sniff_image(data) is None


def test_text_handout_lifecycle(db, games_config):
    hid = handouts.create_text("rifts", "  The   letter ", "Dear friend,\r\nCome at once.")
    item = handouts.get_handout(hid)
    assert item["title"] == "The letter" and item["body"] == "Dear friend,\nCome at once." and item["kind"] == "text"
    handouts.update_handout(hid, "title", "A letter")
    handouts.update_handout(hid, "body", "New text")
    assert handouts.get_handout(hid)["title"] == "A letter" and handouts.get_handout(hid)["body"] == "New text"
    assert [h["id"] for h in handouts.list_handouts("rifts")] == [hid]
    assert handouts.list_handouts("dnd5e") == []
    handouts.delete_handout(hid)
    assert handouts.get_handout(hid) is None


def test_handout_validation(db, games_config):
    with pytest.raises(ValueError, match="title"):
        handouts.create_text("rifts", " ", "text")
    with pytest.raises(ValueError, match="text"):
        handouts.create_text("rifts", "T", "  ")
    with pytest.raises(ValueError, match="too long"):
        handouts.create_text("rifts", "T", "x" * (handouts.MAX_BODY + 1))
    hid = handouts.create_text("rifts", "T", "body")
    for field, value in (("title", ""), ("body", ""), ("kind", "image"), ("game_key", "x")):
        with pytest.raises(ValueError):
            handouts.update_handout(hid, field, value)
    with pytest.raises(ValueError):
        handouts.update_handout(99999, "title", "x")


def test_image_handout_storage_and_limits(db, games_config):
    hid = handouts.create_image("rifts", "Map scrap", PNG)
    item = handouts.get_handout(hid)
    assert item["kind"] == "image" and item["image_url"] == f"/handouts/{hid}/image" and item["body"] == ""
    assert handouts.get_image(hid) == (PNG, "image/png")
    with pytest.raises(ValueError, match="doesn't look like"):
        handouts.create_image("rifts", "Bad", b"<svg onload=alert(1)>")
    with pytest.raises(ValueError, match="too big"):
        handouts.create_image("rifts", "Huge", PNG + b"\x00" * handouts.MAX_IMAGE_BYTES)
    with pytest.raises(ValueError, match="Choose an image"):
        handouts.create_image("rifts", "Empty", b"")
    with pytest.raises(ValueError):
        handouts.update_handout(hid, "body", "text on an image")


def test_a_game_can_only_hold_so_many_handouts(db, games_config, monkeypatch):
    monkeypatch.setattr(handouts, "MAX_PER_GAME", 3)
    for i in range(3):
        handouts.create_text("rifts", f"H{i}", "x")
    with pytest.raises(ValueError, match="most handouts"):
        handouts.create_text("rifts", "One more", "x")
    handouts.create_text("dnd5e", "Another game is fine", "x")


# ---------------------------------------------------------------- handouts on a map

def test_show_and_hide_on_the_player_view(map_id):
    hid = handouts.create_text("rifts", "Letter", "Hello")
    v0 = _live_v(map_id)
    assert live.live_state(map_id)["handout"] is None
    handouts.show_on_map(map_id, hid)
    shown = live.live_state(map_id)["handout"]
    assert shown == {"id": hid, "game_key": "rifts", "title": "Letter", "kind": "text", "body": "Hello", "image_url": None}
    assert _live_v(map_id) != v0
    assert handouts.list_handouts("rifts")[0]["shown_on"] == [map_id]
    v1 = _live_v(map_id)
    handouts.hide_on_map(map_id)
    assert live.live_state(map_id)["handout"] is None and _live_v(map_id) != v1


def test_editing_the_shown_handout_reaches_the_players(map_id):
    hid = handouts.create_text("rifts", "Letter", "Hello")
    handouts.show_on_map(map_id, hid)
    v = _live_v(map_id)
    handouts.update_handout(hid, "body", "Hello again")
    assert live.live_state(map_id)["handout"]["body"] == "Hello again" and _live_v(map_id) != v


def test_deleting_a_shown_handout_takes_it_down(map_id):
    hid = handouts.create_text("rifts", "Letter", "Hello")
    handouts.show_on_map(map_id, hid)
    v = _live_v(map_id)
    handouts.delete_handout(hid)
    assert live.live_state(map_id)["handout"] is None and _live_v(map_id) != v


def test_show_refuses_other_games_and_unknowns(map_id):
    other = handouts.create_text("dnd5e", "Not for this map", "x")
    with pytest.raises(ValueError):
        handouts.show_on_map(map_id, other)
    with pytest.raises(ValueError):
        handouts.show_on_map(map_id, 99999)
    with pytest.raises(ValueError, match="Unknown map"):
        handouts.show_on_map(99999, handouts.create_text("rifts", "T", "x"))
    with pytest.raises(ValueError, match="Unknown map"):
        handouts.hide_on_map(99999)


# ---------------------------------------------------------------- tokens

def test_token_lifecycle_and_normalising(map_id):
    tid = map_tokens.add_token(map_id, "  Aria  ", "100.46", 50, "#AABBCC", 2)
    t = map_tokens.get_token(tid)
    assert t == {"id": tid, "label": "Aria", "x": 100.5, "y": 50.0, "color": "#aabbcc", "size": 2, "hidden": False}
    map_tokens.move_token(tid, 200, 300)
    map_tokens.update_token(tid, "label", "Aria the Bold")
    map_tokens.update_token(tid, "color", "#112233")
    map_tokens.update_token(tid, "size", "3")
    map_tokens.update_token(tid, "hidden", "1")
    t = map_tokens.get_token(tid)
    assert (t["x"], t["y"], t["label"], t["color"], t["size"], t["hidden"]) == (200.0, 300.0, "Aria the Bold", "#112233", 3, True)
    map_tokens.delete_token(tid)
    assert map_tokens.list_tokens(map_id) == []
    map_tokens.delete_token(tid)       # deleting twice is harmless


@pytest.mark.parametrize("kwargs, message", [
    ({"label": "  "}, "name"),
    ({"color": "red"}, "colour"),
    ({"color": "#12345"}, "colour"),
    ({"size": 0}, "Size"),
    ({"size": 9}, "Size"),
    ({"size": "big"}, "Size"),
    ({"x": "abc"}, "number"),
    ({"x": float("inf")}, "range"),
    ({"y": 10 ** 9}, "range"),
])
def test_token_validation(map_id, kwargs, message):
    args = {"label": "Aria", "x": 10, "y": 10, "color": "#c0863f", "size": 1}
    args.update(kwargs)
    with pytest.raises(ValueError, match=message):
        map_tokens.add_token(map_id, **args)
    assert map_tokens.list_tokens(map_id) == []


def test_token_label_is_clamped_and_blank_color_defaults(map_id):
    tid = map_tokens.add_token(map_id, "x" * 100, 1, 1, "")
    t = map_tokens.get_token(tid)
    assert len(t["label"]) == map_tokens.MAX_LABEL and t["color"] == map_tokens.DEFAULT_COLOR


def test_token_updates_refuse_unknown_fields_and_tokens(map_id):
    tid = map_tokens.add_token(map_id, "Aria", 1, 1)
    for field, value in (("map_id", "2"), ("x", "5"), ("label", " "), ("color", "nope")):
        with pytest.raises(ValueError):
            map_tokens.update_token(tid, field, value)
    with pytest.raises(ValueError):
        map_tokens.update_token(99999, "label", "x")
    with pytest.raises(ValueError):
        map_tokens.move_token(99999, 1, 1)
    with pytest.raises(ValueError, match="Unknown map"):
        map_tokens.add_token(99999, "Aria", 1, 1)


def test_a_map_can_only_hold_so_many_tokens(map_id, monkeypatch):
    monkeypatch.setattr(map_tokens, "MAX_TOKENS", 3)
    for i in range(3):
        map_tokens.add_token(map_id, f"T{i}", i, i)
    with pytest.raises(ValueError, match="most tokens"):
        map_tokens.add_token(map_id, "One more", 1, 1)


def test_clear_tokens_and_map_deletion_cascade(map_id):
    for i in range(3):
        map_tokens.add_token(map_id, f"T{i}", i, i)
    map_tokens.clear_tokens(map_id)
    assert map_tokens.list_tokens(map_id) == []
    map_tokens.add_token(map_id, "Again", 1, 1)
    maps_store.delete_map(map_id)
    with database.get_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM map_tokens").fetchone()[0] == 0
    with pytest.raises(ValueError, match="Unknown map"):
        map_tokens.clear_tokens(map_id)


# ---------------------------------------------------------------- the live poll

def test_live_state_shape_and_unknown_map(map_id):
    state = live.live_state(map_id)
    assert set(state) == {"v", "reveal", "tokens", "token_radius", "handout"}
    assert state["tokens"] == [] and state["handout"] is None and state["reveal"]["enabled"] is False
    assert live.live_state(99999) is None


def test_hidden_tokens_never_reach_the_player_view(map_id):
    map_tokens.add_token(map_id, "Visible", 10, 10)
    map_tokens.add_token(map_id, "Ambush", 20, 20, hidden=True)
    names = [t["label"] for t in live.live_state(map_id)["tokens"]]
    assert names == ["Visible"]
    assert [t["label"] for t in map_tokens.list_tokens(map_id)] == ["Visible", "Ambush"]     # the GM still has both
    assert "hidden" not in live.live_state(map_id)["tokens"][0]


def test_every_kind_of_change_changes_the_version(map_id):
    seen = {_live_v(map_id)}

    def changed(action):
        action()
        v = _live_v(map_id)
        assert v not in seen
        seen.add(v)

    tid = []
    changed(lambda: tid.append(map_tokens.add_token(map_id, "Aria", 10, 10)))
    changed(lambda: map_tokens.move_token(tid[0], 30, 30))
    changed(lambda: map_tokens.update_token(tid[0], "color", "#ff0000"))
    changed(lambda: maps_store.set_fog_enabled(map_id, True))
    changed(lambda: maps_store.add_reveal(map_id, "circle", x=1, y=1, r=5))
    changed(lambda: map_tokens.delete_token(tid[0]))


def test_token_radius_follows_the_grid_or_the_map_size(db, games_config):
    dungeon = maps_store.get_map(maps_store.save_map("rifts", "dungeon", "fantasy", "D", SVG))
    world = maps_store.get_map(maps_store.save_map("rifts", "world", "fantasy", "W", SVG))
    assert live.token_radius(dungeon) == round(16 * 0.42, 1)
    assert live.token_radius(world) == round(800 / 75, 1)


@pytest.mark.parametrize("svg, size", [
    ('<svg viewBox="0 0 640 480">', (640.0, 480.0)),
    ('<svg width="300" height="200">', (300.0, 200.0)),
    ('<svg viewBox="-10,-10,500,400" width="1">', (500.0, 400.0)),
    ("<svg>", (1000.0, 1000.0)),
    ("", (1000.0, 1000.0)),
    ('<svg viewBox="0 0 0 0" width="50" height="60">', (50.0, 60.0)),
])
def test_svg_size(svg, size):
    assert live.svg_size(svg) == size


# ---------------------------------------------------------------- endpoints

def test_live_endpoint(client, map_id):
    res = client.get(f"/api/maps/{map_id}/live")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert res.json()["tokens"] == []
    assert client.get("/api/maps/999/live").status_code == 404


def test_token_endpoints_round_trip(client, map_id):
    made = client.post(f"/api/maps/{map_id}/tokens", data={"label": "Aria", "x": 40, "y": 50, "color": "#ff0000", "size": 2})
    assert made.status_code == 200
    token = made.json()["token"]
    assert (token["label"], token["x"], token["size"], token["hidden"]) == ("Aria", 40.0, 2, False)

    assert client.post(f"/api/map-tokens/{token['id']}/move", data={"x": 70, "y": 80}).json()["token"]["x"] == 70.0
    upd = client.post(f"/api/map-tokens/{token['id']}/update", data={"field": "hidden", "value": "1"})
    assert upd.json()["token"]["hidden"] is True
    assert client.get(f"/api/maps/{map_id}/tokens").json()["tokens"][0]["hidden"] is True
    assert client.get(f"/api/maps/{map_id}/live").json()["tokens"] == []        # hidden: players get nothing

    client.post(f"/api/map-tokens/{token['id']}/update", data={"field": "hidden", "value": "0"})
    assert len(client.get(f"/api/maps/{map_id}/live").json()["tokens"]) == 1
    assert client.post(f"/api/map-tokens/{token['id']}/delete").json() == {"ok": True}
    client.post(f"/api/maps/{map_id}/tokens", data={"label": "A", "x": 1, "y": 1})
    assert client.post(f"/api/maps/{map_id}/tokens/clear").json() == {"ok": True}
    assert client.get(f"/api/maps/{map_id}/tokens").json()["tokens"] == []


def test_token_endpoint_errors(client, map_id):
    assert client.post(f"/api/maps/{map_id}/tokens", data={"label": "", "x": 1, "y": 1}).status_code == 400
    assert client.post(f"/api/maps/{map_id}/tokens", data={"label": "A", "x": "nope", "y": 1}).status_code == 400
    assert client.post("/api/maps/999/tokens", data={"label": "A", "x": 1, "y": 1}).status_code == 404
    assert client.post("/api/maps/999/tokens/clear").status_code == 404
    assert client.post("/api/map-tokens/999/move", data={"x": 1, "y": 1}).status_code == 400
    assert client.post("/api/map-tokens/999/update", data={"field": "label", "value": "x"}).status_code == 400


def test_handout_endpoints_round_trip(client, map_id):
    made = client.post("/api/handouts", data={"game": "rifts", "title": "Letter", "body": "Come at once."})
    assert made.status_code == 200
    hid = made.json()["handout"]["id"]
    assert [h["id"] for h in client.get("/api/handouts", params={"game": "rifts"}).json()["handouts"]] == [hid]
    assert client.post(f"/api/handouts/{hid}/update", data={"field": "title", "value": "A letter"}).json()["handout"]["title"] == "A letter"

    shown = client.post(f"/api/maps/{map_id}/handout/show", data={"handout_id": hid}).json()["handout"]
    assert shown["title"] == "A letter"
    assert client.get(f"/api/maps/{map_id}/live").json()["handout"]["body"] == "Come at once."
    assert client.post(f"/api/maps/{map_id}/handout/hide").json() == {"handout": None}
    assert client.get(f"/api/maps/{map_id}/live").json()["handout"] is None
    assert client.post(f"/api/handouts/{hid}/delete").json() == {"ok": True}
    assert client.get("/api/handouts", params={"game": "rifts"}).json()["handouts"] == []


def test_handout_endpoint_errors(client, map_id):
    assert client.post("/api/handouts", data={"game": "rifts", "title": "", "body": "x"}).status_code == 400
    assert client.post("/api/handouts/999/update", data={"field": "title", "value": "x"}).status_code == 400
    assert client.post("/api/maps/999/handout/show", data={"handout_id": 1}).status_code == 404
    assert client.post(f"/api/maps/{map_id}/handout/show", data={"handout_id": 999}).status_code == 400
    assert client.post("/api/maps/999/handout/hide").status_code == 404


def test_image_upload_and_serving(client, map_id):
    up = client.post("/api/handouts/image", data={"game": "rifts", "title": ""},
                     files={"file": ("old map.png", PNG, "image/png")})
    assert up.status_code == 200
    h = up.json()["handout"]
    assert h["kind"] == "image" and h["title"] == "old map"          # title falls back to the file name
    img = client.get(h["image_url"])
    assert img.status_code == 200 and img.content == PNG
    assert img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in img.headers["content-security-policy"]

    client.post(f"/api/maps/{map_id}/handout/show", data={"handout_id": h["id"]})
    live_h = client.get(f"/api/maps/{map_id}/live").json()["handout"]
    assert live_h["kind"] == "image" and live_h["image_url"] == h["image_url"]
    assert client.get("/handouts/999/image").status_code == 404


def test_image_upload_rejects_non_images_and_oversize(client, map_id):
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    bad = client.post("/api/handouts/image", data={"game": "rifts"}, files={"file": ("x.svg", svg, "image/svg+xml")})
    assert bad.status_code == 400 and "PNG" in bad.json()["error"]
    lie = client.post("/api/handouts/image", data={"game": "rifts"}, files={"file": ("x.png", b"not a png", "image/png")})
    assert lie.status_code == 400                                     # the claimed type doesn't matter, the bytes do
    big = client.post("/api/handouts/image", data={"game": "rifts"},
                      files={"file": ("big.png", PNG + b"\x00" * (handouts.MAX_IMAGE_BYTES + 10), "image/png")})
    assert big.status_code == 400 and "too big" in big.json()["error"]
    assert handouts.list_handouts("rifts") == []


# ---------------------------------------------------------------- pages

def test_player_page_carries_live_state_and_the_overlay(client, map_id):
    map_tokens.add_token(map_id, "Aria", 10, 10)
    map_tokens.add_token(map_id, "Secret ambush", 20, 20, hidden=True)
    hid = handouts.create_text("rifts", "Letter", "Dear friend")
    handouts.show_on_map(map_id, hid)
    html = client.get(f"/maps/{map_id}/player").text
    assert 'id="handout"' in html and "drawTokens(svg" in html
    assert "Aria" in html and "Dear friend" in html
    assert "Secret ambush" not in html                                # hidden tokens aren't even in the page source
    assert "<nav" not in html and "topbar" not in html


def test_handout_text_cannot_break_out_of_the_players_script(client, map_id):
    hid = handouts.create_text("rifts", "</script><b>x</b>", "</script><script>alert(1)</script>")
    handouts.show_on_map(map_id, hid)
    html = client.get(f"/maps/{map_id}/player").text
    assert "<script>alert(1)</script>" not in html
    assert len(re.findall(r"</script>", html)) == len(re.findall(r"<script\b", html))


def test_map_page_has_the_gm_controls_and_strips_tokens_when_saving(client, map_id):
    map_tokens.add_token(map_id, "Aria", 10, 10)
    html = client.get(f"/maps/{map_id}").text
    assert 'id="token-card"' in html and 'id="handout-card"' in html and 'id="place-token-btn"' in html
    assert "fog-el, .token-el" in html                                # tokens never end up in the saved or exported SVG
    assert '"label": "Aria"' in html


def test_saving_the_map_never_stores_a_token_layer(client, map_id):
    # the page strips .token-el before posting; the store keeps whatever SVG it is given
    clean = SVG
    assert client.post(f"/api/maps/{map_id}/svg", data={"svg": clean}).status_code == 200
    assert "token" not in maps_store.get_map(map_id)["svg"]


def test_schema_has_the_new_columns(db):
    cols = database._table_columns("maps")
    assert {"shown_handout_id", "live_v"} <= cols
    database.init_db()                                                  # running the migrations again is harmless
