"""Route-level smoke tests: every page renders, and the main JSON flows work
end to end through FastAPI's TestClient (with the temp DB / games.json the
shared fixtures provide). These were added before main.py was split into
routers, specifically so that refactor had something to prove it didn't
change behavior - and they stay as the "did I break a page?" safety net the
README used to say was missing."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db, games_config):
    from app.main import app

    # Using the context manager runs the app's startup/lifespan hook too.
    with TestClient(app) as c:
        yield c


PAGES = [
    "/",
    "/settings",
    "/games/manage",
    "/library",
    "/library?game=rifts",
    "/library?game=rifts&q=dragon",
    "/bookmarks",
    "/campaigns",
    "/campaigns?game=rifts",
    "/maps",
    "/maps?game=rifts",
    "/tools/dice",
    "/tools/npc",
    "/tools/npc?game=rifts",
    "/tools/initiative",
    "/tools/initiative?game=rifts",
    "/tools/encounters",
    "/tools/oneshot",
    "/screen",
    "/screen?game=rifts",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_renders(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    assert "GM Helper" in resp.text


def test_unknown_things_404(client):
    assert client.get("/campaigns/9999").status_code == 404
    assert client.get("/maps/9999").status_code == 404
    assert client.get("/books/9999/file").status_code == 404


def test_dice_roll(client):
    r = client.post("/api/dice/roll", data={"expr": "2d6+3"})
    assert r.status_code == 200
    assert 5 <= r.json()["grand_total"] <= 15
    assert client.post("/api/dice/roll", data={"expr": "nonsense"}).status_code == 400


def test_npc_generate(client):
    r = client.post("/api/npc/generate", data={"game": "rifts"})
    assert r.status_code == 200
    assert r.json()["name"]


def test_encounter_and_loot(client):
    envs = client.get("/tools/encounters")
    assert envs.status_code == 200
    r = client.post("/api/encounters/generate", data={"environment": "Dungeon"})
    assert r.status_code == 200
    assert client.post("/api/encounters/generate", data={"environment": "Nope"}).status_code == 400
    assert client.post("/api/loot/generate", data={"tier": "mid"}).status_code == 200
    assert client.post("/api/loot/generate", data={"tier": "bogus"}).status_code == 400


def test_ai_routes_refuse_without_a_verified_connection(client):
    assert client.post("/api/oneshot/generate", data={"game": "rifts"}).status_code == 400
    assert client.post("/api/screen/recap", data={"game": "rifts"}).status_code == 400
    assert client.post("/api/npc/dialogue", data={"name": "Bob"}).status_code == 400


def test_theme_roundtrip(client):
    assert client.post("/api/settings/theme", data={"name": "parchment"}).status_code == 200
    assert 'data-theme="parchment"' in client.get("/").text
    assert client.post("/api/settings/theme", data={"name": "no-such-theme"}).status_code == 400


def test_campaign_lifecycle(client):
    r = client.post("/api/campaigns", data={"game_key": "rifts", "title": "Smoke Test"})
    assert r.status_code == 200
    cid = r.json()["id"]
    page = client.get(f"/campaigns/{cid}")
    assert page.status_code == 200 and "Smoke Test" in page.text

    assert client.post(f"/api/campaigns/{cid}/update", data={"field": "pitch", "value": "hi"}).status_code == 200
    assert client.post(f"/api/campaigns/{cid}/update", data={"field": "bogus", "value": "x"}).status_code == 400

    t = client.post(f"/api/campaigns/{cid}/threads", data={"title": "A thread"}).json()["id"]
    n = client.post(f"/api/campaigns/{cid}/npcs", data={"name": "Gruff"}).json()["id"]
    f = client.post(f"/api/campaigns/{cid}/factions", data={"name": "The Guild"}).json()["id"]
    loc = client.post(f"/api/campaigns/{cid}/locations", data={"name": "Town"}).json()["id"]
    s = client.post(f"/api/campaigns/{cid}/sessions", data={}).json()["id"]
    assert client.get(f"/campaigns/{cid}").status_code == 200

    for url in (
        f"/api/campaigns/threads/{t}/delete",
        f"/api/campaigns/npcs/{n}/delete",
        f"/api/campaigns/factions/{f}/delete",
        f"/api/campaigns/locations/{loc}/delete",
        f"/api/campaigns/sessions/{s}/delete",
        f"/api/campaigns/{cid}/delete",
    ):
        assert client.post(url).status_code == 200
    assert client.get(f"/campaigns/{cid}").status_code == 404


def test_campaign_rejects_unknown_game(client):
    r = client.post("/api/campaigns", data={"game_key": "no_such_game", "title": "X"})
    assert r.status_code == 400


def test_map_generate_save_and_view(client):
    gen = client.post("/api/maps/generate", data={"map_type": "dungeon", "genre": "fantasy", "size": "small"})
    assert gen.status_code == 200
    body = gen.json()
    assert body["svg"].startswith("<svg")
    saved = client.post("/api/maps", data={
        "game_key": "rifts", "map_type": "dungeon", "genre": "fantasy",
        "title": "Smoke dungeon", "svg": body["svg"], "seed": body["seed"],
    })
    assert saved.status_code == 200
    mid = saved.json()["id"]
    page = client.get(f"/maps/{mid}")
    assert page.status_code == 200 and "Smoke dungeon" in page.text
    assert client.post(f"/api/maps/{mid}/rename", data={"title": "Renamed"}).status_code == 200
    assert client.post(f"/api/maps/{mid}/revert").status_code == 200
    assert client.post(f"/api/maps/{mid}/delete").status_code == 200
    assert client.post("/api/maps/generate", data={"map_type": "bogus", "genre": "fantasy"}).status_code == 400


def test_gm_screen_cards_and_notes(client):
    page = client.get("/screen?game=rifts")
    assert page.status_code == 200
    card = client.post("/api/screen/cards", data={"game": "rifts", "title": "House rule", "body": "x"}).json()
    assert client.post(f"/api/screen/cards/{card['id']}", data={"field": "body", "value": "y"}).status_code == 200
    assert client.post(f"/api/screen/cards/{card['id']}/move", data={"direction": "up"}).status_code == 200
    assert client.post("/api/screen/notes", data={"game": "rifts", "body": "notes!"}).status_code == 200
    assert "notes!" in client.get("/screen?game=rifts").text
    assert client.post(f"/api/screen/cards/{card['id']}/delete").status_code == 200


def test_initiative_flow(client):
    add = client.post("/api/initiative/add", data={"game": "rifts", "name": "Orc", "initiative": 12, "hp": 15})
    assert add.status_code == 200
    state = client.get("/api/initiative?game=rifts").json()
    assert [c["name"] for c in state["combatants"]] == ["Orc"]
    cid = state["combatants"][0]["id"]
    assert client.post("/api/initiative/hp", data={"game": "rifts", "combatant_id": cid, "delta": -3}).status_code == 200
    assert client.post("/api/initiative/next", data={"game": "rifts"}).status_code == 200
    assert client.post("/api/initiative/remove", data={"game": "rifts", "combatant_id": cid}).status_code == 200
    assert client.post("/api/initiative/reset", data={"game": "rifts"}).status_code == 200


def test_bookmarks_flow(client, a_book):
    c = client.post("/api/bookmarks/collections", data={"name": "Prep"})
    assert c.status_code == 200
    cid = c.json()["id"]
    b = client.post("/api/bookmarks/add", data={"book_id": a_book, "page_number": 7, "collection_id": cid})
    assert b.status_code == 200
    assert "Prep" in client.get("/bookmarks").text
    assert client.post(f"/api/bookmarks/{b.json()['id']}/delete").status_code == 200
    assert client.post(f"/api/bookmarks/collections/{cid}/delete").status_code == 200


def test_games_manage_create_and_rename(client):
    r = client.post("/api/games/create", data={"name": "Brand New Game", "system": "generic"})
    assert r.status_code == 200
    key = r.json()["key"]
    assert client.post("/api/games/rename", data={"key": key, "name": "Renamed Game"}).status_code == 200
    assert "Renamed Game" in client.get("/games/manage").text


def test_scan_without_library_root_is_a_clean_400(client):
    assert client.post("/api/scan").status_code == 400
    assert client.post("/api/index", data={"mode": "pending"}).status_code == 400


def test_hidden_attribute_always_wins_over_class_display_rules(client):
    """Regression: `.row` / `.map-editor-panel` set display:flex, which beat the
    browser's weak [hidden] rule, so the map editor's toolbar and side panel were
    visible even with Edit map switched off. A global !important rule fixes it."""
    css = client.get("/static/style.css").text
    assert "[hidden] { display: none !important; }" in css


def test_pages_declare_a_favicon_so_browsers_dont_404_for_one(client):
    assert 'rel="icon"' in client.get("/").text
    assert 'rel="icon"' in client.get("/tools/dice").text
