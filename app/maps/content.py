"""Loads app/data/map_content.json and provides genre-aware picking. A
'mixed' genre draws from the union of all real genres' lists for whichever
bank is being sampled, rather than being its own separate bank to maintain."""
import json

from .. import config

with open(config.MAP_CONTENT_PATH, "r", encoding="utf-8") as f:
    _DATA = json.load(f)

GENRES = ["fantasy", "modern", "hightech"]
GENRE_LABELS = {"fantasy": "High Fantasy", "modern": "Modern", "hightech": "High Technology", "mixed": "Mixed"}


def _bank(name):
    return _DATA[name]


def pick(rng, bank_name, genre):
    """Pick one entry from a word bank for the given genre ('mixed' pools
    every genre's list together)."""
    bank = _bank(bank_name)
    if genre == "mixed":
        pool = [item for g in GENRES for item in bank.get(g, [])]
    else:
        pool = bank.get(genre) or bank.get("fantasy")
    return rng.choice(pool)


def pick_many(rng, bank_name, genre, count, allow_repeats=False):
    bank = _bank(bank_name)
    if genre == "mixed":
        pool = [item for g in GENRES for item in bank.get(g, [])]
    else:
        pool = list(bank.get(genre) or bank.get("fantasy"))
    if allow_repeats or count > len(pool):
        return [rng.choice(pool) for _ in range(count)]
    return rng.sample(pool, count)


def name_parts_pool(genre):
    parts = _bank("name_parts")
    if genre == "mixed":
        prefixes, suffixes = [], []
        for g in GENRES:
            prefixes += parts[g]["prefixes"]
            suffixes += parts[g]["suffixes"]
        return prefixes, suffixes
    p = parts.get(genre) or parts["fantasy"]
    return p["prefixes"], p["suffixes"]


def generate_name(rng, genre, suffix_word=None):
    """A short place/settlement name, e.g. 'Ironhaven' or (with suffix_word)
    'Neo Station'."""
    prefixes, suffixes = name_parts_pool(genre)
    prefix = rng.choice(prefixes)
    suffix = suffix_word or rng.choice(suffixes)
    if suffix[0].isupper():
        return f"{prefix} {suffix}"
    return f"{prefix}{suffix}"


def biome_label(genre, biome_key):
    labels = _bank("biome_labels")
    table = labels.get(genre) if genre != "mixed" else None
    if table is None:
        # mixed: pick a random genre's label for this biome each time it's
        # rendered by the caller passing a fixed rng choice - kept simple by
        # just averaging to the fantasy label as a stable fallback here, and
        # letting terrain.py pick the genre-flavored label per hex instead.
        table = labels["fantasy"]
    return table.get(biome_key, biome_key.capitalize())


def biome_label_mixed(rng, biome_key):
    g = rng.choice(GENRES)
    return _bank("biome_labels")[g].get(biome_key, biome_key.capitalize())
