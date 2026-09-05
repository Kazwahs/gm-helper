"""Dice roller: parses standard tabletop notation.

Supports one or more groups separated by spaces, e.g. "2d6+3 1d20 d8-1".
Each group: [count]d<sides>[+/-modifier], count defaults to 1.
Also supports advantage/disadvantage shorthand: "2d20kh1" (keep highest 1)
and "2d20kl1" (keep lowest 1), which covers D&D 5e advantage/disadvantage.
"""
import random
import re

DICE_RE = re.compile(
    r"^(?P<count>\d*)d(?P<sides>\d+)"
    r"(?:k(?P<keep>[hl])(?P<keepn>\d+))?"
    r"(?P<mod>[+-]\d+)?$",
    re.IGNORECASE,
)


class DiceError(ValueError):
    pass


def roll_group(expr):
    expr = expr.strip().replace(" ", "")
    m = DICE_RE.match(expr)
    if not m:
        raise DiceError(f"Couldn't parse dice expression: {expr!r}")

    count = int(m.group("count")) if m.group("count") else 1
    sides = int(m.group("sides"))
    keep = m.group("keep")
    keepn = int(m.group("keepn")) if m.group("keepn") else None
    mod = int(m.group("mod")) if m.group("mod") else 0

    if count < 1 or count > 1000:
        raise DiceError("Dice count must be between 1 and 1000")
    if sides < 2 or sides > 1000:
        raise DiceError("Die sides must be between 2 and 1000")

    rolls = [random.randint(1, sides) for _ in range(count)]
    kept = rolls
    dropped = []
    if keep and keepn:
        sorted_rolls = sorted(enumerate(rolls), key=lambda t: t[1], reverse=(keep.lower() == "h"))
        keep_idx = {i for i, _ in sorted_rolls[:keepn]}
        kept = [r for i, r in enumerate(rolls) if i in keep_idx]
        dropped = [r for i, r in enumerate(rolls) if i not in keep_idx]

    total = sum(kept) + mod

    return {
        "expr": expr,
        "sides": sides,
        "count": count,
        "rolls": rolls,
        "kept": kept,
        "dropped": dropped,
        "modifier": mod,
        "total": total,
    }


def roll(expression):
    """Roll a whole expression that may contain multiple space-separated groups."""
    groups = [g for g in expression.strip().split() if g]
    if not groups:
        raise DiceError("Enter a dice expression, e.g. 2d6+3")
    results = [roll_group(g) for g in groups]
    grand_total = sum(r["total"] for r in results)
    return {"groups": results, "grand_total": grand_total}
