"""Tests for the GM Screen board (app/gm_screen.py) - the per-game
reference-card board plus session-notes scratchpad."""
import pytest

from app import gm_screen


def test_list_cards_seeds_defaults_on_first_visit(db):
    cards = gm_screen.list_cards("dnd5e")
    assert len(cards) == len(gm_screen.DEFAULT_CARDS)
    titles = [c["title"] for c in cards]
    assert titles == [t for t, _ in gm_screen.DEFAULT_CARDS]


def test_list_cards_does_not_reseed_on_second_call(db):
    gm_screen.list_cards("dnd5e")
    gm_screen.delete_card(gm_screen.list_cards("dnd5e")[0]["id"])
    cards = gm_screen.list_cards("dnd5e")
    assert len(cards) == len(gm_screen.DEFAULT_CARDS) - 1


def test_cards_are_scoped_per_game(db):
    dnd_cards = gm_screen.list_cards("dnd5e")
    rifts_cards = gm_screen.list_cards("rifts")
    assert len(dnd_cards) == len(gm_screen.DEFAULT_CARDS)
    assert len(rifts_cards) == len(gm_screen.DEFAULT_CARDS)
    gm_screen.delete_card(dnd_cards[0]["id"])
    assert len(gm_screen.list_cards("dnd5e")) == len(gm_screen.DEFAULT_CARDS) - 1
    assert len(gm_screen.list_cards("rifts")) == len(gm_screen.DEFAULT_CARDS)


def test_none_game_key_defaults_to_global(db):
    cards = gm_screen.list_cards(None)
    assert len(cards) == len(gm_screen.DEFAULT_CARDS)
    for c in cards:
        assert c["game_key"] == "_global"


def test_create_card(db):
    gm_screen.list_cards("dnd5e")  # seed the defaults first
    card = gm_screen.create_card("dnd5e", "Homebrew Rule", "Crits deal max damage + roll.")
    assert card["title"] == "Homebrew Rule"
    assert card["body"] == "Crits deal max damage + roll."
    # appended after all the seeded defaults
    assert card["position"] == len(gm_screen.DEFAULT_CARDS)


def test_create_card_blank_title_raises(db):
    with pytest.raises(ValueError):
        gm_screen.create_card("dnd5e", "   ")


def test_create_card_defaults_game_key_to_global(db):
    card = gm_screen.create_card(None, "Note")
    assert card["game_key"] == "_global"


def test_update_card_field(db):
    card = gm_screen.create_card("dnd5e", "Original", "body text")
    updated = gm_screen.update_card_field(card["id"], "title", "Renamed")
    assert updated["title"] == "Renamed"
    updated = gm_screen.update_card_field(card["id"], "body", "new body")
    assert updated["body"] == "new body"


def test_update_card_unknown_field_raises(db):
    card = gm_screen.create_card("dnd5e", "Card")
    with pytest.raises(ValueError):
        gm_screen.update_card_field(card["id"], "game_key", "hacked")


def test_update_card_blank_title_raises(db):
    card = gm_screen.create_card("dnd5e", "Card")
    with pytest.raises(ValueError):
        gm_screen.update_card_field(card["id"], "title", "   ")


def test_delete_card(db):
    card = gm_screen.create_card("dnd5e", "Temp")
    gm_screen.delete_card(card["id"])
    assert gm_screen.get_card(card["id"]) is None


def test_move_card_up_and_down(db):
    a = gm_screen.create_card("empty-game", "A")
    b = gm_screen.create_card("empty-game", "B")
    c = gm_screen.create_card("empty-game", "C")
    assert [x["title"] for x in gm_screen.list_cards("empty-game")] == ["A", "B", "C"]

    gm_screen.move_card(b["id"], "up")
    assert [x["title"] for x in gm_screen.list_cards("empty-game")] == ["B", "A", "C"]

    gm_screen.move_card(b["id"], "down")
    assert [x["title"] for x in gm_screen.list_cards("empty-game")] == ["A", "B", "C"]


def test_move_card_at_the_top_is_a_no_op(db):
    a = gm_screen.create_card("empty-game", "A")
    b = gm_screen.create_card("empty-game", "B")
    gm_screen.move_card(a["id"], "up")
    assert [x["title"] for x in gm_screen.list_cards("empty-game")] == ["A", "B"]


def test_move_card_at_the_bottom_is_a_no_op(db):
    a = gm_screen.create_card("empty-game", "A")
    b = gm_screen.create_card("empty-game", "B")
    gm_screen.move_card(b["id"], "down")
    assert [x["title"] for x in gm_screen.list_cards("empty-game")] == ["A", "B"]


def test_move_unknown_card_raises(db):
    with pytest.raises(ValueError):
        gm_screen.move_card(999999, "up")


def test_move_card_bad_direction_raises(db):
    card = gm_screen.create_card("empty-game", "A")
    with pytest.raises(ValueError):
        gm_screen.move_card(card["id"], "sideways")


def test_notes_default_to_empty_string(db):
    assert gm_screen.get_notes("dnd5e") == ""
    assert gm_screen.get_notes(None) == ""


def test_notes_round_trip(db):
    gm_screen.set_notes("dnd5e", "The party is heading to the ruined keep.")
    assert gm_screen.get_notes("dnd5e") == "The party is heading to the ruined keep."


def test_notes_update_overwrites_previous_value(db):
    gm_screen.set_notes("dnd5e", "first draft")
    gm_screen.set_notes("dnd5e", "revised notes")
    assert gm_screen.get_notes("dnd5e") == "revised notes"


def test_notes_are_scoped_per_game(db):
    gm_screen.set_notes("dnd5e", "dnd notes")
    gm_screen.set_notes("rifts", "rifts notes")
    assert gm_screen.get_notes("dnd5e") == "dnd notes"
    assert gm_screen.get_notes("rifts") == "rifts notes"
