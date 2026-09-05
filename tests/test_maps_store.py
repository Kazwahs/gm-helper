"""Tests for saved-map CRUD + campaign attachment (app/maps_store.py) and
the original_svg / editing support added to it."""
import pytest

from app import maps_store, campaigns, database


def test_save_and_get_round_trip(db):
    map_id = maps_store.save_map(
        "unsorted", "dungeon", "fantasy", "My Dungeon", "<svg>x</svg>",
        seed=7, params={"size": "small"},
    )
    m = maps_store.get_map(map_id)
    assert m["title"] == "My Dungeon"
    assert m["map_type"] == "dungeon"
    assert m["genre"] == "fantasy"
    assert m["svg"] == "<svg>x</svg>"
    assert m["seed"] == 7
    assert m["params"] == {"size": "small"}
    assert m["campaign_id"] is None


def test_save_sets_original_svg_to_the_generated_svg(db):
    map_id = maps_store.save_map("unsorted", "urban", "fantasy", "City", "<svg>orig</svg>")
    with database.get_cursor() as cur:
        row = cur.execute("SELECT svg, original_svg FROM maps WHERE id = ?", (map_id,)).fetchone()
    assert row["svg"] == row["original_svg"] == "<svg>orig</svg>"


def test_blank_title_falls_back_to_a_generated_one(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "   ", "<svg/>")
    m = maps_store.get_map(map_id)
    assert m["title"] == "Dungeon map"


def test_get_map_unknown_id_returns_none(db):
    assert maps_store.get_map(999999) is None


def test_list_maps_filters_by_game_key(db):
    maps_store.save_map("game_a", "dungeon", "fantasy", "A1", "<svg/>")
    maps_store.save_map("game_b", "dungeon", "fantasy", "B1", "<svg/>")
    a_maps = maps_store.list_maps(game_key="game_a")
    assert {m["title"] for m in a_maps} == {"A1"}
    all_maps = maps_store.list_maps()
    assert {m["title"] for m in all_maps} == {"A1", "B1"}


def test_list_maps_filters_by_campaign_id(db):
    campaign_id = campaigns.create_campaign("unsorted", "Test Campaign")
    attached = maps_store.save_map("unsorted", "dungeon", "fantasy", "Attached", "<svg/>", campaign_id=campaign_id)
    maps_store.save_map("unsorted", "dungeon", "fantasy", "Unattached", "<svg/>")
    results = maps_store.list_maps(campaign_id=campaign_id)
    assert [m["id"] for m in results] == [attached]


def test_rename_map(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Old Name", "<svg/>")
    maps_store.rename_map(map_id, "New Name")
    assert maps_store.get_map(map_id)["title"] == "New Name"


def test_rename_map_blank_title_raises(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Keep Me", "<svg/>")
    with pytest.raises(ValueError):
        maps_store.rename_map(map_id, "   ")
    assert maps_store.get_map(map_id)["title"] == "Keep Me"


def test_set_campaign_attach_and_detach(db):
    campaign_id = campaigns.create_campaign("unsorted", "Some Campaign")
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Map", "<svg/>")
    maps_store.set_campaign(map_id, campaign_id)
    assert maps_store.get_map(map_id)["campaign_id"] == campaign_id
    maps_store.set_campaign(map_id, None)
    assert maps_store.get_map(map_id)["campaign_id"] is None


def test_deleting_a_campaign_detaches_its_maps_instead_of_deleting_them(db):
    campaign_id = campaigns.create_campaign("unsorted", "Doomed Campaign")
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Survivor", "<svg/>", campaign_id=campaign_id)
    campaigns.delete_campaign(campaign_id)
    m = maps_store.get_map(map_id)
    assert m is not None  # the map itself must survive
    assert m["campaign_id"] is None  # but it's no longer attached to anything


def test_delete_map(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Gone Soon", "<svg/>")
    maps_store.delete_map(map_id)
    assert maps_store.get_map(map_id) is None


def test_update_svg_persists_hand_edits_without_touching_original(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Edited Map", "<svg>generated</svg>")
    maps_store.update_svg(map_id, "<svg>hand-edited</svg>")
    m = maps_store.get_map(map_id)
    assert m["svg"] == "<svg>hand-edited</svg>"
    assert m["original_svg"] == "<svg>generated</svg>"


def test_revert_svg_restores_the_generated_version(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Edited Map", "<svg>generated</svg>")
    maps_store.update_svg(map_id, "<svg>hand-edited</svg>")
    maps_store.revert_svg(map_id)
    m = maps_store.get_map(map_id)
    assert m["svg"] == "<svg>generated</svg>"
    assert m["original_svg"] == "<svg>generated</svg>"


def test_revert_svg_is_a_no_op_when_no_edits_were_made(db):
    map_id = maps_store.save_map("unsorted", "dungeon", "fantasy", "Untouched", "<svg>only-version</svg>")
    maps_store.revert_svg(map_id)
    assert maps_store.get_map(map_id)["svg"] == "<svg>only-version</svg>"
