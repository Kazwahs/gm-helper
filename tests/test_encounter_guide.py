"""Encounter builder: the attacks/mdc line options, the rough difficulty guide, the saved party, and the endpoints."""
import json
import math

import pytest
from fastapi.testclient import TestClient

from app import config, database, encounter_guide as eg, saved_encounters as se
from app.tools import initiative as ini


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def member(hp, attacks=1, mdc=False, name="PC"):
    return {"name": name, "hp": hp, "attacks": attacks, "mdc": mdc}


def group(name, hp, quantity=1, attacks=1, mdc=False):
    e = {"name": name, "quantity": quantity, "hp": hp}
    if attacks:
        e["attacks"] = attacks
    if mdc:
        e["mdc"] = True
    return e


# ------------------------------------------------- attacks / mdc on a line

@pytest.mark.parametrize("line, attacks, mdc", [
    ("Dragon (hp 150, mdc, attacks 3)", 3, True),
    ("Dragon (hp 150, M.D.C., attack: 2)", 2, True),
    ("Ogre (hp 59, attacks per round 4)", 4, False),
    ("Orc (hp 20, attacks=2)", 2, False),
    ("Wolf (hp 11, MDC)", None, True),
])
def test_attacks_and_mdc_parse(line, attacks, mdc):
    e = se.parse_line(line)
    assert e.get("attacks") == attacks
    assert e.get("mdc", False) is mdc


def test_lines_without_them_keep_the_old_entry_shape():
    assert se.parse_line("4x Goblin (hp 7, init 1d20+2)") == {
        "name": "Goblin", "quantity": 4, "hp": 7, "initiative": "1d20+2", "notes": ""}


@pytest.mark.parametrize("line", ["Orc (attacks 0)", "Orc (attacks 21)", "Orc (attacks)", "Orc (attacks lots)"])
def test_bad_attacks_are_refused(line):
    with pytest.raises(ValueError):
        se.parse_line(line)


def test_attacks_and_mdc_survive_formatting_and_notes_keep_their_commas():
    line = "2x Dragon Hatchling (hp 150, init 1d20+2, mdc, attacks 3, notes: breathes fire, flies)"
    entry = se.parse_line(line)
    assert entry["notes"] == "breathes fire, flies"
    assert se.format_entry(entry) == line
    assert se.parse_line(se.format_entry(entry)) == entry


def test_loading_an_mdc_group_marks_the_combatants(db, games_config):
    eid = se.create_encounter("rifts", "Hatchlings", "2x Dragon Hatchling (hp 150, mdc, attacks 3)\nGoblin (hp 7)")
    se.load_into_tracker(eid, "rifts")
    board = ini.get_state("rifts")["combatants"]
    hatchlings = [c for c in board if c["name"].startswith("Dragon Hatchling")]
    assert len(hatchlings) == 2 and all(c["notes"].startswith("M.D.C.") for c in hatchlings)
    assert [c["notes"] for c in board if c["name"] == "Goblin"] == [""]


# ---------------------------------------------------------------- profiles

def test_profile_lookup_by_game_then_system_then_generic(db, games_config, monkeypatch):
    assert eg.profile_for("rifts")["key"] == "rifts"                # a profile of its own
    assert eg.profile_for("dnd5e")["key"] == "d20"                  # no profile of its own: its system's
    assert eg.profile_for("_global")["key"] == "generic"
    assert eg.profile_for("no-such-game")["key"] == "generic"
    assert eg.profile_for(None)["key"] == "generic"
    monkeypatch.delitem(eg._DATA["profiles"], "rifts")
    assert eg.profile_for("rifts")["key"] == "palladium"            # rifts is a palladium-system game


def test_the_shipped_profiles_cover_the_three_named_games_and_every_system():
    with open(config.DIFFICULTY_GUIDE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("rifts", "palladium_fantasy", "heroes_unlimited", "palladium", "d20", "generic"):
        profile = data["profiles"][key]
        assert profile["label"] and profile["notes"]
        assert 1 <= profile["party_attacks"] <= eg.MAX_ATTACKS and 1 <= profile["foe_attacks"] <= eg.MAX_ATTACKS
    with open(config.GAMES_CONFIG_PATH, encoding="utf-8") as f:
        games = json.load(f)["games"]
    for g in games:
        assert g["key"] in data["profiles"] or g["system"] in data["profiles"], g["key"]


def test_the_shipped_bands_are_ordered_and_open_ended():
    with open(config.DIFFICULTY_GUIDE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    limits = [b["max"] for b in data["bands"]]
    assert limits[-1] is None
    assert limits[:-1] == sorted(limits[:-1]) and len(set(limits[:-1])) == len(limits) - 1
    assert data["default_foe_hits"] in {o["key"] for o in data["foe_hits"]}
    assert any(o["factor"] == 1 for o in data["foe_hits"])
    assert data["mdc_to_sdc"] == 100


def test_the_rifts_profile_knows_about_mdc(db, games_config):
    p = eg.profile_for("rifts")
    assert p["label"] == "Rifts" and p["mdc"] is True
    assert any("100" in note for note in p["notes"])
    assert eg.profile_for("dnd5e")["mdc"] is False


def test_profile_copy_is_not_shared(db, games_config):
    eg.profile_for("dnd5e")["notes"].append("scribble")
    assert "scribble" not in eg.profile_for("dnd5e")["notes"]


# ------------------------------------------------------------------- maths

def test_even_sides_are_a_coin_flip(db, games_config):
    party = [member(30, 2)] * 3
    r = eg.assess("dnd5e", [group("Orc", 30, 3, 2)], party)
    assert r["pressure"] == 1.0
    assert r["winner"] == "neither" and r["left_pct"] == 0
    assert r["band"] == "deadly"


def test_a_much_weaker_foe_is_easy_and_the_party_keeps_most_of_its_hp(db, games_config):
    r = eg.assess("dnd5e", [group("Rat", 30, 1, 2)], [member(30, 2)] * 3)
    # foes 30 hp x 2 attacks = 60; party 90 hp x 6 attacks = 540
    assert r["pressure"] == pytest.approx(60 / 540, abs=1e-3)
    assert r["winner"] == "party" and r["left_pct"] == round(math.sqrt(1 - 60 / 540) * 100) == 94
    assert r["band"] == "easy"
    assert "party wins" in r["outcome"] and "94%" in r["outcome"]


def test_the_square_law_numbers(db, games_config):
    # pressure 0.64 -> the party keeps sqrt(0.36) = 60%; pressure 4 -> the foes keep sqrt(0.75) = 87%
    r = eg.assess("dnd5e", [group("A", 64)], [member(100)])
    assert r["pressure"] == 0.64 and r["left_pct"] == 60 and r["winner"] == "party"
    r = eg.assess("dnd5e", [group("A", 400)], [member(100)])
    assert r["pressure"] == 4.0 and r["left_pct"] == 87 and r["winner"] == "foes"
    assert "foes win" in r["outcome"]


def test_band_edges(db, games_config):
    def band(foe_hp):
        return eg.assess("dnd5e", [group("A", foe_hp)], [member(100)])["band"]
    assert band(25) == "easy" and band(26) == "fair"
    assert band(60) == "fair" and band(61) == "hard"
    assert band(90) == "hard" and band(91) == "deadly"
    assert band(150) == "deadly" and band(151) == "overwhelming"


def test_how_hard_foes_hit_scales_the_pressure(db, games_config):
    base = eg.assess("dnd5e", [group("A", 50)], [member(100)])["pressure"]
    for key, factor in (("weaker", 0.5), ("similar", 1), ("harder", 2), ("much_harder", 4)):
        r = eg.assess("dnd5e", [group("A", 50)], [member(100)], foe_hits=key)
        assert r["pressure"] == pytest.approx(base * factor, abs=1e-3), key
        assert r["foe_hits"] == key
    assert any("harder" in c.lower() for c in eg.assess("dnd5e", [group("A", 50)], [member(100)], foe_hits="harder")["cautions"])
    assert eg.assess("dnd5e", [group("A", 50)], [member(100)])["foe_hits"] == "similar"
    with pytest.raises(eg.GuideError):
        eg.assess("dnd5e", [group("A", 50)], [member(100)], foe_hits="gentle")


def test_group_quantities_multiply_hp_and_attacks(db, games_config):
    r = eg.assess("dnd5e", [group("Goblin", 7, 4, 2), group("Boss", 21, 1, 3)], [member(40, 2), member(40, 2)])
    assert r["foes"] == {"count": 5, "groups": 2, "hp": 4 * 7 + 21, "attacks": 4 * 2 + 3}
    assert r["party"] == {"members": 2, "hp": 80, "attacks": 4}


def test_missing_attacks_use_the_profile_default_and_say_so(db, games_config):
    r = eg.assess("rifts", [group("Goblin", 10, 1, attacks=None), group("Ogre", 10, 1, attacks=2)], [member(100, 1)])
    default = eg.profile_for("rifts")["foe_attacks"]
    assert r["foes"]["attacks"] == default + 2
    notice = [c for c in r["cautions"] if "No attacks per round" in c]
    assert len(notice) == 1 and "Goblin" in notice[0] and "Ogre" not in notice[0] and str(default) in notice[0]
    assert not any("No attacks per round" in c for c in eg.assess("rifts", [group("Ogre", 10, 1, 2)], [member(100)])["cautions"])


def test_foes_without_hp_are_named_in_the_error(db, games_config):
    with pytest.raises(eg.GuideError, match="Orc, Ogre"):
        eg.assess("dnd5e", [{"name": "Orc", "quantity": 1, "hp": None}, group("Goblin", 7), {"name": "Ogre", "quantity": 1}], [member(10)])


def test_nothing_to_compare_is_a_friendly_error(db, games_config):
    with pytest.raises(eg.GuideError, match="party member"):
        eg.assess("dnd5e", [group("A", 5)], [])
    with pytest.raises(eg.GuideError, match="foes"):
        eg.assess("dnd5e", [], [member(5)])


def test_assess_text_parses_the_lines_and_names_a_bad_one(db, games_config):
    r = eg.assess_text("dnd5e", "# the ambush\n4x Goblin (hp 7, attacks 2)\n", [member(30, 2)])
    assert r["foes"]["count"] == 4 and r["foes"]["attacks"] == 8
    with pytest.raises(eg.GuideError, match="Line 2"):
        eg.assess_text("dnd5e", "Goblin (hp 7)\nOrc (hp)", [member(30)])


# ------------------------------------------------------------ M.D.C. scale

def test_only_one_scale_means_no_conversion(db, games_config):
    r = eg.assess("rifts", [group("Dragon", 400, 1, 3, mdc=True)], [member(300, 4, mdc=True)])
    assert r["unit"] == "M.D.C." and r["mixed"] is False
    assert r["foes"]["hp"] == 400 and r["party"]["hp"] == 300
    r = eg.assess("rifts", [group("Goblin", 7)], [member(30)])
    assert r["unit"] == "hit points" and r["mixed"] is False


def test_mixed_scales_convert_mdc_at_one_to_one_hundred(db, games_config):
    # an S.D.C. party of 100 against 1 M.D.C. is a dead heat once converted
    r = eg.assess("rifts", [group("Drone", 1, 1, 1, mdc=True)], [member(100, 1)])
    assert r["mixed"] is True and r["unit"] == "S.D.C. equivalent"
    assert r["foes"]["hp"] == 100 and r["party"]["hp"] == 100
    assert r["pressure"] == 1.0
    assert any("1 M.D.C. = 100 S.D.C." in c for c in r["cautions"])


def test_mixed_scale_warnings_say_who_cannot_hurt_whom(db, games_config):
    foes_mdc = eg.assess("rifts", [group("Drone", 10, 1, 1, mdc=True)], [member(100), member(100)])
    assert any("foes are M.D.C. and the party is S.D.C." in c for c in foes_mdc["cautions"])
    party_mdc = eg.assess("rifts", [group("Goblin", 10)], [member(100, mdc=True)])
    assert any("party is M.D.C. and the foes are S.D.C." in c for c in party_mdc["cautions"])
    both = eg.assess("rifts", [group("Drone", 10, 1, 1, mdc=True), group("Goblin", 10)], [member(100, mdc=True), member(100)])
    assert both["mixed"] and not any("can't hurt" in c for c in both["cautions"] if "party is" in c or "foes are" in c)


def test_profile_notes_ride_along(db, games_config):
    r = eg.assess("dnd5e", [group("A", 5)], [member(50)])
    assert r["profile"]["key"] == "d20"
    assert any("XP budget" in c for c in r["cautions"])
    assert r["rough"].startswith("Rough guide only")
    assert r["band_text"]


# ------------------------------------------------------------- party rules

def test_party_validation(db, games_config):
    ok = eg.clean_party([{"name": "  Ana   Lopez ", "hp": "45", "attacks": "3", "mdc": 1}, {"hp": 30}], "rifts")
    assert ok[0] == {"name": "Ana Lopez", "hp": 45, "attacks": 3, "mdc": True}
    assert ok[1]["name"] == "PC 2" and ok[1]["attacks"] == eg.profile_for("rifts")["party_attacks"] and ok[1]["mdc"] is False
    bad = [{"hp": ""}, {"hp": "lots"}, {"hp": 0}, {"hp": -5}, {"hp": eg.MAX_HP + 1}, {"hp": 10, "attacks": 0},
           {"hp": 10, "attacks": 99}, {"hp": 10, "attacks": "many"}, "not a dict"]
    for member_ in bad:
        with pytest.raises(eg.GuideError):
            eg.clean_party([member_], "rifts")
    with pytest.raises(eg.GuideError):
        eg.clean_party([member(10)] * (eg.MAX_PARTY + 1), "rifts")
    with pytest.raises(eg.GuideError):
        eg.clean_party("nope", "rifts")
    assert eg.clean_party([member(10)] * eg.MAX_PARTY, "rifts")[0]["hp"] == 10


def test_party_is_saved_per_game(db, games_config):
    assert eg.get_party("rifts") == []
    saved = eg.set_party("rifts", [{"name": "Ana", "hp": 50, "attacks": 4}])
    assert eg.get_party("rifts") == saved == [{"name": "Ana", "hp": 50, "attacks": 4, "mdc": False}]
    assert eg.get_party("dnd5e") == [] and eg.get_party("_global") == []
    eg.set_party("rifts", [])
    assert eg.get_party("rifts") == []


def test_a_bad_save_keeps_the_old_party(db, games_config):
    eg.set_party("rifts", [{"name": "Ana", "hp": 50}])
    with pytest.raises(eg.GuideError):
        eg.set_party("rifts", [{"name": "Broken", "hp": "x"}])
    assert [m["name"] for m in eg.get_party("rifts")] == ["Ana"]


def test_a_damaged_party_setting_is_ignored(db, games_config):
    database.set_setting("party:rifts", "{not json")
    assert eg.get_party("rifts") == []
    database.set_setting("party:rifts", json.dumps([{"hp": "x"}]))
    assert eg.get_party("rifts") == []


def test_party_from_the_board(db, games_config):
    ini.add_combatant("rifts", "Ana", 15, hp=52, is_pc=True)
    ini.add_combatant("rifts", "Bo", 12, hp=None, is_pc=True)         # no hp: left out
    ini.add_combatant("rifts", "Goblin", 10, hp=7, quantity=2)        # a foe: left out
    ini.add_combatant("dnd5e", "Elsewhere", 10, hp=9, is_pc=True)     # another game
    party = eg.party_from_board("rifts")
    assert party == [{"name": "Ana", "hp": 52, "attacks": eg.profile_for("rifts")["party_attacks"], "mdc": False}]
    assert eg.party_from_board("_global") == []


# --------------------------------------------------------------- endpoints

def test_guide_endpoint(client, db, games_config):
    se.create_encounter("rifts", "Ambush", "4x Goblin (hp 7)")
    eg.set_party("rifts", [{"name": "Ana", "hp": 50, "attacks": 4}])
    body = client.get("/api/guide?game=rifts").json()
    assert set(body) == {"options", "profile", "party", "encounters"}
    assert body["profile"]["party_attacks"] >= 1 and body["profile"]["label"]
    assert body["party"][0]["name"] == "Ana"
    assert body["encounters"] == [{"id": body["encounters"][0]["id"], "name": "Ambush", "body": "4x Goblin (hp 7)"}]
    assert {o["key"] for o in body["options"]["foe_hits"]} == {"weaker", "similar", "harder", "much_harder"}
    assert [b["key"] for b in body["options"]["bands"]][0] == "easy"
    empty = client.get("/api/guide").json()
    assert empty["party"] == [] and empty["encounters"] == []


def test_party_endpoints(client, db, games_config):
    res = client.post("/api/guide/party", data={"game": "rifts", "party": json.dumps([{"name": "Ana", "hp": 50}])})
    assert res.status_code == 200 and res.json()["party"][0]["hp"] == 50
    assert eg.get_party("rifts")[0]["name"] == "Ana"
    assert client.post("/api/guide/party", data={"game": "rifts", "party": json.dumps([{"hp": "x"}])}).status_code == 400
    assert client.post("/api/guide/party", data={"game": "rifts", "party": "{oops"}).status_code == 400
    assert eg.get_party("rifts")[0]["name"] == "Ana"
    ini.add_combatant("rifts", "Cy", 10, hp=33, is_pc=True)
    board = client.get("/api/guide/party/board?game=rifts")
    assert board.status_code == 200 and board.json()["party"][0]["name"] == "Cy"


def test_assess_endpoint_uses_the_typed_party_or_the_saved_one(client, db, games_config):
    typed = client.post("/api/guide/assess", data={
        "game": "dnd5e", "body": "4x Goblin (hp 7)", "party": json.dumps([member(40, 2)] * 3)})
    assert typed.status_code == 200
    assert typed.json()["band"] == "easy" and typed.json()["party"]["members"] == 3
    eg.set_party("dnd5e", [member(40, 2), member(40, 2)])
    saved = client.post("/api/guide/assess", data={"game": "dnd5e", "body": "4x Goblin (hp 7)"})
    assert saved.status_code == 200 and saved.json()["party"]["members"] == 2
    harder = client.post("/api/guide/assess", data={"game": "dnd5e", "body": "4x Goblin (hp 7)", "foe_hits": "much_harder"})
    assert harder.json()["pressure"] == pytest.approx(saved.json()["pressure"] * 4, abs=1e-3)


def test_assess_endpoint_errors_are_400_with_a_message(client, db, games_config):
    party = json.dumps([member(40)])
    cases = [
        {"game": "dnd5e", "body": "", "party": party},                          # no foes
        {"game": "dnd5e", "body": "Goblin", "party": party},                     # no hp
        {"game": "dnd5e", "body": "Goblin (hp 7)", "party": "[]"},               # no party
        {"game": "dnd5e", "body": "Goblin (hp 7)"},                              # nothing saved either
        {"game": "dnd5e", "body": "Goblin (hp)", "party": party},                # unreadable line
        {"game": "dnd5e", "body": "Goblin (hp 7)", "party": "{oops"},
        {"game": "dnd5e", "body": "Goblin (hp 7)", "party": party, "foe_hits": "gentle"},
    ]
    for data in cases:
        res = client.post("/api/guide/assess", data=data)
        assert res.status_code == 400 and res.json()["error"], data


def test_encounters_page_has_the_builder(client, db, games_config):
    html = client.get("/tools/encounters?game=rifts").text
    for needle in ('id="builder-card"', 'data-game="rifts"', "encounter_builder.js", 'id="rate-btn"', "Fight builder", "not a rule from any game book"):
        assert needle in html, needle
    assert 'data-game="_global"' in client.get("/tools/encounters").text
