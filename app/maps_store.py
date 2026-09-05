"""Saved maps: persists a generated map's SVG (and the seed/params that made
it) so it redisplays instantly without regenerating, and optionally links it
to a campaign the same way a bookmark can - a map is meant to be a piece of
reference material a GM comes back to, not something recomputed each time."""
import json

from . import database


def list_maps(game_key=None, campaign_id=None):
    sql = "SELECT id, game_key, campaign_id, map_type, genre, title, seed, created_at FROM maps"
    clauses, params = [], []
    if game_key:
        clauses.append("game_key = ?")
        params.append(game_key)
    if campaign_id is not None:
        clauses.append("campaign_id = ?")
        params.append(campaign_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    with database.get_cursor() as cur:
        rows = cur.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_map(map_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
    if not row:
        return None
    m = dict(row)
    try:
        m["params"] = json.loads(m["params_json"]) if m["params_json"] else {}
    except ValueError:
        m["params"] = {}
    return m


def save_map(game_key, map_type, genre, title, svg, seed=None, params=None, campaign_id=None):
    title = (title or "").strip() or f"{map_type.capitalize()} map"
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO maps (game_key, campaign_id, map_type, genre, title, seed, params_json, svg, original_svg) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (game_key, campaign_id, map_type, genre, title, seed, json.dumps(params or {}), svg, svg),
        )
        return cur.lastrowid


def rename_map(map_id, title):
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the map a title.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET title = ? WHERE id = ?", (title, map_id))


def set_campaign(map_id, campaign_id):
    """campaign_id may be None to detach the map from any campaign."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET campaign_id = ? WHERE id = ?", (campaign_id, map_id))


def delete_map(map_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM maps WHERE id = ?", (map_id,))


def update_svg(map_id, svg):
    """Persists hand-edited SVG from the map editor. Leaves original_svg
    untouched so 'Revert to Generated' can always get back to the
    as-generated version."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET svg = ? WHERE id = ?", (svg, map_id))


def revert_svg(map_id):
    """Discards any hand edits, restoring svg to the original generated
    version."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE maps SET svg = original_svg WHERE id = ?", (map_id,))
