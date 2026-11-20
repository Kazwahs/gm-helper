"""Tests for the GM Screen board (app/gm_screen.py) - the per-game
reference-card board plus session-notes scratchpad."""
import pytest

from app import database, gm_screen


def test_list_cards_seeds_the_systems_starter_set_on_first_visit(db):
    cards = gm_screen.list_cards("dnd5e", "d20")
    titles = [c["title"] for c in cards]
    assert titles == [t for t, _ in gm_screen.cards_for_system("d20")]
    assert titles[: len(gm_screen.DEFAULT_CARDS)] == [t for t, _ in gm_screen.DEFAULT_CARDS]


def test_each_system_gets_its_own_cards(db):
    palladium = [c["title"] for c in gm_screen.list_cards("rifts", "palladium")]
    savage = [c["title"] for c in gm_screen.list_cards("deadlands", "savage_worlds")]
    fitd = [c["title"] for c in gm_screen.list_cards("blades", "forged_in_the_dark")]
    assert "S.D.C., Hit Points & M.D.C." in palladium
    assert "Bennies" in savage
    assert "Action Roll" in fitd
    # system-specific cards never leak into another system's screen
    assert "Bennies" not in palladium and "Action Roll" not in palladium
    assert "Conditions" not in palladium  # the 5e-flavored card


def test_unknown_or_missing_system_falls_back_to_generic(db):
    expected = [t for t, _ in gm_screen.cards_for_system("generic")]
    assert [c["title"] for c in gm_screen.list_cards("a", "some-new-system")] == expected
    assert [c["title"] for c in gm_screen.list_cards("b", None)] == expected


def test_system_hint_is_normalized(db):
    assert gm_screen.normalize_system("Savage Worlds") == "savage_worlds"
    assert gm_screen.normalize_system("Forged-in-the-Dark") == "forged_in_the_dark"
    assert gm_screen.normalize_system(None) == "generic"
    assert gm_screen.cards_for_system("Palladium") == gm_screen.cards_for_system("palladium")


def test_every_starter_card_has_a_title_and_a_body():
    seen = set()
    for system in gm_screen.SYSTEM_CARDS:
        titles = [t for t, _ in gm_screen.cards_for_system(system)]
        assert len(titles) == len(set(titles)), f"duplicate titles in {system}"
        for title, body in gm_screen.cards_for_system(system):
            assert title.strip() and body.strip()
            seen.add(title)
    assert len(seen) > 10


def test_list_cards_does_not_reseed_on_second_call(db):
    n = len(gm_screen.cards_for_system("d20"))
    gm_screen.delete_card(gm_screen.list_cards("dnd5e", "d20")[0]["id"])
    cards = gm_screen.list_cards("dnd5e", "d20")
    assert len(cards) == n - 1


def test_cards_are_scoped_per_game(db):
    n = len(gm_screen.cards_for_system("d20"))
    dnd_cards = gm_screen.list_cards("dnd5e", "d20")
    rifts_cards = gm_screen.list_cards("rifts", "d20")
    assert len(dnd_cards) == n and len(rifts_cards) == n
    gm_screen.delete_card(dnd_cards[0]["id"])
    assert len(gm_screen.list_cards("dnd5e", "d20")) == n - 1
    assert len(gm_screen.list_cards("rifts", "d20")) == n


def test_none_game_key_defaults_to_global(db):
    cards = gm_screen.list_cards(None)
    assert len(cards) == len(gm_screen.cards_for_system(None))
    for c in cards:
        assert c["game_key"] == "_global"


def test_create_card(db):
    gm_screen.list_cards("dnd5e", "d20")  # seed the defaults first
    card = gm_screen.create_card("dnd5e", "Homebrew Rule", "Crits deal max damage + roll.")
    assert card["title"] == "Homebrew Rule"
    assert card["body"] == "Crits deal max damage + roll."
    # appended after all the seeded defaults
    assert card["position"] == len(gm_screen.cards_for_system("d20"))


def test_add_missing_system_cards_fills_gaps_without_duplicating(db):
    gm_screen.list_cards("rifts", "palladium")
    full = len(gm_screen.cards_for_system("palladium"))
    first = gm_screen.list_cards("rifts", "palladium")[0]
    gm_screen.delete_card(first["id"])
    gm_screen.update_card_field(gm_screen.list_cards("rifts", "palladium")[0]["id"], "body", "my edit")
    assert gm_screen.add_missing_system_cards("rifts", "palladium") == 1
    cards = gm_screen.list_cards("rifts", "palladium")
    assert len(cards) == full
    assert cards[-1]["title"] == first["title"]  # re-added at the end
    assert any(c["body"] == "my edit" for c in cards)  # edits are never overwritten
    assert gm_screen.add_missing_system_cards("rifts", "palladium") == 0


def test_add_missing_picks_up_the_right_system_after_a_wrong_seed(db):
    # a screen seeded before per-system sets existed (5e cards on a Palladium game)
    gm_screen.list_cards("rifts", "d20")
    added = gm_screen.add_missing_system_cards("rifts", "palladium")
    titles = [c["title"] for c in gm_screen.list_cards("rifts", "palladium")]
    assert added >= 4 and "Melee Round & Attacks" in titles


def test_pin_page_creates_a_linked_card(db, a_book):
    card = gm_screen.pin_page("dnd5e", a_book, 12, "Wandering monsters table")
    assert card["book_id"] == a_book and card["page_number"] == 12
    assert card["title"].endswith("p.12")
    assert card["body"] == "Wandering monsters table"
    assert card["book_title"] == "Test Book"


def test_pinning_the_same_page_twice_returns_the_existing_card(db, a_book):
    first = gm_screen.pin_page("dnd5e", a_book, 12)
    again = gm_screen.pin_page("dnd5e", a_book, 12)
    assert first["id"] == again["id"]
    assert gm_screen.pin_page("dnd5e", a_book, 13)["id"] != first["id"]
    assert gm_screen.pin_page("rifts", a_book, 12)["id"] != first["id"]  # other game


def test_pin_page_for_a_missing_book_raises(db):
    with pytest.raises(ValueError):
        gm_screen.pin_page("dnd5e", 424242, 1)


def test_pinned_card_survives_when_its_book_is_removed(db, a_book):
    card = gm_screen.pin_page("dnd5e", a_book, 3)
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM books WHERE id = ?", (a_book,))
    cards = [c for c in gm_screen.list_cards("dnd5e", "generic") if c["id"] == card["id"]]
    assert cards and cards[0]["book_title"] is None


def test_append_notes_adds_a_block_to_the_scratchpad(db):
    gm_screen.append_notes("dnd5e", "3 goblins, 1 worg", heading="Encounter")
    gm_screen.append_notes("dnd5e", "A rusted key")
    assert gm_screen.get_notes("dnd5e") == "Encounter: 3 goblins, 1 worg\n\nA rusted key"


def test_append_notes_keeps_existing_text(db):
    gm_screen.set_notes("dnd5e", "Where we left off: the keep.\n\n")
    gm_screen.append_notes("dnd5e", "Loot: 40gp")
    assert gm_screen.get_notes("dnd5e") == "Where we left off: the keep.\n\nLoot: 40gp"


def test_append_notes_rejects_empty_text(db):
    with pytest.raises(ValueError):
        gm_screen.append_notes("dnd5e", "   ")


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
