"""'Ask the Library': answer a rules/lore question from the GM's own books.

This is retrieval-augmented generation in the smallest useful form. The
question is turned into search terms, the best-matching book pages are pulled
from the full-text index, and the configured LLM is asked to answer **only**
from those numbered excerpts and to cite them as [1], [2]... The page links
come back alongside the answer so every claim can be checked against the book.
Nothing here talks to the network except the (optional, user-configured) LLM
call itself.
"""
import re

from .. import llm
from . import search

MAX_QUESTION_CHARS = 500
MAX_PAGES = 6
MAX_CHARS_PER_PAGE = 2200
_CITE_RE = re.compile(r"\[(\d{1,2})\]")


class AskError(Exception):
    """A user-facing problem: empty question, nothing found, AI unavailable."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _excerpt(text, terms):
    """Up to MAX_CHARS_PER_PAGE characters of a page, centred on the first
    place any search term appears (so long pages still show the relevant part)."""
    text = " ".join((text or "").split())
    if len(text) <= MAX_CHARS_PER_PAGE:
        return text
    lowered = text.lower()
    positions = [lowered.find(t.lower()) for t in terms if t and lowered.find(t.lower()) >= 0]
    centre = min(positions) if positions else 0
    start = max(0, min(centre - MAX_CHARS_PER_PAGE // 4, len(text) - MAX_CHARS_PER_PAGE))
    chunk = text[start:start + MAX_CHARS_PER_PAGE]
    return ("… " if start else "") + chunk + (" …" if start + MAX_CHARS_PER_PAGE < len(text) else "")


def gather_sources(question, game_key=None):
    """The numbered pages to show the model: [{n, book_id, title, page_number,
    text}], best match first. Raises AskError when there's nothing to go on."""
    terms = search.question_to_terms(question)
    found = search.search_page(question, game_key=game_key, per_page=MAX_PAGES,
                               mode="any", with_text=True, terms=terms)
    if not found["results"]:
        raise AskError(
            "I couldn't find anything about that in the indexed books"
            + (" for this game" if game_key else "")
            + ". Try different keywords, or check that the library is scanned and indexed in Settings.",
            status=404,
        )
    return [
        {"n": i, "book_id": r["book_id"], "title": r["title"], "page_number": r["page_number"],
         "text": _excerpt(r["text"], terms)}
        for i, r in enumerate(found["results"], start=1)
    ]


def build_prompts(question, sources, game_name=None):
    system_prompt = (
        "You are a rules assistant for a tabletop RPG Game Master. Answer the question using ONLY "
        "the numbered book excerpts provided. Cite the excerpts you rely on with their numbers in "
        "square brackets, like [1] or [2][3]. If the excerpts do not contain the answer, say so "
        "plainly instead of guessing, and never invent rules, page numbers or stats. Keep the answer "
        "short and practical - a GM is reading it mid-session. Plain text or simple Markdown only."
    )
    blocks = "\n\n".join(f"[{s['n']}] {s['title']}, page {s['page_number']}:\n{s['text']}" for s in sources)
    header = f"Game: {game_name}\n\n" if game_name else ""
    user_prompt = f"{header}Excerpts:\n\n{blocks}\n\nQuestion: {question}"
    return system_prompt, user_prompt


def cited_numbers(answer, count):
    """The excerpt numbers an answer actually cites, in order, ignoring any
    out-of-range [n] the model made up."""
    seen = []
    for m in _CITE_RE.finditer(answer or ""):
        n = int(m.group(1))
        if 1 <= n <= count and n not in seen:
            seen.append(n)
    return seen


def ask(question, game_key=None, game_name=None):
    """Returns {answer, sources:[{n, book_id, title, page_number, cited}], cited}."""
    question = (question or "").strip()
    if not question:
        raise AskError("Type a question first.")
    if len(question) > MAX_QUESTION_CHARS:
        raise AskError(f"Keep the question under {MAX_QUESTION_CHARS} characters.")
    if not llm.is_enabled():
        raise AskError("AI features aren't enabled yet - set up and test a connection in Settings.")

    sources = gather_sources(question, game_key)
    system_prompt, user_prompt = build_prompts(question, sources, game_name)
    try:
        # Generous budget: "thinking" models spend part of it reasoning first.
        answer = llm.generate(system_prompt, user_prompt, max_tokens=1600)
    except llm.LLMError as e:
        raise AskError(str(e), status=502)
    if not (answer or "").strip():
        raise AskError(
            "The model didn't return any text - if it's a 'thinking' model it may have used its whole "
            "response budget reasoning internally. Try again or ask a narrower question.",
            status=502,
        )
    cited = cited_numbers(answer, len(sources))
    return {
        "answer": answer.strip(),
        "cited": cited,
        "sources": [
            {"n": s["n"], "book_id": s["book_id"], "title": s["title"],
             "page_number": s["page_number"], "cited": s["n"] in cited}
            for s in sources
        ],
    }
