"""Sign in / sign out, changing your own password, and (GM only) managing accounts,
campaign players and what each player may open. Who may reach which URL is decided
in access.py; this file is just the pages and the actions behind them."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import access, accounts, campaigns as campaigns_module
from ..library import games as games_module
from ..web import base_ctx, templates

router = APIRouter()


def _fail(message, status=400):
    return JSONResponse({"error": message}, status_code=status)


def _set_session_cookie(request, response, token):
    response.set_cookie(access.SESSION_COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, path="/",
                        httponly=True, samesite="lax", secure=access.secure_cookies(request))


def _home_for(user):
    return "/" if user["role"] == "gm" else "/play"


# ------------------------------------------------------------------ sign in
def _login_page(request, next_url, error="", status=200, headers=None, username=""):
    ctx = base_ctx(request)
    ctx.update({"next": access.safe_next(next_url), "error": error, "username": username})
    return templates.TemplateResponse(request, "login.html", ctx, status_code=status, headers=headers)


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if not accounts.accounts_enabled():
        return RedirectResponse("/", status_code=303)          # nothing to sign in to
    user = access.current_user(request)
    if user:
        return RedirectResponse(access.safe_next(next) if next != "/" else _home_for(user), status_code=303)
    return _login_page(request, next)


@router.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not accounts.accounts_enabled():
        return RedirectResponse("/", status_code=303)
    ip = access.client_ip(request)
    wait = accounts.throttle.retry_after(username, ip)
    if wait:
        minutes = max(1, (wait + 59) // 60)
        return _login_page(request, next, f"Too many tries. Wait about {minutes} minute{'s' if minutes != 1 else ''} and try again.",
                           status=429, headers={"Retry-After": str(wait)}, username=username)
    user = accounts.authenticate(username, password)
    if not user:
        accounts.throttle.record_failure(username, ip)
        return _login_page(request, next, "That username and password don't match.", status=401, username=username)
    accounts.throttle.record_success(username, ip)
    token = accounts.create_session(user["id"])
    destination = "/account" if user["must_change"] else (access.safe_next(next) if next not in ("", "/") else _home_for(user))
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(request, response, token)
    return response


@router.post("/logout")
def logout(request: Request):
    accounts.end_session(request.cookies.get(access.SESSION_COOKIE))
    response = RedirectResponse("/login" if accounts.accounts_enabled() else "/", status_code=303)
    response.delete_cookie(access.SESSION_COOKIE, path="/")
    return response


# ------------------------------------------------------------- my account
@router.get("/account")
def account_page(request: Request):
    user = access.current_user(request)
    if user is None:
        return RedirectResponse("/users", status_code=303)      # accounts aren't on yet
    ctx = base_ctx(request)
    ctx.update({"me": user, "layout": "base.html" if user["role"] == "gm" else "base_play.html"})
    return templates.TemplateResponse(request, "account.html", ctx)


@router.post("/api/account/password")
def api_change_password(request: Request, current: str = Form(""), new: str = Form("")):
    user = access.current_user(request)
    if user is None:
        return _fail("Accounts aren't switched on.")
    if not accounts.authenticate(user["username"], current):
        return _fail("Your current password isn't right.", 400)
    if new == current:
        return _fail("Choose a password that's different from the current one.")
    try:
        accounts.set_password(user["id"], new, must_change=False, keep_token=request.cookies.get(access.SESSION_COOKIE))
    except ValueError as e:
        return _fail(str(e))
    return JSONResponse({"ok": True, "next": _home_for(user)})


# ------------------------------------------------------------- GM: users
def _game_rows():
    return [{"key": g["key"], "name": g["name"]} for g in games_module.all_game_choices()]


@router.get("/users")
def users_page(request: Request):
    ctx = base_ctx(request)
    ctx.update({
        "enabled": accounts.accounts_enabled(),
        "me": access.current_user(request),
        "users": accounts.list_users(),
        "games": _game_rows(),
        "min_password": accounts.MIN_PASSWORD,
    })
    return templates.TemplateResponse(request, "users.html", ctx)


@router.post("/api/users/first-gm")
def api_first_gm(request: Request, username: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    """Turns accounts on. You're signed in as this account straight away."""
    if password != confirm:
        return _fail("The two passwords don't match.")
    try:
        user_id = accounts.bootstrap_first_gm(username, password)
    except ValueError as e:
        return _fail(str(e))
    response = JSONResponse({"ok": True})
    _set_session_cookie(request, response, accounts.create_session(user_id))
    return response


@router.get("/api/users")
def api_list_users():
    users = accounts.list_users()
    for u in users:
        u["games"] = accounts.game_access(u["id"]) if u["role"] == "player" else {}
    return JSONResponse({"users": users, "games": _game_rows()})


@router.post("/api/users")
def api_create_user(username: str = Form(""), display_name: str = Form(""), role: str = Form("player"),
                    password: str = Form("")):
    """Make an account. Leave the password blank for a generated temporary one (shown
    once); either way the person is asked to choose their own at first sign-in."""
    generated = not password
    password = password or accounts.generate_password()
    try:
        user_id = accounts.create_user(username, password, role=role, display_name=display_name, must_change=True)
    except ValueError as e:
        return _fail(str(e))
    body = {"user": accounts.get_user(user_id)}
    if generated:
        body["temp_password"] = password
    return JSONResponse(body)


@router.post("/api/users/{user_id}/update")
def api_update_user(user_id: int, display_name: str = Form(None), role: str = Form(None), disabled: str = Form(None)):
    flag = None if disabled is None else disabled.strip().lower() in ("1", "true", "on", "yes")
    try:
        accounts.update_user(user_id, display_name=display_name, role=role, disabled=flag)
    except ValueError as e:
        return _fail(str(e), 404 if str(e) == "Unknown user." else 400)
    return JSONResponse({"user": accounts.get_user(user_id)})


@router.post("/api/users/{user_id}/reset-password")
def api_reset_password(user_id: int):
    try:
        temp = accounts.reset_password(user_id)
    except ValueError as e:
        return _fail(str(e), 404)
    return JSONResponse({"temp_password": temp})


@router.post("/api/users/{user_id}/delete")
def api_delete_user(request: Request, user_id: int):
    me = access.current_user(request)
    if me and me["id"] == user_id:
        return _fail("You can't delete the account you're signed in with.")
    try:
        accounts.delete_user(user_id)
    except ValueError as e:
        return _fail(str(e), 404 if str(e) == "Unknown user." else 400)
    return JSONResponse({"ok": True})


@router.post("/api/users/{user_id}/games")
def api_user_games(user_id: int, game_key: str = Form(""), enabled: str = Form("1"), library: str = Form("0")):
    on = lambda v: str(v).strip().lower() in ("1", "true", "on", "yes")      # noqa: E731
    if game_key not in {g["key"] for g in games_module.all_game_choices()}:
        return _fail("Unknown game.", 404)
    try:
        accounts.set_game_access(user_id, game_key, on(enabled), library=on(library) and on(enabled))
    except ValueError as e:
        return _fail(str(e), 404 if str(e) == "Unknown user." else 400)
    return JSONResponse({"games": accounts.game_access(user_id)})


# ------------------------------------------------- GM: a campaign's players
@router.get("/api/campaigns/{campaign_id}/members")
def api_members(campaign_id: int):
    if not campaigns_module.get_campaign(campaign_id):
        return _fail("Unknown campaign.", 404)
    members = accounts.members(campaign_id)
    ids = {m["id"] for m in members}
    available = [{"id": u["id"], "name": u["display_name"]} for u in accounts.list_users()
                 if u["role"] == "player" and not u["disabled"] and u["id"] not in ids]
    return JSONResponse({"enabled": accounts.accounts_enabled(), "members": members, "available": available})


@router.post("/api/campaigns/{campaign_id}/members")
def api_add_member(campaign_id: int, user_id: int = Form(...)):
    try:
        accounts.add_member(campaign_id, user_id)
    except ValueError as e:
        return _fail(str(e), 404 if "Unknown" in str(e) else 400)
    return api_members(campaign_id)


@router.post("/api/campaigns/{campaign_id}/members/{user_id}/remove")
def api_remove_member(campaign_id: int, user_id: int):
    accounts.remove_member(campaign_id, user_id)
    return api_members(campaign_id)


@router.get("/api/campaigns/{campaign_id}/player-notes")
def api_player_notes(campaign_id: int):
    if not campaigns_module.get_campaign(campaign_id):
        return _fail("Unknown campaign.", 404)
    return JSONResponse({"notes": accounts.notes_for_campaign(campaign_id)})
