"""Sign-in and who-may-open-what, end to end through the real app (app/access.py,
routers/accounts.py, routers/play.py): the route-policy table, sign-in behaviour,
the GM / player matrix, and checks that a player never receives GM-only material."""
import pytest
from fastapi.testclient import TestClient

from app import (access, accounts, campaign_world, campaigns, config, database, handouts, live, map_tokens,
                 maps_store)

PW = "correct horse battery"
GM_MARK = "SECRETMARK-GM-ONLY"
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240">'
       f'<rect width="320" height="240"/><g class="gm-only"><circle cx="5" cy="5" r="5"/><text>{GM_MARK}</text></g></svg>')


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(accounts, "_N", 2 ** 4)                              # cheap hashing in tests
    monkeypatch.setattr(accounts, "throttle", accounts.LoginThrottle())      # no lockouts leaking between tests
    for name in ("GM_HELPER_TRUST_PROXY", "GM_HELPER_SECURE_COOKIES", "GM_HELPER_TRUSTED_ORIGINS", config.PASSWORD_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def new_client(db, games_config):
    from app.main import app
    return lambda: TestClient(app)          # each one has its own cookie jar


def login(client, username, password=PW, **extra):
    r = client.post("/login", data={"username": username, "password": password, **extra}, follow_redirects=False)
    return r


def signed_in(new_client, username, password=PW):
    c = new_client()
    r = login(c, username, password)
    assert r.status_code == 303, r.text
    return c


def allowed(r):
    return r.status_code not in (303, 401, 403)


def denied(r):
    return r.status_code in (303, 403)


@pytest.fixture
def world(db, games_config):
    ids = {}
    ids["gm"] = accounts.create_user("zak", PW, role="gm", display_name="Zak", must_change=False)
    ids["aria"] = accounts.create_user("aria", PW, display_name="Aria", must_change=False)
    ids["bob"] = accounts.create_user("bob", PW, display_name="Bob", must_change=False)
    ids["eve"] = accounts.create_user("eve", PW, display_name="Eve", must_change=False)     # in no campaign
    ids["A"] = campaigns.create_campaign("rifts", "Shadows over Chi-Town", pitch="Neon and old magic.", tone="Gritty noir")
    ids["B"] = campaigns.create_campaign("rifts", "The Other Table")
    accounts.add_member(ids["A"], ids["aria"])
    accounts.add_member(ids["A"], ids["bob"])
    accounts.add_member(ids["B"], ids["bob"])

    ids["map_a"] = maps_store.save_map("rifts", "dungeon", "fantasy", "Crypt of A", SVG, campaign_id=ids["A"])
    ids["map_b"] = maps_store.save_map("rifts", "dungeon", "fantasy", "Crypt of B", SVG, campaign_id=ids["B"])
    ids["map_n"] = maps_store.save_map("rifts", "dungeon", "fantasy", "Loose map", SVG)

    ids["t_aria"] = map_tokens.add_token(ids["map_a"], "Aria", 10, 10)
    ids["t_bob"] = map_tokens.add_token(ids["map_a"], "Bob", 20, 20)
    ids["t_free"] = map_tokens.add_token(ids["map_a"], "Goblin", 30, 30)
    ids["t_ghost"] = map_tokens.add_token(ids["map_a"], "Ghost", 40, 40, hidden=True)
    ids["t_b"] = map_tokens.add_token(ids["map_b"], "Elsewhere", 5, 5)
    map_tokens.update_token(ids["t_aria"], "user_id", str(ids["aria"]))
    map_tokens.update_token(ids["t_bob"], "user_id", str(ids["bob"]))
    map_tokens.update_token(ids["t_ghost"], "user_id", str(ids["aria"]))
    with database.get_cursor(commit=True) as cur:      # a stale owner: Aria isn't in campaign B
        cur.execute("UPDATE map_tokens SET user_id = ? WHERE id = ?", (ids["aria"], ids["t_b"]))

    ids["h_shown"] = handouts.create_text("rifts", "A letter", "Dear adventurers")
    ids["h_hidden"] = handouts.create_text("rifts", "Unshown", "GM eyes only")
    ids["h_b"] = handouts.create_text("rifts", "B's letter", "For table B")
    handouts.show_on_map(ids["map_a"], ids["h_shown"])
    handouts.show_on_map(ids["map_b"], ids["h_b"])

    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO books (path, filename, title, publisher, series, game_key) VALUES "
                    "('R/r.pdf', 'r.pdf', 'Rifts Ultimate', 'P', 'S', 'rifts')")
        ids["book_rifts"] = cur.lastrowid
        cur.execute("INSERT INTO books (path, filename, title, publisher, series, game_key) VALUES "
                    "('D/d.pdf', 'd.pdf', 'Monster Manual', 'P', 'S', 'dnd5e')")
        ids["book_dnd"] = cur.lastrowid
    return ids


# ======================================================== the policy table
def _all_routes():
    from app.main import ROUTER_MODULES
    found = set()
    for module in ROUTER_MODULES:
        for r in module.router.routes:
            for method in r.methods:
                if method not in ("HEAD", "OPTIONS"):
                    found.add(f"{method} {r.path}")
    return found


def test_every_route_has_a_deliberate_policy():
    """Adding a route without deciding who may reach it fails here, by name."""
    missing = sorted(_all_routes() - set(access.ROUTE_POLICY))
    assert not missing, f"Add these to access.ROUTE_POLICY (GM unless players need them): {missing}"


def test_the_policy_table_has_no_stale_entries():
    stale = sorted(set(access.ROUTE_POLICY) - _all_routes())
    assert not stale, f"These policy lines match no route any more: {stale}"


def test_every_policy_resolves_to_what_it_declares():
    import re
    for key, declared in access.ROUTE_POLICY.items():
        method, template = key.split(" ", 1)
        sample = re.sub(r"\{\w+\}", "1", template)
        got, _ = access.policy_for(method, sample)
        assert got == declared, f"{key}: path {sample} resolves to {got}, not {declared}"


def test_only_a_short_reviewed_list_of_routes_is_open_to_players_or_everyone():
    """If this list changes, someone opened a door: make sure that was meant."""
    wide = {k: v for k, v in access.ROUTE_POLICY.items() if v != access.GM}
    assert sorted(wide) == sorted([
        "GET /healthz", "GET /login", "POST /login", "POST /logout", "GET /account", "POST /api/account/password",
        "GET /play", "GET /play/campaigns/{campaign_id}", "GET /api/play/campaigns/{campaign_id}/notes",
        "POST /api/play/campaigns/{campaign_id}/notes", "GET /play/library", "GET /api/play/search",
        "GET /maps/{map_id}/player", "GET /api/maps/{map_id}/live", "GET /handouts/{handout_id}/image",
        "POST /api/map-tokens/{token_id}/move", "GET /books/{book_id}/file",
    ])


def test_policy_for_basics():
    assert access.policy_for("GET", "/play/campaigns/7") == (("player", "campaign"), {"campaign_id": "7"})
    assert access.policy_for("HEAD", "/maps/3/player")[0] == ("player", "map")
    assert access.policy_for("GET", "/healthz")[0] == access.OPEN
    assert access.policy_for("GET", "/no/such/page")[0] == access.GM          # unknown = GM only
    assert access.policy_for("POST", "/play")[0] == access.GM                  # wrong method = GM only
    assert access.policy_for("GET", "/maps/3/player/extra")[0] == access.GM


def test_the_strictest_matching_entry_wins(monkeypatch):
    import re
    rx = access._compile("/x/{id}")
    monkeypatch.setattr(access, "_MATCHERS", [("GET", rx, access.USER), ("GET", re.compile("^/x/special$"), access.GM)])
    assert access.policy_for("GET", "/x/special")[0] == access.GM
    assert access.policy_for("GET", "/x/other")[0] == access.USER


@pytest.mark.parametrize("value,expected", [
    ("/play", "/play"), ("/maps/3?x=1", "/maps/3?x=1"), ("", "/"), (None, "/"), ("//evil.example", "/"),
    ("https://evil.example", "/"), ("/\\evil.example", "/"), ("evil", "/"), ("/ok\r\nSet-Cookie: x=1", "/"),
])
def test_safe_next_only_allows_paths_on_this_site(value, expected):
    assert access.safe_next(value) == expected


# ================================================== no accounts: as before
def test_without_accounts_the_app_is_open_and_the_new_pages_point_home(new_client):
    c = new_client()
    assert c.get("/").status_code == 200
    assert c.get("/login", follow_redirects=False).headers["location"] == "/"
    assert c.get("/play", follow_redirects=False).headers["location"] == "/"
    assert c.get("/account", follow_redirects=False).headers["location"] == "/users"
    page = c.get("/users")
    assert page.status_code == 200 and "Turn on accounts" in page.text
    assert "x-frame-options" not in c.get("/").headers          # nothing changes until accounts are on


def test_first_gm_switches_accounts_on_and_signs_you_in(new_client):
    c = new_client()
    r = c.post("/api/users/first-gm", data={"username": "zak", "password": PW, "confirm": PW})
    assert r.status_code == 200
    assert c.get("/users").status_code == 200                    # the cookie it set is already working
    assert c.get("/api/users").json()["users"][0]["username"] == "zak"
    stranger = new_client()
    assert stranger.get("/", follow_redirects=False).status_code == 303
    again = c.post("/api/users/first-gm", data={"username": "evil", "password": PW, "confirm": PW})
    assert again.status_code == 400 and "already set up" in again.json()["error"]


def test_first_gm_checks_the_passwords(new_client):
    c = new_client()
    assert "don't match" in c.post("/api/users/first-gm", data={"username": "zak", "password": PW, "confirm": "nope nope nope"}).json()["error"]
    assert c.post("/api/users/first-gm", data={"username": "zak", "password": "short", "confirm": "short"}).status_code == 400
    assert c.post("/api/users/first-gm", data={"username": "bad name!", "password": PW, "confirm": PW}).status_code == 400
    assert accounts.accounts_enabled() is False


def test_the_old_shared_password_still_guards_the_app_until_accounts_exist(new_client, monkeypatch):
    import base64
    monkeypatch.setenv(config.PASSWORD_ENV, "hunter2")
    basic = {"Authorization": "Basic " + base64.b64encode(b"gm:hunter2").decode()}
    c = new_client()
    assert c.post("/api/users/first-gm", data={"username": "zak", "password": PW, "confirm": PW}).status_code == 401
    assert c.post("/api/users/first-gm", data={"username": "zak", "password": PW, "confirm": PW}, headers=basic).status_code == 200
    # accounts took over: the shared password alone no longer opens anything
    other = new_client()
    assert other.get("/", headers=basic, follow_redirects=False).status_code == 303


# ===================================================== signed-out visitors
def test_signed_out_visitors_are_sent_to_sign_in_and_apis_say_401(world, new_client):
    c = new_client()
    r = c.get("/campaigns?game=rifts", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/campaigns?game=rifts"
    r = c.get("/api/search?q=dragon")
    assert r.status_code == 401 and r.json() == {"error": "Sign in required."}
    assert c.post("/api/screen/notes/append", data={"text": "x"}).status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/static/style.css").status_code == 200
    page = c.get("/login")
    assert page.status_code == 200 and 'name="password"' in page.text


def test_nothing_but_the_open_routes_answers_a_signed_out_visitor(world, new_client):
    c = new_client()
    import re
    for key, policy in access.ROUTE_POLICY.items():
        if policy == access.OPEN:
            continue
        method, template = key.split(" ", 1)
        path = re.sub(r"\{\w+\}", "1", template)
        r = c.request(method, path, follow_redirects=False)
        assert r.status_code in (303, 401), f"{key} answered a signed-out visitor with {r.status_code}"


# ============================================================== signing in
def test_wrong_password_and_unknown_user_look_the_same(world, new_client):
    c = new_client()
    a, b = login(c, "zak", "not the password"), login(c, "nobody", "not the password")
    assert a.status_code == b.status_code == 401
    assert "username and password" in a.text and "username and password" in b.text
    assert "set-cookie" not in a.headers


def test_signing_in_sets_a_hardened_session_cookie(world, new_client):
    c = new_client()
    r = login(c, "zak")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith(access.SESSION_COOKIE + "=")
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie and "max-age=" in cookie
    assert "secure" not in cookie                                   # plain http on the LAN
    assert c.get("/").status_code == 200


def test_the_cookie_is_secure_over_https_or_when_forced(world, new_client, monkeypatch):
    c = new_client()
    r = c.post("/login", data={"username": "zak", "password": PW}, headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()
    monkeypatch.setenv("GM_HELPER_SECURE_COOKIES", "1")
    r = login(new_client(), "zak")
    assert "secure" in r.headers["set-cookie"].lower()


def test_players_land_on_their_page_and_gms_on_home(world, new_client):
    assert login(new_client(), "aria").headers["location"] == "/play"
    assert login(new_client(), "zak").headers["location"] == "/"


def test_sign_in_honours_a_local_next_and_ignores_other_sites(world, new_client):
    assert login(new_client(), "zak", next="/campaigns?game=rifts").headers["location"] == "/campaigns?game=rifts"
    for bad in ("//evil.example/x", "https://evil.example", "/\\evil.example"):
        assert login(new_client(), "zak", next=bad).headers["location"] == "/"


def test_the_sign_in_page_is_skipped_when_already_signed_in(world, new_client):
    c = signed_in(new_client, "aria")
    assert c.get("/login", follow_redirects=False).headers["location"] == "/play"


def test_five_wrong_tries_lock_that_name_from_that_address(world, new_client):
    c = new_client()
    for _ in range(5):
        assert login(c, "zak", "nope nope nope").status_code == 401
    r = login(c, "zak")                                              # even the right password is refused now
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "Too many tries" in r.text
    assert login(c, "aria").status_code == 303                       # other names from this address still work


def test_lockout_follows_the_forwarded_address_only_when_the_proxy_is_trusted(world, new_client, monkeypatch):
    monkeypatch.setenv("GM_HELPER_TRUST_PROXY", "1")
    c = new_client()
    for _ in range(5):
        c.post("/login", data={"username": "zak", "password": "nope nope nope"}, headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
    assert c.post("/login", data={"username": "zak", "password": PW}, headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 429
    ok = c.post("/login", data={"username": "zak", "password": PW}, headers={"X-Forwarded-For": "8.8.8.8"}, follow_redirects=False)
    assert ok.status_code == 303


def test_a_success_clears_earlier_failures(world, new_client):
    c = new_client()
    for _ in range(4):
        login(c, "zak", "nope nope nope")
    assert login(c, "zak").status_code == 303
    for _ in range(4):
        login(c, "zak", "nope nope nope")
    assert login(c, "zak").status_code == 303


def test_signing_out_ends_the_session_for_good(world, new_client):
    c = signed_in(new_client, "aria")
    token = c.cookies.get(access.SESSION_COOKIE)
    r = c.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert accounts.user_for_token(token) is None
    replay = new_client()
    assert replay.get("/play", headers={"Cookie": f"{access.SESSION_COOKIE}={token}"}, follow_redirects=False).status_code == 303


def test_disabling_someone_cuts_them_off_on_their_next_click(world, new_client):
    c = signed_in(new_client, "aria")
    assert c.get("/play").status_code == 200
    accounts.update_user(world["aria"], disabled=True)
    assert c.get("/play", follow_redirects=False).status_code == 303
    assert login(new_client(), "aria").status_code == 401


def test_accounts_mode_adds_security_headers(world, new_client):
    r = signed_in(new_client, "zak").get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["referrer-policy"] == "same-origin"
    assert "x-frame-options" in new_client().get("/login").headers


# ===================================================================== CSRF
def test_posts_from_another_site_are_refused(world, new_client):
    c = new_client()
    r = c.post("/login", data={"username": "zak", "password": PW}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "another site" in r.json()["error"]
    gm = signed_in(new_client, "zak")
    r = gm.post(f"/api/campaigns/{world['A']}/delete", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert campaigns.get_campaign(world["A"]) is not None
    assert gm.post("/api/users", data={"username": "x1", "password": PW}, headers={"Origin": "null"}).status_code == 403


def test_posts_from_this_site_or_a_listed_proxy_name_are_fine(world, new_client, monkeypatch):
    c = new_client()
    assert c.post("/login", data={"username": "zak", "password": PW}, headers={"Origin": "http://testserver"},
                  follow_redirects=False).status_code == 303
    assert new_client().post("/login", data={"username": "zak", "password": PW},
                             headers={"Origin": "https://gm.example.com", "X-Forwarded-Host": "gm.example.com"},
                             follow_redirects=False).status_code == 303
    assert new_client().post("/login", data={"username": "zak", "password": PW}, headers={"Origin": "https://gm.example.com"}).status_code == 403
    monkeypatch.setenv("GM_HELPER_TRUSTED_ORIGINS", "https://gm.example.com")
    assert new_client().post("/login", data={"username": "zak", "password": PW}, headers={"Origin": "https://gm.example.com"},
                             follow_redirects=False).status_code == 303


# ===================================================== temporary passwords
@pytest.fixture
def temp_player(world):
    accounts.create_user("newbie", "temporary-pass-1", display_name="Newbie", must_change=True)
    return "newbie"


def test_a_temporary_password_has_to_be_replaced_before_anything_else(world, temp_player, new_client):
    c = new_client()
    r = login(c, temp_player, "temporary-pass-1")
    assert r.status_code == 303 and r.headers["location"] == "/account"
    assert c.get("/play", follow_redirects=False).headers["location"] == "/account"
    blocked = c.post("/api/play/campaigns/1/notes", data={"body": "x"})
    assert blocked.status_code == 403 and blocked.json()["must_change"] is True
    assert c.get("/account").status_code == 200
    assert c.post("/logout", follow_redirects=False).status_code == 303          # signing out stays possible


def test_changing_the_password_unblocks_the_account(world, temp_player, new_client):
    c = new_client()
    login(c, temp_player, "temporary-pass-1")
    bad = c.post("/api/account/password", data={"current": "wrong wrong wrong", "new": "a fresh long password"})
    assert bad.status_code == 400 and "isn't right" in bad.json()["error"]
    assert c.post("/api/account/password", data={"current": "temporary-pass-1", "new": "temporary-pass-1"}).status_code == 400
    assert c.post("/api/account/password", data={"current": "temporary-pass-1", "new": "short"}).status_code == 400
    ok = c.post("/api/account/password", data={"current": "temporary-pass-1", "new": "a fresh long password"})
    assert ok.status_code == 200 and ok.json()["next"] == "/play"
    assert c.get("/play").status_code == 200
    assert login(new_client(), temp_player, "temporary-pass-1").status_code == 401
    assert login(new_client(), temp_player, "a fresh long password").headers["location"] == "/play"


def test_changing_your_password_signs_out_your_other_devices(world, new_client):
    phone, laptop = signed_in(new_client, "aria"), signed_in(new_client, "aria")
    r = laptop.post("/api/account/password", data={"current": PW, "new": "a different long password"})
    assert r.status_code == 200
    assert laptop.get("/play").status_code == 200
    assert phone.get("/play", follow_redirects=False).status_code == 303


# ============================================ a GM can do GM things
def test_the_gm_reaches_the_gm_pages_and_previews_the_player_view(world, new_client):
    gm = signed_in(new_client, "zak")
    for path in ("/", "/settings", "/users", "/campaigns?game=rifts", f"/campaigns/{world['A']}", f"/maps/{world['map_a']}",
                 "/library", "/account", "/api/users", f"/api/campaigns/{world['A']}/members"):
        assert gm.get(path).status_code == 200, path
    assert gm.get(f"/play/campaigns/{world['A']}").status_code == 200
    assert gm.get("/play", follow_redirects=False).headers["location"] == "/"
    assert "Sign out" in gm.get("/").text


# ================================================== a player's boundaries
def test_a_player_is_turned_away_from_every_gm_route(world, new_client):
    """Systematic: every route marked GM answers a player with a redirect or a 403 - never its content."""
    import re
    c = signed_in(new_client, "aria")
    checked = 0
    for key, policy in access.ROUTE_POLICY.items():
        if policy != access.GM:
            continue
        method, template = key.split(" ", 1)
        path = re.sub(r"\{\w+\}", "1", template)
        r = c.request(method, path, follow_redirects=False)
        assert denied(r), f"{key} gave a player status {r.status_code}"
        checked += 1
    assert checked > 150


def test_gm_pages_send_a_player_home_and_apis_say_403(world, new_client):
    c = signed_in(new_client, "aria")
    r = c.get("/campaigns?game=rifts", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/play"
    r = c.get("/api/jump?q=a")
    assert r.status_code == 403 and r.json() == {"error": "You don't have access to that."}
    assert c.get("/backup/full", follow_redirects=False).headers["location"] == "/play"


def test_players_cannot_manage_accounts_or_campaigns(world, new_client):
    c = signed_in(new_client, "aria")
    assert c.post("/api/users", data={"username": "sidekick", "password": PW, "role": "gm"}).status_code == 403
    assert c.post(f"/api/users/{world['gm']}/reset-password").status_code == 403
    assert c.post(f"/api/users/{world['aria']}/update", data={"role": "gm"}).status_code == 403
    assert c.post(f"/api/campaigns/{world['A']}/members", data={"user_id": world["eve"]}).status_code == 403
    assert c.post(f"/api/campaigns/{world['A']}/secrets", data={"text": "x"}).status_code == 403
    assert accounts.get_user(world["aria"])["role"] == "player"
    assert accounts.get_user(world["gm"]) is not None


def test_a_player_following_a_stale_link_gets_a_readable_page_not_bare_text(world, new_client):
    eve = signed_in(new_client, "eve")
    r = eve.get(f"/maps/{world['map_a']}/player", follow_redirects=False)
    assert r.status_code == 403 and r.headers["content-type"].startswith("text/html")
    assert "No access" in r.text and 'href="/play"' in r.text and "/static/style.css" in r.text
    r = eve.get(f"/api/maps/{world['map_a']}/live")                     # scripts still get JSON
    assert r.status_code == 403 and r.json() == {"error": "You don't have access to that."}


def test_a_player_sees_their_campaigns_and_not_others(world, new_client):
    aria = signed_in(new_client, "aria")
    page = aria.get("/play")
    assert page.status_code == 200 and "Shadows over Chi-Town" in page.text and "The Other Table" not in page.text
    assert aria.get(f"/play/campaigns/{world['A']}").status_code == 200
    assert denied(aria.get(f"/play/campaigns/{world['B']}", follow_redirects=False))
    bob = signed_in(new_client, "bob")
    assert "The Other Table" in bob.get("/play").text
    eve = signed_in(new_client, "eve")
    assert "haven't been added" in eve.get("/play").text
    assert denied(eve.get(f"/play/campaigns/{world['A']}", follow_redirects=False))


def test_player_campaign_page_shows_only_the_safe_slice(world, new_client):
    a = world["A"]
    campaigns.add_npc(a, "Baron Secretus", role="villain", secret="He is the lich")
    campaigns.add_location(a, "The Hidden Lair", "Beneath the opera house")
    campaigns.add_thread(a, "Betrayal at dawn", "GM-only thread")
    campaigns.add_faction(a, "The Black Hand", goal="Rule everything")
    campaign_world.add_clock(a, "Doom approaches")
    campaign_world.add_secret(a, "The vault code is 4417")
    found = campaign_world.add_secret(a, "The baron is a vampire")
    campaign_world.reveal(found)
    played = campaigns.add_session(a, "Arrival")
    campaigns.update_session_field(played, "summary", "The party reached the city and met a stranger.")
    campaigns.update_session_field(played, "status", "played")
    campaigns.update_session_field(played, "strong_start", "GM plan: ambush on the bridge")
    planned = campaigns.add_session(a, "Ambush at the docks")
    campaigns.update_session_field(planned, "summary", "Not played yet: SECRET PLAN")

    text = signed_in(new_client, "aria").get(f"/play/campaigns/{a}").text
    for shown in ("Neon and old magic.", "Gritty noir", "The baron is a vampire", "The party reached the city",
                  "Session 1", "Crypt of A"):
        assert shown in text, shown
    for hidden in ("Baron Secretus", "He is the lich", "The Hidden Lair", "Betrayal at dawn", "The Black Hand", "Rule everything",
                   "Doom approaches", "The vault code is 4417", "Ambush at the docks", "SECRET PLAN", "GM plan: ambush", "Crypt of B"):
        assert hidden not in text, hidden


def test_player_pages_escape_what_the_gm_typed(world, new_client):
    campaigns.update_campaign_field(world["A"], "pitch", "<script>alert(1)</script> & <b>bold</b>")
    text = signed_in(new_client, "aria").get(f"/play/campaigns/{world['A']}").text
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text


# --------------------------------------------------------------- the map
def test_a_player_opens_only_their_campaigns_maps(world, new_client):
    aria = signed_in(new_client, "aria")
    assert aria.get(f"/maps/{world['map_a']}/player").status_code == 200
    assert aria.get(f"/api/maps/{world['map_a']}/live").status_code == 200
    for key in ("map_b", "map_n"):
        assert denied(aria.get(f"/maps/{world[key]}/player", follow_redirects=False)), key
        assert denied(aria.get(f"/api/maps/{world[key]}/live", follow_redirects=False)), key
    assert denied(aria.get("/maps/999999/player", follow_redirects=False))             # unknown map: nothing to learn from it
    assert denied(aria.get(f"/maps/{world['map_a']}", follow_redirects=False))         # the GM's editor stays closed
    assert denied(aria.get(f"/api/maps/{world['map_a']}/key", follow_redirects=False))
    assert denied(aria.get(f"/api/maps/{world['map_a']}/vtt", follow_redirects=False))
    assert denied(aria.get(f"/api/maps/{world['map_a']}/tokens", follow_redirects=False))


def test_a_players_copy_of_the_map_has_no_gm_only_markers(world, new_client):
    player_page = signed_in(new_client, "aria").get(f"/maps/{world['map_a']}/player").text
    gm_page = signed_in(new_client, "zak").get(f"/maps/{world['map_a']}/player").text
    assert GM_MARK not in player_page
    assert GM_MARK in gm_page                      # the GM's second-screen view is unchanged (CSS hides it there)
    assert "Crypt of A" in player_page


def test_live_state_marks_only_the_viewers_own_tokens_and_never_leaks_owners(world, new_client):
    aria = signed_in(new_client, "aria").get(f"/api/maps/{world['map_a']}/live").json()
    by_label = {t["label"]: t for t in aria["tokens"]}
    assert set(by_label) == {"Aria", "Bob", "Goblin"}                       # the hidden token is never sent
    assert by_label["Aria"]["mine"] is True
    assert "mine" not in by_label["Bob"] and "mine" not in by_label["Goblin"]
    assert "user_id" not in str(aria)
    bob = signed_in(new_client, "bob").get(f"/api/maps/{world['map_a']}/live").json()
    assert {t["label"] for t in bob["tokens"] if t.get("mine")} == {"Bob"}
    gm = signed_in(new_client, "zak").get(f"/api/maps/{world['map_a']}/live").json()
    assert not any("mine" in t for t in gm["tokens"])


def test_the_player_page_knows_whether_you_can_drag(world, new_client):
    assert "const canMove = true" in signed_in(new_client, "aria").get(f"/maps/{world['map_a']}/player").text
    assert "const canMove = false" in signed_in(new_client, "zak").get(f"/maps/{world['map_a']}/player").text


def test_a_player_moves_only_a_token_the_gm_gave_them(world, new_client):
    aria = signed_in(new_client, "aria")
    r = aria.post(f"/api/map-tokens/{world['t_aria']}/move", data={"x": "55.5", "y": "66"})
    assert r.status_code == 200
    t = map_tokens.get_token(world["t_aria"])
    assert (t["x"], t["y"]) == (55.5, 66.0)
    for key in ("t_bob", "t_free", "t_ghost", "t_b"):
        before = map_tokens.get_token(world[key])
        r = aria.post(f"/api/map-tokens/{world[key]}/move", data={"x": "1", "y": "1"})
        assert r.status_code == 403, key
        assert map_tokens.get_token(world[key]) == before, key
    assert aria.post("/api/map-tokens/999999/move", data={"x": "1", "y": "1"}).status_code == 403


def test_a_player_cannot_edit_delete_or_hand_over_tokens(world, new_client):
    aria = signed_in(new_client, "aria")
    tid = world["t_aria"]
    assert aria.post(f"/api/map-tokens/{tid}/update", data={"field": "hidden", "value": "1"}).status_code == 403
    assert aria.post(f"/api/map-tokens/{tid}/update", data={"field": "user_id", "value": str(world["bob"])}).status_code == 403
    assert aria.post(f"/api/map-tokens/{tid}/delete").status_code == 403
    assert aria.post(f"/api/maps/{world['map_a']}/tokens", data={"label": "Fake", "x": "1", "y": "1"}).status_code == 403
    assert map_tokens.get_token(tid)["user_id"] == world["aria"]


def test_a_removed_player_loses_the_token_and_the_map(world, new_client):
    aria = signed_in(new_client, "aria")
    accounts.remove_member(world["A"], world["aria"])
    assert denied(aria.get(f"/maps/{world['map_a']}/player", follow_redirects=False))
    assert aria.post(f"/api/map-tokens/{world['t_aria']}/move", data={"x": "1", "y": "1"}).status_code == 403


def test_the_gm_hands_a_token_to_a_player_through_the_api(world, new_client):
    gm = signed_in(new_client, "zak")
    r = gm.post(f"/api/map-tokens/{world['t_free']}/update", data={"field": "user_id", "value": str(world["aria"])})
    assert r.status_code == 200 and r.json()["token"]["user_id"] == world["aria"]
    assert gm.post(f"/api/map-tokens/{world['t_free']}/update", data={"field": "user_id", "value": str(world["eve"])}).status_code == 400
    assert signed_in(new_client, "aria").post(f"/api/map-tokens/{world['t_free']}/move", data={"x": "9", "y": "9"}).status_code == 200


def test_the_gms_map_page_lists_the_campaigns_players(world, new_client):
    page = signed_in(new_client, "zak").get(f"/maps/{world['map_a']}").text
    assert "const tokenPlayers = [" in page and "Aria" in page and "Bob" in page and "Eve" not in page.split("const tokenPlayers")[1].split("\n")[0]


# ------------------------------------------------------------- handouts
def test_a_player_gets_only_the_handout_being_shown_on_their_map(world, new_client):
    aria = signed_in(new_client, "aria")
    assert allowed(aria.get(f"/handouts/{world['h_shown']}/image"))
    for key in ("h_hidden", "h_b"):
        assert aria.get(f"/handouts/{world[key]}/image").status_code == 403, key
    handouts.hide_on_map(world["map_a"])                                       # the GM puts it away
    assert aria.get(f"/handouts/{world['h_shown']}/image").status_code == 403
    assert aria.get("/api/handouts").status_code == 403


# ---------------------------------------------------------------- notes
def test_player_notes_are_private_to_the_player_and_readable_by_the_gm(world, new_client):
    aria, bob, gm = (signed_in(new_client, n) for n in ("aria", "bob", "zak"))
    a = world["A"]
    assert aria.get(f"/api/play/campaigns/{a}/notes").json()["body"] == ""
    assert aria.post(f"/api/play/campaigns/{a}/notes", data={"body": "The baron limps."}).json()["ok"] is True
    assert aria.get(f"/api/play/campaigns/{a}/notes").json()["body"] == "The baron limps."
    assert bob.get(f"/api/play/campaigns/{a}/notes").json()["body"] == ""             # Bob's own notebook, not Aria's
    notes = {n["name"]: n["body"] for n in gm.get(f"/api/campaigns/{a}/player-notes").json()["notes"]}
    assert notes == {"Aria": "The baron limps.", "Bob": ""}
    assert aria.get(f"/api/campaigns/{a}/player-notes").status_code == 403           # players can't read each other's
    assert aria.get(f"/api/play/campaigns/{world['B']}/notes").status_code == 403
    assert aria.post(f"/api/play/campaigns/{world['B']}/notes", data={"body": "x"}).status_code == 403


def test_notes_have_a_size_limit_and_gms_have_no_player_notebook(world, new_client):
    aria, gm = signed_in(new_client, "aria"), signed_in(new_client, "zak")
    r = aria.post(f"/api/play/campaigns/{world['A']}/notes", data={"body": "x" * (accounts.MAX_NOTE + 1)})
    assert r.status_code == 400 and "Notes can hold" in r.json()["error"]
    assert gm.post(f"/api/play/campaigns/{world['A']}/notes", data={"body": "x"}).status_code == 400
    assert gm.get(f"/api/play/campaigns/{world['A']}/notes").status_code == 400


def test_the_notes_box_only_appears_for_players(world, new_client):
    assert 'id="notes-area"' in signed_in(new_client, "aria").get(f"/play/campaigns/{world['A']}").text
    gm_view = signed_in(new_client, "zak").get(f"/play/campaigns/{world['A']}").text
    assert 'id="notes-area"' not in gm_view and "Player preview" in gm_view


# --------------------------------------------------------------- library
def test_library_access_is_off_until_the_gm_ticks_it(world, new_client):
    aria = signed_in(new_client, "aria")
    assert 'href="/play/library"' not in aria.get("/play").text           # no menu entry without any library
    assert "hasn't opened any libraries" in aria.get("/play/library").text
    assert aria.get("/play/library?game=rifts").status_code == 403
    assert aria.get("/api/play/search?q=dragon&game=rifts").status_code == 403
    assert aria.get(f"/books/{world['book_rifts']}/file").status_code == 403
    for path in ("/library?game=rifts", "/api/search?q=dragon", "/api/library/ask"):
        assert denied(aria.get(path, follow_redirects=False)), path


def test_a_library_grant_opens_exactly_that_game(world, new_client):
    accounts.set_game_access(world["aria"], "rifts", True, library=True)
    aria = signed_in(new_client, "aria")
    assert 'href="/play/library"' in aria.get("/play").text
    page = aria.get("/play/library?game=rifts")
    assert page.status_code == 200 and "Rifts Ultimate" in page.text and "Monster Manual" not in page.text
    assert aria.get("/api/play/search?q=dragon&game=rifts").status_code == 200
    assert allowed(aria.get(f"/books/{world['book_rifts']}/file"))        # no library folder in tests: 404, not 403
    assert aria.get(f"/books/{world['book_dnd']}/file").status_code == 403
    assert aria.get("/play/library?game=dnd5e").status_code == 403
    assert aria.get("/api/play/search?q=dragon&game=dnd5e").status_code == 403
    assert aria.get("/api/play/search?q=dragon").status_code == 403       # a game is required


def test_seeing_a_game_without_the_library_flag_is_not_library_access(world, new_client):
    accounts.set_game_access(world["aria"], "rifts", True, library=False)
    aria = signed_in(new_client, "aria")
    assert aria.get("/play/library?game=rifts").status_code == 403
    accounts.set_game_access(world["aria"], "rifts", True, library=True)
    assert aria.get("/play/library?game=rifts").status_code == 200
    accounts.set_game_access(world["aria"], "rifts", False)               # revoked: takes effect immediately
    assert aria.get("/play/library?game=rifts").status_code == 403


def test_library_search_stays_inside_the_granted_game(world, new_client, monkeypatch):
    seen = []
    from app.library import search as search_module
    real = search_module.search
    monkeypatch.setattr(search_module, "search", lambda q, game_key=None, **kw: seen.append(game_key) or real(q, game_key=game_key, **kw))
    accounts.set_game_access(world["aria"], "rifts", True, library=True)
    signed_in(new_client, "aria").get("/api/play/search?q=dragon&game=rifts")
    assert seen == ["rifts"]


# ---------------------------------------------------- the users admin API
def test_gm_creates_players_with_generated_or_chosen_passwords(world, new_client):
    gm = signed_in(new_client, "zak")
    r = gm.post("/api/users", data={"username": "cara", "display_name": "Cara"})
    body = r.json()
    assert r.status_code == 200 and body["user"]["must_change"] is True and body["user"]["role"] == "player"
    assert accounts.authenticate("cara", body["temp_password"]) is not None
    r = gm.post("/api/users", data={"username": "dan", "password": "chosen by the gm 1"})
    assert r.status_code == 200 and "temp_password" not in r.json()
    assert gm.post("/api/users", data={"username": "CARA"}).status_code == 400
    assert gm.post("/api/users", data={"username": "ed", "password": "short"}).status_code == 400
    assert gm.post("/api/users", data={"username": "fay", "role": "wizard"}).status_code == 400


def test_gm_resets_disables_and_deletes_accounts(world, new_client):
    gm = signed_in(new_client, "zak")
    aria = signed_in(new_client, "aria")
    temp = gm.post(f"/api/users/{world['aria']}/reset-password").json()["temp_password"]
    assert aria.get("/play", follow_redirects=False).status_code == 303          # signed out by the reset
    assert login(new_client(), "aria", temp).headers["location"] == "/account"
    assert gm.post(f"/api/users/{world['aria']}/update", data={"disabled": "1"}).json()["user"]["disabled"] is True
    assert gm.post(f"/api/users/{world['aria']}/update", data={"display_name": "Aria Q"}).json()["user"]["display_name"] == "Aria Q"
    assert gm.post(f"/api/users/{world['eve']}/delete").json() == {"ok": True}
    assert gm.post("/api/users/999999/delete").status_code == 404
    assert gm.post("/api/users/999999/reset-password").status_code == 404


def test_a_gm_cannot_delete_or_demote_themselves_into_a_lockout(world, new_client):
    gm = signed_in(new_client, "zak")
    assert "can't delete the account you're signed in with" in gm.post(f"/api/users/{world['gm']}/delete").json()["error"]
    assert "at least one active GM" in gm.post(f"/api/users/{world['gm']}/update", data={"role": "player"}).json()["error"]
    assert "at least one active GM" in gm.post(f"/api/users/{world['gm']}/update", data={"disabled": "1"}).json()["error"]


def test_gm_sets_game_and_library_access_per_player(world, new_client):
    gm = signed_in(new_client, "zak")
    r = gm.post(f"/api/users/{world['eve']}/games", data={"game_key": "rifts", "enabled": "1", "library": "1"})
    assert r.json()["games"] == {"rifts": {"library": True}}
    r = gm.post(f"/api/users/{world['eve']}/games", data={"game_key": "dnd5e", "enabled": "1", "library": "0"})
    assert r.json()["games"]["dnd5e"] == {"library": False}
    r = gm.post(f"/api/users/{world['eve']}/games", data={"game_key": "rifts", "enabled": "0", "library": "1"})
    assert list(r.json()["games"]) == ["dnd5e"]                                   # off means off, library flag or not
    assert gm.post(f"/api/users/{world['eve']}/games", data={"game_key": "nonsense"}).status_code == 404
    assert gm.post(f"/api/users/{world['gm']}/games", data={"game_key": "rifts"}).status_code == 400
    listing = gm.get("/api/users").json()
    eve = next(u for u in listing["users"] if u["username"] == "eve")
    assert list(eve["games"]) == ["dnd5e"]
    assert next(u for u in listing["users"] if u["username"] == "zak")["games"] == {}
    assert {"key": "rifts", "name": "Rifts"} in listing["games"]


def test_gm_manages_a_campaigns_players(world, new_client):
    gm = signed_in(new_client, "zak")
    a = world["A"]
    r = gm.get(f"/api/campaigns/{a}/members").json()
    assert r["enabled"] is True and {m["username"] for m in r["members"]} == {"aria", "bob"}
    assert [p["name"] for p in r["available"]] == ["Eve"]
    r = gm.post(f"/api/campaigns/{a}/members", data={"user_id": world["eve"]}).json()
    assert {m["username"] for m in r["members"]} == {"aria", "bob", "eve"} and r["available"] == []
    assert accounts.game_access(world["eve"]) == {"rifts": {"library": False}}
    assert gm.post(f"/api/campaigns/{a}/members", data={"user_id": world["gm"]}).status_code == 400
    assert gm.post(f"/api/campaigns/{a}/members", data={"user_id": 999999}).status_code == 404
    assert gm.post("/api/campaigns/999999/members", data={"user_id": world["eve"]}).status_code == 404
    r = gm.post(f"/api/campaigns/{a}/members/{world['eve']}/remove").json()
    assert {m["username"] for m in r["members"]} == {"aria", "bob"}
    assert gm.get("/api/campaigns/999999/members").status_code == 404
    assert gm.get("/api/campaigns/999999/player-notes").status_code == 404


def test_the_gm_campaign_page_has_a_players_card(world, new_client):
    page = signed_in(new_client, "zak").get(f"/campaigns/{world['A']}").text
    assert 'id="players-card"' in page and "initPlayersCard(" in page and "accounts.js" in page
    assert signed_in(new_client, "zak").get("/users").text.count("Add someone") == 1


def test_the_users_page_is_gm_only(world, new_client):
    assert denied(signed_in(new_client, "aria").get("/users", follow_redirects=False))
    assert "Turn on accounts" not in signed_in(new_client, "zak").get("/users").text


def test_the_nav_offers_users_to_the_gm(world, new_client):
    assert 'href="/users"' in signed_in(new_client, "zak").get("/").text
