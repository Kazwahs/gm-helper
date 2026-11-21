"""The living side of a campaign: secrets and clues the party may uncover,
progress clocks that tick while the party is busy elsewhere, how factions stand
with the party, and the ties between NPCs and factions.

  - secrets         prepared in advance, then marked *revealed* at the table; revealing
                    one writes a 'clue' line into the session log, so the recap sees it
  - clocks          "the ritual is 3 of 6 done": fill them as the world moves
  - factions        each has a standing with the party (hostile ... allied); see campaigns.STANDINGS
  - relationships   ties between NPCs and factions (allies, rivals, owes, family ...)
"""
import re

from . import campaigns, database, session_log

SECRET_STATUSES = ("hidden", "revealed")
STANDINGS = campaigns.STANDINGS
TIE_KINDS = ("ally", "enemy", "rival", "family", "employer", "lover", "owes", "fears", "other")
TIE_LABELS = {"ally": "allied with", "enemy": "enemy of", "rival": "rival of", "family": "family of",
              "employer": "employs", "lover": "involved with", "owes": "owes", "fears": "fears", "other": "tied to"}
ENTITY_KINDS = ("npc", "faction")
MIN_SEGMENTS, MAX_SEGMENTS = 2, 12
MAX_TEXT = 1000
_BULLET = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")


# ---------------------------------------------------------------- helpers ---

def _text(value, label, required=True, limit=MAX_TEXT):
    value = " ".join((value or "").split())
    if required and not value:
        raise ValueError(f"Write the {label} first.")
    return value[:limit]


def _require_campaign(campaign_id):
    if not campaigns.get_campaign(campaign_id):
        raise ValueError("Unknown campaign.")


def _session_in(campaign_id, session_id):
    """None for blank, otherwise a session id that belongs to this campaign."""
    if session_id in (None, ""):
        return None
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        raise ValueError("Pick a valid session.")
    with database.get_cursor() as cur:
        row = cur.execute("SELECT 1 FROM campaign_sessions WHERE id = ? AND campaign_id = ?",
                          (session_id, campaign_id)).fetchone()
    if not row:
        raise ValueError("That session isn't part of this campaign.")
    return session_id


# ---------------------------------------------------------------- secrets ---

def list_secrets(campaign_id):
    """Hidden ones first (in the order they were written), then revealed ones;
    each with the session numbers filled in."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT s.*, p.session_number AS planned_number, r.session_number AS revealed_number
               FROM campaign_secrets s
               LEFT JOIN campaign_sessions p ON p.id = s.session_id
               LEFT JOIN campaign_sessions r ON r.id = s.revealed_session_id
               WHERE s.campaign_id = ?
               ORDER BY (s.status = 'revealed'), s.id""", (campaign_id,)).fetchall()
    return [dict(r) for r in rows]


def get_secret(secret_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM campaign_secrets WHERE id = ?", (secret_id,)).fetchone()
    return dict(row) if row else None


def _secret_or_error(secret_id):
    secret = get_secret(secret_id)
    if not secret:
        raise ValueError("That secret no longer exists.")
    return secret


def add_secret(campaign_id, text, source="", session_id=None):
    _require_campaign(campaign_id)
    text = _text(text, "secret")
    source = _text(source, "source", required=False, limit=200)
    session_id = _session_in(campaign_id, session_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO campaign_secrets (campaign_id, text, source, session_id) VALUES (?, ?, ?, ?)",
                    (campaign_id, text, source, session_id))
        return cur.lastrowid


def update_secret(secret_id, field, value):
    secret = _secret_or_error(secret_id)
    if field == "text":
        value = _text(value, "secret")
    elif field == "source":
        value = _text(value, "source", required=False, limit=200)
    elif field == "session_id":
        value = _session_in(secret["campaign_id"], value)
    else:
        raise ValueError("That can't be changed.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_secrets SET {field} = ? WHERE id = ?", (value, secret_id))


def delete_secret(secret_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_secrets WHERE id = ?", (secret_id,))


def reveal(secret_id, session_id=None):
    """Marks a secret as learned by the party and notes it in the session log.
    The log goes into `session_id`, else the session it was prepped for, else
    the session being played. Revealing one that is already out does nothing."""
    secret = _secret_or_error(secret_id)
    if secret["status"] == "revealed":
        return secret
    campaign_id = secret["campaign_id"]
    chosen = _session_in(campaign_id, session_id) or secret["session_id"]
    if chosen is None:
        current = session_log.default_session(campaign_id)
        chosen = current["id"] if current else None
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaign_secrets SET status = 'revealed', revealed_session_id = ?, "
                    "revealed_at = datetime('now') WHERE id = ?", (chosen, secret_id))
    session_log.add_entry(campaign_id, "Revealed: " + secret["text"], session_id=chosen, kind="clue")
    return get_secret(secret_id)


def hide_again(secret_id):
    """Takes a secret back to hidden (the session log line stays: it did happen)."""
    _secret_or_error(secret_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaign_secrets SET status = 'hidden', revealed_session_id = NULL, "
                    "revealed_at = NULL WHERE id = ?", (secret_id,))
    return get_secret(secret_id)


def import_from_sessions(campaign_id):
    """Turns the 'Secrets & clues' text of each session's prep into secrets,
    one per line (leading bullets and numbers dropped), each prepped for the
    session it came from. Lines already in the list are skipped."""
    _require_campaign(campaign_id)
    have = {s["text"].lower() for s in list_secrets(campaign_id)}
    added = skipped = 0
    for session in reversed(campaigns.list_sessions(campaign_id)):  # oldest first
        for raw in (session["secrets_clues"] or "").splitlines():
            line = " ".join(_BULLET.sub("", raw).split())
            if not line:
                continue
            line = line[:MAX_TEXT]
            if line.lower() in have:
                skipped += 1
                continue
            have.add(line.lower())
            with database.get_cursor(commit=True) as cur:
                cur.execute("INSERT INTO campaign_secrets (campaign_id, text, session_id) VALUES (?, ?, ?)",
                            (campaign_id, line, session["id"]))
            added += 1
    return {"added": added, "skipped": skipped}


# ----------------------------------------------------------------- clocks ---

def _segments(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"A clock needs {MIN_SEGMENTS} to {MAX_SEGMENTS} segments.")
    if not MIN_SEGMENTS <= number <= MAX_SEGMENTS:
        raise ValueError(f"A clock needs {MIN_SEGMENTS} to {MAX_SEGMENTS} segments.")
    return number


def list_clocks(campaign_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT c.*, f.name AS faction_name FROM campaign_clocks c
               LEFT JOIN campaign_factions f ON f.id = c.faction_id
               WHERE c.campaign_id = ? ORDER BY (c.filled >= c.segments), c.id""", (campaign_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["full"] = d["filled"] >= d["segments"]
        out.append(d)
    return out


def get_clock(clock_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM campaign_clocks WHERE id = ?", (clock_id,)).fetchone()
    return dict(row) if row else None


def _clock_or_error(clock_id):
    clock = get_clock(clock_id)
    if not clock:
        raise ValueError("That clock no longer exists.")
    return clock


def add_clock(campaign_id, name, segments=6, faction_id=None, note=""):
    _require_campaign(campaign_id)
    name = _text(name, "clock's name", limit=120)
    segments = _segments(segments)
    faction_id = campaigns._link_id(faction_id, "campaign_factions", campaign_id, "faction")
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO campaign_clocks (campaign_id, faction_id, name, segments, note) VALUES (?, ?, ?, ?, ?)",
                    (campaign_id, faction_id, name, segments, _text(note, "note", required=False)))
        return cur.lastrowid


def advance_clock(clock_id, delta=1):
    """Moves the clock by `delta` segments (negative to roll it back), stopping
    at empty and full. Returns the clock, with `just_filled` set when this
    move is the one that completed it."""
    clock = _clock_or_error(clock_id)
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        raise ValueError("Move the clock by a whole number of segments.")
    filled = max(0, min(clock["segments"], clock["filled"] + delta))
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaign_clocks SET filled = ? WHERE id = ?", (filled, clock_id))
    result = get_clock(clock_id)
    result["just_filled"] = filled >= clock["segments"] and clock["filled"] < clock["segments"]
    return result


def update_clock(clock_id, field, value):
    clock = _clock_or_error(clock_id)
    if field == "name":
        value = _text(value, "clock's name", limit=120)
    elif field == "note":
        value = _text(value, "note", required=False)
    elif field == "segments":
        value = _segments(value)
    elif field == "faction_id":
        value = campaigns._link_id(value, "campaign_factions", clock["campaign_id"], "faction")
    else:
        raise ValueError("That can't be changed.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_clocks SET {field} = ? WHERE id = ?", (value, clock_id))
        if field == "segments":  # shrinking a clock never leaves it over-full
            cur.execute("UPDATE campaign_clocks SET filled = MIN(filled, segments) WHERE id = ?", (clock_id,))


def delete_clock(clock_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_clocks WHERE id = ?", (clock_id,))


# ---------------------------------------------------------- relationships ---

def _entity_name(cur, kind, entity_id, campaign_id):
    table = "campaign_npcs" if kind == "npc" else "campaign_factions"
    row = cur.execute(f"SELECT name FROM {table} WHERE id = ? AND campaign_id = ?", (entity_id, campaign_id)).fetchone()
    return row["name"] if row else None


def list_relationships(campaign_id):
    """Every tie, with both names filled in and a readable phrase:
    {a_name, b_name, kind, label, note, phrase}."""
    with database.get_cursor() as cur:
        rows = cur.execute("SELECT * FROM campaign_relationships WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["a_name"] = _entity_name(cur, d["a_kind"], d["a_id"], campaign_id)
            d["b_name"] = _entity_name(cur, d["b_kind"], d["b_id"], campaign_id)
            if d["a_name"] is None or d["b_name"] is None:
                continue  # one end is gone
            d["label"] = TIE_LABELS.get(d["kind"], d["kind"])
            d["phrase"] = f"{d['a_name']} {d['label']} {d['b_name']}"
            out.append(d)
    return out


def _parse_entity(value):
    """'npc:3' or 'faction:2' -> ('npc', 3)."""
    kind, _, raw = (value or "").partition(":")
    if kind not in ENTITY_KINDS or not raw.strip().isdigit():
        raise ValueError("Pick who the tie is between.")
    return kind, int(raw)


def add_relationship(campaign_id, a, b, kind="ally", note=""):
    """`a` and `b` look like 'npc:3' or 'faction:2'. Reads as 'A <kind> B'."""
    _require_campaign(campaign_id)
    kind = (kind or "").strip().lower()
    if kind not in TIE_KINDS:
        raise ValueError("Unknown kind of tie.")
    a_kind, a_id = _parse_entity(a)
    b_kind, b_id = _parse_entity(b)
    if (a_kind, a_id) == (b_kind, b_id):
        raise ValueError("Pick two different people or factions.")
    with database.get_cursor() as cur:
        if _entity_name(cur, a_kind, a_id, campaign_id) is None or _entity_name(cur, b_kind, b_id, campaign_id) is None:
            raise ValueError("Both ends have to be in this campaign.")
    note = _text(note, "note", required=False, limit=300)
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_relationships (campaign_id, a_kind, a_id, b_kind, b_id, kind, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (campaign_id, a_kind, a_id, b_kind, b_id) "
            "DO UPDATE SET kind = excluded.kind, note = excluded.note",
            (campaign_id, a_kind, a_id, b_kind, b_id, kind, note))
        return cur.execute(
            "SELECT id FROM campaign_relationships WHERE campaign_id = ? AND a_kind = ? AND a_id = ? "
            "AND b_kind = ? AND b_id = ?", (campaign_id, a_kind, a_id, b_kind, b_id)).fetchone()["id"]


def delete_relationship(relationship_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_relationships WHERE id = ?", (relationship_id,))


def relationships_of(campaign_id, kind, entity_id):
    """The ties that mention one NPC or faction, as phrases."""
    return [r["phrase"] for r in list_relationships(campaign_id)
            if (r["a_kind"], r["a_id"]) == (kind, entity_id) or (r["b_kind"], r["b_id"]) == (kind, entity_id)]


def entity_choices(campaign_id):
    """[{value: 'npc:3', name, kind}] for the tie pickers."""
    out = [{"value": f"faction:{f['id']}", "name": f["name"], "kind": "faction"} for f in campaigns.list_factions(campaign_id)]
    out += [{"value": f"npc:{n['id']}", "name": n["name"], "kind": "npc"} for n in campaigns.list_npcs(campaign_id)]
    return out


# ----------------------------------------------------------------- bundle ---

def payload(campaign_id):
    return {
        "secrets": list_secrets(campaign_id),
        "clocks": list_clocks(campaign_id),
        "relationships": list_relationships(campaign_id),
        "entities": entity_choices(campaign_id),
        "sessions": [{"id": s["id"], "number": s["session_number"], "title": s["title"] or ""}
                     for s in campaigns.list_sessions(campaign_id)],
        "tie_kinds": [{"value": k, "label": TIE_LABELS[k]} for k in TIE_KINDS],
    }
