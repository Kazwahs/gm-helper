"""Full-text search over indexed book pages.

What the search box understands:

* plain words - a page must contain **all** of them; if nothing does, the
  search quietly widens to pages containing **any** of them (best matches
  first) and says so, so a long, chatty query still finds something;
* "quoted phrases" - matched as an exact phrase;
* everything is escaped, so punctuation can't break the FTS5 query syntax.

Results are page-level hits. `search_page` paginates them and `group_by_book`
folds one page of hits into per-book groups for display.
"""
import html
import re

from .. import database

PAGE_SIZE = 20
MAX_TERMS = 12

# Private-use control characters that can't appear in page text. FTS5's
# snippet() wraps matches in these, we HTML-escape everything, and only then
# swap them for <mark> tags - so page text (which comes from arbitrary PDFs)
# can never inject markup into the results page.
_HL_OPEN, _HL_CLOSE = "\x02", "\x03"

_TERM_RE = re.compile(r'"([^"]*)"|(\S+)')

_STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further
had has have having he her here hers him his how i if in into is it its just me more most my
no nor not of off on once only or other our out over own same she should so some such than
that the their them then there these they this those through to too under until up very was
we were what when where which while who whom why will with would you your
""".split())


def parse_terms(raw_query):
    """Split user input into search terms: a "quoted phrase" is one term,
    otherwise one term per whitespace-separated word. Terms with no letters
    or digits in them (stray punctuation) are dropped."""
    terms = []
    for m in _TERM_RE.finditer(raw_query or ""):
        term = (m.group(1) if m.group(1) is not None else m.group(2)).replace('"', " ").strip()
        term = " ".join(term.split())
        if term and re.search(r"\w", term):
            terms.append(term)
    return terms[:MAX_TERMS]


def question_to_terms(question):
    """Turn a natural-language question into search terms by dropping filler
    words ("how does the grapple rule work" -> grapple, rule, work)."""
    words = [t for t in parse_terms(question) if t.lower() not in _STOPWORDS]
    return words or parse_terms(question)


def _fts_expr(terms, mode):
    joiner = " AND " if mode == "all" else " OR "
    return joiner.join('"' + t.replace('"', '""') + '"' for t in terms)


def _where(game_key):
    sql = "WHERE book_pages_fts MATCH ?"
    params = []
    if game_key:
        sql += " AND b.game_key = ?"
        params.append(game_key)
    return sql, params


def _count(match_expr, game_key):
    where, extra = _where(game_key)
    sql = f"SELECT COUNT(*) AS n FROM book_pages_fts f JOIN books b ON b.id = f.book_id {where}"
    with database.get_cursor() as cur:
        return cur.execute(sql, [match_expr, *extra]).fetchone()["n"]


def _fetch(match_expr, game_key, limit, offset, with_text):
    where, extra = _where(game_key)
    text_col = ", f.content AS text" if with_text else ""
    sql = f"""
        SELECT
            b.id AS book_id, b.title, b.publisher, b.series, b.game_key, b.path,
            f.page_number,
            snippet(book_pages_fts, 0, ?, ?, ' … ', 24) AS snippet,
            bm25(book_pages_fts) AS rank{text_col}
        FROM book_pages_fts f
        JOIN books b ON b.id = f.book_id
        {where}
        ORDER BY rank LIMIT ? OFFSET ?
    """
    params = [_HL_OPEN, _HL_CLOSE, match_expr, *extra, limit, offset]
    with database.get_cursor() as cur:
        rows = [dict(r) for r in cur.execute(sql, params).fetchall()]
    for row in rows:
        row["snippet"] = _safe_snippet(row["snippet"])
    return rows


def _safe_snippet(raw):
    escaped = html.escape(raw or "", quote=False)
    return escaped.replace(_HL_OPEN, "<mark>").replace(_HL_CLOSE, "</mark>")


def search_page(raw_query, game_key=None, page=1, per_page=PAGE_SIZE, mode="auto",
                with_text=False, terms=None):
    """One page of ranked page-level hits.

    mode "auto": all terms, widening to any term if that finds nothing.
    mode "any": always any term (used for natural-language questions).
    Returns {results, total, mode, page, pages, per_page, terms}; `mode` is
    the one actually used ("all" / "any" / None when there was nothing to
    search for). Each result's `snippet` is HTML-safe (matches wrapped in
    <mark>); with_text=True also returns the page's full `text`.
    """
    terms = list(terms) if terms is not None else parse_terms(raw_query)
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or PAGE_SIZE))
    empty = {"results": [], "total": 0, "mode": None, "page": 1, "pages": 0,
             "per_page": per_page, "terms": terms}
    if not terms:
        return empty

    used = "any" if mode == "any" else "all"
    expr = _fts_expr(terms, used)
    total = _count(expr, game_key)
    if total == 0 and used == "all" and len(terms) > 1:
        used = "any"
        expr = _fts_expr(terms, used)
        total = _count(expr, game_key)
    if total == 0:
        return {**empty, "mode": used}

    pages = (total + per_page - 1) // per_page
    page = min(page, pages)
    results = _fetch(expr, game_key, per_page, (page - 1) * per_page, with_text)
    return {"results": results, "total": total, "mode": used, "page": page,
            "pages": pages, "per_page": per_page, "terms": terms}


def search(raw_query, game_key=None, limit=50, offset=0):
    """Flat list of ranked hits (the shape /api/search returns)."""
    terms = parse_terms(raw_query)
    if not terms:
        return []
    rows = _fetch(_fts_expr(terms, "all"), game_key, limit, offset, False)
    if not rows and offset == 0 and len(terms) > 1:
        rows = _fetch(_fts_expr(terms, "any"), game_key, limit, offset, False)
    return rows


def group_by_book(results):
    """Folds page-level hits into [{book_id, title, publisher, series,
    game_key, hits: [...]}], keeping the order of each book's best hit."""
    groups = {}
    order = []
    for r in results:
        g = groups.get(r["book_id"])
        if g is None:
            g = {k: r[k] for k in ("book_id", "title", "publisher", "series", "game_key")}
            g["hits"] = []
            groups[r["book_id"]] = g
            order.append(g)
        g["hits"].append(r)
    return order


def search_stats(game_key=None):
    sql = "SELECT COUNT(*) AS n, SUM(has_text) AS with_text FROM books"
    params = []
    if game_key:
        sql += " WHERE game_key = ?"
        params.append(game_key)
    with database.get_cursor() as cur:
        row = cur.execute(sql, params).fetchone()
    return {"total": row["n"] or 0, "searchable": row["with_text"] or 0}
