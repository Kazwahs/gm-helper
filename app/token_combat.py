"""Map tokens that stand for combatants in the initiative tracker.

A token can be linked to a combatant. The map then shows whose turn it is
(a ring on the token) and who is down (greyed with a cross), the GM's token list
shows hit points and conditions with a "Hit" button, and "Next turn" can be
pressed from the map. Players only ever see the ring and the cross, never
hit points, conditions or names beyond the token's own label.

The initiative state stays where it always was (combat_sessions); the link is
two columns on the token: the combatant's id and its name, so a token goes
"unlinked" rather than following a different combatant after the tracker has
been cleared.
"""
import math
import zlib

from . import database, live, map_tokens
from .tools import initiative as initiative_tool

PC_COLOR = "#3b82f6"
FOE_COLOR = "#c2410c"


def _state(game_key):
    return initiative_tool.get_state(game_key)


def _links(map_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT id, combatant_id, combatant_name FROM map_tokens WHERE map_id = ? AND combatant_id IS NOT NULL",
            (map_id,)).fetchall()
    return {r["id"]: (r["combatant_id"], r["combatant_name"] or "") for r in rows}


def _match(combatants, combatant_id, name):
    for c in combatants:
        if c["id"] == combatant_id and (not name or c["name"].lower() == name.lower()):
            return c
    return None


def decorate(map_id, tokens, game_key, for_players=False):
    """Adds combat information to tokens that are linked to a combatant.
    Players get only `current` and `down`; the GM also gets `combatant_id`, `hp`,
    `max_hp`, `conditions` and, for a link whose combatant has left, `orphan`.
    Unlinked tokens are returned as they are."""
    links = _links(map_id)
    if not links:
        return tokens
    state = _state(game_key)
    out = []
    for t in tokens:
        link = links.get(t["id"])
        if link is None:
            out.append(t)
            continue
        c = _match(state["combatants"], *link)
        item = dict(t)
        if c is None:
            if not for_players:
                item.update(combatant_id=link[0], orphan=True)
        else:
            item.update(current=(c["id"] == state["current_id"]), down=bool(c["down"]))
            if not for_players:
                item.update(combatant_id=c["id"], hp=c["hp"], max_hp=c["max_hp"],
                            conditions=[x["name"] + (f" ({x['rounds']})" if x["rounds"] else "") for x in c["conditions"]])
        out.append(item)
    return out


def version_part(map_id, game_key):
    """A short string that changes whenever what the players can see of the
    combat changes (whose turn, who is down), or '' if no token is linked."""
    links = _links(map_id)
    if not links:
        return ""
    state = _state(game_key)
    seen = []
    for token_id in sorted(links):
        c = _match(state["combatants"], *links[token_id])
        seen.append((token_id, bool(c), c and c["id"] == state["current_id"], c and bool(c["down"])))
    return format(zlib.crc32(repr(seen).encode("utf-8")), "08x")


def combat_view(map_id, game_key):
    """What the GM's token panel needs: decorated tokens, the combatants
    available to link, and the round and whose turn it is."""
    state = _state(game_key)
    tokens = decorate(map_id, map_tokens.list_tokens(map_id), game_key)
    current = next((c for c in state["combatants"] if c["id"] == state["current_id"]), None)
    return {
        "tokens": tokens,
        "combatants": [{"id": c["id"], "name": c["name"], "is_pc": bool(c.get("is_pc"))} for c in state["combatants"]],
        "round": state["round"] if state["combatants"] else None,
        "current": current["name"] if current else None,
        "orphans": sum(1 for t in tokens if t.get("orphan")),
    }


def _map(map_id):
    from . import maps_store
    m = maps_store.get_map(map_id)
    if not m:
        raise ValueError("Unknown map.")
    return m


def set_link(token_id, combatant_id):
    """Links a token to a combatant (or unlinks it with combatant_id None/'')."""
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT t.id, t.map_id, m.game_key FROM map_tokens t JOIN maps m ON m.id = t.map_id WHERE t.id = ?",
            (token_id,)).fetchone()
    if not row:
        raise ValueError("That token isn't on the map any more.")
    name = None
    if combatant_id in (None, ""):
        combatant_id = None
    else:
        try:
            combatant_id = int(combatant_id)
        except (TypeError, ValueError):
            raise ValueError("Pick a combatant from the list.")
        c = _match(_state(row["game_key"])["combatants"], combatant_id, "")
        if c is None:
            raise ValueError("That combatant isn't on the tracker.")
        name = c["name"]
        with database.get_cursor() as cur:
            taken = cur.execute(
                "SELECT id FROM map_tokens WHERE map_id = ? AND combatant_id = ? AND id != ?",
                (row["map_id"], combatant_id, token_id)).fetchone()
        if taken:
            raise ValueError(f"{name} already has a token on this map.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE map_tokens SET combatant_id = ?, combatant_name = ? WHERE id = ?",
                    (combatant_id, name, token_id))
        map_tokens._bump(cur, row["map_id"])


def _has_token(linked, c):
    return any(cid == c["id"] and (not name or name.lower() == c["name"].lower()) for cid, name in linked)


def add_from_initiative(map_id, foes_hidden=True):
    """Puts a token on the map for each combatant that hasn't got one yet: the
    party in a row along the top, the foes in rows below. Foes start hidden so
    they can be dragged into position before the players see them."""
    m = _map(map_id)
    state = _state(m["game_key"])
    linked = list(_links(map_id).values())
    width, _height = live.svg_size(m["svg"])
    radius = live.token_radius(m)
    step = radius * 2.3
    per_row = max(1, int((width - radius * 2) // step))
    party = sum(1 for c in state["combatants"] if c.get("is_pc"))
    foe_top = radius * 1.4 + math.ceil(party / per_row) * step + radius
    placed = {True: 0, False: 0}
    added = skipped = 0
    for c in state["combatants"]:
        if _has_token(linked, c):
            continue
        pc = bool(c.get("is_pc"))
        row, col = divmod(placed[pc], per_row)
        placed[pc] += 1
        x = radius * 1.4 + col * step
        y = (radius * 1.4 if pc else foe_top) + row * step
        try:
            token_id = map_tokens.add_token(map_id, c["name"], x, y, PC_COLOR if pc else FOE_COLOR,
                                            1, hidden=(not pc and foes_hidden))
        except ValueError:
            skipped += 1  # the map is full
            continue
        with database.get_cursor(commit=True) as cur:
            cur.execute("UPDATE map_tokens SET combatant_id = ?, combatant_name = ? WHERE id = ?",
                        (c["id"], c["name"], token_id))
        added += 1
    return {"added": added, "skipped": skipped}


def remove_orphans(map_id):
    """Removes tokens whose combatant has left the tracker."""
    m = _map(map_id)
    state = _state(m["game_key"])
    removed = 0
    for token_id, link in _links(map_id).items():
        if _match(state["combatants"], *link) is None:
            map_tokens.delete_token(token_id)
            removed += 1
    return removed
