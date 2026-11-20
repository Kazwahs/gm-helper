"""Optional HTTP Basic authentication for the whole app.

Off by default (a home-LAN tool shouldn't make you log in). Set the
GM_HELPER_PASSWORD environment variable and every page, API call and static
file asks for a login first; GM_HELPER_USER (default "gm") is the username.
/healthz is always open so Docker's health check keeps working.

Basic auth sends the password with every request, so it belongs on a trusted
network or behind HTTPS (a reverse proxy) - it keeps the casual passer-by on
your LAN out, it is not a substitute for HTTPS on the open internet.
"""
import base64
import binascii
import os
import secrets

from starlette.responses import PlainTextResponse

from . import config

OPEN_PATHS = frozenset({"/healthz"})


def _password():
    return os.environ.get(config.PASSWORD_ENV, "")


def _username():
    return os.environ.get(config.USER_ENV, "") or "gm"


def is_enabled():
    return bool(_password())


def is_authorized(authorization_header):
    """True if an Authorization header carries the right Basic credentials."""
    if not authorization_header:
        return False
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    # Compare both fields every time (no early exit) so timing doesn't reveal which was wrong.
    user_ok = secrets.compare_digest(user.encode("utf-8"), _username().encode("utf-8"))
    pass_ok = secrets.compare_digest(password.encode("utf-8"), _password().encode("utf-8"))
    return user_ok and pass_ok


class BasicAuthMiddleware:
    """Pure-ASGI middleware (so streaming downloads aren't buffered)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not is_enabled() or scope.get("path") in OPEN_PATHS:
            return await self.app(scope, receive, send)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        if is_authorized(headers.get(b"authorization", b"").decode("latin-1")):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = PlainTextResponse(
            "Login required.", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="GM Helper", charset="UTF-8"'},
        )
        await response(scope, receive, send)
