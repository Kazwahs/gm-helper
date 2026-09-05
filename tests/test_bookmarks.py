"""Tests for bookmark collections + bookmarks (app/bookmarks.py)."""
import pytest

from app import bookmarks


def test_create_and_list_collections(db):
    cid = bookmarks.create_collection("Session 12 prep")
    collections = bookmarks.list_collections()
    assert [c["name"] for c in collections] == ["Session 12 prep"]
    assert collections[0]["id"] == cid


def test_create_collection_blank_name_raises(db):
    with pytest.raises(ValueError):
        bookmarks.create_collection("   ")


def test_create_collection_duplicate_name_raises(db):
    bookmarks.create_collection("Duplicate")
    with pytest.raises(ValueError):
        bookmarks.create_collection("Duplicate")


def test_get_or_create_collection_reuses_existing(db):
    first = bookmarks.get_or_create_collection("Reused")
    second = bookmarks.get_or_create_collection("Reused")
    assert first == second
    assert len(bookmarks.list_collections()) == 1


def test_rename_collection(db):
    cid = bookmarks.create_collection("Old Name")
    bookmarks.rename_collection(cid, "New Name")
    assert bookmarks.list_collections()[0]["name"] == "New Name"


def test_rename_collection_blank_raises(db):
    cid = bookmarks.create_collection("Keep Me")
    with pytest.raises(ValueError):
        bookmarks.rename_collection(cid, "")


def test_delete_collection_cascades_its_bookmarks(db, a_book):
    cid = bookmarks.create_collection("Doomed")
    bookmarks.add_bookmark(cid, a_book, page_number=5, note="a note")
    bookmarks.delete_collection(cid)
    assert bookmarks.list_collections() == []
    # The cascading FK delete on bookmarks should have removed the bookmark
    # row too, not just the collection.
    all_with_bookmarks = bookmarks.list_collections_with_bookmarks()
    assert all_with_bookmarks == []


def test_add_bookmark_and_update_note(db, a_book):
    cid = bookmarks.create_collection("Prep")
    bm_id = bookmarks.add_bookmark(cid, a_book, page_number=42, note="first note")
    detail = bookmarks.list_collections_with_bookmarks()[0]
    assert detail["bookmarks"][0]["id"] == bm_id
    assert detail["bookmarks"][0]["page_number"] == 42
    assert detail["bookmarks"][0]["note"] == "first note"

    bookmarks.update_bookmark_note(bm_id, "updated note")
    detail = bookmarks.list_collections_with_bookmarks()[0]
    assert detail["bookmarks"][0]["note"] == "updated note"


def test_add_bookmark_without_page_or_note(db, a_book):
    cid = bookmarks.create_collection("General refs")
    bm_id = bookmarks.add_bookmark(cid, a_book)
    detail = bookmarks.list_collections_with_bookmarks()[0]
    assert detail["bookmarks"][0]["id"] == bm_id
    assert detail["bookmarks"][0]["page_number"] is None
    assert detail["bookmarks"][0]["note"] == ""


def test_delete_bookmark_leaves_collection_and_other_bookmarks(db, a_book):
    cid = bookmarks.create_collection("Multi")
    bm1 = bookmarks.add_bookmark(cid, a_book, note="keep")
    bm2 = bookmarks.add_bookmark(cid, a_book, note="remove")
    bookmarks.delete_bookmark(bm2)
    detail = bookmarks.list_collections_with_bookmarks()[0]
    assert [b["id"] for b in detail["bookmarks"]] == [bm1]


def test_list_collections_with_bookmarks_groups_correctly(db, a_book):
    c1 = bookmarks.create_collection("C1")
    c2 = bookmarks.create_collection("C2")
    bookmarks.add_bookmark(c1, a_book, note="in c1")
    bookmarks.add_bookmark(c2, a_book, note="in c2 - one")
    bookmarks.add_bookmark(c2, a_book, note="in c2 - two")

    by_name = {c["name"]: c for c in bookmarks.list_collections_with_bookmarks()}
    assert len(by_name["C1"]["bookmarks"]) == 1
    assert len(by_name["C2"]["bookmarks"]) == 2


def test_empty_collection_has_empty_bookmarks_list(db):
    bookmarks.create_collection("Empty")
    detail = bookmarks.list_collections_with_bookmarks()[0]
    assert detail["bookmarks"] == []
