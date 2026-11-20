"""Export a campaign as one Markdown document - a backup you can read in any
editor, paste into a wiki or Obsidian vault, or print.

Two flavours: the full GM version (everything, secrets included) and a
player-safe handout that leaves out anything a GM keeps hidden - plot
threads, NPC wants/secrets/dispositions, faction goals, secrets & clues, prep
notes - and only shows sessions that have actually been played.
"""
import re

from . import campaigns


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
                _field(out, "Goal", f["goal"])
                _field(out, "Notes", f["notes"])
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
