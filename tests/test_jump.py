"""Quick jump (Ctrl+K): what /api/jump finds, how it ranks, and that the page
carries the palette and the anchors it links to."""
import pytest
from fastapi.testclient import TestClient

from app import bookmarks, campaigns, jump, maps_store, random_tables


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def world(db, games_config, a_book):
    """One campaign with a bit of everything, a map, a table and a bookmark."""
    cid = campaigns.create_campaign("rifts", "Shadows over Chi-Town")
    npc = campaigns.add_npc(cid, "Marlowe Vance", role="Fixer", motivation="Wants the ledger back")
    loc = campaigns.add_location(cid, "Old Dockside Warehouse", description="Smugglers meet here")
    fac = campaigns.add_faction(cid, "Coalition Spies", goal="Control the docks")
    thread = campaigns.add_thread(cid, "The missing ledger", description="Who took it?")
    sess = campaigns.add_session(cid)
    campaigns.update_session_field(sess, "title", "Night at the docks")
    map_id = maps_store.save_map("rifts", "urban", "modern", "Chi-Town docks", "<svg></svg>")
    table = random_tables.create_table("rifts", "Dock rumours", "d6", "1: a\n2: b\n3: c\n4: d\n5: e\n6: f")
    shared = random_tables.create_table("_global", "Weather today", "d4", "1: sun\n2: rain\n3: fog\n4: gale")
    bm = bookmarks.add_bookmark(bookmarks.get_or_create_collection("Favourites"), a_book, 42, "Power armour rules")
    return dict(cid=cid, npc=npc, loc=loc, fac=fac, thread=thread, sess=sess, map=map_id,
                table=table, shared=shared, bm=bm, book=a_book)


def _by_kind(results, kind):
    return [r for r in results if r["kind"] == kind]


# --------------------------------------------------------------- finding things

def test_finds_campaign_by_title(world):
    hits = _by_kind(jump.search("chi-town"), "campaign")
    assert [h["href"] for h in hits] == [f"/campaigns/{world['cid']}"]
    assert hits[0]["detail"] == "Rifts"


def test_finds_cast_places_factions_threads_sessions(world):
    cid = world["cid"]
    assert _by_kind(jump.search("marlowe"), "npc")[0]["href"] == f"/campaigns/{cid}#npc-{world['npc']}"
    assert _by_kind(jump.search("warehouse"), "location")[0]["href"] == f"/campaigns/{cid}#location-{world['loc']}"
    assert _by_kind(jump.search("coalition"), "faction")[0]["href"] == f"/campaigns/{cid}#faction-{world['fac']}"
    assert _by_kind(jump.search("ledger"), "thread")[0]["href"] == f"/campaigns/{cid}#thread-{world['thread']}"
    sess = _by_kind(jump.search("night at the"), "session")[0]
    assert sess["href"] == f"/campaigns/{cid}#session-{world['sess']}"
    assert sess["label"].startswith("Session 1")


def test_finds_text_in_other_fields_not_just_names(world):
    # "fixer" is Marlowe's role, "smugglers" is in the warehouse description
    assert _by_kind(jump.search("fixer"), "npc")
    assert _by_kind(jump.search("smugglers"), "location")


def test_every_typed_word_must_match(world):
    assert _by_kind(jump.search("marlowe fixer"), "npc")
    assert not _by_kind(jump.search("marlowe zebra"), "npc")


def test_finds_map_table_and_bookmark(world):
    m = _by_kind(jump.search("docks"), "map")[0]
    assert m["href"] == f"/maps/{world['map']}"
    t = _by_kind(jump.search("rumours"), "table")[0]
    assert t["href"] == f"/tools/tables?game=rifts#table-{world['table']}"
    b = _by_kind(jump.search("power armour"), "bookmark")[0]
    assert b["href"] == f"/books/{world['book']}/file#page=42"
    assert "p. 42" in b["detail"]


def test_shared_tables_link_with_the_current_game(world):
    with_game = _by_kind(jump.search("weather", game_key="rifts"), "table")[0]
    assert with_game["href"] == f"/tools/tables?game=rifts#table-{world['shared']}"
    assert with_game["detail"] == "Every game"
    without = _by_kind(jump.search("weather"), "table")[0]
    assert without["href"] == f"/tools/tables#table-{world['shared']}"


def test_finds_tool_pages(world):
    hits = _by_kind(jump.search("initiative", game_key="rifts"), "page")
    assert hits and hits[0]["href"] == "/tools/initiative?game=rifts"
    assert _by_kind(jump.search("settings"), "page")[0]["href"] == "/settings"


def test_match_is_case_insensitive_and_ignores_wildcards(world):
    assert _by_kind(jump.search("MARLOWE"), "npc")
    # a literal % or _ must not behave like a wildcard that matches everything
    assert not _by_kind(jump.search("%"), "npc")
    assert not _by_kind(jump.search("_"), "npc")
    assert not _by_kind(jump.search("mar_owe"), "npc")


def test_quotes_and_sql_in_the_query_are_just_text(world):
    assert not _by_kind(jump.search("' OR 1=1 --"), "npc")


# --------------------------------------------------------------- ranking & shape

def test_library_search_is_always_the_last_choice(world):
    results = jump.search("power armour", game_key="rifts")
    assert results[-1]["kind"] == "library"
    assert results[-1]["href"] == "/library?q=power%20armour&game=rifts"
    assert jump.search("zzzz nothing")[-1]["href"] == "/library?q=zzzz%20nothing"
    assert len(jump.search("zzzz nothing")) == 1


def test_names_that_start_with_the_text_come_first(world):
    campaigns.create_campaign("rifts", "The Vance Affair")           # contains "vance"
    campaigns.add_npc(world["cid"], "Vance Marlowe")                  # starts with "vance"
    results = jump.search("vance")
    labels = [r["label"] for r in results if r["kind"] in ("npc", "campaign")]
    assert labels[0] == "Vance Marlowe"
    assert "Marlowe Vance" in labels and "The Vance Affair" in labels


def test_current_game_wins_ties(db, games_config):
    campaigns.create_campaign("dnd5e", "Lost Mine")
    campaigns.create_campaign("rifts", "Lost Cause")
    in_rifts = [r["label"] for r in _by_kind(jump.search("lost", game_key="rifts"), "campaign")]
    in_dnd = [r["label"] for r in _by_kind(jump.search("lost", game_key="dnd5e"), "campaign")]
    assert in_rifts == ["Lost Cause", "Lost Mine"]
    assert in_dnd == ["Lost Mine", "Lost Cause"]


def test_one_kind_cannot_fill_the_whole_list(db, games_config):
    cid = campaigns.create_campaign("rifts", "Many NPCs")
    for i in range(20):
        campaigns.add_npc(cid, f"Guard {i}")
    results = jump.search("guard")
    assert len(_by_kind(results, "npc")) == jump.PER_KIND
    assert len(results) <= jump.MAX_RESULTS


def test_results_have_only_public_fields(world):
    for r in jump.search("a"):
        assert set(r) == {"kind", "kind_label", "label", "detail", "href"}


def test_empty_query_offers_pages_and_recent_work(world):
    results = jump.search("", game_key="rifts")
    kinds = {r["kind"] for r in results}
    assert "page" in kinds and "campaign" in kinds and "map" in kinds
    assert "library" not in kinds


def test_unknown_game_is_ignored(world):
    assert _by_kind(jump.search("marlowe", game_key="not-a-game"), "npc")


def test_long_query_is_trimmed(world):
    assert jump.search("x" * 5000)[-1]["kind"] == "library"
    assert len(jump.search("x" * 5000)[-1]["label"]) < 200


# --------------------------------------------------------------- HTTP & page

def test_api_returns_json(client, world):
    res = client.get("/api/jump", params={"q": "marlowe", "game": "rifts"})
    assert res.status_code == 200
    kinds = [r["kind"] for r in res.json()["results"]]
    assert "npc" in kinds and kinds[-1] == "library"


def test_api_without_query_works(client, world):
    assert client.get("/api/jump").status_code == 200


def test_pages_carry_the_palette_and_script(client, world):
    html = client.get("/").text
    assert 'id="jump-overlay"' in html and 'id="jump-input"' in html and 'id="jump-btn"' in html
    js = client.get("/static/app.js").text
    assert "/api/jump" in js and "metaKey" in js


def test_palette_markup_is_accessible(client, world):
    html = client.get("/").text
    assert 'role="dialog"' in html and 'aria-modal="true"' in html
    assert 'role="combobox"' in html and 'role="listbox"' in html


def test_campaign_page_has_the_anchors_jump_links_to(client, world):
    html = client.get(f"/campaigns/{world['cid']}").text
    for anchor in (f"npc-{world['npc']}", f"location-{world['loc']}", f"faction-{world['fac']}",
                   f"thread-{world['thread']}", f"session-{world['sess']}"):
        assert f'id="{anchor}"' in html


def test_tables_page_has_the_anchor(client, world):
    assert f'id="table-{world["table"]}"' in client.get("/tools/tables?game=rifts").text
