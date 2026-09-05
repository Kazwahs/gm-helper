"""Tests for the map-generator word-bank picker (app/maps/content.py).
Reads the real app/data/map_content.json (no database involved), so these
also catch a malformed/missing entry in that data file."""
import random

import pytest

from app.maps import content


@pytest.mark.parametrize("genre", ["fantasy", "modern", "hightech"])
def test_pick_returns_an_entry_from_that_genres_bank(genre):
    rng = random.Random(1)
    value = content.pick(rng, "room_names", genre)
    assert value  # non-empty


def test_pick_mixed_can_return_entries_from_any_real_genre():
    rng = random.Random(1)
    per_genre_pools = {
        g: set(content._bank("room_names").get(g, [])) for g in content.GENRES
    }
    seen_pools = set()
    r = random.Random(42)
    for _ in range(200):
        value = content.pick(r, "room_names", "mixed")
        for g, pool in per_genre_pools.items():
            if value in pool:
                seen_pools.add(g)
    # Over enough draws, "mixed" should have touched more than one genre's pool.
    assert len(seen_pools) > 1


def test_pick_many_without_repeats_returns_unique_items():
    rng = random.Random(1)
    items = content.pick_many(rng, "room_names", "fantasy", 5, allow_repeats=False)
    assert len(items) == 5
    assert len(set(items)) == 5


def test_pick_many_with_repeats_allowed_can_repeat():
    rng = random.Random(0)
    # Ask for far more than the pool could possibly hold without repeats -
    # this must not raise (rng.sample would raise if count > population).
    items = content.pick_many(rng, "room_names", "fantasy", 500, allow_repeats=True)
    assert len(items) == 500


def test_pick_many_count_exceeding_pool_falls_back_to_repeats_even_if_not_requested():
    rng = random.Random(0)
    pool_size = len(content._bank("cave_features")["fantasy"])
    items = content.pick_many(rng, "cave_features", "fantasy", pool_size + 10, allow_repeats=False)
    assert len(items) == pool_size + 10


def test_generate_name_shape():
    rng = random.Random(7)
    name = content.generate_name(rng, "fantasy")
    assert isinstance(name, str) and name.strip() == name and name != ""
    assert len(name.split(" ")) <= 2


def test_generate_name_is_reproducible_for_a_given_seed():
    a = content.generate_name(random.Random(99), "hightech")
    b = content.generate_name(random.Random(99), "hightech")
    assert a == b


@pytest.mark.parametrize("genre", ["fantasy", "modern", "hightech"])
def test_biome_label_known_and_unknown_keys(genre):
    label = content.biome_label(genre, "forest")
    assert isinstance(label, str) and label

    # An unrecognized biome key still returns something sensible (a
    # capitalized fallback of the key itself) rather than raising.
    fallback = content.biome_label(genre, "not_a_real_biome")
    assert fallback == "Not_a_real_biome"


def test_biome_label_mixed_uses_a_real_genres_table():
    rng = random.Random(3)
    label = content.biome_label_mixed(rng, "forest")
    all_labels = {content.biome_label(g, "forest") for g in content.GENRES}
    assert label in all_labels
