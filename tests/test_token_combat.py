"""Tests for map tokens linked to the initiative tracker (app/token_combat.py):
adding a token per combatant, turn ring and down state, hit points for the GM
only, the player view's version, orphans, and the API."""
import pytest
from fastapi.testclient import TestClient

from app import database, live, map_tokens, maps_store, token_combat
from app.maps import generate_map
from app.tools import initiative as init

GAME = "rifts"


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def map_id(db, games_config):
    r = generate_map("dungeon", "fantasy", {"size": "small", "style": "ink"}, 3)
    return maps_store.save_map(GAME, "dungeon", "fantasy", "Arena", r["svg"], seed=3, params=r["params"])


@pytest.fixture
def party(db, games_config):
    """Aria (PC, first), Bram (PC), and two goblins."""
    init.add_combatant(GAME, "Aria", 20, hp=30, is_pc=True)
    init.add_combatant(GAME, "Bram", 15, hp=40, is_pc=True)
    init.add_combatant(GAME, "Goblin", 10, hp=7, is_pc=False, quantity=2)
    return init.get_state(GAME)


def _by_label(map_id):
    return {t["label"]: t for t in token_combat.decorate(map_id, map_tokens.list_tokens(map_id), GAME)}


# ----------------------------------------------------------- adding tokens ---
def test_everyone_in_the_tracker_gets_a_token_foes_hidden(map_id, party):
    assert token_combat.add_from_initiative(map_id) == {"added": 4, "skipped": 0}
    tokens = _by_label(map_id)
    assert set(tokens) == {"Aria", "Bram", "Goblin 1", "Goblin 2"}
    assert tokens["Aria"]["hidden"] is False and tokens["Aria"]["color"] == token_combat.PC_COLOR
    assert tokens["Goblin 1"]["hidden"] is True and tokens["Goblin 1"]["color"] == token_combat.FOE_COLOR


def test_foes_can_start_visible(map_id, party):
    token_combat.add_from_initiative(map_id, foes_hidden=False)
    assert not any(t["hidden"] for t in _by_label(map_id).values())


def test_adding_twice_never_duplicates_and_picks_up_newcomers(map_id, party):
    token_combat.add_from_initiative(map_id)
    assert token_combat.add_from_initiative(map_id) == {"added": 0, "skipped": 0}
    init.add_combatant(GAME, "Ogre", 5, hp=50)
    assert token_combat.add_from_initiative(map_id)["added"] == 1
    assert len(map_tokens.list_tokens(map_id)) == 5


def test_tokens_are_placed_inside_the_map_without_overlapping(map_id, party):
    token_combat.add_from_initiative(map_id)
    m = maps_store.get_map(map_id)
    width, height = live.svg_size(m["svg"])
    radius = live.token_radius(m)
    tokens = map_tokens.list_tokens(map_id)
    for t in tokens:
        assert 0 < t["x"] < width and 0 < t["y"] < height
    for i, a in enumerate(tokens):
        for b in tokens[i + 1:]:
            assert ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5 > radius * 1.5


def test_a_full_map_reports_what_didnt_fit(map_id, db):
    for batch in range(3):
        init.add_combatant(GAME, f"Rat{batch}", 5, hp=1, quantity=25)
    result = token_combat.add_from_initiative(map_id)
    assert result == {"added": map_tokens.MAX_TOKENS, "skipped": 15}


def test_unknown_map(db):
    with pytest.raises(ValueError):
        token_combat.add_from_initiative(999)
    with pytest.raises(ValueError):
        token_combat.remove_orphans(999)


# ------------------------------------------------------------ what is shown ---
def test_the_gm_sees_hit_points_conditions_and_whose_turn(map_id, party):
    token_combat.add_from_initiative(map_id)
    init.add_condition(GAME, party["combatants"][0]["id"], "Stunned", 2)
    aria = _by_label(map_id)["Aria"]
    assert (aria["hp"], aria["max_hp"], aria["current"], aria["down"]) == (30, 30, True, False)
    assert aria["conditions"] == ["Stunned (2)"]
    assert _by_label(map_id)["Bram"]["current"] is False


def test_players_see_only_the_turn_ring_and_who_is_down(map_id, party):
    token_combat.add_from_initiative(map_id, foes_hidden=False)
    goblin_id = next(c["id"] for c in party["combatants"] if c["name"] == "Goblin 1")
    init.update_hp(GAME, goblin_id, -9)
    shown = live.live_state(map_id)["tokens"]
    by_label = {t["label"]: t for t in shown}
    assert by_label["Aria"]["current"] is True and by_label["Bram"]["current"] is False
    assert by_label["Goblin 1"]["down"] is True and by_label["Goblin 2"]["down"] is False
    for t in shown:
        assert set(t) <= {"id", "label", "x", "y", "color", "size", "current", "down"}


def test_hidden_foes_stay_out_of_the_player_view_even_when_linked(map_id, party):
    token_combat.add_from_initiative(map_id)
    assert {t["label"] for t in live.live_state(map_id)["tokens"]} == {"Aria", "Bram"}


def test_unlinked_tokens_are_untouched(map_id, party):
    tid = map_tokens.add_token(map_id, "Statue", 50, 50)
    token_combat.add_from_initiative(map_id)
    statue = _by_label(map_id)["Statue"]
    assert "current" not in statue and "hp" not in statue
    assert statue == map_tokens.get_token(tid)


def test_the_player_view_version_changes_with_the_turn_and_with_downs(map_id, party):
    token_combat.add_from_initiative(map_id, foes_hidden=False)
    seen = [live.live_state(map_id)["v"]]
    init.next_turn(GAME)
    seen.append(live.live_state(map_id)["v"])
    init.update_hp(GAME, party["combatants"][2]["id"], -100)
    seen.append(live.live_state(map_id)["v"])
    assert len(set(seen)) == 3
    assert live.live_state(map_id)["v"] == seen[-1]          # stable while nothing changes
    init.update_hp(GAME, party["combatants"][0]["id"], -1)    # hit points alone don't move players' screens
    assert live.live_state(map_id)["v"] == seen[-1]


def test_the_version_format_is_unchanged_when_nothing_is_linked(map_id, db):
    map_tokens.add_token(map_id, "Statue", 5, 5)
    assert token_combat.version_part(map_id, GAME) == ""
    assert live.live_state(map_id)["v"].count(".") == 1


# ------------------------------------------------------------------ links ---
def test_link_and_unlink_an_existing_token(map_id, party):
    tid = map_tokens.add_token(map_id, "My hero", 60, 60)
    aria = party["combatants"][0]["id"]
    token_combat.set_link(tid, aria)
    assert _by_label(map_id)["My hero"]["combatant_id"] == aria
    token_combat.set_link(tid, "")
    assert "combatant_id" not in _by_label(map_id)["My hero"]


def test_link_changes_bump_the_player_view(map_id, party):
    tid = map_tokens.add_token(map_id, "My hero", 60, 60)
    before = live.live_state(map_id)["v"]
    token_combat.set_link(tid, party["combatants"][0]["id"])
    assert live.live_state(map_id)["v"] != before


def test_bad_links_are_refused(map_id, party):
    a, b = map_tokens.add_token(map_id, "A", 5, 5), map_tokens.add_token(map_id, "B", 9, 9)
    aria = party["combatants"][0]["id"]
    token_combat.set_link(a, aria)
    for call in (lambda: token_combat.set_link(b, aria), lambda: token_combat.set_link(b, 9999),
                 lambda: token_combat.set_link(b, "abc"), lambda: token_combat.set_link(99999, aria)):
        with pytest.raises(ValueError):
            call()
    token_combat.set_link(a, aria)   # relinking itself is fine


# ----------------------------------------------------------------- orphans ---
def test_tokens_of_removed_combatants_become_orphans_and_can_be_cleared(map_id, party):
    token_combat.add_from_initiative(map_id)
    init.clear_foes(GAME)
    view = token_combat.combat_view(map_id, GAME)
    orphans = [t for t in view["tokens"] if t.get("orphan")]
    assert {t["label"] for t in orphans} == {"Goblin 1", "Goblin 2"} and view["orphans"] == 2
    assert all("current" not in t for t in orphans)
    assert token_combat.remove_orphans(map_id) == 2
    assert {t["label"] for t in map_tokens.list_tokens(map_id)} == {"Aria", "Bram"}
    assert token_combat.remove_orphans(map_id) == 0


def test_a_token_never_follows_a_different_combatant_with_a_reused_id(map_id, party):
    token_combat.add_from_initiative(map_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE map_tokens SET combatant_name = 'Somebody else' WHERE label = 'Aria'")
    assert _by_label(map_id)["Aria"].get("orphan") is True


def test_tokens_for_players_drop_orphans_quietly(map_id, party):
    token_combat.add_from_initiative(map_id, foes_hidden=False)
    init.clear_foes(GAME)
    shown = {t["label"]: t for t in live.live_state(map_id)["tokens"]}
    assert "current" not in shown["Goblin 1"] and "down" not in shown["Goblin 1"]


# ---------------------------------------------------------------- the view ---
def test_combat_view_summary(map_id, party):
    token_combat.add_from_initiative(map_id)
    view = token_combat.combat_view(map_id, GAME)
    assert view["round"] == 1 and view["current"] == "Aria"
    assert [c["name"] for c in view["combatants"]] == ["Aria", "Bram", "Goblin 1", "Goblin 2"]
    assert [c["is_pc"] for c in view["combatants"]] == [True, True, False, False]
    init.reset(GAME)
    empty = token_combat.combat_view(map_id, GAME)
    assert empty["round"] is None and empty["current"] is None and empty["orphans"] == 4


# --------------------------------------------------------------------- API ---
def test_api_add_next_hit_link_and_remove(client, map_id, party):
    res = client.post(f"/api/maps/{map_id}/combat/add", data={"foes_hidden": "1"}).json()
    assert res["added"] == 4 and res["current"] == "Aria"

    nxt = client.post(f"/api/maps/{map_id}/combat/next").json()
    assert nxt["current"] == "Bram"
    tokens = {t["label"]: t for t in nxt["tokens"]}
    assert tokens["Bram"]["current"] is True and tokens["Aria"]["current"] is False

    hit = client.post(f"/api/maps/{map_id}/combat/hit", data={"combatant_id": tokens["Goblin 1"]["combatant_id"], "delta": -10}).json()
    goblin = next(t for t in hit["tokens"] if t["label"] == "Goblin 1")
    assert goblin["hp"] == -3 and goblin["down"] is True

    plain = client.post(f"/api/maps/{map_id}/tokens", data={"label": "Cleric", "x": 40, "y": 40}).json()["token"]
    init.add_combatant(GAME, "Cleric", 12, hp=20, is_pc=True)
    cid = next(c["id"] for c in client.get(f"/api/maps/{map_id}/combat").json()["combatants"] if c["name"] == "Cleric")
    linked = client.post(f"/api/map-tokens/{plain['id']}/link", data={"combatant_id": cid}).json()
    assert next(t for t in linked["tokens"] if t["label"] == "Cleric")["hp"] == 20
    assert client.post(f"/api/map-tokens/{plain['id']}/link", data={"combatant_id": "9999"}).status_code == 400
    assert client.post("/api/map-tokens/99999/link", data={"combatant_id": ""}).status_code == 400

    init.clear_foes(GAME)
    cleared = client.post(f"/api/maps/{map_id}/combat/remove-orphans").json()
    assert cleared["removed"] == 2 and cleared["orphans"] == 0


def test_api_unknown_map(client):
    assert client.get("/api/maps/999/combat").status_code == 404
    assert client.post("/api/maps/999/combat/next").status_code == 404
    assert client.post("/api/maps/999/combat/hit", data={"combatant_id": 1, "delta": -1}).status_code == 404
    assert client.post("/api/maps/999/combat/add").status_code == 404
    assert client.post("/api/maps/999/combat/remove-orphans").status_code == 404


def test_the_map_page_and_scripts_have_the_combat_controls(client, map_id, party):
    page = client.get(f"/maps/{map_id}").text
    for needle in ('id="combat-add"', 'id="combat-next"', "refreshCombat", "/combat/hit"):
        assert needle in page
    js = client.get("/static/app.js").text
    assert "token-turn-ring" in js and "token-down-cross" in js
    player = client.get(f"/maps/{map_id}/player").text
    assert "drawTokens" in player


def test_the_page_starts_with_linked_tokens_already_described(client, map_id, party):
    token_combat.add_from_initiative(map_id)
    page = client.get(f"/maps/{map_id}").text
    assert '"combatant_id"' in page and '"hp"' in page
