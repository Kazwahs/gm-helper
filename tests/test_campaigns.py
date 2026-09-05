"""Tests for the campaign planner (app/campaigns.py): campaigns themselves,
their auto-created bookmark collection, and each sub-resource (threads,
cast/npcs, locations - including the parent-cycle guard, factions,
sessions, materials)."""
import pytest

from app import bookmarks, campaigns


# ------------------------------------------------------------- campaigns
def test_create_campaign_requires_a_title(db):
    with pytest.raises(ValueError):
        campaigns.create_campaign("unsorted", "   ")


def test_create_campaign_gets_its_own_bookmark_collection(db):
    campaign_id = campaigns.create_campaign("unsorted", "The Sunken Crown")
    campaign = campaigns.get_campaign(campaign_id)
    assert campaign["bookmark_collection_id"] is not None
    collection_names = [c["name"] for c in bookmarks.list_collections()]
    assert "Campaign: The Sunken Crown" in collection_names


def test_create_campaign_handles_bookmark_collection_name_collision(db):
    # A collection with the exact name a second campaign would want already
    # exists (e.g. from a previous campaign of the same title) - creation
    # must still succeed by disambiguating instead of raising.
    bookmarks.create_collection("Campaign: Reused Title")
    campaign_id = campaigns.create_campaign("unsorted", "Reused Title")
    campaign = campaigns.get_campaign(campaign_id)
    assert campaign["bookmark_collection_id"] is not None


def test_list_campaigns_filters_by_game_key(db):
    campaigns.create_campaign("game_a", "A's Campaign")
    campaigns.create_campaign("game_b", "B's Campaign")
    assert [c["title"] for c in campaigns.list_campaigns(game_key="game_a")] == ["A's Campaign"]
    assert len(campaigns.list_campaigns()) == 2


def test_update_campaign_field_unknown_field_raises(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    with pytest.raises(ValueError):
        campaigns.update_campaign_field(campaign_id, "not_a_real_field", "x")


def test_update_campaign_title_cannot_be_blanked(db):
    campaign_id = campaigns.create_campaign("unsorted", "Has A Title")
    with pytest.raises(ValueError):
        campaigns.update_campaign_field(campaign_id, "title", "   ")


def test_update_campaign_title_renames_its_bookmark_collection_too(db):
    campaign_id = campaigns.create_campaign("unsorted", "Original Title")
    campaigns.update_campaign_field(campaign_id, "title", "Renamed Title")
    collection_names = [c["name"] for c in bookmarks.list_collections()]
    assert "Campaign: Renamed Title" in collection_names
    assert "Campaign: Original Title" not in collection_names


def test_update_campaign_pitch_and_status(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    campaigns.update_campaign_field(campaign_id, "pitch", "A grim tale")
    campaigns.update_campaign_field(campaign_id, "status", "active")
    campaign = campaigns.get_campaign(campaign_id)
    assert campaign["pitch"] == "A grim tale"
    assert campaign["status"] == "active"


def test_delete_campaign_also_deletes_its_bookmark_collection(db):
    campaign_id = campaigns.create_campaign("unsorted", "Doomed")
    campaign = campaigns.get_campaign(campaign_id)
    collection_id = campaign["bookmark_collection_id"]
    campaigns.delete_campaign(campaign_id)
    assert campaigns.get_campaign(campaign_id) is None
    assert all(c["id"] != collection_id for c in bookmarks.list_collections())


def test_delete_campaign_cascades_its_threads_npcs_factions_locations_sessions(db):
    campaign_id = campaigns.create_campaign("unsorted", "Full Campaign")
    campaigns.add_thread(campaign_id, "A thread")
    campaigns.add_npc(campaign_id, "An NPC")
    campaigns.add_faction(campaign_id, "A faction")
    campaigns.add_location(campaign_id, "A location")
    campaigns.add_session(campaign_id, "Session 1")

    campaigns.delete_campaign(campaign_id)

    assert campaigns.list_threads(campaign_id) == []
    assert campaigns.list_npcs(campaign_id) == []
    assert campaigns.list_factions(campaign_id) == []
    assert campaigns.locations_tree(campaign_id) == []
    assert campaigns.list_sessions(campaign_id) == []


# --------------------------------------------------------------- threads
def test_add_thread_requires_a_title(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    with pytest.raises(ValueError):
        campaigns.add_thread(campaign_id, "")


def test_thread_defaults_and_update(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    thread_id = campaigns.add_thread(campaign_id, "The missing envoy")
    thread = campaigns.list_threads(campaign_id)[0]
    assert thread["status"] == "seed"
    campaigns.update_thread_field(thread_id, "status", "active")
    campaigns.update_thread_field(thread_id, "stakes", "war breaks out")
    thread = campaigns.list_threads(campaign_id)[0]
    assert thread["status"] == "active"
    assert thread["stakes"] == "war breaks out"


def test_delete_thread(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    thread_id = campaigns.add_thread(campaign_id, "Gone soon")
    campaigns.delete_thread(thread_id)
    assert campaigns.list_threads(campaign_id) == []


# ------------------------------------------------------------------ npcs
def test_add_npc_requires_a_name(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    with pytest.raises(ValueError):
        campaigns.add_npc(campaign_id, "  ")


def test_npc_defaults_and_update(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    npc_id = campaigns.add_npc(campaign_id, "Baron Voss")
    npc = campaigns.list_npcs(campaign_id)[0]
    assert npc["disposition"] == "neutral"
    campaigns.update_npc_field(npc_id, "disposition", "villain")
    campaigns.update_npc_field(npc_id, "secret", "is a doppelganger")
    npc = campaigns.list_npcs(campaign_id)[0]
    assert npc["disposition"] == "villain"
    assert npc["secret"] == "is a doppelganger"


def test_update_npc_unknown_field_raises(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    npc_id = campaigns.add_npc(campaign_id, "Someone")
    with pytest.raises(ValueError):
        campaigns.update_npc_field(npc_id, "hit_points", 10)


# ------------------------------------------------------------- locations
def test_add_location_requires_a_name(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    with pytest.raises(ValueError):
        campaigns.add_location(campaign_id, "")


def test_locations_tree_orders_children_under_parents(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    region_id = campaigns.add_location(campaign_id, "The Region")
    city_id = campaigns.add_location(campaign_id, "A City", parent_id=region_id)
    campaigns.add_location(campaign_id, "A District", parent_id=city_id)

    tree = campaigns.locations_tree(campaign_id)
    by_name = {loc["name"]: loc for loc in tree}
    assert by_name["The Region"]["depth"] == 0
    assert by_name["A City"]["depth"] == 1
    assert by_name["A District"]["depth"] == 2


def test_location_cannot_be_its_own_parent(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    location_id = campaigns.add_location(campaign_id, "Self")
    with pytest.raises(ValueError):
        campaigns.update_location_field(location_id, "parent_id", str(location_id))


def test_location_cannot_be_moved_inside_its_own_descendant(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    parent_id = campaigns.add_location(campaign_id, "Parent")
    child_id = campaigns.add_location(campaign_id, "Child", parent_id=parent_id)
    grandchild_id = campaigns.add_location(campaign_id, "Grandchild", parent_id=child_id)
    # Moving "Parent" under its own grandchild would create a cycle.
    with pytest.raises(ValueError):
        campaigns.update_location_field(parent_id, "parent_id", str(grandchild_id))


def test_location_can_be_reparented_to_a_non_cyclic_target(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    a = campaigns.add_location(campaign_id, "A")
    b = campaigns.add_location(campaign_id, "B")
    campaigns.update_location_field(b, "parent_id", str(a))
    tree = {loc["name"]: loc for loc in campaigns.locations_tree(campaign_id)}
    assert tree["B"]["depth"] == 1


def test_location_parent_id_can_be_cleared_back_to_top_level(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    a = campaigns.add_location(campaign_id, "A")
    b = campaigns.add_location(campaign_id, "B", parent_id=a)
    campaigns.update_location_field(b, "parent_id", "")
    tree = {loc["name"]: loc for loc in campaigns.locations_tree(campaign_id)}
    assert tree["B"]["depth"] == 0


# -------------------------------------------------------------- factions
def test_add_faction_requires_a_name(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    with pytest.raises(ValueError):
        campaigns.add_faction(campaign_id, "")


def test_faction_update_and_delete(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    faction_id = campaigns.add_faction(campaign_id, "The Ashen Concord")
    campaigns.update_faction_field(faction_id, "goal", "control the docks")
    faction = campaigns.list_factions(campaign_id)[0]
    assert faction["goal"] == "control the docks"
    campaigns.delete_faction(faction_id)
    assert campaigns.list_factions(campaign_id) == []


# -------------------------------------------------------------- sessions
def test_sessions_auto_number_sequentially(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    s1 = campaigns.add_session(campaign_id, "First")
    s2 = campaigns.add_session(campaign_id, "Second")
    sessions_by_id = {s["id"]: s for s in campaigns.list_sessions(campaign_id)}
    assert sessions_by_id[s1]["session_number"] == 1
    assert sessions_by_id[s2]["session_number"] == 2


def test_sessions_list_newest_number_first(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    campaigns.add_session(campaign_id, "First")
    campaigns.add_session(campaign_id, "Second")
    titles_in_order = [s["title"] for s in campaigns.list_sessions(campaign_id)]
    assert titles_in_order == ["Second", "First"]


def test_update_session_field(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    session_id = campaigns.add_session(campaign_id, "Session 1")
    campaigns.update_session_field(session_id, "summary", "The party won.")
    session = campaigns.list_sessions(campaign_id)[0]
    assert session["summary"] == "The party won."


# ------------------------------------------------------------- materials
def test_add_material_uses_the_campaigns_bookmark_collection(db, a_book):
    campaign_id = campaigns.create_campaign("unsorted", "Test")
    campaigns.add_material(campaign_id, a_book, page_number=12, note="stat block")
    materials = campaigns.list_materials(campaign_id)
    assert len(materials) == 1
    assert materials[0]["page_number"] == 12
    assert materials[0]["note"] == "stat block"

    # And it really did land in the campaign's own bookmark collection, not
    # a fresh throwaway one.
    campaign = campaigns.get_campaign(campaign_id)
    collection = bookmarks.list_collections_with_bookmarks()
    assert any(c["id"] == campaign["bookmark_collection_id"] and c["bookmarks"] for c in collection)


def test_add_material_unknown_campaign_raises(db, a_book):
    with pytest.raises(ValueError):
        campaigns.add_material(999999, a_book)


def test_books_for_game_scopes_to_that_game(db):
    from app import database as db_module
    with db_module.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO books (path, filename, title, publisher, series, game_key) VALUES (?, ?, ?, ?, ?, ?)",
            ("a.pdf", "a.pdf", "Book A", "Pub", "Series", "game_a"),
        )
        cur.execute(
            "INSERT INTO books (path, filename, title, publisher, series, game_key) VALUES (?, ?, ?, ?, ?, ?)",
            ("b.pdf", "b.pdf", "Book B", "Pub", "Series", "game_b"),
        )
    titles = [b["title"] for b in campaigns.books_for_game("game_a")]
    assert titles == ["Book A"]


# ------------------------------------------------------------- aggregate
def test_get_campaign_detail_unknown_id_returns_none(db):
    assert campaigns.get_campaign_detail(999999) is None


def test_get_campaign_detail_bundles_everything(db, a_book):
    campaign_id = campaigns.create_campaign("unsorted", "Full Campaign", pitch="A pitch", tone="grim")
    campaigns.add_thread(campaign_id, "A thread")
    campaigns.add_npc(campaign_id, "An NPC")
    campaigns.add_faction(campaign_id, "A faction")
    campaigns.add_location(campaign_id, "A location")
    campaigns.add_session(campaign_id, "Session 1")
    campaigns.add_material(campaign_id, a_book, note="ref")

    detail = campaigns.get_campaign_detail(campaign_id)
    assert detail["campaign"]["title"] == "Full Campaign"
    assert len(detail["threads"]) == 1
    assert len(detail["npcs"]) == 1
    assert len(detail["factions"]) == 1
    assert len(detail["locations"]) == 1
    assert len(detail["sessions"]) == 1
    assert len(detail["materials"]) == 1
