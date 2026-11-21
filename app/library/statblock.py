"""Pull creature stat blocks out of one book page, so a monster can go from
"found it in the book" to "on the initiative board" without retyping it.

Two ways, tried in this order:
  1. the configured LLM (if AI features are on) reads the page text and lists
     each creature as `NAME | HP | AC | INITIATIVE BONUS`;
  2. a plain text scan that looks for "Hit Points 7" / "HP 7" style lines and
     takes the name from the line above them.

Both are best effort on text that came out of a PDF (columns can be jumbled), so
the page shows the result in editable boxes - the GM checks it, nothing is
added automatically. Only the one page's text is sent to the LLM, never anything else.
"""
import re

from .. import database, llm

MAX_CREATURES = 12
MAX_PAGE_CHARS = 6000
MAX_NAME = 80

_HP_RE = re.compile(r"\b(?:hit\s*points?|h\.p\.|hp)\s*[:=]?\s*(\d{1,5})\b", re.IGNORECASE)
_AC_RE = re.compile(r"\b(?:armou?r\s*class|ac)\s*[:=]?\s*(\d{1,2})\b", re.IGNORECASE)
_INIT_RE = re.compile(r"\binitiative\s*[:=]?\s*([+\-−]?\s*\d{1,2})\b", re.IGNORECASE)
_DEX_RE = re.compile(r"\bdex(?:terity)?\s*\d{1,2}\s*\(\s*([+\-−]\s*\d{1,2})\s*\)", re.IGNORECASE)
_NOT_A_NAME = re.compile(
    r"^(armou?r class|ac\b|hit points|hp\b|speed|size|tiny|small|medium|large|huge|gargantuan|challenge|cr\b|"
    r"str\b|dex\b|con\b|int\b|wis\b|cha\b|skills|senses|languages|saving throws|actions|traits)",
    re.IGNORECASE)
_ALIGNMENT = re.compile(r"\b(lawful|neutral|chaotic|unaligned|any alignment|good|evil)\b", re.IGNORECASE)


def page_text(book_id, page_number):
    """(book title, page text) from the search index, or (None, '')."""
    with database.get_cursor() as cur:
        book = cur.execute("SELECT title FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book:
            return None, ""
        row = cur.execute(
            "SELECT content FROM book_pages_fts WHERE book_id = ? AND page_number = ?",
            (book_id, page_number),
        ).fetchone()
    return book["title"], (row["content"] if row else "") or ""


def _int(text):
    return int(re.sub(r"\s+", "", text.replace("−", "-")))


def _title_case_if_shouting(name):
    letters = [c for c in name if c.isalpha()]
    return name.title() if letters and all(c.isupper() for c in letters) else name


def _initiative(bonus):
    if bonus is None:
        return "1d20"
    return "1d20" + (f"+{bonus}" if bonus > 0 else (str(bonus) if bonus < 0 else ""))


def _creature(name, hp, ac=None, bonus=None):
    return {"name": name[:MAX_NAME], "hp": hp, "ac": ac, "init_bonus": bonus, "initiative": _initiative(bonus)}


def _name_before(text, pos):
    """The line just above a stat block's hit points that looks like a heading."""
    lines = [ln.strip() for ln in text[max(0, pos - 500):pos].splitlines() if ln.strip()]
    for line in reversed(lines[-6:]):
        if len(line.split()) > 6 or len(line) > MAX_NAME or _NOT_A_NAME.match(line):
            continue
        if "," in line and _ALIGNMENT.search(line):       # "Small humanoid (goblinoid), neutral evil"
            continue
        if re.search(r"\d", line) and len(re.findall(r"\d", line)) > 2:
            continue
        cleaned = re.sub(r"[^\w' \-]", "", line).strip(" -")
        if cleaned:
            return _title_case_if_shouting(cleaned)
    return None


def _nearest(regex, text, pos, start, end):
    """The match of regex inside text[start:end] whose start is closest to pos."""
    best = None
    for m in regex.finditer(text, start, end):
        if best is None or abs(m.start() - pos) < abs(best.start() - pos):
            best = m
    return best


def extract_text(text):
    """Heuristic scan; returns up to MAX_CREATURES creature dicts. Each stat
    block is the stretch of text between one 'Hit Points N' and the next, so
    one creature's armor class or ability scores can't leak into its neighbour."""
    text = (text or "")[:MAX_PAGE_CHARS * 2].replace("\r", "")
    hits = [m for m in _HP_RE.finditer(text) if int(m.group(1)) >= 1]
    found, seen = [], set()
    for i, m in enumerate(hits):
        hp = int(m.group(1))
        lo = hits[i - 1].end() if i else 0
        hi = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        lo, hi = max(lo, m.start() - 400), min(hi, m.end() + 700)
        ac = _nearest(_AC_RE, text, m.start(), lo, hi)
        bonus = None
        init = _nearest(_INIT_RE, text, m.start(), lo, hi)
        if init:
            bonus = _int(init.group(1))
        else:
            dex = _DEX_RE.search(text, m.end(), hi)
            if dex:
                bonus = _int(dex.group(1))
        if bonus is not None and not -10 <= bonus <= 30:
            bonus = None
        name = _name_before(text[:m.start()], m.start()) or f"Creature {len(found) + 1}"
        if (name.lower(), hp) in seen:
            continue
        seen.add((name.lower(), hp))
        found.append(_creature(name, hp, int(ac.group(1)) if ac else None, bonus))
        if len(found) >= MAX_CREATURES:
            break
    return found


# ------------------------------------------------------------------ AI

AI_SYSTEM_PROMPT = (
    "You extract creature stat blocks from the text of one page of a tabletop RPG book. For every "
    "creature, NPC or monster stat block on the page, write exactly one line in this form:\n"
    "NAME | HP | AC | INITIATIVE BONUS\n"
    "HP, AC and the initiative bonus are plain numbers (the bonus like +2 or -1); write ? for anything "
    "the page does not state. If a number is given as a dice average (like 7 (2d6)), use the average. "
    "Never invent numbers. Output only these lines, nothing else. If the page has no stat block, "
    "output the single word NONE."
)


def parse_ai_lines(reply):
    creatures, seen = [], set()
    for raw in (reply or "").splitlines():
        line = raw.strip().strip("-*• ").strip()
        if "|" not in line:
            continue
        parts = [p.strip().strip("*`") for p in line.split("|")]
        name = _title_case_if_shouting(re.sub(r"\s+", " ", parts[0]))[:MAX_NAME]
        if not name or name.upper() in ("NAME", "NONE"):
            continue
        nums = []
        for p in (parts + ["?", "?", "?"])[1:4]:
            m = re.search(r"[+\-−]?\s*\d+", p)
            nums.append(_int(m.group(0)) if m else None)
        hp, ac, bonus = nums
        if hp is not None and not 1 <= hp <= 100000:
            hp = None
        if ac is not None and not 0 <= ac <= 99:
            ac = None
        if bonus is not None and not -10 <= bonus <= 30:
            bonus = None
        if hp is None and ac is None and bonus is None:     # a chatty line, not a creature
            continue
        if (name.lower(), hp) in seen:
            continue
        seen.add((name.lower(), hp))
        creatures.append(_creature(name, hp, ac, bonus))
        if len(creatures) >= MAX_CREATURES:
            break
    return creatures


def extract_ai(text, game_name=None):
    user = (f"Game: {game_name}\n\n" if game_name else "") + "Page text:\n" + (text or "")[:MAX_PAGE_CHARS]
    reply = llm.generate(AI_SYSTEM_PROMPT, user, max_tokens=900)
    return parse_ai_lines(reply)


def extract(book_id, page_number, game_name=None, use_ai=True):
    """{'creatures': [...], 'method': 'ai' | 'text', 'book': title, 'page': n}.
    Raises ValueError with a readable message when there's nothing to read."""
    title, text = page_text(book_id, page_number)
    if title is None:
        raise ValueError("That book no longer exists - try rescanning the library.")
    if not text.strip():
        raise ValueError("There's no searchable text for that page (is the book indexed, or scanned images?).")
    creatures, method = [], "text"
    if use_ai and llm.is_enabled():
        try:
            creatures = extract_ai(text, game_name)
            method = "ai"
        except llm.LLMError:
            creatures = []
    if not creatures:
        creatures, method = extract_text(text), "text"
    if not creatures:
        raise ValueError("I couldn't find a stat block on that page.")
    return {"creatures": creatures, "method": method, "book": title, "page": page_number}
