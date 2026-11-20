"""Navigation bar: which link is the current page, the game switcher, the
remembered game, and the cache-busting that keeps a browser from running old
JavaScript next to new HTML (the "menu button does nothing" bug)."""
import os
import re

import pytest
from fastapi.testclient import TestClient

from app import nav as nav_module
from app import web


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _labels(items):
    return [i["label"] for i in items]


def _active(items):
    return [i["label"] for i in items if i["active"]]


# ------------------------------------------------------------- build_nav

def test_no_game_hides_library_and_campaigns(games_config):
    nav = nav_module.build_nav("/", None)
    assert _labels(nav["primary"]) == ["Games", "Maps", "GM Screen"]
    assert nav["game_key"] is None and nav["game_name"] is None
    assert "Initiative" not in _labels(nav["tools"])


def test_game_shows_game_links_and_name(games_config):
    nav = nav_module.build_nav("/library", "rifts")
    assert _labels(nav["primary"]) == ["Games", "Library", "Campaigns", "Maps", "GM Screen"]
    assert nav["game_name"] == "Rifts"
    hrefs = {i["label"]: i["href"] for i in nav["primary"]}
    assert hrefs["Library"] == "/library?game=rifts"
    assert hrefs["Maps"] == "/maps?game=rifts"
    assert _labels(nav["tools"]) == ["Dice", "Random Tables", "Initiative", "NPCs", "Encounters", "One-Shot"]


@pytest.mark.parametrize("path,expected", [
    ("/", ["Games"]),
    ("/library", ["Library"]),
    ("/campaigns", ["Campaigns"]),
    ("/campaigns/7", ["Campaigns"]),
    ("/maps", ["Maps"]),
    ("/maps/3", ["Maps"]),
    ("/screen", ["GM Screen"]),
    ("/settings", []),
    ("/tools/dice", []),
])
def test_current_page_in_main_bar(games_config, path, expected):
    assert _active(nav_module.build_nav(path, "rifts")["primary"]) == expected


def test_a_similar_prefix_is_not_a_match(games_config):
    # /library-old must not light up Library; /mapsfoo must not light up Maps
    assert _active(nav_module.build_nav("/library-old", "rifts")["primary"]) == []
    assert _active(nav_module.build_nav("/mapsfoo", "rifts")["primary"]) == []


def test_tools_and_more_menus_know_when_they_contain_the_current_page(games_config):
    nav = nav_module.build_nav("/tools/tables", "rifts")
    assert nav["tools_active"] and _active(nav["tools"]) == ["Random Tables"]
    assert not nav["more_active"]
    nav = nav_module.build_nav("/settings", None)
    assert nav["more_active"] and _active(nav["more"]) == ["Settings"]
    assert not nav["tools_active"]
    nav = nav_module.build_nav("/games/manage", None)
    assert _active(nav["more"]) == ["Manage Games"]


def test_remembered_game_fills_in_when_the_page_has_none(games_config):
    nav = nav_module.build_nav("/settings", None, "rifts")
    assert nav["game_key"] == "rifts" and nav["game_name"] == "Rifts"
    assert "Library" in _labels(nav["primary"])


def test_page_game_beats_remembered_game(games_config):
    # games_config has dnd5e + rifts
    nav = nav_module.build_nav("/library", "dnd5e", "rifts")
    assert nav["game_key"] == "dnd5e" and nav["game_name"] == "D&D 5E"


def test_unknown_or_hostile_remembered_game_is_ignored(games_config):
    for bad in ("nope", "../../etc/passwd", '"><script>', ""):
        nav = nav_module.build_nav("/settings", None, bad)
        assert nav["game_key"] is None
        assert "Library" not in _labels(nav["primary"])


def test_unknown_page_game_is_ignored_too(games_config):
    assert nav_module.build_nav("/library", "nope")["game_key"] is None


def test_switcher_lists_every_game_and_marks_current(games_config):
    nav = nav_module.build_nav("/library", "rifts")
    keys = [g["key"] for g in nav["games"]]
    assert keys[:2] == ["dnd5e", "rifts"] and keys[-1] == "unsorted"
    assert [g["key"] for g in nav["games"] if g["current"]] == ["rifts"]


@pytest.mark.parametrize("path,target", [
    ("/library", "/library?game=dnd5e"),
    ("/campaigns", "/campaigns?game=dnd5e"),
    ("/campaigns/12", "/campaigns?game=dnd5e"),   # one campaign belongs to the old game
    ("/maps/4", "/maps?game=dnd5e"),
    ("/screen", "/screen?game=dnd5e"),
    ("/tools/initiative", "/tools/initiative?game=dnd5e"),
    ("/tools/tables", "/tools/tables?game=dnd5e"),
    ("/settings", "/library?game=dnd5e"),
    ("/", "/library?game=dnd5e"),
    ("/tools/", "/library?game=dnd5e"),
])
def test_switching_game_lands_on_the_same_kind_of_page(games_config, path, target):
    nav = nav_module.build_nav(path, "rifts")
    assert {g["key"]: g["href"] for g in nav["games"]}["dnd5e"] == target


def test_game_keys_are_url_encoded_in_links(games_config, tmp_path, monkeypatch):
    from app.library import games as games_module

    monkeypatch.setattr(games_module, "get_game", lambda k: {"key": k, "name": "Odd & Ends", "system": "generic"})
    monkeypatch.setattr(games_module, "all_game_choices", lambda: [{"key": "a b&c", "name": "Odd & Ends"}])
    nav = nav_module.build_nav("/library", "a b&c")
    assert nav["games"][0]["href"] == "/library?game=a%20b%26c"
    assert {i["label"]: i["href"] for i in nav["primary"]}["Library"] == "/library?game=a%20b%26c"


# ------------------------------------------------------------- rendered pages

def test_current_page_is_marked_in_the_html(client):
    html = client.get("/library?game=rifts").text
    assert re.search(r'<a href="/library\?game=rifts" aria-current="page">Library</a>', html)
    assert not re.search(r'aria-current="page">Campaigns<', html)


def test_game_name_shown_in_the_nav(client):
    html = client.get("/campaigns?game=rifts").text
    assert '<summary title="Switch game">Rifts</summary>' in html
    assert 'class="current" aria-current="true">Rifts</a>' in html


def test_nav_prompts_to_choose_a_game_when_there_is_none(client):
    html = client.get("/").text
    assert '<summary title="Switch game">Choose a game</summary>' in html


def test_game_names_in_the_switcher_are_escaped(client):
    html = client.get("/library?game=dnd5e").text
    assert "D&amp;D 5E" in html and "D&D 5E</a>" not in html


def test_campaign_detail_page_keeps_its_game_in_the_nav(client):
    r = client.post("/api/campaigns", data={"game_key": "rifts", "title": "T"})
    html = client.get(f"/campaigns/{r.json()['id']}").text
    assert '<summary title="Switch game">Rifts</summary>' in html
    assert re.search(r'aria-current="page">Campaigns<', html)


def test_settings_keeps_library_link_via_the_remembered_game(client):
    client.cookies.set("gm_game", "rifts")
    html = client.get("/settings").text
    assert '<a href="/library?game=rifts">Library</a>' in html
    assert re.search(r'aria-current="page">Settings<', html)


def test_bad_cookie_does_not_break_the_page(client):
    client.cookies.set("gm_game", "not-a-game")
    r = client.get("/settings")
    assert r.status_code == 200 and "Library</a>" not in r.text


def test_body_remembers_game_only_on_pages_with_a_game(client):
    assert '<body data-game="rifts">' in client.get("/library?game=rifts").text
    html = client.get("/settings").text
    assert "data-game=" not in html
    client.cookies.set("gm_game", "rifts")
    assert "data-game=" not in client.get("/settings").text   # cookie isn't re-saved from itself


def test_nav_javascript_handles_escape_and_remembers_the_game(client):
    js = client.get("/static/app.js").text
    assert '"Escape"' in js and "gm_game=" in js and "nav-toggle" in js


# ------------------------------------------------------------- cache busting

def test_pages_load_css_and_js_with_a_version(client):
    for url in ("/", "/library?game=rifts", "/settings"):
        html = client.get(url).text
        assert re.search(r'href="/static/style\.css\?v=[0-9a-f]{10}"', html), url
        assert re.search(r'src="/static/app\.js\?v=[0-9a-f]{10}"', html), url


def test_stand_alone_pages_are_versioned_too(client):
    r = client.post("/api/maps/generate", data={"map_type": "dungeon", "genre": "fantasy", "size": "small"}).json()
    m = client.post("/api/maps", data={"game_key": "rifts", "map_type": "dungeon", "genre": "fantasy",
                                       "title": "T", "svg": r["svg"], "seed": r["seed"]}).json()["id"]
    html = client.get(f"/maps/{m}/player").text
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{10}', html)
    assert re.search(r'/static/style\.css\?v=[0-9a-f]{10}', html)


def test_version_changes_when_the_file_changes(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "x.js").write_text("one")
    monkeypatch.setattr(web.config, "APP_DIR", str(tmp_path))
    web._asset_versions.clear()
    first = web.asset_url("x.js")
    assert first == web.asset_url("x.js")                 # stable while unchanged
    (static / "x.js").write_text("two two")
    os.utime(static / "x.js", (1, 1))                     # even with a "same" mtime the size/content differ
    assert web.asset_url("x.js") != first


def test_missing_asset_does_not_crash_a_page(tmp_path, monkeypatch):
    monkeypatch.setattr(web.config, "APP_DIR", str(tmp_path))
    web._asset_versions.clear()
    assert web.asset_url("nope.js") == "/static/nope.js?v=0"


def test_static_files_must_be_revalidated(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag")
    etag = r.headers["etag"]
    assert client.get("/static/app.js", headers={"If-None-Match": etag}).status_code == 304
