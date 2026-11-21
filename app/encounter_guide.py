"""Encounter builder: a rough "how hard is this fight?" guide.

Palladium games (Rifts, Palladium Fantasy, Heroes Unlimited) have no challenge
rating or XP budget, so this does not pretend to be one. It compares the two
sides' strength, where a side's strength is

    attacks per round  x  hit points  x  how hard each hit lands

and the result is the foes' strength divided by the party's, called *pressure*.
That ratio is Lanchester's square law for a straight fight in which everyone
keeps attacking until one side is gone, so it also gives a projected result:
at pressure P below 1 the party wins with about sqrt(1 - P) of its hit points
left, and above 1 the foes win with about sqrt(1 - 1/P) of theirs left.

What it leaves out is the whole point of the caveats it prints: armor, magic,
psionics, special powers, tactics, terrain, and surprise. The defaults and the
difficulty bands are editable in data/difficulty_guide.json and are this app's
own rough choices, not published rules.

Rifts mixes two scales: one mega-damage (M.D.C.) point is worth 100 S.D.C.
Groups and party members can be flagged `mdc`; when both scales appear in one
fight, hit points are converted to S.D.C. before comparing.
"""
import json
import math

from . import config, database, saved_encounters
from .library import games as games_module
from .tools import initiative as initiative_tool

with open(config.DIFFICULTY_GUIDE_PATH, "r", encoding="utf-8") as _f:
    _DATA = json.load(_f)

MAX_PARTY = 12
MAX_NAME = 60
MAX_HP = 1000000
MAX_ATTACKS = saved_encounters.MAX_ATTACKS


class GuideError(ValueError):
    pass


# --------------------------------------------------------------- profiles ---

def profile_for(game_key):
    """The profile for a game: its own, else the one for its system, else generic.
    Returns a copy with `key`, `label`, `party_attacks`, `foe_attacks`, `mdc`, `notes`."""
    profiles = _DATA["profiles"]
    game = games_module.get_game(game_key) if game_key and game_key != "_global" else None
    system = (game or {}).get("system") or "generic"
    for key in (game_key, system, "generic"):
        if key in profiles:
            out = dict(profiles[key])
            out["key"] = key
            out["notes"] = list(out.get("notes", []))
            return out
    raise GuideError("No difficulty profile is configured.")        # the shipped file always has "generic"


def options():
    """What the page needs to draw its controls."""
    return {
        "foe_hits": [{"key": o["key"], "label": o["label"]} for o in _DATA["foe_hits"]],
        "default_foe_hits": _DATA["default_foe_hits"],
        "bands": [{"key": b["key"], "label": b["label"], "text": b["text"]} for b in _DATA["bands"]],
    }


# ------------------------------------------------------------------ party ---

def _clean_member(raw, profile, number):
    where = f"Party member {number}: "
    if not isinstance(raw, dict):
        raise GuideError(where + "that isn't a valid entry.")
    name = " ".join(str(raw.get("name") or "").split())[:MAX_NAME] or f"PC {number}"
    try:
        hp = int(raw.get("hp"))
    except (TypeError, ValueError):
        raise GuideError(where + "enter hit points as a whole number.")
    if not 1 <= hp <= MAX_HP:
        raise GuideError(where + f"hit points must be between 1 and {MAX_HP}.")
    raw_attacks = raw.get("attacks")
    if raw_attacks in (None, ""):
        attacks = profile["party_attacks"]
    else:
        try:
            attacks = int(raw_attacks)
        except (TypeError, ValueError):
            raise GuideError(where + "attacks per round must be a whole number.")
    if not 1 <= attacks <= MAX_ATTACKS:
        raise GuideError(where + f"attacks per round must be between 1 and {MAX_ATTACKS}.")
    return {"name": name, "hp": hp, "attacks": attacks, "mdc": bool(raw.get("mdc"))}


def clean_party(members, game_key):
    if not isinstance(members, list):
        raise GuideError("The party should be a list.")
    if len(members) > MAX_PARTY:
        raise GuideError(f"A party can have up to {MAX_PARTY} members.")
    profile = profile_for(game_key)
    return [_clean_member(m, profile, i) for i, m in enumerate(members, start=1)]


def _party_key(game_key):
    return "party:" + (game_key or "_global")


def get_party(game_key):
    raw = database.get_setting(_party_key(game_key))
    if not raw:
        return []
    try:
        return clean_party(json.loads(raw), game_key)
    except (ValueError, TypeError):         # damaged setting: start over rather than break the page
        return []


def set_party(game_key, members):
    cleaned = clean_party(members, game_key)
    database.set_setting(_party_key(game_key), json.dumps(cleaned))
    return cleaned


def party_from_board(game_key):
    """The PCs on the initiative board that have hit points, as party members.
    Attacks per round come from the game's profile; M.D.C. is left for the GM to tick."""
    profile = profile_for(game_key)
    members = []
    for c in initiative_tool.get_state(game_key)["combatants"]:
        if c.get("is_pc") and c.get("max_hp"):
            members.append({"name": c["name"], "hp": int(c["max_hp"]), "attacks": profile["party_attacks"], "mdc": False})
    return clean_party(members[:MAX_PARTY], game_key)


# ----------------------------------------------------------------- assess ---

def _band_for(pressure):
    for band in _DATA["bands"]:
        if band["max"] is None or pressure <= band["max"]:
            return band
    return _DATA["bands"][-1]


def _foe_hits_factor(key):
    for option in _DATA["foe_hits"]:
        if option["key"] == key:
            return option
    raise GuideError("Pick how hard the foes hit from the list.")


def _ratio_text(value, noun):
    if value >= 10:
        return f"about {round(value)} times {noun}"
    if 0.95 <= value < 1.05:
        return f"about {noun}"
    if value < 0.1:
        return f"{value:.2f} times {noun}"
    return f"{value:.1f} times {noun}"


def assess(game_key, entries, party, foe_hits=None):
    """Rates a fight. `entries` are saved-encounter entries (name, quantity, hp,
    optional attacks and mdc); `party` is a list of members (see clean_party).
    Raises GuideError with a plain-language message when something is missing."""
    profile = profile_for(game_key)
    party = clean_party(party or [], game_key)
    if not party:
        raise GuideError("Add at least one party member first.")
    if not entries:
        raise GuideError("Add some foes first, one group per line like 4x Goblin (hp 7).")
    hits = _foe_hits_factor(foe_hits or _DATA["default_foe_hits"])

    missing = [e["name"] for e in entries if not e.get("hp")]
    if missing:
        raise GuideError("Give these foes hit points so they can be compared: " + ", ".join(missing) + ". For example: 4x Goblin (hp 7).")

    foe_groups = []
    defaulted = []
    for e in entries:
        attacks = e.get("attacks")
        if not attacks:
            attacks = profile["foe_attacks"]
            defaulted.append(e["name"])
        foe_groups.append({"name": e["name"], "count": e.get("quantity", 1), "hp": e["hp"], "attacks": attacks, "mdc": bool(e.get("mdc"))})

    party_mdc = [m["mdc"] for m in party]
    foe_mdc = [g["mdc"] for g in foe_groups]
    any_mdc, any_sdc = any(party_mdc + foe_mdc), not all(party_mdc + foe_mdc)
    mixed = any_mdc and any_sdc
    scale = _DATA["mdc_to_sdc"] if mixed else 1
    unit = "S.D.C. equivalent" if mixed else ("M.D.C." if any_mdc else "hit points")

    def pool(hp, is_mdc):
        return hp * (scale if is_mdc else 1)

    party_hp = sum(pool(m["hp"], m["mdc"]) for m in party)
    party_attacks = sum(m["attacks"] for m in party)
    foe_hp = sum(pool(g["hp"], g["mdc"]) * g["count"] for g in foe_groups)
    foe_attacks = sum(g["attacks"] * g["count"] for g in foe_groups)
    foe_count = sum(g["count"] for g in foe_groups)

    pressure = (foe_hp * foe_attacks * hits["factor"]) / (party_hp * party_attacks)
    band = _band_for(pressure)
    if pressure < 1:
        winner, left = "party", math.sqrt(1 - pressure)
    elif pressure > 1:
        winner, left = "foes", math.sqrt(1 - 1 / pressure)
    else:
        winner, left = "neither", 0.0
    left_pct = int(round(left * 100))

    if winner == "party":
        outcome = f"In a straight fight the party wins with about {left_pct}% of its hit points left."
    elif winner == "foes":
        outcome = f"In a straight fight the foes win with about {left_pct}% of their hit points left."
    else:
        outcome = "In a straight fight both sides wipe each other out."

    hp_text = _ratio_text(foe_hp / party_hp, "the party's hit points")
    attack_text = _ratio_text(foe_attacks / party_attacks, "the party's attacks per round")
    comparison = f"The foes have {hp_text} and make {attack_text}."
    cautions = []
    if mixed:
        cautions.append(f"This fight mixes M.D.C. and S.D.C. Hit points were converted at 1 M.D.C. = {scale} S.D.C., but damage was not: "
                        "S.D.C. weapons generally can't hurt M.D.C. targets at all, and mega-damage hits are worth "
                        f"{scale} times as much against S.D.C. targets. Use 'how hard the foes hit' and your own judgment.")
        if all(party_mdc) and not any(foe_mdc):
            cautions.append("The party is M.D.C. and the foes are S.D.C.: unless the foes carry mega-damage weapons, they can't hurt the party.")
        elif all(foe_mdc) and not any(party_mdc):
            cautions.append("The foes are M.D.C. and the party is S.D.C.: unless the party has mega-damage weapons or magic, they can't hurt the foes.")
    if defaulted:
        cautions.append(f"No attacks per round were given for {', '.join(defaulted)}, so {profile['foe_attacks']} per round was assumed. "
                        "Add attacks N to a line to change that.")
    if hits["factor"] != 1:
        cautions.append(f"Foe damage per hit was set to {hits['label'].lower()}.")
    cautions.extend(profile["notes"])

    return {
        "pressure": round(pressure, 3),
        "band": band["key"], "label": band["label"], "band_text": band["text"],
        "winner": winner, "left_pct": left_pct,
        "outcome": outcome, "comparison": comparison,
        "unit": unit, "mixed": mixed,
        "party": {"members": len(party), "hp": party_hp, "attacks": party_attacks},
        "foes": {"count": foe_count, "groups": len(foe_groups), "hp": foe_hp, "attacks": foe_attacks},
        "foe_hits": hits["key"],
        "cautions": cautions,
        "profile": {"key": profile["key"], "label": profile["label"], "mdc": bool(profile.get("mdc")),
                    "party_attacks": profile["party_attacks"], "foe_attacks": profile["foe_attacks"]},
        "rough": "Rough guide only: it is this app's own comparison, not a rule from any game book.",
    }


def assess_text(game_key, body, party, foe_hits=None):
    """assess() for foes typed as text (one group per line, as saved encounters)."""
    try:
        entries = saved_encounters.parse_body(body)
    except ValueError as e:
        raise GuideError(str(e))
    return assess(game_key, entries, party, foe_hits)
