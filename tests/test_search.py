"""Tests for library full-text search (app/library/search.py): query
parsing, AND-then-OR widening, phrases, escaping of snippets, pagination and
grouping by book."""
import pytest

from app import database
from app.library import search


def _add_book(title, pages, game_key="rifts", path=None):
    """Inserts a book plus FTS rows for `pages` (list of page text)."""
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO books (path, filename, title, publisher, series, game_key, has_text) "
            "VALUES (?, ?, ?, 'Pub', 'Series', ?, 1)",
            (path or f"Pub/Series/{title}.pdf", f"{title}.pdf", title, game_key),
        )
        book_id = cur.lastrowid
        for i, text in enumerate(pages, start=1):
            cur.execute(
                "INSERT INTO book_pages_fts (content, book_id, page_number, book_title) VALUES (?, ?, ?, ?)",
                (text, book_id, i, title),
            )
    return book_id


# ---------------------------------------------------------------- parsing

def test_parse_terms_splits_words_and_keeps_quoted_phrases():
    assert search.parse_terms('dragon "fire breath" lair') == ["dragon", "fire breath", "lair"]


def test_parse_terms_drops_punctuation_only_tokens_and_stray_quotes():
    assert search.parse_terms('dragon - ?? " lair') == ["dragon", "lair"]
    assert search.parse_terms("   ") == []
    assert search.parse_terms(None) == []


def test_parse_terms_caps_the_number_of_terms():
    assert len(search.parse_terms(" ".join(f"w{i}" for i in range(50)))) == search.MAX_TERMS


def test_question_to_terms_drops_filler_words():
    assert search.question_to_terms("How does the grapple rule work?") == ["grapple", "rule", "work?"]
    # all filler -> keep the original rather than searching for nothing
    assert search.question_to_terms("what is it") == ["what", "is", "it"]


# ------------------------------------------------------------ basic search

def test_single_word_search_finds_matching_pages(db):
    _add_book("Monsters", ["A red dragon sleeps here.", "Goblins everywhere."])
    res = search.search_page("dragon")
    assert res["total"] == 1 and res["mode"] == "all"
    hit = res["results"][0]
    assert hit["title"] == "Monsters" and hit["page_number"] == 1
    assert "<mark>dragon</mark>" in hit["snippet"]


def test_porter_stemming_matches_word_forms(db):
    _add_book("Rules", ["Characters can grapple opponents."])
    assert search.search_page("grappling")["total"] == 1


def test_all_terms_are_required_first(db):
    _add_book("B", ["dragon lair treasure", "dragon only", "treasure only"])
    res = search.search_page("dragon treasure")
    assert res["mode"] == "all" and res["total"] == 1
    assert res["results"][0]["page_number"] == 1


def test_widens_to_any_term_when_nothing_has_them_all(db):
    _add_book("B", ["dragon only here", "treasure only here"])
    res = search.search_page("dragon treasure")
    assert res["mode"] == "any" and res["total"] == 2


def test_no_match_reports_nothing(db):
    _add_book("B", ["hello"])
    res = search.search_page("zzzzqqq")
    assert res["total"] == 0 and res["results"] == [] and res["pages"] == 0


def test_empty_query_returns_empty(db):
    res = search.search_page("   ")
    assert res["total"] == 0 and res["mode"] is None
    assert search.search("") == []


def test_quoted_phrase_must_match_exactly(db):
    _add_book("B", ["the fire breath weapon", "breath of fire and ice"])
    res = search.search_page('"fire breath"')
    assert res["total"] == 1 and res["results"][0]["page_number"] == 1


def test_game_filter(db):
    _add_book("Rifts Book", ["dragon"], game_key="rifts")
    _add_book("DnD Book", ["dragon"], game_key="dnd5e")
    assert search.search_page("dragon", game_key="rifts")["total"] == 1
    assert search.search_page("dragon")["total"] == 2


@pytest.mark.parametrize("nasty", ['AND', 'OR NOT', 'dragon*', 'a:b', '(x', 'x)', "it's", 'c++', '"', '^dragon', 'NEAR(a b)'])
def test_odd_input_never_raises(db, nasty):
    _add_book("B", ["dragon and it's a c++ thing"])
    search.search_page(nasty)
    search.search(nasty)


# --------------------------------------------------------------- escaping

def test_snippet_html_is_escaped_but_highlights_survive(db):
    _add_book("B", ["<script>alert('x')</script> dragon <b>bold</b> & more"])
    hit = search.search_page("dragon")["results"][0]
    assert "<script>" not in hit["snippet"] and "<b>" not in hit["snippet"]
    assert "&lt;script&gt;" in hit["snippet"] and "&amp;" in hit["snippet"]
    assert "<mark>dragon</mark>" in hit["snippet"]


def test_flat_search_snippets_are_escaped_too(db):
    _add_book("B", ["<img src=x onerror=1> dragon"])
    snippet = search.search("dragon")[0]["snippet"]
    assert "<img" not in snippet and "&lt;img" in snippet


# ------------------------------------------------------------ pagination

def test_pagination_splits_results_and_clamps_page(db):
    _add_book("Big", [f"dragon page {i}" for i in range(1, 8)])
    p1 = search.search_page("dragon", per_page=3, page=1)
    p3 = search.search_page("dragon", per_page=3, page=3)
    assert p1["total"] == 7 and p1["pages"] == 3 and len(p1["results"]) == 3
    assert len(p3["results"]) == 1
    seen = {r["page_number"] for r in p1["results"]}
    assert not seen & {r["page_number"] for r in p3["results"]}
    # asking past the end clamps to the last page; nonsense falls back to page 1
    assert search.search_page("dragon", per_page=3, page=99)["page"] == 3
    assert search.search_page("dragon", per_page=3, page=0)["page"] == 1


def test_flat_search_limit_and_offset(db):
    _add_book("Big", [f"dragon {i}" for i in range(5)])
    assert len(search.search("dragon", limit=2)) == 2
    assert len(search.search("dragon", limit=10, offset=4)) == 1


def test_with_text_returns_the_full_page_text(db):
    _add_book("B", ["the dragon " + "x " * 500])
    plain = search.search_page("dragon")["results"][0]
    full = search.search_page("dragon", with_text=True)["results"][0]
    assert "text" not in plain and full["text"].startswith("the dragon")


def test_any_mode_ranks_pages_with_more_terms_first(db):
    _add_book("B", ["grapple", "grapple rules chart", "unrelated chart"])
    res = search.search_page("x", mode="any", terms=["grapple", "rules"])
    assert res["results"][0]["page_number"] == 2


# ---------------------------------------------------------------- grouping

def test_group_by_book_keeps_best_book_first_and_collects_hits(db):
    a = _add_book("A", ["dragon", "nothing", "dragon dragon dragon"])
    b = _add_book("B", ["dragon"])
    groups = search.group_by_book(search.search_page("dragon")["results"])
    assert {g["book_id"] for g in groups} == {a, b}
    group_a = next(g for g in groups if g["book_id"] == a)
    assert sorted(h["page_number"] for h in group_a["hits"]) == [1, 3]
    assert groups[0]["hits"][0]["rank"] <= groups[-1]["hits"][0]["rank"] or len(groups) == 1


def test_search_stats(db):
    _add_book("A", ["x"], game_key="rifts")
    _add_book("B", ["y"], game_key="dnd5e")
    assert search.search_stats() == {"total": 2, "searchable": 2}
    assert search.search_stats("rifts") == {"total": 1, "searchable": 1}
