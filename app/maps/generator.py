"""Top-level dispatcher: generate_map(map_type, genre, params) picks the
right generator module and returns a reproducible, ready-to-embed SVG."""
import random

from . import caves, rooms, terrain, urban

MAP_TYPES = [
    {"key": "dungeon", "name": "Dungeon"},
    {"key": "interior", "name": "Building Interior"},
    {"key": "urban", "name": "City / Urban"},
    {"key": "underground", "name": "Underground / Caves"},
    {"key": "wilderness", "name": "Wilderness"},
    {"key": "world", "name": "World"},
]
_MAP_TYPE_KEYS = {m["key"] for m in MAP_TYPES}

GENRES = [
    {"key": "fantasy", "name": "High Fantasy"},
    {"key": "modern", "name": "Modern"},
    {"key": "hightech", "name": "High Technology"},
    {"key": "mixed", "name": "Mixed"},
]
_GENRE_KEYS = {g["key"] for g in GENRES}

SIZES = ["small", "medium", "large"]


def generate_map(map_type, genre, params=None, seed=None):
    if map_type not in _MAP_TYPE_KEYS:
        raise ValueError(f"Unknown map type: {map_type!r}")
    if genre not in _GENRE_KEYS:
        raise ValueError(f"Unknown genre: {genre!r}")
    params = dict(params or {})
    if params.get("size") not in SIZES:
        params["size"] = "medium"
    if seed is None:
        seed = random.randrange(1_000_000_000)
    rng = random.Random(seed)

    if map_type == "dungeon":
        result = rooms.generate(rng, genre, "dungeon", params)
    elif map_type == "interior":
        result = rooms.generate(rng, genre, "interior", params)
    elif map_type == "urban":
        result = urban.generate(rng, genre, params)
    elif map_type == "underground":
        result = caves.generate(rng, genre, params)
    elif map_type in ("wilderness", "world"):
        result = terrain.generate(rng, genre, map_type, params)
    else:  # pragma: no cover - guarded above
        raise ValueError(f"Unknown map type: {map_type!r}")

    result["seed"] = seed
    result["map_type"] = map_type
    result["genre"] = genre
    result["params"] = params
    return result
