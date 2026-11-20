"""Tests for random tables (app/random_tables.py + routes): parsing ranges
and plain lists, validation warnings, rolling with nested [[tables]] and
{dice}, loop/depth guards, history, built-in import and 'send to notes'."""
import pytest
from fastapi.testclient import TestClient

from app import gm_screen, random_tables as rt
from app.tools import dice as dice_tool


@pytest.fixture
def fixed_dice(monkeypatch):
    """Makes every die roll come out as `fixed_dice.value` (clamped to the die)."""
    class Box:
        value = 1

    box = Box()
    monkeypatch.setattr(dice_tool.random, "randint", lambda lo, hi: max(lo, min(hi, box.value)))
    return box


# ---------------------------------------------------------------- parsing
def test_ranged_entries_parse_with_single_numbers_and_ranges():
    entries, numbered = rt.parse_body("1-3: Goblins\n4: An orc\n5–6: Nothing")
    assert numbered is True
    assert [(e["lo"], e["hi"], e["text"]) for e in entries] == [(1, 3, "Goblins"), (4, 4, "An orc"), (5, 6, "Nothing")]


def test_plain_lines_are_auto_numbered_and_blank_lines_ignored():
    entries, numbered = rt.parse_body("Goblins\n\n10 gold coins\n  \nAn orc\n")
    assert numbered is False
    assert [(e["lo"], e["text"]) for e in entries] == [(1, "Goblins"), (2, "10 gold coins"), (3, "An orc")]


def test_a_plain_line_starting_with_a_number_is_not_mistaken_for_a_range():
    entries, numbered = rt.parse_body("2 guards: bribable\n3 rats")
    assert numbered is False and entries[0]["text"] == "2 guards: bribable"


def test_mixed_numbered_and_plain_lines_are_rejected():
    with pytest.raises(rt.TableError, match="number every line"):
        rt.parse_body("1-2: Goblins\nAn orc")


def test_backwards_range_and_too_many_entries_are_rejected():
    with pytest.raises(rt.TableError, match="backwards"):
        rt.parse_body("5-2: Oops")
    with pytest.raises(rt.TableError, match="at most"):
        rt.parse_body("\n".join(f"line {i}" for i in range(rt.MAX_ENTRIES + 1)))


@pytest.mark.parametrize("die,expected", [("d6", (1, 6)), ("2d6", (2, 12)), ("D20", (1, 20)), ("d100", (1, 100)),
                                          ("2d6+1", (3, 13)), ("d6-1", (0, 5))])
def test_die_ranges(die, expected):
    assert rt.die_range(die) == expected


@pytest.mark.parametrize("bad", ["", "x", "6", "d1", "d1001", "21d6", "d6d6", "2d6 3d4"])
def test_bad_dice_are_rejected(bad):
    with pytest.raises(rt.TableError):
        rt.parse_die(bad)


# ------------------------------------------------------------- validation
def test_plain_tables_roll_a_dn_regardless_of_the_die_field():
    info = rt.check("d100", "a\nb\nc\nd")
    assert info["die"] == "d4" and info["numbered"] is False and info["warnings"] == []


def test_ranged_tables_default_to_d100():
    assert rt.check("", "1-50: a\n51-100: b")["die"] == "d100"


def test_gaps_and_overlaps_produce_warnings():
    info = rt.check("d6", "1-2: a\n2-3: b\n5: c")
    text = " ".join(info["warnings"])
    assert "Overlapping entries at 2" in text and "No entry for 4, 6" in text


def test_an_entry_outside_the_dice_range_is_an_error():
    with pytest.raises(rt.TableError, match="can't be rolled with d6"):
        rt.check("d6", "1-3: a\n4-9: b")


def test_empty_body_is_an_error():
    with pytest.raises(rt.TableError):
        rt.check("d6", "  \n ")


# ----------------------------------------------------------------- storage
def test_create_list_update_delete(db):
    tid = rt.create_table("rifts", "Weather", "", "Sunny\nRain\nSnow")
    tables = rt.list_tables("rifts")
    assert [t["name"] for t in tables] == ["Weather"]
    assert tables[0]["entry_count"] == 3 and tables[0]["effective_die"] == "d3"
    rt.update_table(tid, "name", "Weather Today")
    rt.update_table(tid, "body", "1-3: Sunny\n4-6: Rain")
    rt.update_table(tid, "die", "d6")
    assert rt.get_table(tid)["body"].startswith("1-3")
    rt.delete_table(tid)
    assert rt.list_tables("rifts") == []


def test_names_are_unique_per_game_case_insensitively(db):
    rt.create_table("rifts", "Weather", "", "a")
    with pytest.raises(rt.TableError, match="already a table"):
        rt.create_table("rifts", "weather", "", "b")
    rt.create_table("dnd5e", "Weather", "", "c")   # a different game is fine
    other = rt.create_table("rifts", "Other", "", "d")
    with pytest.raises(rt.TableError):
        rt.update_table(other, "name", "WEATHER")


def test_invalid_edits_are_rejected_and_leave_the_table_alone(db):
    tid = rt.create_table("rifts", "T", "d6", "1-3: a\n4-6: b")
    for field, value in (("die", "banana"), ("die", "d2"), ("body", "1-9: too big"), ("body", "1: a\nb"),
                         ("name", "  "), ("name", "bad [[name]]"), ("nope", "x")):
        with pytest.raises(rt.TableError):
            rt.update_table(tid, field, value)
    t = rt.get_table(tid)
    assert (t["name"], t["die"], t["body"]) == ("T", "d6", "1-3: a\n4-6: b")


def test_blank_body_is_allowed_while_a_table_is_being_written(db):
    tid = rt.create_table("rifts", "Draft")
    rt.update_table(tid, "body", "")
    with pytest.raises(rt.TableError, match="no entries"):
        rt.roll_table(tid)


def test_global_tables_show_in_every_game_but_game_tables_only_in_their_own(db):
    rt.create_table(rt.GLOBAL_KEY, "Everywhere", "", "a")
    rt.create_table("rifts", "Rifts Only", "", "b")
    assert [t["name"] for t in rt.list_tables("rifts")] == ["Rifts Only", "Everywhere"]
    assert [t["name"] for t in rt.list_tables("dnd5e")] == ["Everywhere"]
    assert [t["name"] for t in rt.list_tables(None)] == ["Everywhere"]


def test_list_tables_survives_a_table_that_no_longer_parses(db):
    tid = rt.create_table("rifts", "Broken", "", "a\nb")
    from app import database
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE random_tables SET die = 'zzz', body = '1-2: x' WHERE id = ?", (tid,))
    t = rt.list_tables("rifts")[0]
    assert t["error"] and t["entry_count"] == 0


# ----------------------------------------------------------------- rolling
def test_roll_picks_the_entry_whose_range_contains_the_roll(db, fixed_dice):
    tid = rt.create_table("rifts", "Loot", "d100", "1-50: Common\n51-90: Uncommon\n91-100: Rare")
    for value, expected in ((1, "Common"), (50, "Common"), (51, "Uncommon"), (91, "Rare"), (100, "Rare")):
        fixed_dice.value = value
        assert rt.roll_table(tid, record=False)["text"] == expected


def test_roll_reports_a_gap_instead_of_failing(db, fixed_dice):
    tid = rt.create_table("rifts", "Gappy", "d6", "1-2: a\n5-6: b")
    fixed_dice.value = 4
    res = rt.roll_table(tid, record=False)
    assert res["roll"] == 4 and res["text"] == "(no entry for 4)"


def test_explicit_roll_value_for_testing(db):
    tid = rt.create_table("rifts", "T", "", "a\nb\nc")
    assert rt.roll_table(tid, record=False, roll=2)["text"] == "b"


def test_inline_dice_are_rolled(db, fixed_dice):
    tid = rt.create_table("rifts", "Coin", "", "You find {2d6} gp and {d4+1} gems")
    fixed_dice.value = 3
    assert rt.roll_table(tid, record=False)["text"] == "You find 6 gp and 4 gems"


def test_nested_table_references_are_resolved_with_steps(db, fixed_dice):
    rt.create_table("rifts", "Weather", "", "Storm")
    rt.create_table("rifts", "Treasure", "", "Gold, found in [[weather]]")   # lookup is case-insensitive
    tid = rt.create_table("rifts", "Hoard", "", "A chest of [[Treasure]]")
    res = rt.roll_table(tid, record=False)
    assert res["text"] == "A chest of Gold, found in Storm"
    assert [s["table"] for s in res["steps"]] == ["Treasure", "Weather"]


def test_references_fall_back_to_global_tables(db):
    rt.create_table(rt.GLOBAL_KEY, "Names", "", "Mara")
    tid = rt.create_table("rifts", "Cast", "", "Met [[Names]]")
    assert rt.roll_table(tid, record=False)["text"] == "Met Mara"


def test_the_games_own_table_beats_a_global_one_of_the_same_name(db):
    rt.create_table(rt.GLOBAL_KEY, "Names", "", "Global Gary")
    rt.create_table("rifts", "Names", "", "Rifts Rita")
    tid = rt.create_table("rifts", "Cast", "", "Met [[Names]]")
    assert rt.roll_table(tid, record=False)["text"] == "Met Rifts Rita"


def test_missing_reference_is_noted_not_fatal(db):
    tid = rt.create_table("rifts", "T", "", "See [[Nowhere]]")
    assert rt.roll_table(tid, record=False)["text"] == "See (missing table: Nowhere)"


def test_reference_loops_are_cut_off(db):
    a = rt.create_table("rifts", "A", "", "a then [[B]]")
    rt.create_table("rifts", "B", "", "b then [[A]]")
    assert rt.roll_table(a, record=False)["text"] == "a then b then (loop: A)"
    self_ref = rt.create_table("rifts", "Self", "", "again [[Self]]")
    assert rt.roll_table(self_ref, record=False)["text"] == "again (loop: Self)"


def test_very_deep_chains_are_cut_off(db):
    names = [f"T{i}" for i in range(rt.MAX_DEPTH + 3)]
    for i, name in enumerate(names):
        rt.create_table("rifts", name, "", f"{name}" + (f" > [[{names[i + 1]}]]" if i + 1 < len(names) else ""))
    first = rt.list_tables("rifts")[0]
    text = rt.roll_table(first["id"], record=False)["text"]
    assert "(too deep:" in text and text.startswith("T0 > T1")


def test_a_referenced_table_with_a_gap_or_error_is_described(db, fixed_dice):
    rt.create_table("rifts", "Gappy", "d6", "1-2: a")
    tid = rt.create_table("rifts", "T", "", "[[Gappy]]")
    fixed_dice.value = 5
    assert "no entry for" in rt.roll_table(tid, record=False)["text"]


def test_rolling_an_unknown_table(db):
    with pytest.raises(rt.TableError, match="no longer exists"):
        rt.roll_table(999)


# ----------------------------------------------------------------- history
def test_rolls_are_recorded_newest_first_per_game(db):
    a = rt.create_table("rifts", "A", "", "alpha")
    b = rt.create_table("dnd5e", "B", "", "beta")
    rt.roll_table(a)
    rt.roll_table(a)
    rt.roll_table(b)
    hist = rt.list_history("rifts")
    assert [h["table_name"] for h in hist] == ["A", "A"] and hist[0]["text"] == "alpha"
    assert len(rt.list_history("dnd5e")) == 1 and rt.list_history(None) == []


def test_history_is_trimmed_to_the_limit(db, monkeypatch):
    monkeypatch.setattr(rt, "HISTORY_LIMIT", 5)
    tid = rt.create_table("rifts", "A", "", "x")
    for _ in range(9):
        rt.roll_table(tid)
    assert len(rt.list_history("rifts", limit=100)) == 5


def test_record_false_leaves_history_alone_and_clear_empties_it(db):
    tid = rt.create_table("rifts", "A", "", "x")
    rt.roll_table(tid, record=False)
    assert rt.list_history("rifts") == []
    rt.roll_table(tid)
    rt.clear_history("rifts")
    assert rt.list_history("rifts") == []


# ---------------------------------------------------------------- built-ins
def test_builtin_tables_are_valid_and_import_is_idempotent(db):
    builtins = rt.builtin_tables()
    assert len(builtins) >= 6
    for name, die, body in builtins:
        rt.check(die, body)           # every shipped table must roll
    first = rt.import_builtin_tables("rifts")
    assert first == len(builtins) and rt.import_builtin_tables("rifts") == 0
    names = {t["name"] for t in rt.list_tables("rifts")}
    assert {"Encounters: Dungeon", "Loot: Item rarity", "Example: Treasure hoard"} <= names


def test_imported_example_hoard_rolls_through_its_references(db, fixed_dice):
    rt.import_builtin_tables("rifts")
    hoard = next(t for t in rt.list_tables("rifts") if t["name"] == "Example: Treasure hoard")
    fixed_dice.value = 6
    text = rt.roll_table(hoard["id"], record=False)["text"]
    assert text.startswith("A sealed chest guarded by ") and "[[" not in text and "(missing" not in text


# ------------------------------------------------------------------ routes
@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_page_renders_and_lists_tables(client):
    rt.create_table("rifts", "Weather", "", "Sunny\nRain")
    html = client.get("/tools/tables?game=rifts").text
    assert "Weather" in html and "2 entries" in html
    assert client.get("/tools/tables").status_code == 200


def test_create_update_roll_delete_flow(client):
    res = client.post("/api/tables", data={"game": "rifts", "name": "Weather", "die": "", "body": ""})
    tid = res.json()["id"]
    upd = client.post(f"/api/tables/{tid}/update", data={"field": "body", "value": "1-3: Sunny\n4-6: Rain"})
    assert upd.status_code == 200 and upd.json()["entry_count"] == 2 and upd.json()["effective_die"] == "d100"
    assert any("No entry for" in w for w in upd.json()["warnings"])
    roll = client.post(f"/api/tables/{tid}/roll").json()
    assert roll["table"] == "Weather" and "text" in roll and "steps" in roll
    assert len(client.get("/api/tables/history?game=rifts").json()) == 1
    assert client.post(f"/api/tables/{tid}/delete").json() == {"ok": True}
    assert client.post(f"/api/tables/{tid}/roll").status_code == 404


def test_route_errors_are_400s_with_a_message(client):
    bad = client.post("/api/tables", data={"game": "rifts", "name": ""})
    assert bad.status_code == 400 and "error" in bad.json()
    tid = client.post("/api/tables", data={"game": "rifts", "name": "T", "body": "a"}).json()["id"]
    bad = client.post(f"/api/tables/{tid}/update", data={"field": "die", "value": "banana"})
    assert bad.status_code == 400 and "banana" in bad.json()["error"]
    dup = client.post("/api/tables", data={"game": "rifts", "name": "t"})
    assert dup.status_code == 400


def test_everywhere_flag_makes_a_global_table(client):
    tid = client.post("/api/tables", data={"game": "rifts", "name": "G", "body": "a", "everywhere": 1}).json()["id"]
    assert rt.get_table(tid)["game_key"] == rt.GLOBAL_KEY
    assert "all games" in client.get("/tools/tables?game=dnd5e").text


def test_import_and_history_clear_routes(client):
    assert client.post("/api/tables/import-builtin", data={"game": "rifts"}).json()["added"] >= 6
    assert client.post("/api/tables/import-builtin", data={"game": "rifts"}).json()["added"] == 0
    tid = rt.list_tables("rifts")[0]["id"]
    client.post(f"/api/tables/{tid}/roll")
    assert client.post("/api/tables/history/clear", data={"game": "rifts"}).json() == {"ok": True}
    assert client.get("/api/tables/history?game=rifts").json() == []


def test_table_text_is_escaped_in_the_page(client):
    rt.create_table("rifts", "<img src=x>", "", "<script>alert(1)</script>")
    html = client.get("/tools/tables?game=rifts").text
    assert "<script>alert(1)</script>" not in html and "<img src=x>" not in html


def test_send_rolls_to_session_notes(client):
    tid = rt.create_table("rifts", "Weather", "", "Sunny")
    result = client.post(f"/api/tables/{tid}/roll").json()
    client.post("/api/screen/notes/append", data={"game": "rifts", "text": result["text"], "heading": result["table"]})
    assert gm_screen.get_notes("rifts") == "Weather: Sunny"


def test_encounter_page_has_send_to_notes_buttons(client):
    html = client.get("/tools/encounters?game=rifts").text
    assert "enc-notes-btn" in html and "loot-notes-btn" in html and '"rifts"' in html
