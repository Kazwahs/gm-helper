"""Export a campaign as one Markdown document - a backup you can read in any
editor, paste into a wiki or Obsidian vault, or print.

Two flavours: the full GM version (everything, secrets included) and a
player-safe handout that leaves out anything a GM keeps hidden - plot
threads, NPC wants/secrets/dispositions, faction goals and standing, secrets & clues
(only the ones the party has already found are shown), clocks, ties, prep notes -
and only shows sessions that have actually been played.
"""
import re

from . import campaign_world, campaigns, session_log


def slugify(text, fallback="campaign"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or fallback


def _block(text):
    return (text or "").strip()


def _field(lines, label, value):
    value = _block(value)
    if value:
        lines.append(f"**{label}:** {value}")


def _heading(text):
    # keep a stray leading "#" in user text from becoming a heading level
    return " ".join((text or "").split())


def to_markdown(campaign_id, player_safe=False, game_name=None):
    detail = campaigns.get_campaign_detail(campaign_id)
    if not detail:
        return None
    c = detail["campaign"]
    out = [f"# {_heading(c['title'])}", ""]
    meta = []
    if game_name:
        meta.append(game_name)
    if not player_safe:
        meta.append(f"Status: {c['status']}")
    if meta:
        out += ["*" + " - ".join(meta) + "*", ""]
    if _block(c["pitch"]):
        out += ["## Premise", "", _block(c["pitch"]), ""]
    if _block(c["tone"]):
        out += ["## Tone", "", _block(c["tone"]), ""]

    if not player_safe and detail["threads"]:
        out += ["## Plot threads", ""]
        for t in detail["threads"]:
            out.append(f"### {_heading(t['title'])} ({t['status']})")
            out.append("")
            for label, key in (("Description", "description"), ("Stakes", "stakes")):
                _field(out, label, t[key])
            out.append("")

    if detail["npcs"]:
        out += ["## Cast", ""]
        for n in detail["npcs"]:
            title = _heading(n["name"])
            if _block(n["role"]):
                title += f" - {_heading(n['role'])}"
            if not player_safe and n["disposition"]:
                title += f" ({n['disposition']})"
            out += [f"### {title}", ""]
            if n.get("faction_name"):
                out.append(f"**Faction:** {n['faction_name']}")
            if n.get("location_name"):
                out.append(f"**Location:** {n['location_name']}")
            if not player_safe:
                _field(out, "Wants", n["motivation"])
                _field(out, "Secret", n["secret"])
                _field(out, "Notes", n["notes"])
            out.append("")

    if detail["factions"]:
        out += ["## Factions", ""]
        for f in detail["factions"]:
            out += [f"### {_heading(f['name'])}", ""]
            if not player_safe:
                _field(out, "Standing with the party", f.get("standing"))
                _field(out, "Goal", f["goal"])
                _field(out, "Notes", f["notes"])
            out.append("")

    if not player_safe:
        ties = campaign_world.list_relationships(campaign_id)
        if ties:
            out += ["## Ties", ""]
            for t in ties:
                line = f"- {_heading(t['phrase'])}"
                if _block(t["note"]):
                    line += f" - {' '.join(_block(t['note']).split())}"
                out.append(line)
            out.append("")
        clocks = campaign_world.list_clocks(campaign_id)
        if clocks:
            out += ["## Clocks", ""]
            for c in clocks:
                line = f"- **{_heading(c['name'])}**: {c['filled']} of {c['segments']}"
                if c["full"]:
                    line += " (complete)"
                if c.get("faction_name"):
                    line += f" - {_heading(c['faction_name'])}"
                if _block(c["note"]):
                    line += f" - {' '.join(_block(c['note']).split())}"
                out.append(line)
            out.append("")

    secrets = campaign_world.list_secrets(campaign_id)
    if not player_safe and secrets:
        out += ["## Secrets & clues", ""]
        for sec in secrets:
            mark = "x" if sec["status"] == "revealed" else " "
            line = f"- [{mark}] {' '.join(_block(sec['text']).split())}"
            extra = []
            if _block(sec["source"]):
                extra.append(f"source: {_heading(sec['source'])}")
            if sec["status"] == "revealed" and sec.get("revealed_number"):
                extra.append(f"revealed in session {sec['revealed_number']}")
            if extra:
                line += f" ({'; '.join(extra)})"
            out.append(line)
        out.append("")
    if player_safe:
        found = [x for x in secrets if x["status"] == "revealed"]
        if found:
            out += ["## What the party has learned", ""]
            out += [f"- {' '.join(_block(x['text']).split())}" for x in found]
            out.append("")

    if detail["locations"]:
        out += ["## Locations", ""]
        for loc in detail["locations"]:
            indent = "  " * loc["depth"]
            line = f"{indent}- **{_heading(loc['name'])}**"
            if loc.get("map_title"):
                line += f" (map: {_heading(loc['map_title'])})"
            if _block(loc["description"]):
                line += f" - {' '.join(_block(loc['description']).split())}"
            out.append(line)
        out.append("")

    sessions = list(reversed(detail["sessions"]))  # oldest first reads like a log
    if player_safe:
        sessions = [s for s in sessions if s["status"] == "played"]
    if sessions:
        out += ["## Sessions", ""]
        for s in sessions:
            title = f"Session {s['session_number']}"
            if _block(s["title"]):
                title += f": {_heading(s['title'])}"
            out += [f"### {title}", ""]
            meta = [x for x in (_block(s["date"]), None if player_safe else s["status"]) if x]
            if meta:
                out += ["*" + " - ".join(meta) + "*", ""]
            if not player_safe:
                _field(out, "Strong start", s["strong_start"])
                _field(out, "Scenes", s["scenes"])
                _field(out, "Secrets & clues", s["secrets_clues"])
                _field(out, "Other prep", s["prep_notes"])
            if _block(s["summary"]):
                out += ["**What happened:**", "", _block(s["summary"])]
            if not player_safe:  # raw log lines are GM-only: they can mention secrets
                log = session_log.session_log_text(s["id"])
                if log:
                    out += ["", "**Session log:**", "", log] if _block(s["summary"]) else ["**Session log:**", "", log]
            out.append("")

    if not player_safe and detail["materials"]:
        out += ["## Materials", ""]
        for m in detail["materials"]:
            line = f"- {m['book_title']}"
            if m["page_number"]:
                line += f", p.{m['page_number']}"
            if _block(m["note"]):
                line += f" - {_block(m['note'])}"
            out.append(line)
        out.append("")

    return "\n".join(out).rstrip() + "\n"
