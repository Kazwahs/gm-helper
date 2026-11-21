"""Who may open which URL - the single place that decides.

Every route in the app has a line in ROUTE_POLICY, and a test fails if a route is
added without one, so "I forgot to think about who can reach this" can't happen
quietly. A route is one of:

  OPEN             anyone, signed in or not (health check, the sign-in page)
  USER             any signed-in account
  GM               GM accounts only - which is the default for anything powerful
  player(<scope>)  a GM, or a player who passes the named check on the thing in
                   the URL: member of that campaign, may see that map, owns that
                   token, may use that game's library ...

With no accounts at all (the app as it has always run) none of this applies: the
old behaviour - open, or one shared password - is kept unchanged.

The checks are server-side because hiding a button is not security: a player can
type any URL.
"""
import re
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import accounts, database, security

OPEN, USER, GM = "open", "user", "gm"
SESSION_COOKIE = "gmh_session"
_RANK = {"open": 0, "user": 1, "player": 2, "gm": 3}      # when two entries match, the stricter wins

# A temporary password has to be replaced before anything else works.
ALLOWED_WHILE_MUST_CHANGE = frozenset({"/account", "/api/account/password", "/logout"})


def player(scope):
    return ("player", scope)


ROUTE_POLICY = {
    # dashboard
    "GET /": GM,
    "GET /api/jump": GM,
    # settings
    "GET /settings": GM,
    "POST /api/settings/ai": GM,
    "POST /api/settings/ai/test": GM,
    "POST /settings/library-root": GM,
    "POST /api/settings/theme": GM,
    "POST /api/settings/custom-theme": GM,
    "POST /api/scan": GM,
    "POST /api/index": GM,
    "GET /api/index/progress": GM,
    "POST /api/ocr": GM,
    "GET /api/ocr/progress": GM,
    # games
    "GET /games/manage": GM,
    "POST /api/games/create": GM,
    "POST /api/games/rename": GM,
    "POST /api/games/merge": GM,
    "POST /api/games/move-series": GM,
    # library
    "GET /library": GM,
    "GET /api/search": GM,
    "POST /api/library/ask": GM,
    "POST /api/library/statblock": GM,
    "POST /api/books/move": GM,
    "POST /api/books/{book_id}/reset-game": GM,
    "GET /books/{book_id}/file": player("book"),
    # bookmarks
    "GET /bookmarks": GM,
    "GET /api/bookmarks/collections": GM,
    "POST /api/bookmarks/collections": GM,
    "POST /api/bookmarks/collections/{collection_id}/rename": GM,
    "POST /api/bookmarks/collections/{collection_id}/delete": GM,
    "POST /api/bookmarks/add": GM,
    "POST /api/bookmarks/{bookmark_id}/note": GM,
    "POST /api/bookmarks/{bookmark_id}/delete": GM,
    # campaigns
    "GET /campaigns": GM,
    "POST /api/campaigns": GM,
    "GET /campaigns/{campaign_id}": GM,
    "GET /campaigns/{campaign_id}/export.md": GM,
    "POST /api/campaigns/{campaign_id}/recap": GM,
    "POST /api/campaigns/{campaign_id}/prep-suggestions": GM,
    "POST /api/campaigns/{campaign_id}/update": GM,
    "POST /api/campaigns/{campaign_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/threads": GM,
    "POST /api/campaigns/threads/{thread_id}/update": GM,
    "POST /api/campaigns/threads/{thread_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/npcs": GM,
    "POST /api/campaigns/npcs/{npc_id}/update": GM,
    "POST /api/campaigns/npcs/{npc_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/locations": GM,
    "POST /api/campaigns/locations/{location_id}/update": GM,
    "POST /api/campaigns/locations/{location_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/factions": GM,
    "POST /api/campaigns/factions/{faction_id}/update": GM,
    "POST /api/campaigns/factions/{faction_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/sessions": GM,
    "POST /api/campaigns/sessions/{session_id}/update": GM,
    "POST /api/campaigns/sessions/{session_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/materials": GM,
    "POST /api/campaigns/materials/{bookmark_id}/delete": GM,
    "GET /api/campaigns/{campaign_id}/log": GM,
    "POST /api/campaigns/{campaign_id}/log": GM,
    "POST /api/campaigns/log/{entry_id}/update": GM,
    "POST /api/campaigns/log/{entry_id}/delete": GM,
    "POST /api/campaigns/sessions/{session_id}/log/summary": GM,
    "POST /api/log": GM,
    # campaign_world
    "GET /api/campaigns/{campaign_id}/world": GM,
    "POST /api/campaigns/{campaign_id}/secrets": GM,
    "POST /api/campaigns/{campaign_id}/secrets/import": GM,
    "POST /api/campaigns/secrets/{secret_id}/update": GM,
    "POST /api/campaigns/secrets/{secret_id}/reveal": GM,
    "POST /api/campaigns/secrets/{secret_id}/hide": GM,
    "POST /api/campaigns/secrets/{secret_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/clocks": GM,
    "POST /api/campaigns/clocks/{clock_id}/advance": GM,
    "POST /api/campaigns/clocks/{clock_id}/update": GM,
    "POST /api/campaigns/clocks/{clock_id}/delete": GM,
    "POST /api/campaigns/{campaign_id}/relationships": GM,
    "POST /api/campaigns/relationships/{relationship_id}/delete": GM,
    # maps
    "GET /maps": GM,
    "POST /api/maps/generate": GM,
    "POST /api/maps": GM,
    "GET /maps/{map_id}/player": player("map"),
    "GET /maps/{map_id}/print": GM,
    "GET /api/maps/{map_id}/reveal": GM,
    "POST /api/maps/{map_id}/reveal/enabled": GM,
    "POST /api/maps/{map_id}/reveal/add": GM,
    "POST /api/maps/{map_id}/reveal/undo": GM,
    "POST /api/maps/{map_id}/reveal/clear": GM,
    "GET /maps/{map_id}": GM,
    "POST /api/maps/{map_id}/rename": GM,
    "POST /api/maps/{map_id}/attach": GM,
    "POST /api/maps/{map_id}/svg": GM,
    "POST /api/maps/{map_id}/revert": GM,
    "POST /api/maps/{map_id}/delete": GM,
    "GET /api/maps/{map_id}/key": GM,
    "POST /api/maps/{map_id}/key/generate": GM,
    "POST /api/maps/{map_id}/key/clear": GM,
    "POST /api/maps/{map_id}/key/{room}": GM,
    "POST /api/maps/{map_id}/key/{room}/ai": GM,
    "GET /maps/{map_id}/key": GM,
    "GET /api/maps/{map_id}/vtt": GM,
    "GET /api/maps/{map_id}/links": GM,
    "POST /api/maps/{map_id}/links/create": GM,
    "POST /api/maps/{map_id}/links/add": GM,
    "POST /api/maps/{map_id}/links/{link_id}/delete": GM,
    # live
    "GET /api/maps/{map_id}/live": player("map"),
    "GET /api/maps/{map_id}/tokens": GM,
    "POST /api/maps/{map_id}/tokens": GM,
    "POST /api/maps/{map_id}/tokens/clear": GM,
    "POST /api/map-tokens/{token_id}/move": player("token_own"),
    "POST /api/map-tokens/{token_id}/update": GM,
    "POST /api/map-tokens/{token_id}/delete": GM,
    "GET /api/maps/{map_id}/combat": GM,
    "POST /api/maps/{map_id}/combat/add": GM,
    "POST /api/maps/{map_id}/combat/remove-orphans": GM,
    "POST /api/maps/{map_id}/combat/next": GM,
    "POST /api/maps/{map_id}/combat/hit": GM,
    "POST /api/map-tokens/{token_id}/link": GM,
    "GET /api/handouts": GM,
    "POST /api/handouts": GM,
    "POST /api/handouts/image": GM,
    "POST /api/handouts/{handout_id}/update": GM,
    "POST /api/handouts/{handout_id}/delete": GM,
    "GET /handouts/{handout_id}/image": player("handout"),
    "POST /api/maps/{map_id}/handout/show": GM,
    "POST /api/maps/{map_id}/handout/hide": GM,
    # screen
    "GET /screen": GM,
    "POST /api/screen/cards": GM,
    "POST /api/screen/add-missing": GM,
    "POST /api/screen/pin": GM,
    "POST /api/screen/notes/append": GM,
    "POST /api/screen/cards/{card_id}": GM,
    "POST /api/screen/cards/{card_id}/move": GM,
    "POST /api/screen/cards/{card_id}/delete": GM,
    "POST /api/screen/notes": GM,
    "POST /api/screen/recap": GM,
    # tools
    "GET /tools/dice": GM,
    "POST /api/dice/roll": GM,
    "GET /tools/oneshot": GM,
    "POST /api/oneshot/generate": GM,
    "GET /tools/npc": GM,
    "POST /api/npc/generate": GM,
    "POST /api/npc/dialogue": GM,
    "GET /tools/initiative": GM,
    "GET /api/initiative": GM,
    "POST /api/initiative/add": GM,
    "POST /api/initiative/set": GM,
    "POST /api/initiative/remove": GM,
    "POST /api/initiative/hp": GM,
    "POST /api/initiative/condition": GM,
    "POST /api/initiative/condition/remove": GM,
    "POST /api/initiative/next": GM,
    "POST /api/initiative/clear-foes": GM,
    "POST /api/initiative/reset": GM,
    "GET /tools/encounters": GM,
    "POST /api/encounters/generate": GM,
    "POST /api/loot/generate": GM,
    "GET /api/guide": GM,
    "POST /api/guide/party": GM,
    "GET /api/guide/party/board": GM,
    "POST /api/guide/assess": GM,
    "GET /api/initiative/encounters": GM,
    "POST /api/initiative/encounters": GM,
    "POST /api/initiative/encounters/{encounter_id}/update": GM,
    "POST /api/initiative/encounters/{encounter_id}/delete": GM,
    "POST /api/initiative/encounters/{encounter_id}/load": GM,
    "POST /api/initiative/encounters/{encounter_id}/add-foe": GM,
    # tables
    "GET /tools/tables": GM,
    "POST /api/tables": GM,
    "POST /api/tables/import-builtin": GM,
    "POST /api/tables/history/clear": GM,
    "GET /api/tables/history": GM,
    "POST /api/tables/{table_id}/update": GM,
    "POST /api/tables/{table_id}/delete": GM,
    "POST /api/tables/{table_id}/roll": GM,
    # ops
    "GET /healthz": OPEN,
    "GET /backup/full": GM,
    "GET /backup/user-data": GM,
    "GET /api/backups": GM,
    "POST /api/backups/settings": GM,
    "POST /api/backups/run": GM,
    "GET /backup/auto/{name}": GM,
    # accounts (routers/accounts.py)
    "GET /login": OPEN,
    "POST /login": OPEN,
    "POST /logout": USER,
    "GET /account": USER,
    "POST /api/account/password": USER,
    "GET /users": GM,
    "POST /api/users/first-gm": GM,
    "GET /api/users": GM,
    "POST /api/users": GM,
    "POST /api/users/{user_id}/update": GM,
    "POST /api/users/{user_id}/reset-password": GM,
    "POST /api/users/{user_id}/delete": GM,
    "POST /api/users/{user_id}/games": GM,
    "GET /api/campaigns/{campaign_id}/members": GM,
    "POST /api/campaigns/{campaign_id}/members": GM,
    "POST /api/campaigns/{campaign_id}/members/{user_id}/remove": GM,
    "GET /api/campaigns/{campaign_id}/player-notes": GM,
    # the player side (routers/play.py)
    "GET /play": USER,
    "GET /play/campaigns/{campaign_id}": player("campaign"),
    "GET /api/play/campaigns/{campaign_id}/notes": player("campaign"),
    "POST /api/play/campaigns/{campaign_id}/notes": player("campaign"),
    "GET /play/library": player("library_optional"),
    "GET /api/play/search": player("library_game"),
}


# ---------------------------------------------------------------- matching
def _compile(template):
    pattern = re.sub(r"\{(\w+)\}", lambda m: f"(?P<{m.group(1)}>[^/]+)", re.escape(template).replace(r"\{", "{").replace(r"\}", "}"))
    return re.compile("^" + pattern + "$")


def _kind(policy):
    return policy[0] if isinstance(policy, tuple) else policy


_MATCHERS = []
for _key, _policy in ROUTE_POLICY.items():
    _method, _template = _key.split(" ", 1)
    _MATCHERS.append((_method, _compile(_template), _policy))


def policy_for(method, path):
    """(policy, path_params) for a request. Several entries can match one path (a
    literal segment and a {parameter} in the same place); the strictest wins, and a
    path nothing matches is GM-only."""
    method = "GET" if method == "HEAD" else method
    best, params = GM, {}
    best_rank = -1
    for m, rx, policy in _MATCHERS:
        if m != method:
            continue
        hit = rx.match(path)
        if hit:
            rank = _RANK[_kind(policy)]
            if rank > best_rank:
                best, params, best_rank = policy, hit.groupdict(), rank
    if best_rank < 0:
        return GM, {}
    return best, params


# ------------------------------------------------------------ player scopes
def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _campaign_of_map(map_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT campaign_id FROM maps WHERE id = ?", (map_id,)).fetchone()
    return row["campaign_id"] if row else None


def scope_allows(name, user, params, query):
    """True if this signed-in player may touch the thing named in the URL."""
    uid = user["id"]
    if name == "campaign":
        return accounts.is_member(uid, _int(params.get("campaign_id")))
    if name == "map":
        return accounts.is_member(uid, _campaign_of_map(_int(params.get("map_id"))))
    if name == "handout":
        # only the handout the GM is showing right now, on a map of one of their campaigns
        with database.get_cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM maps m JOIN campaign_members cm ON cm.campaign_id = m.campaign_id "
                "WHERE m.shown_handout_id = ? AND cm.user_id = ?", (_int(params.get("handout_id")), uid)).fetchone()
        return row is not None
    if name == "token_own":
        with database.get_cursor() as cur:
            row = cur.execute(
                "SELECT t.user_id, t.hidden, m.campaign_id FROM map_tokens t JOIN maps m ON m.id = t.map_id "
                "WHERE t.id = ?", (_int(params.get("token_id")),)).fetchone()
        return bool(row and row["user_id"] == uid and not row["hidden"] and accounts.is_member(uid, row["campaign_id"]))
    if name == "book":
        with database.get_cursor() as cur:
            row = cur.execute("SELECT game_key FROM books WHERE id = ?", (_int(params.get("book_id")),)).fetchone()
        return bool(row and accounts.library_allowed(uid, row["game_key"]))
    if name == "library_game":
        game = (query.get("game") or [""])[0]
        return accounts.library_allowed(uid, game)
    if name == "library_optional":
        game = (query.get("game") or [""])[0]
        return not game or accounts.library_allowed(uid, game)
    return False


# ------------------------------------------------------------------ helpers
def current_user(request):
    """The signed-in account dict, or None when accounts are off."""
    return request.scope.get("gm_user")


def is_gm(request):
    """True for a GM account - and for everybody while accounts are off."""
    user = current_user(request)
    return user is None or user["role"] == "gm"


def trust_proxy():
    import os
    return os.environ.get("GM_HELPER_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes", "on")


def client_ip(request):
    if trust_proxy():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded.strip():
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


def secure_cookies(request):
    """Mark the session cookie Secure when the page came over HTTPS (directly or
    through a proxy that says so), or when GM_HELPER_SECURE_COOKIES is set."""
    import os
    if os.environ.get("GM_HELPER_SECURE_COOKIES", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def safe_next(value):
    """A post-sign-in destination, only if it's a path on this site (never another site)."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value


def _origin_ok(headers):
    """Cross-site form posts are refused: if the browser says where a request came
    from, it has to be this site (or one listed in GM_HELPER_TRUSTED_ORIGINS)."""
    import os
    origin = headers.get("origin")
    if not origin:
        return True            # not a browser form/fetch (curl, tests): nothing to compare
    if origin == "null":
        return False
    host = urlsplit(origin).netloc.lower()
    allowed = {headers.get("host", "").lower(), headers.get("x-forwarded-host", "").split(",")[0].strip().lower()}
    for extra in os.environ.get("GM_HELPER_TRUSTED_ORIGINS", "").split(","):
        extra = extra.strip().lower()
        if extra:
            allowed.add(urlsplit(extra if "//" in extra else "//" + extra).netloc)
    allowed.discard("")
    return host in allowed


def _wants_json(path):
    return path.startswith("/api/")


def _cookie_token(headers):
    raw = headers.get("cookie", "")
    if not raw:
        return None
    try:
        jar = SimpleCookie()
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel else None


_NO_ACCESS_PAGE = (
    '<!doctype html><html lang="en" data-theme="tavern"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>GM Helper - No access</title>'
    '<link rel="stylesheet" href="/static/style.css"></head><body class="play"><main class="login-wrap">'
    '<div class="card login-card"><h1 class="login-title">No access</h1>'
    '<p>You don\'t have access to that page. It may belong to a campaign you haven\'t been added to, '
    'or your GM may have taken it away.</p>'
    '<p style="margin-bottom:0;"><a href="/play">Back to my campaigns</a></p></div></main></body></html>'
)


def decide(scope):
    """The access decision for one request: ("allow", user) or ("respond", response)."""
    path = scope.get("path", "")
    method = scope.get("method", "GET")
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    policy, params = policy_for(method, path)
    kind = _kind(policy)
    api = _wants_json(path)

    if method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(headers):
        return "respond", JSONResponse({"error": "That request came from another site, so it was refused."}, status_code=403)

    user = accounts.user_for_token(_cookie_token(headers))
    if kind == "open":
        return "allow", user

    if user is None:
        if api or method not in ("GET", "HEAD"):
            return "respond", JSONResponse({"error": "Sign in required."}, status_code=401)
        target = path + ("?" + scope["query_string"].decode("latin-1") if scope.get("query_string") else "")
        return "respond", RedirectResponse("/login?next=" + quote(target, safe="/?=&"), status_code=303)

    if user["must_change"] and path not in ALLOWED_WHILE_MUST_CHANGE:
        if api or method not in ("GET", "HEAD"):
            return "respond", JSONResponse({"error": "Choose a new password first.", "must_change": True}, status_code=403)
        return "respond", RedirectResponse("/account", status_code=303)

    if user["role"] == "gm" or kind == "user":
        return "allow", user

    # a player from here on
    if kind == "player" and scope_allows(policy[1], user, params, query):
        return "allow", user
    if api or method not in ("GET", "HEAD"):
        return "respond", JSONResponse({"error": "You don't have access to that."}, status_code=403)
    if kind == "gm":
        return "respond", RedirectResponse("/play", status_code=303)      # a GM page: send them home
    return "respond", HTMLResponse(_NO_ACCESS_PAGE, status_code=403)


_SECURITY_HEADERS = ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"SAMEORIGIN"),
                     (b"referrer-policy", b"same-origin"))


class AccessMiddleware:
    """Pure-ASGI (so streaming downloads aren't buffered). While there are no accounts
    it hands straight to the old shared-password check."""

    def __init__(self, app):
        self.app = app
        self.legacy = security.BasicAuthMiddleware(app)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in security.OPEN_PATHS:
            return await self.app(scope, receive, send)
        if not await run_in_threadpool(accounts.accounts_enabled):
            return await self.legacy(scope, receive, send)       # exactly the old behaviour, static files included
        if path.startswith("/static/"):
            return await self.app(scope, receive, send)          # the sign-in page needs its CSS and scripts

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        verdict, value = await run_in_threadpool(decide, scope)
        if verdict == "respond":
            return await value(scope, receive, send)

        scope["gm_user"] = value

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in _SECURITY_HEADERS if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
