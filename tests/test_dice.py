"""Tests for the dice notation parser (app/tools/dice.py) - no database
needed, this is pure parsing + random.randint logic."""
import random

import pytest

from app.tools import dice


def test_simple_roll_shape():
    result = dice.roll_group("1d20")
    assert result["sides"] == 20
    assert result["count"] == 1
    assert len(result["rolls"]) == 1
    assert 1 <= result["rolls"][0] <= 20
    assert result["kept"] == result["rolls"]
    assert result["dropped"] == []
    assert result["modifier"] == 0
    assert result["total"] == result["rolls"][0]


def test_count_defaults_to_one():
    result = dice.roll_group("d6")
    assert result["count"] == 1
    assert len(result["rolls"]) == 1


def test_modifier_applied_to_total_only():
    random.seed(0)
    result = dice.roll_group("3d6+5")
    assert result["count"] == 3
    assert result["modifier"] == 5
    assert result["total"] == sum(result["rolls"]) + 5


def test_negative_modifier():
    result = dice.roll_group("1d20-3")
    assert result["modifier"] == -3
    assert result["total"] == result["rolls"][0] - 3


@pytest.mark.parametrize("expr", ["2d20kh1", "2d20KH1"])
def test_keep_highest_advantage(expr):
    # Run many times since it's randomized - the invariant must hold every time.
    for _ in range(200):
        result = dice.roll_group(expr)
        assert len(result["rolls"]) == 2
        assert result["kept"] == [max(result["rolls"])]
        assert result["dropped"] == [min(result["rolls"])]
        assert result["total"] == max(result["rolls"])


@pytest.mark.parametrize("expr", ["2d20kl1", "2d20KL1"])
def test_keep_lowest_disadvantage(expr):
    for _ in range(200):
        result = dice.roll_group(expr)
        assert result["kept"] == [min(result["rolls"])]
        assert result["dropped"] == [max(result["rolls"])]
        assert result["total"] == min(result["rolls"])


def test_keep_highest_of_more_than_two():
    result = dice.roll_group("4d6kh3")
    assert len(result["kept"]) == 3
    assert len(result["dropped"]) == 1
    assert sum(result["kept"]) + sum(result["dropped"]) == sum(result["rolls"])
    assert min(result["kept"]) >= max(result["dropped"] or [0])


@pytest.mark.parametrize("bad_expr", [
    "", "d", "20", "1d", "xd20", "1d20++5", "1d20kx1", "1e20",
])
def test_invalid_expressions_raise(bad_expr):
    with pytest.raises(dice.DiceError):
        dice.roll_group(bad_expr)


@pytest.mark.parametrize("bad_expr", ["0d6", "1001d6", "2001d1"])
def test_count_out_of_bounds_raises(bad_expr):
    with pytest.raises(dice.DiceError):
        dice.roll_group(bad_expr)


@pytest.mark.parametrize("bad_expr", ["1d1", "1d1001"])
def test_sides_out_of_bounds_raises(bad_expr):
    with pytest.raises(dice.DiceError):
        dice.roll_group(bad_expr)


def test_bounds_are_inclusive():
    # 1 die, 2 sides, 1000 sides, 1000 dice are all explicitly allowed.
    dice.roll_group("1d2")
    dice.roll_group("1d1000")
    dice.roll_group("1000d2")


def test_whitespace_inside_a_single_group_is_tolerated():
    result = dice.roll_group("  1 d 20 + 3  ")
    assert result["sides"] == 20
    assert result["modifier"] == 3


def test_roll_multiple_groups():
    result = dice.roll("1d20+5 2d6+2")
    assert len(result["groups"]) == 2
    assert result["groups"][0]["sides"] == 20
    assert result["groups"][1]["sides"] == 6
    assert result["grand_total"] == result["groups"][0]["total"] + result["groups"][1]["total"]


def test_roll_empty_expression_raises():
    with pytest.raises(dice.DiceError):
        dice.roll("   ")


def test_roll_propagates_group_error():
    with pytest.raises(dice.DiceError):
        dice.roll("1d20 not-a-die")


def test_rolls_are_reproducible_with_seeded_random():
    random.seed(12345)
    first = dice.roll_group("10d20")["rolls"]
    random.seed(12345)
    second = dice.roll_group("10d20")["rolls"]
    assert first == second
