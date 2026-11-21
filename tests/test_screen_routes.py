"""Route tests for the GM Screen additions: per-system seeding, the
'starter cards' button, pinning book pages and sending text to the notes."""
import pytest
from fastapi.testclient import TestClient

from app import database


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _titles(client, game):
    html = client.get(f"/screen?game={game}").text
    return html


def test_screen_page_seeds_the_games_own_system_cards(client):
    # the test games config marks `rifts` as a palladium game
    html = client.get("/screen?game=rifts").text
    assert "Melee Round" in html
    assert "S.D.C." in html
    assert "Bennies" not in html


def test_global_screen_gets_generic_cards(client):
    html = client.get("/screen").text
    assert "Combat Checklist" in html
    assert "Melee Round" not in html


def test_add_missing_endpoint_restores_deleted_starter_cards(client):
    client.get("/screen?game=rifts")
    from app import gm_screen

    cards = gm_screen.list_cards("rifts", "palladium")
    client.post(f"/api/screen/cards/{cards[0]['id']}/delete")
    res = client.post("/api/screen/add-missing", data={"game": "rifts"})
    assert res.status_code == 200 and res.json() == {"added": 1}
    assert client.post("/api/screen/add-missing", data={"game": "rifts"}).json() == {"added": 0}


@pytest.fixture
def book_id(db):
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO books (path, filename, title, publisher, series, game_key) "
            "VALUES ('P/S/b.pdf', 'b.pdf', 'Core Rules', 'P', 'S', 'rifts')"
        )
        return cur.lastrowid


def test_pin_endpoint_creates_a_card_that_links_to_the_page(client, book_id):
    res = client.post("/api/screen/pin", data={"game": "rifts", "book_id": book_id, "page_number": 77})
    assert res.status_code == 200
    card = res.json()
    assert card["title"] == "Core Rules - p.77"
    html = client.get("/screen?game=rifts").text
    assert f"/books/{book_id}/file#page=77" in html
    assert "screen-card-pinned" in html


def test_pin_endpoint_rejects_a_missing_book(client):
    res = client.post("/api/screen/pin", data={"game": "rifts", "book_id": 999, "page_number": 1})
    assert res.status_code == 400 and "error" in res.json()


def test_append_notes_endpoint(client):
    res = client.post("/api/screen/notes/append", data={"game": "rifts", "text": "2d6 orcs", "heading": "Encounter"})
    assert res.status_code == 200
    assert res.json()["notes"] == "Encounter: 2d6 orcs"
    client.post("/api/screen/notes/append", data={"game": "rifts", "text": "A key"})
    assert "Encounter: 2d6 orcs\n\nA key" in client.get("/screen?game=rifts").text


def test_append_notes_endpoint_rejects_empty_text(client):
    res = client.post("/api/screen/notes/append", data={"game": "rifts", "text": "  "})
    assert res.status_code == 400


def test_card_text_is_escaped_in_the_page(client):
    card = client.post(
        "/api/screen/cards", data={"game": "rifts", "title": "<b>x</b>", "body": "<script>alert(1)</script>"}
    ).json()
    html = client.get("/screen?game=rifts").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert card["id"]


# ---- card titles wrap instead of being cut off; the screen prints cleanly ----

def test_card_titles_are_wrapping_textareas_not_single_line_inputs(client):
    html = client.get("/screen?game=rifts").text
    assert 'textarea class="screen-card-title"' in html
    assert 'input type="text" class="screen-card-title"' not in html


def test_title_edits_are_collapsed_to_one_tidy_line(client):
    card = client.post("/api/screen/cards", data={"game": "rifts", "title": "T", "body": ""}).json()
    r = client.post(f"/api/screen/cards/{card['id']}", data={"field": "title", "value": "  Melee   round\n& attack  "})
    assert r.status_code == 200
    from app import gm_screen
    assert gm_screen.get_card(card["id"])["title"] == "Melee round & attack"


def test_title_is_length_capped_and_cannot_be_blank(client):
    card = client.post("/api/screen/cards", data={"game": "rifts", "title": "T", "body": ""}).json()
    client.post(f"/api/screen/cards/{card['id']}", data={"field": "title", "value": "x" * 500})
    from app import gm_screen
    assert len(gm_screen.get_card(card["id"])["title"]) == 200
    r = client.post(f"/api/screen/cards/{card['id']}", data={"field": "title", "value": " \n "})
    assert r.status_code == 400


def test_screen_has_a_print_button_and_print_styles(client):
    assert 'id="print-btn"' in client.get("/screen?game=rifts").text
    css = client.get("/static/style.css").text
    assert "@media print" in css and ".screen-card-actions" in css and "column-count: 2" in css
