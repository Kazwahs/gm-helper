"""AI helpers for a campaign: a 'Previously on...' recap built from the GM's
own session summaries, and next-session prep suggestions built from the
campaign's open threads, cast and factions.

Both only ever send the GM's own campaign text to the configured LLM, and
both are gated behind llm.is_enabled() like every other AI feature.
"""
import re

from . import campaigns, llm, session_log

RECAP_SESSIONS = 5
SUMMARY_CHARS = 1500


class CampaignAIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _require_ai():
    if not llm.is_enabled():
        raise CampaignAIError("AI features aren't enabled yet - set up and test a connection in Settings.")


def _clip(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def played_summaries(campaign_id, limit=RECAP_SESSIONS):
    """The most recent sessions with something to recap, oldest first: played
    sessions that have a written summary, plus any session with no summary
    yet whose live log has lines in it (the log text stands in for the summary,
    so jotting notes during play is enough for a recap)."""
    logged = session_log.counts_by_session(campaign_id)
    picked = []
    for s in campaigns.list_sessions(campaign_id):  # newest first
        summary = (s["summary"] or "").strip()
        if summary:
            if s["status"] == "played":
                picked.append(s)
        elif logged.get(s["id"]):
            text = session_log.session_log_text(s["id"])
            if text:
                picked.append({**s, "summary": text, "from_log": True})
        if len(picked) >= limit:
            break
    return list(reversed(picked))


def _campaign_context(detail):
    c = detail["campaign"]
    lines = [f"Campaign: {c['title']}"]
    if (c["pitch"] or "").strip():
        lines.append(f"Premise: {_clip(c['pitch'], 600)}")
    if (c["tone"] or "").strip():
        lines.append(f"Tone: {_clip(c['tone'], 300)}")
    return lines


def build_recap_prompts(detail, sessions):
    system_prompt = (
        "You are a tabletop RPG assistant helping a Game Master recap the story so far before play "
        "resumes. Write a short, evocative 'Previously on...' recap addressed to the players, based "
        "only on the GM's session summaries below. Do not invent plot details that aren't in the "
        "summaries, and do not reveal anything marked as a secret. One tight paragraph, 4-7 sentences."
    )
    parts = _campaign_context(detail)
    for s in sessions:
        label = f"Session {s['session_number']}" + (f" - {s['title']}" if (s["title"] or "").strip() else "")
        parts.append(f"{label}:\n{_clip(s['summary'], SUMMARY_CHARS)}")
    return system_prompt, "\n\n".join(parts)


def generate_recap(campaign_id):
    _require_ai()
    detail = campaigns.get_campaign_detail(campaign_id)
    if not detail:
        raise CampaignAIError("Unknown campaign.", status=404)
    sessions = played_summaries(campaign_id)
    if not sessions:
        raise CampaignAIError(
            "There are no played sessions with a 'What actually happened' summary or session log yet - "
            "mark a session as Played and write a few lines about it first, or log a few lines as you play."
        )
    system_prompt, user_prompt = build_recap_prompts(detail, sessions)
    try:
        # Generous budget: "thinking" models spend part of it reasoning first.
        recap = llm.generate(system_prompt, user_prompt, max_tokens=1600)
    except llm.LLMError as e:
        raise CampaignAIError(str(e), status=502)
    if not (recap or "").strip():
        raise CampaignAIError(
            "The model didn't return any text - try again, or try a model that doesn't spend its "
            "whole budget reasoning internally.", status=502)
    return {"recap": recap.strip(), "sessions_used": [s["session_number"] for s in sessions]}


# ------------------------------------------------------------ prep ideas
_SECTION_RE = re.compile(
    r"^\s*(?:#+\s*)?[*_]*\s*(STRONG START|SCENES|SECRETS(?: AND| &)? CLUES)\s*[*_]*\s*(?::\s*[*_]*\s*(.*))?$",
    re.IGNORECASE,
)
_SECTION_KEYS = {"strong start": "strong_start", "scenes": "scenes"}


def parse_prep(text):
    """Splits the model's answer into {strong_start, scenes, secrets_clues} by
    its 'STRONG START / SCENES / SECRETS AND CLUES' headings. Anything the
    model wrote that doesn't fit (or if it ignored the headings) is left for
    the caller to show raw."""
    sections = {"strong_start": [], "scenes": [], "secrets_clues": []}
    current = None
    for line in (text or "").splitlines():
        m = _SECTION_RE.match(line)
        if m:
            name = re.sub(r"\s+", " ", m.group(1).lower().replace("&", "and"))
            current = _SECTION_KEYS.get(name, "secrets_clues")
            if (m.group(2) or "").strip():
                sections[current].append(m.group(2).strip())
            continue
        if current is not None:
            sections[current].append(line.rstrip())
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def build_prep_prompts(detail, sessions):
    system_prompt = (
        "You are a tabletop RPG assistant helping a Game Master prepare the next session, in the "
        "'lazy' style: a strong start, a handful of flexible scenes, and secrets/clues that could "
        "surface. Use ONLY the campaign material provided and build on its open threads, cast and "
        "factions - don't introduce a new plot. Answer in exactly this format, with these three "
        "headings and plain text lists under them:\n"
        "STRONG START:\n(one or two sentences)\n"
        "SCENES:\n(5 short bullet points, each one a scene that could happen)\n"
        "SECRETS AND CLUES:\n(5 short bullet points, each a discoverable secret or clue)"
    )
    parts = _campaign_context(detail)
    open_threads = [t for t in detail["threads"] if t["status"] in ("seed", "active")]
    if open_threads:
        parts.append("Open plot threads:\n" + "\n".join(
            f"- {t['title']}" + (f": {_clip(t['description'], 200)}" if (t["description"] or "").strip() else "")
            + (f" (stakes: {_clip(t['stakes'], 150)})" if (t["stakes"] or "").strip() else "")
            for t in open_threads))
    if detail["npcs"]:
        parts.append("Cast:\n" + "\n".join(
            f"- {n['name']}" + (f" ({n['role']})" if (n["role"] or "").strip() else "")
            + (f", {n['disposition']}" if n["disposition"] else "")
            + (f", wants: {_clip(n['motivation'], 150)}" if (n["motivation"] or "").strip() else "")
            + (f", secret: {_clip(n['secret'], 150)}" if (n["secret"] or "").strip() else "")
            + (f" [{n['faction_name']}]" if n.get("faction_name") else "")
            + (f" @ {n['location_name']}" if n.get("location_name") else "")
            for n in detail["npcs"][:15]))
    if detail["factions"]:
        parts.append("Factions:\n" + "\n".join(
            f"- {f['name']}" + (f": {_clip(f['goal'], 150)}" if (f["goal"] or "").strip() else "")
            for f in detail["factions"][:10]))
    if sessions:
        s = sessions[-1]
        parts.append(f"Last played session (Session {s['session_number']}):\n{_clip(s['summary'], SUMMARY_CHARS)}")
    return system_prompt, "\n\n".join(parts)


def suggest_prep(campaign_id):
    _require_ai()
    detail = campaigns.get_campaign_detail(campaign_id)
    if not detail:
        raise CampaignAIError("Unknown campaign.", status=404)
    has_material = (
        (detail["campaign"]["pitch"] or "").strip() or detail["threads"] or detail["npcs"] or detail["factions"]
    )
    if not has_material:
        raise CampaignAIError(
            "Add a premise, a plot thread, or a few NPCs or factions first - there's nothing to build prep from yet.")
    system_prompt, user_prompt = build_prep_prompts(detail, played_summaries(campaign_id, limit=1))
    try:
        raw = llm.generate(system_prompt, user_prompt, max_tokens=2000)
    except llm.LLMError as e:
        raise CampaignAIError(str(e), status=502)
    if not (raw or "").strip():
        raise CampaignAIError("The model didn't return any text - try again.", status=502)
    return {"raw": raw.strip(), **parse_prep(raw)}
