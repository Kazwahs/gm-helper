"""Procedural map generator: dungeon/interior floor plans, city blocks, cave
systems, and hex-grid regional/world terrain, all rendered as inline SVG.

Genre ("fantasy" / "modern" / "hightech" / "mixed") and map type are fully
independent - a "mixed" genre draws its vocabulary from the union of all
three genre banks, so any type/genre combination (a high-tech dungeon, a
fantasy building interior, a mixed-genre wilderness) produces something
sensible. See generate_map() in .generator for the entry point.
"""
from .generator import MAP_TYPES, generate_map

__all__ = ["MAP_TYPES", "generate_map"]
