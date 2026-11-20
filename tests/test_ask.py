"""Tests for 'Ask the Library' (app/library/ask.py and its route): the model is
stubbed - these check what it is *given* (only retrieved pages, numbered,
trimmed) and how its answer is turned into cited sources."""
import pytest
from fastapi.testclient import TestClient

from app import database, llm
from app.library import ask

from .test_search import _add_book


@pytest.fixture
def ai_on(monkeypatch):
    """AI enabled, with generate() recording its prompts and returning a canned answer."""
    calls = []
    state = {"answer": "A grapple is an opposed check [1]. See also [2]."}
    monkeypatch.setattr(llm, "is_enabled", lambda: True)

    def fake_generate(system_prompt, user_prompt, max_tokens=1600):
        calls.append({"system": system_prompt, "user": user_prompt, "max_tokens": max_tokens})
        return state["answer"]

    monkeypatch.setattr(llm, "generate", fake_generate)
    return {"calls": calls, "state": state}


@pytest.fixture
def library(db):
    return {
        "rules": _add_book("Core Rules", ["Grapple: opposed Strength check to hold a foe.", "Unrelated page."], "dnd5e"),
        "other": _add_book("Other Game", ["Grapple rules differ in this system."], "rifts"),
    }


def test_ask_answers_from_retrieved_pages_and_marks_citations(ai_on, library):
    res = ask.ask("How does grapple work?", game_key="dnd5e", game_name="D&D 5E")
    assert res["answer"].startswith("A grapple is")
    assert res["cited"] == [1]  # [2] doesn't exist for a single-page game, so it's ignored
    assert [s["title"] for s in res["sources"]] == ["Core Rules"]
    assert res["sources"][0]["page_number"] == 1 and res["sources"][0]["cited"] is True
    prompt = ai_on["calls"][0]["user"]
    assert "[1] Core Rules, page 1:" in prompt and "Grapple: opposed Strength check" in prompt
    assert "Unrelated page" not in prompt  # never sends pages that didn't match
    assert "Other Game" not in prompt  # respects the game filter
    assert "Game: D&D 5E" in prompt and prompt.rstrip().endswith("Question: How does grapple work?")
    assert "ONLY" in ai_on["calls"][0]["system"]


def test_ask_without_a_game_searches_every_book(ai_on, library):
    res = ask.ask("grapple rules")
    assert {s["title"] for s in res["sources"]} == {"Core Rules", "Other Game"}


def test_uncited_sources_are_flagged(ai_on, library):
    ai_on["state"]["answer"] = "No citation here."
    res = ask.ask("grapple")
    assert res["cited"] == [] and all(not s["cited"] for s in res["sources"])


def test_filler_words_do_not_stop_a_match(ai_on, library):
    # "how does the ... work" appear in no page; only the content words matter
    assert ask.ask("How does the grapple thing work in this game?", game_key="dnd5e")["sources"]


def test_nothing_found_raises_404_without_calling_the_model(ai_on, db):
    _add_book("B", ["hello world"])
    with pytest.raises(ask.AskError) as e:
        ask.ask("quantum flux capacitor")
    assert e.value.status == 404 and ai_on["calls"] == []


def test_blank_and_overlong_questions_are_rejected(ai_on, library):
    with pytest.raises(ask.AskError):
        ask.ask("   ")
    with pytest.raises(ask.AskError):
        ask.ask("x" * (ask.MAX_QUESTION_CHARS + 1))
    assert ai_on["calls"] == []


def test_refuses_when_ai_is_not_enabled(monkeypatch, library):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    with pytest.raises(ask.AskError) as e:
        ask.ask("grapple")
    assert "Settings" in str(e.value)


def test_llm_failure_becomes_a_502(monkeypatch, library):
    monkeypatch.setattr(llm, "is_enabled", lambda: True)

    def boom(*a, **k):
        raise llm.LLMError("connection refused")

    monkeypatch.setattr(llm, "generate", boom)
    with pytest.raises(ask.AskError) as e:
        ask.ask("grapple")
    assert e.value.status == 502 and "connection refused" in str(e.value)


def test_empty_model_answer_is_reported(monkeypatch, library):
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "   ")
    with pytest.raises(ask.AskError) as e:
        ask.ask("grapple")
    assert e.value.status == 502


def test_long_pages_are_trimmed_around_the_match(ai_on, db):
    filler = "lorem ipsum " * 600
    _add_book("Big", [filler + "the grapple rule is here " + filler])
    res = ask.gather_sources("grapple")
    text = res[0]["text"]
    assert len(text) <= ask.MAX_CHARS_PER_PAGE + 4
    assert "grapple" in text and text.startswith("…") and text.endswith("…")


def test_at_most_max_pages_are_sent(ai_on, db):
    _add_book("Many", [f"grapple page {i}" for i in range(20)])
    assert len(ask.gather_sources("grapple")) == ask.MAX_PAGES


def test_cited_numbers_ignores_out_of_range_and_duplicates():
    assert ask.cited_numbers("a [2] b [2][1] c [9] d [0]", 3) == [2, 1]
    assert ask.cited_numbers(None, 3) == []


# ------------------------------------------------------------------ route

@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_route_returns_answer_and_sources(client, ai_on, library):
    res = client.post("/api/library/ask", data={"question": "grapple", "game": "dnd5e"})
    assert res.status_code == 200
    body = res.json()
    assert body["sources"][0]["title"] == "Core Rules" and body["cited"] == [1]


def test_route_maps_errors_to_status_codes(client, monkeypatch, db):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    assert client.post("/api/library/ask", data={"question": "x"}).status_code == 400
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    assert client.post("/api/library/ask", data={"question": "nothing matches zzqq"}).status_code == 404


def test_library_page_shows_ask_box_only_when_ai_is_on(client, monkeypatch, db):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    assert "ask-card" not in client.get("/library").text
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    assert "ask-card" in client.get("/library").text


def test_library_page_groups_paginates_and_escapes(client, db):
    _add_book("Bestiary", [f"dragon {i} <b>x</b>" for i in range(30)], "rifts")
    html = client.get("/library?game=rifts&q=dragon").text
    assert "30 matching pages" in html and "page 1 of 2" in html
    assert html.count('class="hit"') == 20  # one page of hits
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "q=dragon&amp;page=2" in html
    page2 = client.get("/library?game=rifts&q=dragon&page=2").text
    assert page2.count('class="hit"') == 10 and "Previous" in page2


def test_library_page_explains_the_any_fallback(client, db):
    _add_book("B", ["dragon here", "treasure there"], "rifts")
    html = client.get("/library?game=rifts&q=dragon+treasure").text
    assert "no page has every word" in html
