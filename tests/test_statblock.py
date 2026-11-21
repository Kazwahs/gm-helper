"""Stat-block extraction from a library page, and the pieces that use it."""
import pytest
from fastapi.testclient import TestClient

from app import database, llm, saved_encounters
from app.library import statblock
from app.tools import initiative

PAGE = """The caves are damp and dark.
GOBLIN
Small humanoid (goblinoid), neutral evil
Armor Class 15 (leather armor, shield)
Hit Points 7 (2d6)
Speed 30 ft.
STR 8 (-1) DEX 14 (+2) CON 10 (+0)
Challenge 1/4

Goblin Boss
Small humanoid (goblinoid), neutral evil
Armor Class 17 (chain shirt, shield)
Hit Points 21 (6d6)
STR 10 (+0) DEX 12 (+1)

The cleric can regain 7 hit points when she prays.
"""


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def book(db, games_config):
    """A book with the stat-block page indexed as page 12 (and a blank page 13)."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO books (path, filename, title, publisher, series, game_key) "
                    "VALUES ('P/S/mm.pdf', 'mm.pdf', 'Monster Manual', 'P', 'S', 'rifts')")
        book_id = cur.lastrowid
        for page, text in ((12, PAGE), (13, "   "), (14, "Just lore. Nothing with numbers here.")):
            cur.execute("INSERT INTO book_pages_fts (content, book_id, page_number, book_title) VALUES (?, ?, ?, 'Monster Manual')",
                        (text, book_id, page))
    return book_id


# ---------------------------------------------------------------- text scan

def test_text_scan_finds_each_creature_with_its_own_numbers():
    found = statblock.extract_text(PAGE)
    assert [(c["name"], c["hp"], c["ac"], c["init_bonus"], c["initiative"]) for c in found] == [
        ("Goblin", 7, 15, 2, "1d20+2"),
        ("Goblin Boss", 21, 17, 1, "1d20+1"),
    ]


def test_text_scan_handles_a_compact_one_line_block():
    found = statblock.extract_text("Wolf\nInitiative: +2 AC 13 HP 11\n")
    assert found == [{"name": "Wolf", "hp": 11, "ac": 13, "init_bonus": 2, "initiative": "1d20+2"}]


def test_text_scan_ignores_prose_and_missing_numbers():
    assert statblock.extract_text("Hit Points are tracked on your sheet. You regain 7 hit points.") == []
    assert statblock.extract_text("") == [] and statblock.extract_text(None) == []


def test_negative_and_zero_bonuses_become_dice():
    block = "Slug\nHit Points 9\nDEX 6 (-2)\n\nRat\nHit Points 2\nDEX 10 (+0)\n"
    inits = {c["name"]: c["initiative"] for c in statblock.extract_text(block)}
    assert inits == {"Slug": "1d20-2", "Rat": "1d20"}


def test_unnamed_blocks_get_a_placeholder_name():
    found = statblock.extract_text("Hit Points 30")
    assert found[0]["name"] == "Creature 1" and found[0]["hp"] == 30


def test_the_scan_is_capped():
    text = "\n".join(f"Foe{i}\nHit Points {i + 1}\n" for i in range(40))
    assert len(statblock.extract_text(text)) == statblock.MAX_CREATURES


# ---------------------------------------------------------------- AI reply parsing

def test_parse_ai_lines():
    reply = "- **Goblin** | 7 | 15 | +2\nGOBLIN BOSS | 21 (6d6) | 17 | ?\nNAME | HP | AC | INITIATIVE BONUS\nBad|abc|x|y\nplain chatter"
    got = statblock.parse_ai_lines(reply)
    assert [(c["name"], c["hp"], c["ac"], c["initiative"]) for c in got] == [
        ("Goblin", 7, 15, "1d20+2"), ("Goblin Boss", 21, 17, "1d20")]


def test_parse_ai_none_and_garbage():
    assert statblock.parse_ai_lines("NONE") == []
    assert statblock.parse_ai_lines("") == [] and statblock.parse_ai_lines(None) == []
    # out-of-range numbers are dropped rather than trusted
    got = statblock.parse_ai_lines("Titan | 999999999 | 500 | +99")
    assert got == []


# ---------------------------------------------------------------- extract()

def test_extract_uses_the_text_scan_without_ai(book, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    res = statblock.extract(book, 12)
    assert res["method"] == "text" and res["book"] == "Monster Manual" and res["page"] == 12
    assert [c["name"] for c in res["creatures"]] == ["Goblin", "Goblin Boss"]


def test_extract_prefers_ai_and_sends_only_that_page(book, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    seen = {}

    def fake(system_prompt, user_prompt, max_tokens=0):
        seen["system"], seen["user"] = system_prompt, user_prompt
        return "Goblin | 7 | 15 | +2"

    monkeypatch.setattr(llm, "generate", fake)
    res = statblock.extract(book, 12, game_name="Rifts")
    assert res["method"] == "ai" and [c["name"] for c in res["creatures"]] == ["Goblin"]
    assert "Game: Rifts" in seen["user"] and "damp and dark" in seen["user"]
    assert "Never invent numbers" in seen["system"]


def test_extract_falls_back_when_the_model_fails_or_finds_nothing(book, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: True)

    def boom(*a, **k):
        raise llm.LLMError("down")

    monkeypatch.setattr(llm, "generate", boom)
    assert statblock.extract(book, 12)["method"] == "text"
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "NONE")
    assert statblock.extract(book, 12)["method"] == "text"


def test_extract_errors(book, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    with pytest.raises(ValueError, match="no longer exists"):
        statblock.extract(99999, 1)
    with pytest.raises(ValueError, match="no searchable text"):
        statblock.extract(book, 13)
    with pytest.raises(ValueError, match="no searchable text"):
        statblock.extract(book, 99)
    with pytest.raises(ValueError, match="couldn't find a stat block"):
        statblock.extract(book, 14)


# ---------------------------------------------------------------- endpoints & page

def test_statblock_endpoint(client, book, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    res = client.post("/api/library/statblock", data={"book_id": book, "page_number": 12, "game": "rifts"})
    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "text" and len(body["creatures"]) == 2
    bad = client.post("/api/library/statblock", data={"book_id": book, "page_number": 14})
    assert bad.status_code == 404 and "stat block" in bad.json()["error"]


def test_a_creature_can_go_straight_onto_the_board(client, book, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    c = client.post("/api/library/statblock", data={"book_id": book, "page_number": 12}).json()["creatures"][0]
    res = client.post("/api/initiative/add", data={
        "game": "rifts", "name": c["name"], "initiative": c["initiative"], "hp": c["hp"], "quantity": 3})
    assert res.status_code == 200
    board = initiative.get_state("rifts")["combatants"]
    assert sorted(x["name"] for x in board) == ["Goblin 1", "Goblin 2", "Goblin 3"] and board[0]["hp"] == 7


def test_add_foe_to_a_saved_encounter(client, db, games_config):
    enc = saved_encounters.create_encounter("rifts", "Ambush", "Boss (hp 21)")
    res = client.post(f"/api/initiative/encounters/{enc}/add-foe",
                      data={"name": "Goblin", "quantity": "4", "hp": "7", "initiative": "1d20+2"})
    assert res.status_code == 200
    assert res.json()["encounter"]["body"] == "Boss (hp 21)\n4x Goblin (hp 7, init 1d20+2)"
    # blanks are fine: no hp, default initiative
    client.post(f"/api/initiative/encounters/{enc}/add-foe", data={"name": "Rat"})
    assert saved_encounters.get_encounter(enc)["body"].endswith("\nRat")


@pytest.mark.parametrize("data", [
    {"name": " "}, {"name": "X", "quantity": "0"}, {"name": "X", "quantity": "99"},
    {"name": "X", "hp": "0"}, {"name": "X", "initiative": "banana"},
])
def test_add_foe_validation(client, db, games_config, data):
    enc = saved_encounters.create_encounter("rifts", "Ambush", "")
    assert client.post(f"/api/initiative/encounters/{enc}/add-foe", data=data).status_code == 400
    assert client.post("/api/initiative/encounters/99999/add-foe", data={"name": "X"}).status_code == 400
    assert saved_encounters.get_encounter(enc)["entries"] == []


def test_library_results_offer_the_stat_block_button(client, book):
    html = client.get("/library?q=goblin&game=rifts").text
    assert 'class="secondary statblock-btn"' in html and 'data-game-key="rifts"' in html
    assert "/api/library/statblock" in html
