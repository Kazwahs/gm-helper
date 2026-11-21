"""Tests for linked maps (app/map_links.py): drilling from a settlement to its
city, from a dungeon level to the next, and the API and card for it."""
import pytest
from fastapi.testclient import TestClient

from app import campaigns, database, map_links, maps_store
from app.maps import generate_map


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _save(map_type="dungeon", seed=3, genre="fantasy", style="ink", size="small", title=None, game="rifts", campaign_id=None):
    r = generate_map(map_type, genre, {"size": size, "style": style}, seed)
    return maps_store.save_map(game, map_type, genre, title or f"{map_type} {seed}", r["svg"], seed=seed,
                               params=r["params"], campaign_id=campaign_id)


def _map(map_id):
    return maps_store.get_map(map_id)


# ------------------------------------------------------------ settlements ---
@pytest.mark.parametrize("style", ["ink", "classic"])
@pytest.mark.parametrize("map_type", ["world", "wilderness"])
def test_settlements_are_named_on_the_map(style, map_type):
    r = generate_map(map_type, "fantasy", {"size": "medium", "style": style}, 5)
    found = map_links.settlements(r["svg"])
    assert found
    assert len({s["name"].lower() for s in found}) == len(found)
    assert {s["tier"] for s in found} <= {"city", "town", "village"}
    assert any(s["tier"] == "city" for s in found)


def test_old_maps_without_settlement_names_offer_none():
    assert map_links.settlements('<svg><g class="map-el" data-kind="settlement" data-cx="1" data-cy="2"/></svg>') == []
    assert map_links.settlements("") == []


def test_names_are_unescaped():
    svg = '<svg><g class="map-el" data-kind="settlement" data-name="Rock &amp; Roll" data-tier="town"></g></svg>'
    assert map_links.settlements(svg) == [{"name": "Rock & Roll", "tier": "town"}]


@pytest.mark.parametrize("style", ["ink", "classic"])
def test_a_city_can_be_given_a_name(style):
    r = generate_map("urban", "fantasy", {"size": "small", "style": style, "name": "Ironhaven"}, 2)
    assert r["title_suggestion"].startswith("Ironhaven - City Districts")
    plain = generate_map("urban", "fantasy", {"size": "small", "style": style}, 2)
    assert plain["title_suggestion"].startswith("City Districts")


def test_editing_a_settlement_label_keeps_its_name(db):
    from pathlib import Path
    page = Path("app/templates/map_detail.html").read_text(encoding="utf-8")
    assert "'settlement'" in page[page.index("function syncRoomFacts"):page.index("function renderPanel")]


# --------------------------------------------------------------- linking ---
def test_link_and_list_from_both_sides(db):
    a, b = _save(seed=1), _save(seed=2)
    map_links.link(a, b, "below")
    out = map_links.list_links(a)
    assert [(l["map_id"], l["role"], l["kind"]) for l in out["out"]] == [(b, "Level below", "below")]
    assert out["in"] == []
    back = map_links.list_links(b)
    assert [(l["map_id"], l["role"]) for l in back["in"]] == [(a, "Level above")]
    assert back["in"][0]["title"] == _map(a)["title"]


def test_the_roles_read_correctly_for_every_kind(db):
    a, b = _save(seed=1), _save(seed=2)
    for kind, here, there in (("detail", "Detail map", "Part of"), ("below", "Level below", "Level above"),
                              ("above", "Level above", "Level below")):
        map_links.link(a, b, kind)
        assert map_links.list_links(a)["out"][0]["role"] == here
        assert map_links.list_links(b)["in"][0]["role"] == there


def test_linking_a_pair_again_updates_it_rather_than_duplicating(db):
    a, b = _save(seed=1), _save(seed=2)
    first = map_links.link(a, b, "detail", "old")
    second = map_links.link(a, b, "below", "new")
    assert first == second
    (only,) = map_links.list_links(a)["out"]
    assert (only["kind"], only["label"]) == ("below", "new")


def test_linking_the_other_way_round_replaces_the_link(db):
    a, b = _save(seed=1), _save(seed=2)
    map_links.link(a, b, "below")
    map_links.link(b, a, "detail")
    assert map_links.list_links(a)["out"] == []
    assert len(map_links.list_links(b)["out"]) == 1


def test_bad_links_are_refused(db, games_config):
    a, b = _save(seed=1), _save(seed=2)
    other_game = _save(seed=3, game="dnd5e")
    for call in (lambda: map_links.link(a, a), lambda: map_links.link(a, b, "sideways"),
                 lambda: map_links.link(a, other_game), lambda: map_links.link(a, 9999)):
        with pytest.raises(map_links.LinkError):
            call()
    assert map_links.list_links(a) == {"out": [], "in": []}


def test_unlink_and_deleting_a_map_remove_the_link(db):
    a, b, c = _save(seed=1), _save(seed=2), _save(seed=3)
    link_id = map_links.link(a, b, "below")
    map_links.link(a, c, "detail")
    map_links.unlink(b, link_id)  # from the other side: still removes it
    assert [l["map_id"] for l in map_links.list_links(a)["out"]] == [c]
    map_links.unlink(b, 99999)  # unknown ids do nothing
    maps_store.delete_map(c)
    assert map_links.list_links(a) == {"out": [], "in": []}


def test_unlink_only_touches_links_of_that_map(db):
    a, b, c = _save(seed=1), _save(seed=2), _save(seed=3)
    link_id = map_links.link(a, b)
    map_links.unlink(c, link_id)
    assert len(map_links.list_links(a)["out"]) == 1


# --------------------------------------------------------- making new maps ---
def test_levels_get_numbered_titles_and_inherit_style_and_campaign(db, games_config):
    campaign_id = campaigns.create_campaign("rifts", "Crypt campaign")
    top = _save(seed=1, title="Crypt", campaign_id=campaign_id)
    second = map_links.create_linked(_map(top), "below", "dungeon")
    assert _map(second)["title"] == "Crypt - Level 2"
    assert (_map(second)["genre"], _map(second)["game_key"], _map(second)["campaign_id"]) == ("fantasy", "rifts", campaign_id)
    assert _map(second)["params"]["style"] == "ink" and _map(second)["params"]["size"] == "small"
    third = map_links.create_linked(_map(second), "below", "dungeon")
    assert _map(third)["title"] == "Crypt - Level 3"
    assert _map(map_links.create_linked(_map(third), "above", "dungeon"))["title"] == "Crypt - Level 2"
    upper = map_links.create_linked(_map(top), "above", "dungeon")
    assert _map(upper)["title"] == "Crypt - Upper level"
    assert [(l["map_id"], l["kind"]) for l in map_links.list_links(top)["out"]] == [(second, "below"), (upper, "above")]
    assert map_links.list_links(second)["in"][0]["role"] == "Level above"


def test_a_new_level_is_a_different_map_from_its_neighbour(db):
    top = _save(seed=1)
    second = map_links.create_linked(_map(top), "below", "dungeon")
    assert _map(second)["svg"] != _map(top)["svg"]
    assert _map(second)["seed"] != _map(top)["seed"]


def test_a_settlement_becomes_a_city_sized_by_its_tier(db):
    world = _save("world", seed=4, size="medium")
    found = map_links.settlements(_map(world)["svg"])
    city = next(s for s in found if s["tier"] == "city")
    village = next((s for s in found if s["tier"] == "village"), None)
    new_id = map_links.create_linked(_map(world), "detail", "urban", settlement=city["name"], tier="city")
    made = _map(new_id)
    assert made["map_type"] == "urban" and made["title"].startswith(city["name"])
    assert made["params"]["size"] == "large" and made["params"]["name"] == city["name"]
    assert "hex_grid" not in made["params"]
    (link,) = map_links.list_links(world)["out"]
    assert (link["label"], link["kind"], link["map_id"]) == (city["name"], "detail", new_id)
    if village:
        small = map_links.create_linked(_map(world), "detail", "urban", settlement=village["name"], tier="village")
        assert _map(small)["params"]["size"] == "small"


def test_a_building_can_be_made_inside_a_city_and_a_custom_title_is_kept(db):
    city = _save("urban", seed=2)
    building = map_links.create_linked(_map(city), "detail", "interior", title="The Guildhall")
    assert _map(building)["title"] == "The Guildhall" and _map(building)["map_type"] == "interior"
    untitled = map_links.create_linked(_map(city), "detail", "interior")
    assert _map(untitled)["title"].startswith(_map(city)["title"] + " - ")


def test_a_bad_request_makes_no_map(db):
    top = _save(seed=1)
    before = len(maps_store.list_maps())
    for call in (lambda: map_links.create_linked(_map(top), "sideways", "dungeon"),
                 lambda: map_links.create_linked(_map(top), "below", "spaceship")):
        with pytest.raises(map_links.LinkError):
            call()
    assert len(maps_store.list_maps()) == before


def test_stairs_hints_come_from_the_rooms(db):
    for seed in range(1, 12):
        m = _map(_save(seed=seed, size="medium"))
        hint = map_links.stairs_hint(m)
        if hint["down"] or hint["up"]:
            assert all(isinstance(n, int) for n in hint["down"] + hint["up"])
            return
    pytest.fail("no map with stairs in 11 seeds")


def test_payload_shape(db):
    a, b = _save("dungeon", seed=1), _save("world", seed=4)
    p = map_links.payload(_map(a))
    assert set(p) == {"links", "settlements", "stairs", "others", "map_type"}
    assert p["settlements"] == [] and [o["id"] for o in p["others"]] == [b]
    assert map_links.payload(_map(b))["settlements"]
    assert map_links.payload(_map(b))["stairs"] == {"down": [], "up": []}


# ------------------------------------------------------------------- API ---
def test_api_create_a_level_and_a_city_and_unlink(client):
    top = _save(seed=1, title="Crypt")
    made = client.post(f"/api/maps/{top}/links/create", data={"kind": "below", "map_type": "dungeon"}).json()
    assert made["ok"] and made["url"] == f"/maps/{made['id']}"
    assert made["links"]["out"][0]["title"] == "Crypt - Level 2"
    assert client.get(made["url"]).status_code == 200

    world = _save("world", seed=4)
    name = map_links.settlements(_map(world)["svg"])[0]["name"]
    city = client.post(f"/api/maps/{world}/links/create",
                       data={"kind": "detail", "map_type": "urban", "settlement": name}).json()
    assert city["ok"] and city["links"]["out"][0]["label"] == name

    gone = client.post(f"/api/maps/{world}/links/{city['links']['out'][0]['id']}/delete").json()
    assert gone["links"]["out"] == []


def test_api_rejects_places_not_on_the_map_and_bad_types(client):
    world = _save("world", seed=4)
    res = client.post(f"/api/maps/{world}/links/create", data={"kind": "detail", "map_type": "urban", "settlement": "Nowhere"})
    assert res.status_code == 400 and "isn't on this map" in res.json()["error"]
    res = client.post(f"/api/maps/{world}/links/create", data={"kind": "detail", "map_type": "spaceship"})
    assert res.status_code == 400 and not res.json()["ok"]
    assert client.post("/api/maps/999/links/create", data={"kind": "detail", "map_type": "urban"}).status_code == 404
    assert client.get("/api/maps/999/links").status_code == 404


def test_api_link_existing_maps(client, games_config):
    a, b = _save(seed=1), _save(seed=2)
    res = client.post(f"/api/maps/{a}/links/add", data={"other_id": b, "kind": "detail", "label": "the annex"}).json()
    assert res["links"]["out"][0]["label"] == "the annex"
    assert client.post(f"/api/maps/{a}/links/add", data={"other_id": a}).status_code == 400
    other_game = _save(seed=3, game="dnd5e")
    assert client.post(f"/api/maps/{a}/links/add", data={"other_id": other_game}).status_code == 400
    assert client.post(f"/api/maps/{a}/links/add", data={"other_id": 9999}).status_code == 404
    got = client.get(f"/api/maps/{b}/links").json()
    assert got["links"]["in"][0]["map_id"] == a


def test_the_card_and_script_ship(client):
    page = client.get(f"/maps/{_save()}").text
    assert 'id="links-card"' in page and "map_links.js" in page
    assert client.get("/static/map_links.js").status_code == 200
