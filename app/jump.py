"""Quick jump: one search box (Ctrl+K) that finds a campaign, NPC, place,
faction, plot thread, session, map, random table, bookmark or tool page by
name and says where to go.

Pure lookups over the app's own tables - no library full-text search here
(that's heavy and has its own page), but the last entry always offers to run
the typed words through the library.
"""
from urllib.parse import quote

from . import database, maps_store, nav, recent
from .library import games as games_module

MAX_RESULTS = 14
MAX_QUERY = 80
PER_KIND = 6  # one kind can't crowd the list out

KIND_LABELS = {
    "page": "Go to",
    "campaign": "Campaign",
    "npc": "NPC",
    "location": "Place",
    "faction": "Faction",
    "thread": "Plot thread",
    "session": "Session",
    "map": "Map",
    "table": "Random table",
    "bookmark": "Bookmark",
    "library": "Library",
}


def _terms(q):
    return [t for t in (q or "").lower().split() if t][:6]


def _like(term):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _rank(label, terms):
    """Lower is better: exact name, name starts with the words, a word in the
    name starts with them, name contains them, only the other fields do."""
    name = (label or "").lower()
    phrase = " ".join(terms)
    if name == phrase:
        return 0
    if name.startswith(phrase):
        return 1
    if (" " + phrase) in (" " + name):
        return 2
    if all(t in name for t in terms):
        return 3
    return 4


def _game_name(key, cache):
    if key not in cache:
        game = games_module.get_game(key)
        cache[key] = game["name"] if game else (key or "")
    return cache[key]


def _query(sql, params):
    with database.get_cursor() as cur:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]


def _where_all(columns, terms):
    """AND of (col1 LIKE ? OR col2 LIKE ? ...) for every typed word."""
    clauses, params = [], []
    for term in terms:
        clauses.append("(" + " OR ".join(f"COALESCE({c}, '') LIKE ? ESCAPE '\\'" for c in columns) + ")")
        params.extend([_like(term)] * len(columns))
    return " AND ".join(clauses), params


def _item(kind, label, href, detail="", rank=4, game_key=None, current_game=None):
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "label": label,
        "detail": detail,
        "href": href,
        "_rank": rank,
        # things from the game you're in sort ahead of the same-quality match elsewhere
        "_boost": 0 if (not current_game or game_key in (None, current_game, "_global")) else 1,
    }


def _pages(terms, game_key):
    """Navigation entries (same list the menu shows) whose label matches."""
    built = nav.build_nav("/", game_key, game_key)
    entries = built["primary"] + built["tools"] + built["more"]
    out = []
    for e in entries:
        label = e["label"]
        if all(t in label.lower() for t in terms):
            out.append(_item("page", label, e["href"], rank=_rank(label, terms) if terms else 1,
                             game_key=game_key, current_game=game_key))
    return out


def _campaign_href(campaign_id, anchor=None):
    return f"/campaigns/{campaign_id}" + (f"#{anchor}" if anchor else "")


def search(q, game_key=None, limit=MAX_RESULTS):
    """Results for the palette. Empty text gives pages plus what you touched
    last; text gives matches across the app, best first."""
    raw = (q or "").strip()[:MAX_QUERY]
    if game_key and not games_module.get_game(game_key):
        game_key = None
    terms = _terms(raw)
    names = {}

    if not terms:
        # what you touched last first, then the menu, so the list opens useful
        items = []
        for c in recent.recent_campaigns(3):
            items.append(_item("campaign", c["title"], _campaign_href(c["id"]),
                               detail=_game_name(c["game_key"], names), rank=0,
                               game_key=c["game_key"], current_game=game_key))
        for m in maps_store.list_maps()[:3]:
            items.append(_item("map", m["title"], f"/maps/{m['id']}",
                               detail=_game_name(m["game_key"], names), rank=0,
                               game_key=m["game_key"], current_game=game_key))
        items += _pages([], game_key)
        return [_public(i) for i in items[:limit]]

    items = list(_pages(terms, game_key))

    where, params = _where_all(["c.title", "c.pitch", "c.tone"], terms)
    for r in _query(f"SELECT c.* FROM campaigns c WHERE {where} ORDER BY c.updated_at DESC LIMIT 30", params):
        items.append(_item("campaign", r["title"], _campaign_href(r["id"]),
                           detail=_game_name(r["game_key"], names),
                           rank=_rank(r["title"], terms), game_key=r["game_key"], current_game=game_key))

    def child(kind, table, columns, anchor, label_col, detail_fn):
        where, params = _where_all(columns, terms)
        rows = _query(
            f"SELECT t.*, c.title AS campaign_title, c.game_key AS game_key FROM {table} t "
            f"JOIN campaigns c ON c.id = t.campaign_id WHERE {where} ORDER BY t.id DESC LIMIT 30",
            params,
        )
        for r in rows:
            label = r[label_col] or ""
            if kind == "session":
                label = f"Session {r['session_number']}" + (f": {r['title']}" if r.get("title") else "")
            items.append(_item(kind, label, _campaign_href(r["campaign_id"], f"{anchor}-{r['id']}"),
                               detail=detail_fn(r), rank=_rank(label, terms),
                               game_key=r["game_key"], current_game=game_key))

    child("npc", "campaign_npcs", ["t.name", "t.role", "t.motivation", "t.notes"], "npc", "name",
          lambda r: " - ".join(x for x in (r.get("role"), r["campaign_title"]) if x))
    child("location", "campaign_locations", ["t.name", "t.description"], "location", "name",
          lambda r: r["campaign_title"])
    child("faction", "campaign_factions", ["t.name", "t.goal", "t.notes"], "faction", "name",
          lambda r: r["campaign_title"])
    child("thread", "campaign_threads", ["t.title", "t.description", "t.stakes"], "thread", "title",
          lambda r: r["campaign_title"])
    child("session", "campaign_sessions", ["t.title", "t.strong_start", "t.summary"], "session", "title",
          lambda r: r["campaign_title"])

    where, params = _where_all(["m.title", "m.map_type", "m.genre"], terms)
    for r in _query(f"SELECT m.id, m.title, m.game_key, m.map_type FROM maps m WHERE {where} "
                    "ORDER BY m.created_at DESC, m.id DESC LIMIT 30", params):
        items.append(_item("map", r["title"], f"/maps/{r['id']}",
                           detail=f"{_game_name(r['game_key'], names)} - {r['map_type']}",
                           rank=_rank(r["title"], terms), game_key=r["game_key"], current_game=game_key))

    where, params = _where_all(["rt.name", "rt.body"], terms)
    for r in _query(f"SELECT rt.id, rt.name, rt.game_key FROM random_tables rt WHERE {where} "
                    "ORDER BY rt.name COLLATE NOCASE LIMIT 30", params):
        shared = r["game_key"] == "_global"
        target = "" if shared and not game_key else f"?game={quote(game_key if shared else r['game_key'], safe='')}"
        items.append(_item("table", r["name"], f"/tools/tables{target}#table-{r['id']}",
                           detail="Every game" if shared else _game_name(r["game_key"], names),
                           rank=_rank(r["name"], terms), game_key=r["game_key"], current_game=game_key))

    where, params = _where_all(["bm.note", "b.title", "b.series"], terms)
    for r in _query(
        "SELECT bm.id, bm.note, bm.page_number, b.id AS book_id, b.title AS book_title, b.game_key "
        f"FROM bookmarks bm JOIN books b ON b.id = bm.book_id WHERE {where} "
        "ORDER BY bm.created_at DESC, bm.id DESC LIMIT 30", params):
        page = f"#page={r['page_number']}" if r["page_number"] else ""
        label = (r["note"] or "").strip() or r["book_title"]
        detail = r["book_title"] + (f", p. {r['page_number']}" if r["page_number"] else "")
        items.append(_item("bookmark", label[:90], f"/books/{r['book_id']}/file{page}", detail=detail,
                           rank=_rank(label, terms), game_key=r["game_key"], current_game=game_key))

    # best matches first, but no single kind may take every row
    items.sort(key=lambda i: (i["_rank"], i["_boost"]))
    chosen, per_kind = [], {}
    for item in items:
        n = per_kind.get(item["kind"], 0)
        if n >= PER_KIND:
            continue
        per_kind[item["kind"]] = n + 1
        chosen.append(item)
    chosen = chosen[: max(1, limit - 1)]

    lib_href = "/library?q=" + quote(raw, safe="") + (f"&game={quote(game_key, safe='')}" if game_key else "")
    chosen.append(_item("library", f"Search the library for \"{raw}\"", lib_href,
                        detail=_game_name(game_key, names) if game_key else "All games", rank=9))
    return [_public(i) for i in chosen]


def _public(item):
    return {k: v for k, v in item.items() if not k.startswith("_")}
