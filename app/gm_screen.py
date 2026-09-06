"""GM Screen: a per-game board of quick-reference cards plus one freeform
scratchpad, meant to be the one tab a GM keeps open all session instead of
flipping between books (or a physical cardboard screen).

What goes on it is deliberately editable rather than fixed: which rules a
table actually reaches for varies a lot by game system (a d20 game's
Conditions list means nothing to a Palladium/Rifts table's MDC and Horror
Factor). DEFAULT_CARDS below seeds a new game's screen with generic-but-
genuinely-useful starter content - the mix repeatedly named most valuable in
GM/DM screen discussions (conditions, DC benchmarks, cover/vision, death and
dying, movement/travel, combat actions, downtime) - but every card can be
edited, deleted, reordered, or added to, so a table running a different
system can replace them with their own rules instead.
"""
from . import database

_CARD_FIELDS = {"title", "body"}

# (title, body) seed pairs for a game's screen the first time it's opened.
# Kept short and skimmable on purpose - "space for adventure-specific notes"
# was named as valuable as often as any specific rule, so the goal here is a
# useful starting point, not exhaustive rules text.
DEFAULT_CARDS = [
    (
        "Conditions",
        "Blinded - can't see, auto-fail sight checks, attacks vs you have advantage, yours have disadvantage.\n"
        "Charmed - can't attack the charmer; they get advantage on social checks vs you.\n"
        "Deafened - can't hear, auto-fail hearing checks.\n"
        "Frightened - disadvantage on checks/attacks while the source is in sight; can't move closer to it.\n"
        "Grappled - speed 0. Restrained - speed 0, attacks vs you have advantage, yours have disadvantage, disadvantage on Dex saves.\n"
        "Incapacitated - no actions or reactions. Paralyzed/Petrified - incapacitated, auto-fail Str/Dex saves, "
        "attacks vs you have advantage, and hits within 5ft are automatic crits (paralyzed).\n"
        "Poisoned - disadvantage on attack rolls and ability checks.\n"
        "Prone - disadvantage on attacks; melee attacks vs you have advantage, ranged have disadvantage.\n"
        "Stunned - incapacitated, can't move, auto-fail Str/Dex saves, attacks vs you have advantage.\n"
        "Unconscious - incapacitated, prone, drops what it's holding, auto-fail Str/Dex saves, attacks vs you have "
        "advantage, and hits within 5ft are automatic crits.\n"
        "Exhaustion (levels stack): 1 disadvantage on ability checks - 2 speed halved - 3 disadvantage on attacks "
        "and saves - 4 HP max halved - 5 speed 0 - 6 death.",
    ),
    (
        "DC Benchmarks",
        "Very Easy: 5\nEasy: 10\nMedium: 15\nHard: 20\nVery Hard: 25\nNearly Impossible: 30\n\n"
        "Passive check = 10 + modifier (no roll). Group checks: half or more of the group must succeed.",
    ),
    (
        "Cover & Vision",
        "Half cover: +2 AC and Dex saves.\nThree-quarters cover: +5 AC and Dex saves.\nFull cover: can't be targeted directly.\n\n"
        "Lightly obscured: disadvantage on Perception checks relying on sight.\n"
        "Heavily obscured: effectively blinded while looking into/through it.\n"
        "Darkvision: see in dim light as bright, and darkness as dim (usually no color).",
    ),
    (
        "Movement, Falling & Travel",
        "Difficult terrain: costs 1 extra foot of movement per foot moved.\n"
        "Long jump: up to Str score in feet (half without a 10ft running start). High jump: 3 + Str modifier feet (half without a running start).\n"
        "Falling: 1d6 bludgeoning per 10 feet fallen, max 20d6.\n\n"
        "Travel pace - Fast: 400 ft/min, 4 mph, 30 mi/day (-5 passive Perception).\n"
        "Normal: 300 ft/min, 3 mph, 24 mi/day.\nSlow: 200 ft/min, 2 mph, 18 mi/day (can move stealthily).",
    ),
    (
        "Death & Dying",
        "At 0 HP (not instantly killed): roll a d20 death save each turn on your turn.\n"
        "10 or higher: success. Below 10: failure. Natural 20: regain 1 HP. Natural 1: counts as two failures.\n"
        "3 successes: stabilized (unconscious at 0 HP). 3 failures: dead.\n"
        "Taking damage at 0 HP: one death save failure (two if it's a crit); "
        "damage equal to or greater than your max HP in one hit: instant death.",
    ),
    (
        "Combat Actions",
        "Attack, Cast a Spell, Dash, Disengage, Dodge, Help, Hide, Ready, Search, Use an Object.\n\n"
        "Grapple/Shove (uses your Attack): contested (Athletics/Acrobatics) check against the target's choice of "
        "Athletics or Acrobatics.",
    ),
    (
        "Downtime Activities",
        "Between sessions or during lulls: Crafting, Practicing a Profession, Recuperating, Research, "
        "Training (a skill/tool/language), Carousing, Running a Business.\n\n"
        "Worth asking about at the end of most sessions - easy to forget mid-scene.",
    ),
]


def _seed_defaults(game_key):
    with database.get_cursor(commit=True) as cur:
        for i, (title, body) in enumerate(DEFAULT_CARDS):
            cur.execute(
                "INSERT INTO gm_screen_cards (game_key, title, body, position) VALUES (?, ?, ?, ?)",
                (game_key, title, body, i),
            )


def list_cards(game_key):
    game_key = game_key or "_global"
    with database.get_cursor() as cur:
        any_row = cur.execute(
            "SELECT 1 FROM gm_screen_cards WHERE game_key = ? LIMIT 1", (game_key,)
        ).fetchone()
    if not any_row:
        _seed_defaults(game_key)
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM gm_screen_cards WHERE game_key = ? ORDER BY position, id",
            (game_key,),
        ).fetchall()
    return [dict(r) for r in rows]


def create_card(game_key, title, body=""):
    game_key = game_key or "_global"
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the card a title.")
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM gm_screen_cards WHERE game_key = ?",
            (game_key,),
        ).fetchone()
    next_pos = row["next_pos"]
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO gm_screen_cards (game_key, title, body, position) VALUES (?, ?, ?, ?)",
            (game_key, title, body, next_pos),
        )
        card_id = cur.lastrowid
    return get_card(card_id)


def get_card(card_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM gm_screen_cards WHERE id = ?", (card_id,)).fetchone()
    return dict(row) if row else None


def update_card_field(card_id, field, value):
    if field not in _CARD_FIELDS:
        raise ValueError(f"Unknown field: {field}")
    if field == "title" and not (value or "").strip():
        raise ValueError("A card needs a title.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE gm_screen_cards SET {field} = ? WHERE id = ?", (value, card_id))
    return get_card(card_id)


def delete_card(card_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM gm_screen_cards WHERE id = ?", (card_id,))


def move_card(card_id, direction):
    """direction is 'up' or 'down' - swaps this card's position with its
    neighbor in the same game's ordering."""
    card = get_card(card_id)
    if not card:
        raise ValueError("Unknown card.")
    with database.get_cursor() as cur:
        if direction == "up":
            neighbor = cur.execute(
                "SELECT * FROM gm_screen_cards WHERE game_key = ? AND position < ? "
                "ORDER BY position DESC LIMIT 1",
                (card["game_key"], card["position"]),
            ).fetchone()
        elif direction == "down":
            neighbor = cur.execute(
                "SELECT * FROM gm_screen_cards WHERE game_key = ? AND position > ? "
                "ORDER BY position ASC LIMIT 1",
                (card["game_key"], card["position"]),
            ).fetchone()
        else:
            raise ValueError("direction must be 'up' or 'down'")
    if not neighbor:
        return  # already at that end - nothing to do
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE gm_screen_cards SET position = ? WHERE id = ?", (neighbor["position"], card["id"]))
        cur.execute("UPDATE gm_screen_cards SET position = ? WHERE id = ?", (card["position"], neighbor["id"]))


def get_notes(game_key):
    game_key = game_key or "_global"
    with database.get_cursor() as cur:
        row = cur.execute("SELECT body FROM gm_screen_notes WHERE game_key = ?", (game_key,)).fetchone()
    return row["body"] if row else ""


def set_notes(game_key, body):
    game_key = game_key or "_global"
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO gm_screen_notes (game_key, body) VALUES (?, ?) "
            "ON CONFLICT(game_key) DO UPDATE SET body = excluded.body",
            (game_key, body),
        )
