"""Full-text search over indexed book pages."""
from .. import database


def _fts_query(raw_query):
    """Turn free-text user input into a safe FTS5 MATCH expression: quote each
    token so punctuation in filenames/queries can't break FTS5 syntax, and OR
    them together so multi-word searches are lenient by default."""
    tokens = [t for t in raw_query.replace('"', " ").split() if t]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def search(raw_query, game_key=None, limit=50):
    match_expr = _fts_query(raw_query)
    if not match_expr:
        return []

    sql = """
        SELECT
            b.id AS book_id, b.title, b.publisher, b.series, b.game_key, b.path,
            f.page_number,
            snippet(book_pages_fts, 0, '<mark>', '</mark>', ' … ', 12) AS snippet,
            bm25(book_pages_fts) AS rank
        FROM book_pages_fts f
        JOIN books b ON b.id = f.book_id
        WHERE book_pages_fts MATCH ?
    """
    params = [match_expr]
    if game_key:
        sql += " AND b.game_key = ?"
        params.append(game_key)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    with database.get_cursor() as cur:
        rows = cur.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_stats(game_key=None):
    sql = "SELECT COUNT(*) AS n, SUM(has_text) AS with_text FROM books"
    params = []
    if game_key:
        sql += " WHERE game_key = ?"
        params.append(game_key)
    with database.get_cursor() as cur:
        row = cur.execute(sql, params).fetchone()
    return {"total": row["n"] or 0, "searchable": row["with_text"] or 0}
