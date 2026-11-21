"""Saved encounters: the one-line-per-group format, saving, loading into the
initiative tracker, and the endpoints/page."""
import pytest
from fastapi.testclient import TestClient

from app import saved_encounters as se
from app.tools import initiative


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- the text format

@pytest.mark.parametrize("line, expected", [
    ("4x Goblin (hp 7, init 1d20+2)", ("Goblin", 4, 7, "1d20+2", "")),
    ("Goblin Boss (hp 21, init 1d20+1, notes: carries the key, a map)", ("Goblin Boss", 1, 21, "1d20+1", "carries the key, a map")),
    ("Wolf x2 (hp 11)", ("Wolf", 2, 11, "1d20", "")),
    ("3 × Bat (init +3)", ("Bat", 3, None, "1d20+3", "")),
    ("Orc", ("Orc", 1, None, "1d20", "")),
    ("Ogre (INIT 12, HP: 59)", ("Ogre", 1, 59, "12", "")),
    ("  2x   Giant   Rat  ( hp 4 ) ", ("Giant Rat", 2, 4, "1d20", "")),
    ("Wraith (init 1d20 + 3)", ("Wraith", 1, None, "1d20+3", "")),
    ("Xorn x 2", ("Xorn", 2, None, "1d20", "")),
    ("3rd Legion Soldier", ("3rd Legion Soldier", 1, None, "1d20", "")),
])
def test_parse_line(line, expected):
    e = se.parse_line(line)
    assert (e["name"], e["quantity"], e["hp"], e["initiative"], e["notes"]) == expected


@pytest.mark.parametrize("line, message", [
    ("(hp 7)", "needs a name"),
    ("0x Orc", "between 1 and 30"),
    ("31x Rat", "between 1 and 30"),
    ("Orc (hp)", "don't understand"),
    ("Orc (hp 0)", "positive"),
    ("Orc (init banana)", "initiative"),
    ("Orc (colour red)", "don't understand"),
])
def test_parse_line_errors(line, message):
    with pytest.raises(ValueError, match=message):
        se.parse_line(line)


def test_errors_name_the_line():
    with pytest.raises(ValueError, match="Line 3"):
        se.parse_body("Goblin\n\nOrc (hp)\n")


def test_body_skips_blank_and_comment_lines():
    entries = se.parse_body("# the ambush\n\n4x Goblin (hp 7)\n  \nBoss\n")
    assert [e["name"] for e in entries] == ["Goblin", "Boss"]


def test_body_limits():
    with pytest.raises(ValueError, match="up to"):
        se.parse_body("\n".join(f"Foe {i}" for i in range(se.MAX_ENTRIES + 1)))
    with pytest.raises(ValueError, match="too long"):
        se.parse_body("x" * (se.MAX_BODY + 1))


def test_format_round_trips():
    text = "4x Goblin (hp 7, init 1d20+2)\nGoblin Boss (hp 21, notes: carries the key, a map)\nWolf"
    entries = se.parse_body(text)
    assert se.format_body(entries) == text
    assert se.parse_body(se.format_body(entries)) == entries


def test_summary_text():
    assert se.summary(se.parse_body("4x Goblin\nBoss")) == "4x Goblin, Boss"


# ---------------------------------------------------------------- storage

def test_create_list_update_delete(db, games_config):
    a = se.create_encounter("rifts", "Goblin ambush", "4x Goblin (hp 7)\nBoss (hp 21)")
    se.create_encounter("rifts", "Alpha wolves", "2x Wolf")
    se.create_encounter("dnd5e", "Other game", "Orc")
    listed = se.list_encounters("rifts")
    assert [e["name"] for e in listed] == ["Alpha wolves", "Goblin ambush"]    # by name
    ambush = se.get_encounter(a)
    assert ambush["foes"] == 5 and ambush["summary"] == "4x Goblin, Boss"

    se.update_encounter(a, "name", "  Goblin   ambush II ")
    se.update_encounter(a, "body", "6x Goblin")
    assert se.get_encounter(a)["name"] == "Goblin ambush II" and se.get_encounter(a)["foes"] == 6

    se.delete_encounter(a)
    assert se.get_encounter(a) is None
    assert [e["name"] for e in se.list_encounters("rifts")] == ["Alpha wolves"]


def test_names_are_unique_per_game_ignoring_case(db, games_config):
    se.create_encounter("rifts", "Ambush", "Goblin")
    with pytest.raises(ValueError, match="already"):
        se.create_encounter("rifts", "ambush", "Goblin")
    se.create_encounter("dnd5e", "Ambush", "Goblin")       # another game is fine
    other = se.create_encounter("rifts", "Second", "Goblin")
    with pytest.raises(ValueError, match="already"):
        se.update_encounter(other, "name", "AMBUSH")
    se.update_encounter(other, "name", "second")           # renaming to itself, any case, is fine


def test_validation_on_save(db, games_config):
    with pytest.raises(ValueError, match="name"):
        se.create_encounter("rifts", "   ", "Goblin")
    enc = se.create_encounter("rifts", "Ok", "Goblin")
    with pytest.raises(ValueError):
        se.update_encounter(enc, "body", "Goblin (hp)")
    assert se.get_encounter(enc)["body"] == "Goblin"        # a bad edit changes nothing
    with pytest.raises(ValueError):
        se.update_encounter(enc, "game_key", "dnd5e")
    with pytest.raises(ValueError):
        se.update_encounter(99999, "name", "x")


def test_an_empty_encounter_can_be_saved_but_not_loaded(db, games_config):
    enc = se.create_encounter("rifts", "Empty")
    with pytest.raises(ValueError, match="empty"):
        se.load_into_tracker(enc, "rifts")


def test_append_entry(db, games_config):
    enc = se.create_encounter("rifts", "Mixed", "Goblin")
    se.append_entry(enc, "2x Wolf (hp 11)")
    assert se.get_encounter(enc)["body"] == "Goblin\n2x Wolf (hp 11)"
    with pytest.raises(ValueError):
        se.append_entry(enc, "Orc (hp)")
    with pytest.raises(ValueError):
        se.append_entry(99999, "Orc")


# ---------------------------------------------------------------- tracker

def test_load_adds_numbered_foes_with_their_hp_and_notes(db, games_config):
    enc = se.create_encounter("rifts", "Ambush", "3x Goblin (hp 7, init 15)\nBoss (hp 21, init 9, notes: has the key)")
    res = se.load_into_tracker(enc, "rifts")
    assert res["added"] == 4
    board = initiative.get_state("rifts")["combatants"]
    assert sorted(c["name"] for c in board) == ["Boss", "Goblin 1", "Goblin 2", "Goblin 3"]
    boss = next(c for c in board if c["name"] == "Boss")
    assert (boss["hp"], boss["max_hp"], boss["initiative"], boss["notes"], boss["is_pc"]) == (21, 21, 9, "has the key", False)
    assert all(c["initiative"] == 15 for c in board if c["name"].startswith("Goblin"))


def test_each_foe_rolls_its_own_initiative(db, games_config, monkeypatch):
    rolls = iter([5, 12, 20])
    monkeypatch.setattr(initiative, "_roll_initiative", lambda v: next(rolls) if "d" in str(v) else int(v))
    enc = se.create_encounter("rifts", "Rats", "3x Rat")        # create validated with the real roller
    # (the monkeypatch above came after save, so the three loads below use the fake rolls)
    se.load_into_tracker(enc, "rifts")
    assert sorted(c["initiative"] for c in initiative.get_state("rifts")["combatants"]) == [5, 12, 20]


def test_loading_keeps_the_party_and_stacks_on_existing_foes(db, games_config):
    initiative.add_combatant("rifts", "Aria", 14, hp=30, is_pc=True)
    enc = se.create_encounter("rifts", "Goblins", "2x Goblin (init 10)")
    se.load_into_tracker(enc, "rifts")
    se.load_into_tracker(enc, "rifts")
    names = [c["name"] for c in initiative.get_state("rifts")["combatants"]]
    assert "Aria" in names and len(names) == 5            # Aria + 2 + 2, second batch renumbered
    assert len(set(names)) == 5


def test_load_for_the_wrong_game_is_refused(db, games_config):
    enc = se.create_encounter("rifts", "Goblins", "Goblin")
    with pytest.raises(ValueError):
        se.load_into_tracker(enc, "dnd5e")
    with pytest.raises(ValueError):
        se.load_into_tracker(99999, "rifts")


def test_save_the_foes_on_the_board(db, games_config):
    initiative.add_combatant("rifts", "Aria", 14, hp=30, is_pc=True)
    initiative.add_combatant("rifts", "Goblin", 10, hp=7, quantity=3)
    initiative.add_combatant("rifts", "Goblin Boss", 12, hp=21, notes="has the key")
    initiative.add_combatant("rifts", "Wolf", 8)            # no HP
    enc = se.save_board_foes("rifts", "Saved fight")
    e = se.get_encounter(enc)
    assert [(x["name"], x["quantity"], x["hp"]) for x in e["entries"]] == [
        ("Goblin", 3, 7), ("Goblin Boss", 1, 21), ("Wolf", 1, None)]
    assert "Aria" not in e["body"]
    assert next(x for x in e["entries"] if x["name"] == "Goblin Boss")["notes"] == "has the key"


def test_saving_an_empty_board_is_an_error(db, games_config):
    initiative.add_combatant("rifts", "Aria", 14, hp=30, is_pc=True)
    with pytest.raises(ValueError, match="no foes"):
        se.save_board_foes("rifts", "Nothing")
    with pytest.raises(ValueError, match="no foes"):
        se.save_board_foes("dnd5e", "Nothing")


def test_goblins_with_different_hp_stay_separate_groups(db, games_config):
    initiative.add_combatant("rifts", "Goblin", 10, hp=7)
    initiative.add_combatant("rifts", "Goblin", 10, hp=9)
    names = [(e["name"], e["hp"]) for e in se.entries_from_board("rifts")]
    assert sorted(names) == [("Goblin", 7), ("Goblin", 9)]


# ---------------------------------------------------------------- endpoints & page

def test_endpoints_round_trip(client):
    created = client.post("/api/initiative/encounters",
                          data={"game": "rifts", "name": "Ambush", "body": "2x Goblin (hp 7)"})
    assert created.status_code == 200
    enc = created.json()["encounter"]
    assert enc["foes"] == 2 and enc["body"] == "2x Goblin (hp 7)"

    listed = client.get("/api/initiative/encounters", params={"game": "rifts"}).json()["encounters"]
    assert [e["id"] for e in listed] == [enc["id"]]
    assert client.get("/api/initiative/encounters", params={"game": "dnd5e"}).json()["encounters"] == []

    upd = client.post(f"/api/initiative/encounters/{enc['id']}/update", data={"field": "body", "value": "5x Goblin"})
    assert upd.json()["encounter"]["foes"] == 5

    loaded = client.post(f"/api/initiative/encounters/{enc['id']}/load", data={"game": "rifts"}).json()
    assert loaded["added"] == 5 and len(loaded["state"]["combatants"]) == 5
    assert client.get("/api/initiative", params={"game": "rifts"}).json()["combatants"]

    assert client.post(f"/api/initiative/encounters/{enc['id']}/delete").json() == {"ok": True}
    assert client.get("/api/initiative/encounters", params={"game": "rifts"}).json()["encounters"] == []


def test_endpoint_errors(client):
    assert client.post("/api/initiative/encounters", data={"game": "rifts", "name": "", "body": "x"}).status_code == 400
    assert client.post("/api/initiative/encounters", data={"game": "rifts", "name": "A", "body": "Orc (hp)"}).status_code == 400
    assert client.post("/api/initiative/encounters", data={"game": "rifts", "name": "B", "from_board": 1}).status_code == 400
    assert client.post("/api/initiative/encounters/99999/update", data={"field": "name", "value": "x"}).status_code == 400
    assert client.post("/api/initiative/encounters/99999/load", data={"game": "rifts"}).status_code == 400


def test_save_from_board_endpoint(client):
    client.post("/api/initiative/add", data={"game": "rifts", "name": "Goblin", "initiative": "10", "hp": 7, "quantity": 2})
    res = client.post("/api/initiative/encounters", data={"game": "rifts", "name": "Saved", "from_board": 1})
    assert res.status_code == 200 and res.json()["encounter"]["body"] == "2x Goblin (hp 7)"


def test_initiative_page_has_the_saved_encounters_card(client):
    html = client.get("/tools/initiative?game=rifts").text
    assert 'id="saved-card"' in html and 'id="saved-form"' in html and 'id="save-board-btn"' in html
    assert "/api/initiative/encounters" in html
