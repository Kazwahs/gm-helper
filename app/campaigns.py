"""Campaign planner: flesh out a campaign for a game - premise/tone, open
plot threads, NPCs, factions, locations, a session-by-session log, and the
library materials it draws on.

The section layout follows the common structure GMs already use for prep
(Sly Flourish's "lazy DM" checklist, and the "cast / powers / map / threads /
next session / record" shape most campaign wikis converge on):
  - threads    -> open plot points with stakes, tracked until resolved
  - npcs       -> the cast: who they are, what they want, what they hide
  - factions   -> the powers pursuing their own goals off-screen
  - locations  -> the map, kept hierarchical (a place lives under a place)
  - sessions   -> per-session prep (hook / scenes / secrets / notes) and,
                  after the fact, what actually happened
  - materials  -> book/page references, reusing the bookmarks system rather
                  than inventing a second one; each campaign gets its own
                  bookmark collection at creation time.
"""
from . import bookmarks as bookmarks_module
from . import database

_CAMPAIGN_FIELDS = {"title", "pitch", "tone", "status"}
_THREAD_FIELDS = {"title", "description", "stakes", "status"}
_NPC_FIELDS = {"name", "role", "disposition", "motivation", "secret", "notes", "faction_id", "location_id"}
_LOCATION_FIELDS = {"name", "description", "parent_id", "map_id"}
STANDINGS = ("hostile", "unfriendly", "neutral", "friendly", "allied")  # how a faction treats the party
_FACTION_FIELDS = {"name", "goal", "notes", "standing"}
_SESSION_FIELDS = {
    "title", "date", "status", "strong_start", "scenes",
    "secrets_clues", "prep_notes", "summary",
}
_REQUIRED_TEXT_FIELDS = {"title", "name"}  # can't be blanked out via a field update


def _require_known_field(field, allowed):
    if field not in allowed:
        raise ValueError(f"Unknown field: {field}")


def _link_id(value, table, campaign_id, label):
    """Validates a link to another row: blank -> None, otherwise the id must
    exist in `table` and belong to the same campaign (so one campaign can
    never point at another's faction or location)."""
    if value is None or str(value).strip() == "":
        return None
    try:
        link_id = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Pick a valid {label}.")
    with database.get_cursor() as cur:
        row = cur.execute(
            f"SELECT 1 FROM {table} WHERE id = ? AND campaign_id = ?", (link_id, campaign_id)
        ).fetchone()
    if not row:
        raise ValueError(f"That {label} isn't part of this campaign.")
    return link_id


def _map_link_id(value, campaign_id):
    """A location may link to any saved map that is attached to this campaign
    or not attached to any campaign."""
    if value is None or str(value).strip() == "":
        return None
    try:
        map_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("Pick a valid map.")
    with database.get_cursor() as cur:
        row = cur.execute("SELECT campaign_id FROM maps WHERE id = ?", (map_id,)).fetchone()
    if not row:
        raise ValueError("That map no longer exists.")
    if row["campaign_id"] not in (None, campaign_id):
        raise ValueError("That map belongs to a different campaign.")
    return map_id


def _campaign_of(table, row_id):
    with database.get_cursor() as cur:
        row = cur.execute(f"SELECT campaign_id FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if not row:
        raise ValueError("Unknown item - it may have been deleted.")
    return row["campaign_id"]


# --------------------------------------------------------------- campaigns
def list_campaigns(game_key=None):
    sql = "SELECT * FROM campaigns"
    params = []
    if game_key:
        sql += " WHERE game_key = ?"
        params.append(game_key)
    sql += " ORDER BY updated_at DESC, created_at DESC, id DESC"
    with database.get_cursor() as cur:
        rows = cur.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_campaign(campaign_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    return dict(row) if row else None


def create_campaign(game_key, title, pitch="", tone=""):
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the campaign a title.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaigns (game_key, title, pitch, tone) VALUES (?, ?, ?, ?)",
            (game_key, title, pitch or "", tone or ""),
        )
        campaign_id = cur.lastrowid

    # Every campaign gets its own bookmark collection for reference
    # materials, created right away so the Materials section always has
    # somewhere to save to.
    collection_name = f"Campaign: {title}"
    try:
        collection_id = bookmarks_module.create_collection(collection_name)
    except ValueError:
        collection_id = bookmarks_module.create_collection(f"{collection_name} (#{campaign_id})")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE campaigns SET bookmark_collection_id = ? WHERE id = ?",
            (collection_id, campaign_id),
        )
    return campaign_id


def update_campaign_field(campaign_id, field, value):
    _require_known_field(field, _CAMPAIGN_FIELDS)
    if field in _REQUIRED_TEXT_FIELDS and not (value or "").strip():
        raise ValueError("That field can't be blank.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            f"UPDATE campaigns SET {field} = ?, updated_at = datetime('now') WHERE id = ?",
            (value, campaign_id),
        )
    if field == "title":
        with database.get_cursor() as cur:
            row = cur.execute(
                "SELECT bookmark_collection_id FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
        if row and row["bookmark_collection_id"]:
            try:
                bookmarks_module.rename_collection(
                    row["bookmark_collection_id"], f"Campaign: {value.strip()}"
                )
            except ValueError:
                pass  # name collision elsewhere - leave the collection's name as-is


def delete_campaign(campaign_id):
    campaign = get_campaign(campaign_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    if campaign and campaign.get("bookmark_collection_id"):
        bookmarks_module.delete_collection(campaign["bookmark_collection_id"])


# ------------------------------------------------------------------ threads
def list_threads(campaign_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM campaign_threads WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_thread(campaign_id, title, description="", stakes="", status="seed"):
    title = (title or "").strip()
    if not title:
        raise ValueError("Give the thread a title.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_threads (campaign_id, title, description, stakes, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (campaign_id, title, description or "", stakes or "", status or "seed"),
        )
        return cur.lastrowid


def update_thread_field(thread_id, field, value):
    _require_known_field(field, _THREAD_FIELDS)
    if field in _REQUIRED_TEXT_FIELDS and not (value or "").strip():
        raise ValueError("That field can't be blank.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_threads SET {field} = ? WHERE id = ?", (value, thread_id))


def delete_thread(thread_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_threads WHERE id = ?", (thread_id,))


# --------------------------------------------------------------- cast/npcs
def list_npcs(campaign_id):
    """The cast, each with `faction_name` / `location_name` filled in when linked."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT n.*, f.name AS faction_name, l.name AS location_name
               FROM campaign_npcs n
               LEFT JOIN campaign_factions f ON f.id = n.faction_id
               LEFT JOIN campaign_locations l ON l.id = n.location_id
               WHERE n.campaign_id = ? ORDER BY n.id""",
            (campaign_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_npc(campaign_id, name, role="", disposition="neutral", motivation="", secret="", notes="",
            faction_id=None, location_id=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the NPC a name.")
    faction_id = _link_id(faction_id, "campaign_factions", campaign_id, "faction")
    location_id = _link_id(location_id, "campaign_locations", campaign_id, "location")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_npcs (campaign_id, name, role, disposition, motivation, secret, notes, "
            "faction_id, location_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, name, role or "", disposition or "neutral", motivation or "", secret or "",
             notes or "", faction_id, location_id),
        )
        return cur.lastrowid


def update_npc_field(npc_id, field, value):
    _require_known_field(field, _NPC_FIELDS)
    if field in _REQUIRED_TEXT_FIELDS and not (value or "").strip():
        raise ValueError("That field can't be blank.")
    if field in ("faction_id", "location_id"):
        table = "campaign_factions" if field == "faction_id" else "campaign_locations"
        value = _link_id(value, table, _campaign_of("campaign_npcs", npc_id),
                         "faction" if field == "faction_id" else "location")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_npcs SET {field} = ? WHERE id = ?", (value, npc_id))


def delete_npc(npc_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_npcs WHERE id = ?", (npc_id,))


# --------------------------------------------------------------- locations
def locations_tree(campaign_id):
    """Locations in depth-first order, each annotated with `depth`, so the
    template can indent children under their parent ("the map" should always
    be able to answer 'where is this' by looking one level up)."""
    with database.get_cursor() as cur:
        rows = [
            dict(r)
            for r in cur.execute(
                """SELECT l.*, m.title AS map_title
                   FROM campaign_locations l LEFT JOIN maps m ON m.id = l.map_id
                   WHERE l.campaign_id = ? ORDER BY l.name COLLATE NOCASE""",
                (campaign_id,),
            ).fetchall()
        ]
    by_parent = {}
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(r)

    ordered = []

    def walk(parent_id, depth):
        for r in by_parent.get(parent_id, []):
            r["depth"] = depth
            ordered.append(r)
            walk(r["id"], depth + 1)

    walk(None, 0)
    # Anything whose parent got deleted out from under it (shouldn't happen -
    # ON DELETE SET NULL - but just in case) still shows up, at the top level.
    seen_ids = {r["id"] for r in ordered}
    for r in rows:
        if r["id"] not in seen_ids:
            r["depth"] = 0
            ordered.append(r)
    return ordered


def add_location(campaign_id, name, description="", parent_id=None, map_id=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the location a name.")
    parent_id = _link_id(parent_id, "campaign_locations", campaign_id, "parent location")
    map_id = _map_link_id(map_id, campaign_id)
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_locations (campaign_id, name, description, parent_id, map_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (campaign_id, name, description or "", parent_id, map_id),
        )
        return cur.lastrowid


def unlink_map_from_other_campaigns(map_id, keep_campaign_id):
    """When a map is attached to a campaign (or detached), locations in any
    *other* campaign stop pointing at it - a map shouldn't be reachable from
    two campaigns' location lists at once."""
    with database.get_cursor(commit=True) as cur:
        if keep_campaign_id:
            cur.execute(
                "UPDATE campaign_locations SET map_id = NULL WHERE map_id = ? AND campaign_id != ?",
                (map_id, keep_campaign_id),
            )
        # (detaching keeps links: the map is "unattached", which any campaign may link to)


def locations_for_map(map_id):
    """Campaign locations that link to this map (for the map page's 'used in')."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT l.id, l.name, l.campaign_id, c.title AS campaign_title
               FROM campaign_locations l JOIN campaigns c ON c.id = l.campaign_id
               WHERE l.map_id = ? ORDER BY c.title COLLATE NOCASE, l.name COLLATE NOCASE""",
            (map_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_location_field(location_id, field, value):
    _require_known_field(field, _LOCATION_FIELDS)
    if field in _REQUIRED_TEXT_FIELDS and not (value or "").strip():
        raise ValueError("That field can't be blank.")
    if field == "map_id":
        map_id = _map_link_id(value, _campaign_of("campaign_locations", location_id))
        with database.get_cursor(commit=True) as cur:
            cur.execute("UPDATE campaign_locations SET map_id = ? WHERE id = ?", (map_id, location_id))
        return
    if field == "parent_id":
        new_parent_id = _link_id(value, "campaign_locations", _campaign_of("campaign_locations", location_id),
                                 "parent location")
        if new_parent_id == location_id:
            raise ValueError("A location can't be its own parent.")
        if new_parent_id is not None:
            with database.get_cursor() as cur:
                cur_id, seen = new_parent_id, set()
                while cur_id is not None:
                    if cur_id == location_id:
                        raise ValueError("That would put the location inside itself.")
                    if cur_id in seen:
                        break
                    seen.add(cur_id)
                    row = cur.execute(
                        "SELECT parent_id FROM campaign_locations WHERE id = ?", (cur_id,)
                    ).fetchone()
                    cur_id = row["parent_id"] if row else None
        with database.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE campaign_locations SET parent_id = ? WHERE id = ?", (new_parent_id, location_id)
            )
        return
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_locations SET {field} = ? WHERE id = ?", (value, location_id))


def delete_location(location_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_locations WHERE id = ?", (location_id,))


# --------------------------------------------------------------- factions
def list_factions(campaign_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM campaign_factions WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_faction(campaign_id, name, goal="", notes=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the faction a name.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_factions (campaign_id, name, goal, notes) VALUES (?, ?, ?, ?)",
            (campaign_id, name, goal or "", notes or ""),
        )
        return cur.lastrowid


def update_faction_field(faction_id, field, value):
    _require_known_field(field, _FACTION_FIELDS)
    if field in _REQUIRED_TEXT_FIELDS and not (value or "").strip():
        raise ValueError("That field can't be blank.")
    if field == "standing":
        value = (value or "").strip().lower()
        if value not in STANDINGS:
            raise ValueError("Unknown standing.")
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_factions SET {field} = ? WHERE id = ?", (value, faction_id))


def delete_faction(faction_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_factions WHERE id = ?", (faction_id,))


# --------------------------------------------------------------- sessions
def list_sessions(campaign_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM campaign_sessions WHERE campaign_id = ? ORDER BY session_number DESC",
            (campaign_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_session(campaign_id, title="", date=""):
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT COALESCE(MAX(session_number), 0) + 1 AS n FROM campaign_sessions WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        next_number = row["n"]
    with database.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO campaign_sessions (campaign_id, session_number, title, date) VALUES (?, ?, ?, ?)",
            (campaign_id, next_number, title or "", date or ""),
        )
        return cur.lastrowid


def update_session_field(session_id, field, value):
    _require_known_field(field, _SESSION_FIELDS)
    with database.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE campaign_sessions SET {field} = ? WHERE id = ?", (value, session_id))


def delete_session(session_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_sessions WHERE id = ?", (session_id,))


# --------------------------------------------------------------- materials
def list_materials(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign or not campaign.get("bookmark_collection_id"):
        return []
    with database.get_cursor() as cur:
        rows = cur.execute(
            """SELECT bm.*, b.title AS book_title, b.publisher, b.series
               FROM bookmarks bm JOIN books b ON b.id = bm.book_id
               WHERE bm.collection_id = ?
               ORDER BY b.series COLLATE NOCASE, b.title COLLATE NOCASE, bm.page_number""",
            (campaign["bookmark_collection_id"],),
        ).fetchall()
    return [dict(r) for r in rows]


def add_material(campaign_id, book_id, page_number=None, note=""):
    campaign = get_campaign(campaign_id)
    if not campaign:
        raise ValueError("Unknown campaign.")
    collection_id = campaign.get("bookmark_collection_id")
    if not collection_id:
        # Shouldn't normally happen (every campaign gets one at creation) -
        # but don't leave the campaign stranded without a materials list.
        collection_id = bookmarks_module.create_collection(
            f"Campaign: {campaign['title']} (#{campaign_id})"
        )
        with database.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE campaigns SET bookmark_collection_id = ? WHERE id = ?",
                (collection_id, campaign_id),
            )
    return bookmarks_module.add_bookmark(collection_id, book_id, page_number, note)


def books_for_game(game_key):
    """Books to offer in the "add material" picker - scoped to the
    campaign's own game so the dropdown stays a manageable size."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT id, title, series FROM books WHERE game_key = ? "
            "ORDER BY series COLLATE NOCASE, title COLLATE NOCASE",
            (game_key,),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------- aggregate
def get_campaign_detail(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return None
    return {
        "campaign": campaign,
        "threads": list_threads(campaign_id),
        "npcs": list_npcs(campaign_id),
        "locations": locations_tree(campaign_id),
        "factions": list_factions(campaign_id),
        "sessions": list_sessions(campaign_id),
        "materials": list_materials(campaign_id),
    }
