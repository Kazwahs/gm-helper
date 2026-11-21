"""Tests for room keys (app/room_keys.py): the facts the map generators stamp on
rooms, the key generator, storage, the AI helper, the API and the pages."""
import pytest
from fastapi.testclient import TestClient

from app import database, llm, maps_store, random_tables, room_keys
from app.maps import generate_map


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _saved(map_type="dungeon", style="ink", seed=11, genre="fantasy", size="small"):
    result = generate_map(map_type, genre, {"size": size, "style": style}, seed)
    return maps_store.save_map("rifts", map_type, genre, "Test " + map_type, result["svg"], seed=seed), result


# ------------------------------------------------------ facts on the map ---
@pytest.mark.parametrize("map_type", ["dungeon", "interior"])
@pytest.mark.parametrize("style", ["ink", "classic"])
def test_every_room_on_the_map_is_readable(map_type, style):
    result = generate_map(map_type, "fantasy", {"size": "small", "style": style}, 5)
    rooms = room_keys.parse_rooms(result["svg"])
    assert len(rooms) == result["meta"]["rooms"]
    assert [r["number"] for r in rooms] == list(range(1, len(rooms) + 1))
    assert all(r["size"] and r["shape"] for r in rooms)
    # every room is reachable from some exit, and exits point at rooms that exist
    numbers = {r["number"] for r in rooms}
    for r in rooms:
        assert r["exits"], f"room {r['number']} has no exits"
        assert all(n in numbers for n, _ in r["exits"])


def test_ink_exits_name_the_door_that_was_drawn():
    result = generate_map("dungeon", "fantasy", {"size": "medium", "style": "ink"}, 3)
    kinds = {k for r in room_keys.parse_rooms(result["svg"]) for _, k in r["exits"]}
    assert kinds <= {"door", "arch", "swing", "locked", "secret"}
    assert len(kinds) >= 2


def test_secret_doors_in_meta_match_the_key_facts():
    for seed in range(1, 25):
        result = generate_map("dungeon", "fantasy", {"size": "large", "style": "ink"}, seed)
        found = sum(1 for r in room_keys.parse_rooms(result["svg"]) for _, k in r["exits"] if k == "secret")
        assert found == result["meta"]["secret_doors"]


def test_stairs_and_names_are_recorded():
    seen_stairs = False
    for seed in range(1, 15):
        result = generate_map("dungeon", "fantasy", {"size": "medium", "style": "ink"}, seed)
        for r in room_keys.parse_rooms(result["svg"]):
            if r["stairs"]:
                seen_stairs = True
                assert r["name"].startswith("Stairs")
    assert seen_stairs


def test_maps_saved_before_this_feature_still_parse_in_drawing_order():
    old = ('<svg><g class="map-el" data-kind="room" data-cx="1" data-cy="1"><rect/></g>'
           '<g class="map-el" data-kind="object" data-cx="1" data-cy="1"/>'
           '<g class="map-el" data-kind="room" data-cx="2" data-cy="2"><rect/></g></svg>')
    rooms = room_keys.parse_rooms(old)
    assert [r["number"] for r in rooms] == [1, 2]
    assert rooms[0]["name"] == "" and rooms[0]["exits"] == []


def test_attribute_values_are_unescaped():
    svg = '<svg><g class="map-el" data-kind="room" data-room="1" data-name="Rats &amp; Bats" data-exits="2:door"></g></svg>'
    assert room_keys.parse_rooms(svg)[0]["name"] == "Rats & Bats"


def test_editing_a_label_keeps_the_room_facts_in_step():
    # the map editor script does this; make sure the markup it relies on is present
    from pathlib import Path
    page = Path("app/templates/map_detail.html").read_text(encoding="utf-8")
    assert "function syncRoomFacts" in page and "syncRoomFacts(selectedEl)" in page
    result = generate_map("dungeon", "fantasy", {"size": "medium", "style": "ink"}, 2)
    assert 'data-role="feature"' in result["svg"] or "data-feature" in result["svg"]


# ----------------------------------------------------------- generating ---
def _rooms(seed=11, **kw):
    return room_keys.parse_rooms(generate_map("dungeon", "fantasy", {"size": "medium", "style": "ink", **kw}, seed)["svg"])


def test_the_same_seed_gives_the_same_key_and_another_seed_differs():
    rooms = _rooms()
    a = room_keys.generate(rooms, "fantasy", "s1")
    assert a == room_keys.generate(rooms, "fantasy", "s1")
    assert a != room_keys.generate(rooms, "fantasy", "s2")


def test_every_room_gets_a_well_formed_entry():
    rooms = _rooms()
    entries = room_keys.generate(rooms, "fantasy", 1)
    assert [e["room"] for e in entries] == [r["number"] for r in rooms]
    for e in entries:
        assert e["kind"] in room_keys.KINDS
        assert e["title"] and e["description"]
        if e["kind"] == "creatures":
            assert e["contents"].split(":")[0] in ("Light encounter", "Moderate encounter", "Tough encounter", "Boss encounter")
        if e["kind"] in ("trap", "puzzle"):
            assert e["contents"]


def test_one_boss_with_a_hoard_in_a_far_room_and_the_start_is_gentle():
    for seed in range(1, 12):
        rooms = _rooms(seed=seed)
        entries = room_keys.generate(rooms, "fantasy", seed)
        bosses = [e for e in entries if e["contents"].startswith("Boss")]
        assert len(bosses) == 1
        assert bosses[0]["treasure"]
        assert bosses[0]["room"] != rooms[0]["number"]
        depth = room_keys._depths(rooms)
        stairs = {r["number"] for r in rooms if r["stairs"]}
        non_stairs = [depth[r["number"]] for r in rooms if r["number"] not in stairs and r["number"] != rooms[0]["number"]]
        assert depth[bosses[0]["room"]] == max(non_stairs)
        first = entries[0]
        assert not first["contents"].startswith(("Moderate", "Tough", "Boss"))


def test_secret_and_locked_doors_are_listed_for_the_gm():
    found = {"secret": False, "locked": False}
    for seed in range(1, 25):
        rooms = _rooms(seed=seed)
        for e, r in zip(room_keys.generate(rooms, "fantasy", seed), rooms):
            for num, kind in r["exits"]:
                if kind in found:
                    found[kind] = True
                    assert f"to room {num}" in e["secrets"]
    assert all(found.values())


def test_exits_and_size_are_carried_into_the_entry():
    rooms = _rooms()
    e = room_keys.generate(rooms, "fantasy", 1)[0]
    assert e["size"] == rooms[0]["size"]
    assert e["exits"] == room_keys.exits_text(rooms[0]["exits"])
    assert " to " in e["exits"]


@pytest.mark.parametrize("genre", ["fantasy", "modern", "hightech", "mixed"])
def test_every_genre_generates(genre):
    entries = room_keys.generate(_rooms(), genre, 4)
    assert entries and all(e["description"] for e in entries)


def test_a_creature_roller_supplies_the_creatures_and_failures_fall_back():
    rooms = _rooms()
    entries = room_keys.generate(rooms, "fantasy", 3, creature_roller=lambda: "a very specific gribbly")
    monsters = [e for e in entries if e["kind"] == "creatures"]
    assert monsters and all("a very specific gribbly" in e["contents"] for e in monsters)

    def broken():
        raise random_tables.TableError("gone")
    fallback = room_keys.generate(rooms, "fantasy", 3, creature_roller=broken)
    assert all("encounter:" in e["contents"] for e in fallback if e["kind"] == "creatures")
    blank = room_keys.generate(rooms, "fantasy", 3, creature_roller=lambda: "  ")
    assert all("encounter:" in e["contents"] for e in blank if e["kind"] == "creatures")


def test_a_one_room_map_still_works():
    rooms = [{"number": 1, "name": "", "feature": "", "shape": "rect", "size": "5x5", "stairs": "", "exits": []}]
    entries = room_keys.generate(rooms, "fantasy", 1)
    assert len(entries) == 1 and entries[0]["title"] == "Room 1"
    assert room_keys.generate([], "fantasy", 1) == []


def test_stairs_rooms_mention_the_stairs():
    rooms = [{"number": 1, "name": "Hall", "feature": "", "shape": "rect", "size": "6x6", "stairs": "", "exits": [(2, "door")]},
             {"number": 2, "name": "Stairs down", "feature": "", "shape": "rect", "size": "5x5", "stairs": "down",
              "exits": [(1, "door")]}]
    entries = room_keys.generate(rooms, "fantasy", 1)
    assert "leads down" in entries[1]["description"]


# -------------------------------------------------------------- storage ---
def test_generate_for_map_stores_one_row_per_room(db):
    map_id, result = _saved()
    m = maps_store.get_map(map_id)
    entries = room_keys.generate_for_map(m, seed=1)
    assert len(entries) == result["meta"]["rooms"]
    assert room_keys.list_entries(map_id) == entries
    again = room_keys.generate_for_map(m, seed=2)
    assert len(again) == len(entries) and again != entries


def test_unsupported_and_empty_maps_are_refused(db):
    world = generate_map("world", "fantasy", {"size": "small"}, 1)
    m = maps_store.get_map(maps_store.save_map("rifts", "world", "fantasy", "W", world["svg"]))
    with pytest.raises(room_keys.RoomKeyError):
        room_keys.generate_for_map(m)
    blank = maps_store.get_map(maps_store.save_map("rifts", "dungeon", "fantasy", "B", "<svg></svg>"))
    with pytest.raises(room_keys.RoomKeyError):
        room_keys.generate_for_map(blank)


def test_update_entry_changes_only_what_is_named_and_cleans_it(db):
    map_id, _ = _saved()
    room_keys.generate_for_map(maps_store.get_map(map_id), seed=1)
    before = room_keys.get_entry(map_id, 1)
    entry = room_keys.update_entry(map_id, 1, notes="  remember the key  ", kind="trap", title="")
    assert entry["notes"] == "remember the key" and entry["kind"] == "trap"
    assert entry["title"] == "Room 1"
    assert entry["description"] == before["description"]
    with pytest.raises(room_keys.RoomKeyError):
        room_keys.update_entry(map_id, 1, kind="dragon")
    with pytest.raises(room_keys.RoomKeyError) as err:
        room_keys.update_entry(map_id, 999, notes="x")
    assert err.value.status == 404
    assert room_keys.update_entry(map_id, 1, bogus="x", notes=None)["notes"] == "remember the key"


def test_long_text_is_clamped(db):
    map_id, _ = _saved()
    room_keys.generate_for_map(maps_store.get_map(map_id), seed=1)
    entry = room_keys.update_entry(map_id, 1, notes="x" * 9000)
    assert len(entry["notes"]) == room_keys.MAX_TEXT


def test_deleting_the_map_deletes_its_key(db):
    map_id, _ = _saved()
    room_keys.generate_for_map(maps_store.get_map(map_id), seed=1)
    maps_store.delete_map(map_id)
    assert room_keys.list_entries(map_id) == []


# ------------------------------------------------------------------- AI ---
def test_parse_ai_reply():
    assert room_keys.parse_ai_reply("READ ALOUD: You see a hall.\nGM NOTES: The rats bite.") == ("You see a hall.", "The rats bite.")
    assert room_keys.parse_ai_reply("read aloud: Only this.") == ("Only this.", "")
    assert room_keys.parse_ai_reply("Just prose.") == ("Just prose.", "")
    assert room_keys.parse_ai_reply("") == ("", "")


def test_flesh_out_needs_the_ai_to_be_set_up(db, monkeypatch):
    map_id, _ = _saved()
    m = maps_store.get_map(map_id)
    room_keys.generate_for_map(m, seed=1)
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    with pytest.raises(room_keys.RoomKeyError):
        room_keys.flesh_out(m, 1)


def test_flesh_out_rewrites_the_description_and_appends_notes(db, monkeypatch):
    map_id, _ = _saved()
    m = maps_store.get_map(map_id)
    room_keys.generate_for_map(m, seed=1)
    room_keys.update_entry(map_id, 1, notes="my own note")
    sent = {}

    def fake(system, user, max_tokens=0):
        sent["user"] = user
        return "READ ALOUD: Cold air rolls out.\nGM NOTES: The thing wants to be fed."
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate", fake)
    entry = room_keys.flesh_out(m, 1)
    assert entry["description"] == "Cold air rolls out."
    assert entry["notes"] == "my own note\n\nAI notes: The thing wants to be fed."
    assert "Room 1" in sent["user"]


def test_flesh_out_reports_model_errors_and_empty_replies(db, monkeypatch):
    map_id, _ = _saved()
    m = maps_store.get_map(map_id)
    room_keys.generate_for_map(m, seed=1)
    monkeypatch.setattr(llm, "is_enabled", lambda: True)

    def boom(*a, **k):
        raise llm.LLMError("offline")
    monkeypatch.setattr(llm, "generate", boom)
    with pytest.raises(room_keys.RoomKeyError) as err:
        room_keys.flesh_out(m, 1)
    assert err.value.status == 502
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "  ")
    with pytest.raises(room_keys.RoomKeyError):
        room_keys.flesh_out(m, 1)


# ------------------------------------------------------------------ API ---
def test_api_generate_edit_and_clear(client):
    map_id, result = _saved()
    empty = client.get(f"/api/maps/{map_id}/key").json()
    assert empty["entries"] == [] and empty["supported"] is True and empty["kinds"]

    res = client.post(f"/api/maps/{map_id}/key/generate", data={"seed": "5"}).json()
    assert res["ok"] and len(res["entries"]) == result["meta"]["rooms"]
    same = client.post(f"/api/maps/{map_id}/key/generate", data={"seed": "5"}).json()
    strip = lambda rows: [{k: v for k, v in r.items() if k not in ("id", "updated_at")} for r in rows]
    assert strip(same["entries"]) == strip(res["entries"])

    edited = client.post(f"/api/maps/{map_id}/key/2", data={"notes": "hello", "kind": "trap"}).json()
    assert edited["entry"]["notes"] == "hello" and edited["entry"]["kind"] == "trap"
    bad = client.post(f"/api/maps/{map_id}/key/2", data={"kind": "nope"})
    assert bad.status_code == 400 and not bad.json()["ok"]
    assert client.post(f"/api/maps/{map_id}/key/999", data={"notes": "x"}).status_code == 404

    cleared = client.post(f"/api/maps/{map_id}/key/clear").json()
    assert cleared["entries"] == []


def test_api_unknown_map_and_unsupported_type(client):
    assert client.get("/api/maps/999/key").status_code == 404
    assert client.post("/api/maps/999/key/generate").status_code == 404
    world = generate_map("world", "fantasy", {"size": "small"}, 1)
    wid = maps_store.save_map("rifts", "world", "fantasy", "W", world["svg"])
    res = client.post(f"/api/maps/{wid}/key/generate")
    assert res.status_code == 400 and "dungeon" in res.json()["error"]
    assert client.get(f"/api/maps/{wid}/key").json()["supported"] is False


def test_api_uses_one_of_the_games_random_tables(client):
    map_id, _ = _saved()
    tid = random_tables.create_table("rifts", "Foes", "d4", "1. a wandering gribbly\n2. a wandering gribbly\n3. a wandering gribbly\n4. a wandering gribbly")
    res = client.post(f"/api/maps/{map_id}/key/generate", data={"creature_table": str(tid), "seed": "3"}).json()
    monsters = [e for e in res["entries"] if e["kind"] == "creatures"]
    assert monsters and all("a wandering gribbly" in e["contents"] for e in monsters)
    other = random_tables.create_table("dnd5e", "Other", "d4", "1. a\n2. b\n3. c\n4. d")
    bad = client.post(f"/api/maps/{map_id}/key/generate", data={"creature_table": str(other)})
    assert bad.status_code == 400
    assert client.post(f"/api/maps/{map_id}/key/generate", data={"creature_table": "99999"}).status_code == 400


def test_api_ai_route(client, monkeypatch):
    map_id, _ = _saved()
    client.post(f"/api/maps/{map_id}/key/generate", data={"seed": "1"})
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    assert client.post(f"/api/maps/{map_id}/key/1/ai").status_code == 400
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "READ ALOUD: Spooky.\nGM NOTES: Boo.")
    res = client.post(f"/api/maps/{map_id}/key/1/ai").json()
    assert res["entry"]["description"] == "Spooky."
    assert client.get(f"/api/maps/{map_id}/key").json()["ai"] is True


# ---------------------------------------------------------------- pages ---
def test_map_page_has_the_room_key_card_only_for_dungeons(client):
    map_id, _ = _saved()
    page = client.get(f"/maps/{map_id}").text
    assert 'id="key-card"' in page and "room_key.js" in page
    world = generate_map("world", "fantasy", {"size": "small"}, 1)
    wid = maps_store.save_map("rifts", "world", "fantasy", "W", world["svg"])
    assert 'id="key-card"' not in client.get(f"/maps/{wid}").text


def test_the_key_page_lists_entries_and_escapes_text(client):
    map_id, _ = _saved()
    empty = client.get(f"/maps/{map_id}/key")
    assert empty.status_code == 200 and "no room key yet" in empty.text
    client.post(f"/api/maps/{map_id}/key/generate", data={"seed": "1"})
    client.post(f"/api/maps/{map_id}/key/1", data={"notes": "<script>alert(1)</script>", "title": "Rats & Bats"})
    page = client.get(f"/maps/{map_id}/key").text
    assert "Rats &amp; Bats" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Print this key" in page
    assert client.get("/maps/999/key").status_code == 404


def test_the_script_and_styles_ship(client):
    js = client.get("/static/room_key.js")
    assert js.status_code == 200 and "key-generate" in js.text
