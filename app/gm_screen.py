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
GLOBAL_KEY = "_global"

# (title, body) seed pairs for a d20-system game's screen (D&D 5e-flavored: the
# advantage/disadvantage wording is 5e's, so on a 3.x/4e game treat these as
# starting text to edit). Other systems have their own sets further down - see
# SYSTEM_CARDS. Kept short and skimmable on purpose - "space for adventure-specific notes"
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


# --------------------------------------------------------------------------
# Starter cards for other systems. These are short, original summaries of
# the core mechanics meant to jog a GM's memory at the table - not rules
# text - and every card is editable. Where editions differ (Palladium's
# varies a lot between games and printings), they say so.
# --------------------------------------------------------------------------
PALLADIUM_CARDS = [
    (
        "Melee Round & Attacks",
        "- A melee round is about 15 seconds. Everyone spends their **attacks per melee** (APMs) as actions.\n"
        "- **Strike:** roll d20 + strike bonuses. The defender rolls d20 + parry or dodge bonuses and must "
        "**equal or beat** the strike roll to avoid the hit.\n"
        "- Strike, parry and dodge bonuses are separate categories - never add one into another.\n"
        "- A **natural 20** is a critical strike (double damage). Some training and R.C.C.s widen the critical range "
        "(19-20, 18-20).\n"
        "- Each parry or dodge normally uses up one of your attacks that round. Auto-parry, auto-dodge and "
        "other exceptions vary by book - check yours.",
    ),
    (
        "Ranged & Called Shots",
        "- Modern and energy weapons generally need an **8 or higher** on the d20 to strike (after W.P. and "
        "aimed-shot bonuses).\n"
        "- **Called shot:** the attacker declares it before rolling; the GM applies a strike penalty for how "
        "hard the shot is.\n"
        "- Bursts, wild shots, range and movement penalties are in the combat chapter of your core book.",
    ),
    (
        "S.D.C., Hit Points & M.D.C.",
        "- **S.D.C.** is a buffer - damage that hurts but isn't life-threatening. When it's gone, damage "
        "goes to **Hit Points**.\n"
        "- **M.D.C.** (Mega-Damage Capacity) is a separate, far tougher scale: **1 M.D. = 100 S.D.**\n"
        "- S.D.C. attacks can't hurt M.D.C. targets; one point of Mega-Damage is worth 100 points of S.D.C. "
        "damage against an S.D.C. target.\n"
        "- Rifts-style games track M.D.C. for armor, vehicles and monsters; most other Palladium games use "
        "S.D.C. and Hit Points.",
    ),
    (
        "Attributes at a Glance",
        "- **I.Q.** - skill bonus\n- **M.E.** - saves vs psionics and insanity\n- **M.A.** - trust / intimidate\n"
        "- **P.S.** - hand-to-hand damage\n- **P.P.** - bonus to strike, parry and dodge\n"
        "- **P.E.** - saves vs coma/death, magic and poison\n- **P.B.** - charm / impress\n"
        "- **Spd** - how far you run per melee round",
    ),
    (
        "Skills & Saves",
        "- **Skill checks:** roll percentile (d100); equal to or **under** the skill percentage succeeds.\n"
        "- **Saving throws and combat rolls:** d20, roll **high** against a target number from your book "
        "(magic, psionics, poison, insanity, horror factor, and so on).\n"
        "- Skill bonuses from I.Q. are one-time bonuses added when the skill is taken.",
    ),
]

SAVAGE_WORLDS_CARDS = [
    (
        "Trait Rolls (SWADE)",
        "- Roll the trait die (d4-d12). **Wild Cards** also roll a **Wild Die (d6)** and keep the better "
        "result. Dice **ace** - they explode on their maximum.\n"
        "- The target number is usually **4**. Every **+4** over it is a **raise**.\n"
        "- Opposed rolls: the higher total wins; +4 more is a raise.\n"
        "- Wound and Fatigue penalties apply to trait rolls.",
    ),
    (
        "Bennies",
        "- Players start each session with **3 bennies**.\n"
        "- Spend one to **reroll** any trait roll (keep the better), to **soak** damage with a Vigor roll "
        "(each success and raise removes a wound), or to **recover from Shaken** immediately.\n"
        "- Award extra bennies for great roleplay, clever ideas and good humor at the table.",
    ),
    (
        "Combat & Damage",
        "- **Fighting** is rolled against the target's **Parry**. **Shooting/Throwing** is rolled against "
        "**4**, adjusted for range, cover and other modifiers.\n"
        "- **Damage:** if the total **equals or beats Toughness**, the target is **Shaken**. Each **raise "
        "(+4)** over Toughness adds a **Wound**.\n"
        "- **Shaken:** at the start of their turn they roll Spirit to recover (or spend a benny); getting "
        "Shaken again while Shaken causes a wound.\n"
        "- **Wounds:** -1 to trait rolls per wound (max -3). A Wild Card is Incapacitated by a wound beyond "
        "the third; **Extras** are generally taken out by a single wound.\n"
        "- **Multi-action:** each extra action in a round gives **-2** to all of your actions that round.",
    ),
    (
        "Action Deck",
        "- Deal each side or character a card; **highest card acts first** (Ace high; ties broken by suit: "
        "Spades, Hearts, Diamonds, Clubs).\n"
        "- **Jokers** act whenever they like, with **+2** to trait and damage rolls. The deck is reshuffled "
        "after a round in which a Joker was dealt.\n"
        "- **Level Headed** draws extra cards and keeps the best; **Quick** redraws low cards.\n"
        "- A character can go on **Hold** and act later in the round.",
    ),
]

FORGED_IN_THE_DARK_CARDS = [
    (
        "Action Roll",
        "- Roll a pool of d6 equal to the **action rating** (at 0, roll 2d6 and take the **lowest**). Read "
        "the **highest** die.\n"
        "- **6** - full success. **4-5** - success with a consequence. **1-3** - things go badly.\n"
        "- **Multiple 6s** - a critical success.\n"
        "- Add a die: another character **assists**, you **push yourself** (take 2 stress), or you accept a "
        "**devil's bargain**.",
    ),
    (
        "Position & Effect",
        "- **Position** - Controlled, Risky or Desperate: how bad the consequence will be on a 4-5 or 1-3.\n"
        "- **Effect** - Limited, Standard or Great: how much the action accomplishes.\n"
        "- Set both **before** the roll and talk through the trade-offs; the group can adjust them with "
        "advantages and complications.",
    ),
    (
        "Resistance, Stress & Harm",
        "- **Resist** a consequence by rolling the matching attribute (Insight, Prowess or Resolve). "
        "Mark **6 minus the highest die** in stress.\n"
        "- The stress track has **9 boxes**; going past it means **trauma**.\n"
        "- **Harm levels:** 1 Lesser, 2 Moderate, 3 Severe, 4 Fatal. Armor can reduce harm.",
    ),
    (
        "Clocks",
        "- A **clock** is a circle divided into 4, 6 or 8 segments that tracks a threat, a project or a "
        "consequence approaching.\n"
        "- Tick segments as the fiction demands - on a failed roll, a ticking-time event, or progress. "
        "When it fills, the thing happens.",
    ),
]

GENERIC_CARDS = [
    (
        "Combat Checklist",
        "- Who is **surprised**? Who acts first?\n- Where is everyone - ranges, cover, terrain?\n"
        "- What can each combatant do on a turn?\n- Track damage and timed effects as they happen.\n"
        "- Check morale: do the bad guys fight to the death, flee, or surrender?\n"
        "- At the end of each round: effects expire, reinforcements arrive, the environment changes.",
    ),
    (
        "Difficulty Benchmarks",
        "Pick a difficulty *before* the roll and say it out loud.\n\n"
        "- **Routine** - don't roll\n- **Easy**\n- **Average**\n- **Hard**\n- **Heroic**\n\n"
        "Replace these with your game's own target numbers or penalties.",
    ),
]

# Appended to every system's set - the table-craft cards that matter no
# matter what game is being run.
COMMON_CARDS = [
    (
        "Session Checklist",
        "**Before:** review the last session and open threads - pick a strong start - jot 3-5 scenes - "
        "note secrets and clues - have names ready - open the maps.\n\n"
        "**During:** ask \"what do you do?\" - track time and resources - write down promises and "
        "consequences.\n\n"
        "**After:** log what happened - update threads and factions - ask each player for one highlight "
        "and one question - hand out rewards.",
    ),
    (
        "Rulings at the Table",
        "- Unsure of a rule? **Decide fast, note it, look it up after the scene.**\n"
        "- Only call for a roll when failure would be interesting.\n"
        "- End scenes early; skip travel unless something happens.\n"
        "- Check in with the quietest player.",
    ),
]

SYSTEM_CARDS = {
    "d20": DEFAULT_CARDS,
    "palladium": PALLADIUM_CARDS,
    "savage_worlds": SAVAGE_WORLDS_CARDS,
    "forged_in_the_dark": FORGED_IN_THE_DARK_CARDS,
    "generic": GENERIC_CARDS,
}


def normalize_system(system):
    return (system or "generic").strip().lower().replace(" ", "_").replace("-", "_")


def cards_for_system(system):
    """The (title, body) pairs a game with this `system` hint is seeded with:
    its own mechanics cards first, then the shared table-craft cards."""
    base = SYSTEM_CARDS.get(normalize_system(system), GENERIC_CARDS)
    return list(base) + list(COMMON_CARDS)


def _seed_defaults(game_key, system):
    with database.get_cursor(commit=True) as cur:
        for i, (title, body) in enumerate(cards_for_system(system)):
            cur.execute(
                "INSERT INTO gm_screen_cards (game_key, title, body, position) VALUES (?, ?, ?, ?)",
                (game_key, title, body, i),
            )


def list_cards(game_key, system=None):
    """All of a game's cards, seeding the system's starter set the first time
    the screen is opened. Pinned book pages come back with their book's
    title attached (`book_title`; None if the book has since been removed)."""
    game_key = game_key or GLOBAL_KEY
    with database.get_cursor() as cur:
        any_row = cur.execute(
            "SELECT 1 FROM gm_screen_cards WHERE game_key = ? LIMIT 1", (game_key,)
        ).fetchone()
    if not any_row:
        _seed_defaults(game_key, system)
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT c.*, b.title AS book_title
               FROM gm_screen_cards c LEFT JOIN books b ON b.id = c.book_id
               WHERE c.game_key = ? ORDER BY c.position, c.id""",
            (game_key,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_missing_system_cards(game_key, system):
    """Adds any starter card for this system whose title isn't on the screen
    yet (never touches or duplicates existing cards). Lets a screen that was
    seeded with the wrong set - or before per-system sets existed - pick up
    the right ones. Returns how many were added."""
    game_key = game_key or GLOBAL_KEY
    with database.get_cursor() as cur:
        have = {
            r["title"].strip().lower()
            for r in cur.execute(
                "SELECT title FROM gm_screen_cards WHERE game_key = ?", (game_key,)
            ).fetchall()
        }
        next_pos = cur.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM gm_screen_cards WHERE game_key = ?",
            (game_key,),
        ).fetchone()["p"]
    added = 0
    with database.get_cursor(commit=True) as cur:
        for title, body in cards_for_system(system):
            if title.strip().lower() in have:
                continue
            cur.execute(
                "INSERT INTO gm_screen_cards (game_key, title, body, position) VALUES (?, ?, ?, ?)",
                (game_key, title, body, next_pos + added),
            )
            added += 1
    return added


def create_card(game_key, title, body=""):
    game_key = game_key or GLOBAL_KEY
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


def pin_page(game_key, book_id, page_number=None, note=""):
    """Pins a book page to the screen as a card that opens the PDF at that
    page. Pinning the same page twice returns the existing card."""
    game_key = game_key or GLOBAL_KEY
    with database.get_cursor() as cur:
        book = cur.execute("SELECT id, title FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            raise ValueError("That book no longer exists - try rescanning the library.")
        existing = cur.execute(
            "SELECT id FROM gm_screen_cards WHERE game_key = ? AND book_id = ? AND page_number IS ?",
            (game_key, book_id, page_number),
        ).fetchone()
        next_pos = cur.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM gm_screen_cards WHERE game_key = ?",
            (game_key,),
        ).fetchone()["p"]
    if existing:
        return get_card(existing["id"])
    title = book["title"] if page_number is None else f"{book['title']} - p.{page_number}"
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO gm_screen_cards (game_key, title, body, position, book_id, page_number) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (game_key, title, note or "", next_pos, book_id, page_number),
        )
        card_id = cur.lastrowid
    return get_card(card_id)


def get_card(card_id):
    with database.get_cursor() as cur:
        row = cur.execute(
            """SELECT c.*, b.title AS book_title
               FROM gm_screen_cards c LEFT JOIN books b ON b.id = c.book_id
               WHERE c.id = ?""",
            (card_id,),
        ).fetchone()
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
    game_key = game_key or GLOBAL_KEY
    with database.get_cursor() as cur:
        row = cur.execute("SELECT body FROM gm_screen_notes WHERE game_key = ?", (game_key,)).fetchone()
    return row["body"] if row else ""


def set_notes(game_key, body):
    game_key = game_key or GLOBAL_KEY
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO gm_screen_notes (game_key, body) VALUES (?, ?) "
            "ON CONFLICT(game_key) DO UPDATE SET body = excluded.body",
            (game_key, body),
        )


def append_notes(game_key, text, heading=None):
    """Adds a block to the end of the session-notes scratchpad - how an
    encounter roll or a random-table result gets 'sent to session notes'."""
    text = (text or "").strip()
    if not text:
        raise ValueError("There's nothing to add to the notes.")
    existing = get_notes(game_key)
    block = f"{heading}: {text}" if heading else text
    new_body = f"{existing.rstrip()}\n\n{block}" if existing.strip() else block
    set_notes(game_key, new_body)
    return new_body
