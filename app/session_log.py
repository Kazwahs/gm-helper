"""Session log: a running, timestamped list of what happened at the table
("the party met Marlowe", "ambush at the docks", "found 300 gp"), kept per
session. The recap and the 'What actually happened' summary can be built
from it afterwards, so a GM can jot lines mid-game and not write a thing later.

Entries belong to a campaign and (normally) one of its sessions. A line
logged from a tool page, which only knows the game, goes to the game's most
recently active campaign and its current session (see log_target).
"""
from . import campaigns, database, llm

KINDS = ("note", "scene", "combat", "loot", "clue", "roll")
KIND_LABELS = {"note": "Note", "scene": "Scene", "combat": "Combat", "loot": "Loot", "clue": "Clue", "roll": "Roll"}
MAX_TEXT = 1000
SUMMARY_MAX_ENTRIES = 200


def _clean_text(text):
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("Write something to log.")
    return text[:MAX_TEXT]


def _clean_kind(kind):
    kind = (kind or "note").strip().lower()
    if kind not in KINDS:
        raise ValueError("Unknown log type.")
    return kind


def _campaign_of_session(session_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT campaign_id FROM campaign_sessions WHERE id = ?", (session_id,)).fetchone()
    return row["campaign_id"] if row else None


def get_entry(entry_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM campaign_session_log WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def add_entry(campaign_id, text, session_id=None, kind="note"):
    """Adds one line; returns its id. A session that belongs to another
    campaign is refused, so a log can't leak between campaigns."""
    text, kind = _clean_text(text), _clean_kind(kind)
    if not campaigns.get_campaign(campaign_id):
        raise ValueError("Unknown campaign.")
    if session_id not in (None, ""):
        session_id = int(session_id)
        if _campaign_of_session(session_id) != int(campaign_id):
            raise ValueError("That session isn't part of this campaign.")
    else:
        session_id = None
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_session_log (campaign_id, session_id, kind, text) VALUES (?, ?, ?, ?)",
            (campaign_id, session_id, kind, text),
        )
        return cur.lastrowid


def list_entries(campaign_id, session_id=None):
    """Oldest first. session_id=None means the whole campaign."""
    sql, params = "SELECT * FROM campaign_session_log WHERE campaign_id = ?", [campaign_id]
    if session_id not in (None, ""):
        sql += " AND session_id = ?"
        params.append(int(session_id))
    sql += " ORDER BY id"
    with database.get_cursor() as cur:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]


def update_entry(entry_id, field, value):
    if field == "text":
        value = _clean_text(value)
    elif field == "kind":
        value = _clean_kind(value)
    else:
        raise ValueError("That field can't be changed.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_session_log SET {field} = ? WHERE id = ?", (value, entry_id))


def delete_entry(entry_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_session_log WHERE id = ?", (entry_id,))


def counts_by_session(campaign_id):
    """{session_id: number of lines}, for the badges next to each session."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT session_id, COUNT(*) AS n FROM campaign_session_log WHERE campaign_id = ? "
            "AND session_id IS NOT NULL GROUP BY session_id",
            (campaign_id,),
        ).fetchall()
    return {r["session_id"]: r["n"] for r in rows}


# ------------------------------------------------------------ where a line goes
def default_session(campaign_id):
    """The session being played: the lowest-numbered one not yet marked
    played, else the most recent one, else None."""
    sessions = campaigns.list_sessions(campaign_id)  # newest first
    if not sessions:
        return None
    open_ones = [s for s in sessions if (s.get("status") or "planned") != "played"]
    return min(open_ones, key=lambda s: s["session_number"]) if open_ones else sessions[0]


def log_target(game_key):
    """(campaign, session-or-None) a tool page should log into: the game's
    most recently active campaign that isn't completed. None if there isn't one."""
    for c in campaigns.list_campaigns(game_key=game_key):  # most recently active first
        if (c.get("status") or "") != "completed":
            return c, default_session(c["id"])
    return None


# ------------------------------------------------------------ turning a log into text
def entries_as_text(entries):
    """The plain-text version: one '- line' per entry, tagged unless it's a plain note."""
    lines = []
    for e in entries:
        tag = "" if e["kind"] == "note" else f"[{KIND_LABELS.get(e['kind'], e['kind'])}] "
        lines.append(f"- {tag}{e['text']}")
    return "\n".join(lines)


def session_log_text(session_id):
    """Plain text of one session's log, '' when there is none."""
    campaign_id = _campaign_of_session(session_id)
    if campaign_id is None:
        return ""
    return entries_as_text(list_entries(campaign_id, session_id))


def build_summary_prompts(session, entries):
    system_prompt = (
        "You are a tabletop RPG assistant helping a Game Master write up what happened in a session, "
        "for their own campaign records. Turn the raw log lines below into a short summary of one or two "
        "paragraphs in past tense, in the order things happened. Use only what the log says - do not "
        "invent names, outcomes or details, and keep any secrets the GM noted as secrets."
    )
    label = f"Session {session['session_number']}" + (f" - {session['title']}" if (session.get('title') or '').strip() else "")
    return system_prompt, f"{label}\n\nLog:\n{entries_as_text(entries[:SUMMARY_MAX_ENTRIES])}"


def summarize(session_id, use_ai=True):
    """{'summary': text, 'ai': bool}. With AI on this is prose; otherwise (or
    if the model fails) the plain bullet list, so the button always gives
    something usable. Raises ValueError when there is nothing logged."""
    campaign_id = _campaign_of_session(session_id)
    if campaign_id is None:
        raise ValueError("Unknown session.")
    entries = list_entries(campaign_id, session_id)
    if not entries:
        raise ValueError("There's nothing in this session's log yet.")
    plain = entries_as_text(entries)
    if use_ai and llm.is_enabled():
        session = next(s for s in campaigns.list_sessions(campaign_id) if s["id"] == int(session_id))
        system_prompt, user_prompt = build_summary_prompts(session, entries)
        try:
            text = (llm.generate(system_prompt, user_prompt, max_tokens=1200) or "").strip()
        except llm.LLMError:
            text = ""
        if text:
            return {"summary": text, "ai": True}
    return {"summary": plain, "ai": False}
