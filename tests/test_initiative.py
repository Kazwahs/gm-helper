"""Tests for the initiative tracker (app/tools/initiative.py).

Several of these are regression tests for real bugs the old implementation
had: a shared default-state list that leaked one game's combatants into the
next game's tracker, and a list-index "whose turn" pointer that moved the
turn to the wrong person whenever someone was added or removed mid-round.
"""
import json

import pytest

from app import database
from app.tools import dice as dice_tool
from app.tools import initiative as ini


def names(view):
    return [c["name"] for c in view["combatants"]]


def current_name(view):
    for c in view["combatants"]:
        if c["id"] == view["current_id"]:
            return c["name"]
    return None


def _id(view, name):
    return next(c["id"] for c in view["combatants"] if c["name"] == name)


def party(game="g"):
    ini.add_combatant(game, "A", 20)
    ini.add_combatant(game, "B", 15)
    return ini.add_combatant(game, "C", 10)


# ------------------------------------------------------------- isolation
def test_new_game_does_not_inherit_another_games_combatants(db):
    ini.add_combatant("game_one", "Goblin", 12)
    assert ini.get_state("game_two")["combatants"] == []
    ini.add_combatant("game_two", "Orc", 9)
    assert names(ini.get_state("game_two")) == ["Orc"]
    assert names(ini.get_state("game_one")) == ["Goblin"]


def test_opening_the_tracker_does_not_create_a_row(db):
    ini.get_state("untouched")
    with database.get_cursor() as cur:
        n = cur.execute("SELECT COUNT(*) AS n FROM combat_sessions").fetchone()["n"]
    assert n == 0


# ------------------------------------------------------------- ordering
def test_sorted_by_initiative_descending(db):
    ini.add_combatant("g", "slow", 3)
    ini.add_combatant("g", "fast", 25)
    view = ini.add_combatant("g", "mid", 10)
    assert names(view) == ["fast", "mid", "slow"]


def test_ties_put_pcs_first_then_insertion_order(db):
    ini.add_combatant("g", "Goblin", 10)
    ini.add_combatant("g", "Hero", 10, is_pc=True)
    view = ini.add_combatant("g", "Orc", 10)
    assert names(view) == ["Hero", "Goblin", "Orc"]


def test_current_is_top_of_order_before_the_first_next_turn(db):
    view = party()
    assert current_name(view) == "A"
    # a faster combatant added before anyone has acted simply goes first
    view = ini.add_combatant("g", "Fast", 30)
    assert current_name(view) == "Fast"


# ------------------------------------------------------------- turn tracking
def test_next_turn_walks_the_order_and_increments_round(db):
    party()
    assert current_name(ini.next_turn("g")) == "B"
    assert current_name(ini.next_turn("g")) == "C"
    view = ini.next_turn("g")
    assert current_name(view) == "A"
    assert view["round"] == 2


def test_adding_a_combatant_mid_round_does_not_steal_the_turn(db):
    party()
    view = ini.next_turn("g")
    assert current_name(view) == "B"
    view = ini.add_combatant("g", "Monster", 18)  # slots in between A and B
    assert current_name(view) == "B"
    assert names(view) == ["A", "Monster", "B", "C"]


def test_removing_someone_earlier_in_order_keeps_the_turn(db):
    party()
    ini.next_turn("g")
    view = ini.next_turn("g")
    assert current_name(view) == "C"
    view = ini.remove_combatant("g", _id(view, "A"))
    assert current_name(view) == "C"
    assert view["round"] == 1


def test_removing_the_current_combatant_passes_the_turn_on(db):
    party()
    view = ini.next_turn("g")
    assert current_name(view) == "B"
    view = ini.remove_combatant("g", _id(view, "B"))
    assert current_name(view) == "C"
    assert view["round"] == 1


def test_removing_the_last_in_order_while_current_starts_a_new_round(db):
    party()
    ini.next_turn("g")
    view = ini.next_turn("g")
    assert current_name(view) == "C"
    view = ini.remove_combatant("g", _id(view, "C"))
    assert current_name(view) == "A"
    assert view["round"] == 2


def test_removing_everyone_leaves_an_empty_board(db):
    view = ini.add_combatant("g", "Solo", 5)
    view = ini.remove_combatant("g", view["combatants"][0]["id"])
    assert view["combatants"] == []
    assert view["current_id"] is None
    assert ini.next_turn("g")["combatants"] == []  # no crash on an empty board


def test_ids_are_never_reused_after_removal(db):
    first = ini.add_combatant("g", "One", 1)["combatants"][0]["id"]
    ini.remove_combatant("g", first)
    second = ini.add_combatant("g", "Two", 1)["combatants"][0]["id"]
    assert second != first


def test_changing_initiative_reorders_without_moving_the_turn(db):
    party()
    view = ini.next_turn("g")
    assert current_name(view) == "B"
    view = ini.set_initiative("g", _id(view, "C"), 99)
    assert names(view) == ["C", "A", "B"]
    assert current_name(view) == "B"


# ------------------------------------------------------------- adding
def test_quantity_numbers_each_copy(db):
    view = ini.add_combatant("g", "Goblin", 10, hp=7, quantity=3)
    assert sorted(names(view)) == ["Goblin 1", "Goblin 2", "Goblin 3"]
    assert all(c["hp"] == 7 and c["max_hp"] == 7 for c in view["combatants"])


def test_quantity_skips_names_already_in_use(db):
    ini.add_combatant("g", "Goblin 1", 5)
    view = ini.add_combatant("g", "Goblin", 5, quantity=2)
    assert len(set(names(view))) == 3


def test_dice_expression_initiative_is_rolled_per_copy(db, monkeypatch):
    rolls = iter([4, 17, 9])
    monkeypatch.setattr(dice_tool.random, "randint", lambda a, b: next(rolls))
    view = ini.add_combatant("g", "Wolf", "1d20+2", quantity=3)
    assert sorted(c["initiative"] for c in view["combatants"]) == [6, 11, 19]


def test_plain_number_initiative_is_not_rolled(db):
    view = ini.add_combatant("g", "Knight", "14")
    assert view["combatants"][0]["initiative"] == 14


@pytest.mark.parametrize("bad", ["", "   ", "banana", "d"])
def test_bad_initiative_is_rejected(db, bad):
    with pytest.raises(ValueError):
        ini.add_combatant("g", "X", bad)


def test_blank_name_and_bad_quantity_are_rejected(db):
    with pytest.raises(ValueError):
        ini.add_combatant("g", "  ", 5)
    with pytest.raises(ValueError):
        ini.add_combatant("g", "X", 5, quantity=0)
    with pytest.raises(ValueError):
        ini.add_combatant("g", "X", 5, quantity=ini.MAX_QUANTITY + 1)


# ------------------------------------------------------------- hit points
def test_damage_can_go_below_zero_and_flags_the_combatant_down(db):
    view = ini.add_combatant("g", "Z", 5, hp=3)
    cid = view["combatants"][0]["id"]
    view = ini.update_hp("g", cid, -10)
    assert view["combatants"][0]["hp"] == -7
    assert view["combatants"][0]["down"] is True


def test_healing_stops_at_max_hp(db):
    view = ini.add_combatant("g", "Z", 5, hp=10)
    cid = view["combatants"][0]["id"]
    ini.update_hp("g", cid, -6)
    view = ini.update_hp("g", cid, +50)
    assert view["combatants"][0]["hp"] == 10


def test_hp_change_on_a_combatant_without_hp_is_ignored(db):
    view = ini.add_combatant("g", "Statue", 1)
    view = ini.update_hp("g", view["combatants"][0]["id"], -5)
    assert view["combatants"][0]["hp"] is None
    assert view["combatants"][0]["down"] is False


# ------------------------------------------------------------- conditions
def test_conditions_add_replace_and_remove(db):
    view = ini.add_combatant("g", "Z", 5)
    cid = view["combatants"][0]["id"]
    view = ini.add_condition("g", cid, "Stunned", 2)
    view = ini.add_condition("g", cid, "stunned", 5)  # same condition: refreshed, not stacked
    conds = view["combatants"][0]["conditions"]
    assert conds == [{"name": "stunned", "rounds": 5}]
    view = ini.remove_condition("g", cid, "STUNNED")
    assert view["combatants"][0]["conditions"] == []


def test_timed_conditions_tick_down_at_the_end_of_that_creatures_turn(db):
    ini.add_combatant("g", "A", 20)
    view = ini.add_combatant("g", "B", 10)
    a = _id(view, "A")
    ini.add_condition("g", a, "Bless", 2)
    ini.add_condition("g", a, "Marked")  # no duration - stays until removed

    view = ini.next_turn("g")  # A's turn ends: Bless 2 -> 1
    conds = {c["name"]: c["rounds"] for c in view["combatants"][0]["conditions"]}
    assert conds == {"Bless": 1, "Marked": None}

    ini.next_turn("g")  # B's turn ends, new round, A is up again
    view = ini.next_turn("g")  # A's turn ends again: Bless expires
    conds = {c["name"]: c["rounds"] for c in view["combatants"][0]["conditions"]}
    assert conds == {"Marked": None}


def test_bad_condition_input_is_rejected(db):
    view = ini.add_combatant("g", "Z", 5)
    cid = view["combatants"][0]["id"]
    with pytest.raises(ValueError):
        ini.add_condition("g", cid, "  ")
    with pytest.raises(ValueError):
        ini.add_condition("g", cid, "Slowed", 0)
    with pytest.raises(ValueError):
        ini.add_condition("g", 9999, "Slowed")


# ------------------------------------------------------------- clearing
def test_clear_foes_keeps_the_party_and_restarts_the_round(db):
    ini.add_combatant("g", "Hero", 15, is_pc=True)
    ini.add_combatant("g", "Orc", 12, quantity=2)
    ini.next_turn("g")
    ini.next_turn("g")
    view = ini.clear_foes("g")
    assert names(view) == ["Hero"]
    assert view["round"] == 1
    assert current_name(view) == "Hero"


def test_reset_clears_everyone(db):
    party()
    ini.next_turn("g")
    view = ini.reset("g")
    assert view["combatants"] == [] and view["round"] == 1


# ------------------------------------------------------------- old saves
def test_state_saved_by_the_old_version_still_loads(db):
    """The old format used `turn_index` (a position) and had no `next_id`."""
    legacy = {
        "round": 3,
        "turn_index": 1,
        "combatants": [
            {"id": 1, "name": "A", "initiative": 20, "hp": 10, "max_hp": 10,
             "is_pc": True, "notes": "", "conditions": []},
            {"id": 2, "name": "B", "initiative": 15, "hp": None, "max_hp": None,
             "is_pc": False, "notes": "", "conditions": []},
        ],
    }
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO combat_sessions (game_key, name, state_json) VALUES (?, ?, ?)",
            ("legacy", "Encounter", json.dumps(legacy)),
        )
    view = ini.get_state("legacy")
    assert view["round"] == 3
    assert current_name(view) == "B"          # index 1 in initiative order
    view = ini.add_combatant("legacy", "C", 5)
    assert max(c["id"] for c in view["combatants"]) == 3  # next_id was derived


# ------------------------------------------------------------- suggestions
def test_condition_suggestions_normalize_the_system_name():
    assert "Shaken" in ini.suggested_conditions("savage_worlds")
    assert "Shaken" in ini.suggested_conditions("Savage Worlds")
    assert "Harm 2" in ini.suggested_conditions("forged in the dark")
    assert ini.suggested_conditions("who-knows") == ini.suggested_conditions("generic")
    assert ini.suggested_conditions(None) == ini.suggested_conditions("generic")
