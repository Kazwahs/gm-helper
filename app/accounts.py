"""User accounts: who may sign in, and who may see which campaign, game and book.

This is the data side. Deciding which URL a given person may open is access.py;
the sign-in pages are routers/accounts.py.

How it fits together
  * With no rows in `users` the app behaves exactly as before (open, or the one
    shared GM_HELPER_PASSWORD). The first GM account switches accounts on.
  * 'gm' accounts see everything. 'player' accounts see only the campaigns they
    were added to (campaign_members), the games the GM switched on for them
    (user_game_access) and - only where the GM ticked it - that game's library.
  * Passwords are hashed with scrypt (standard library, no new dependency).
    Sessions are random tokens in a cookie; the database keeps only a hash of the
    token, so signing out, disabling or deleting someone really ends their access.

Command line, for when every GM password is lost (run inside the container):
    python -m app.accounts list
    python -m app.accounts create-gm NAME
    python -m app.accounts reset-password NAME
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from . import database

ROLES = ("gm", "player")
MIN_PASSWORD = 10
MAX_PASSWORD = 200          # scrypt is deliberately slow: don't let a 1 MB "password" tie up the server
SESSION_DAYS = 14
MAX_NOTE = 20000
MAX_NAME = 40

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")

# scrypt cost: 2**14 is the usual interactive setting (about 16 MB and a few tens of
# milliseconds). The numbers are stored in each hash, so they can be raised later.
_N, _R, _P = 2 ** 14, 8, 1
_MAXMEM = 64 * 1024 * 1024


# ---------------------------------------------------------------- time
def _now():
    """Current UTC time. A function so tests can move the clock."""
    return datetime.now(timezone.utc)


def _stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- passwords
def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        expected_raw = base64.b64decode(expected)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                            n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM, dklen=len(expected_raw))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected_raw)


_dummy_hash = None


def _burn_time():
    """Hash something so a sign-in for a name that doesn't exist takes as long as
    one that does (so response time doesn't reveal which usernames are real)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password")
    verify_password("another-wrong-guess", _dummy_hash)


def validate_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"Passwords need at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"That password is too long (the limit is {MAX_PASSWORD} characters).")
    if password.strip() != password:
        raise ValueError("A password can't start or end with a space.")


_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i: easy to read out over a call


def generate_password():
    """A temporary password like 'k7mq-x4dn-p9ze' (the player is made to change it)."""
    groups = ["".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


def clean_username(name):
    name = (name or "").strip()
    if not _USERNAME_RE.match(name):
        raise ValueError("Usernames are 2-32 characters: letters, numbers, dot, dash or underscore.")
    return name


def _clean_display(name, fallback):
    name = " ".join((name or "").split())[:MAX_NAME]
    return name or fallback


# ---------------------------------------------------------------- users
def _public(row):
    return {
        "id": row["id"], "username": row["username"], "display_name": row["display_name"] or row["username"],
        "role": row["role"], "must_change": bool(row["must_change"]), "disabled": bool(row["disabled"]),
        "created_at": row["created_at"], "last_login": row["last_login"],
    }


def accounts_enabled():
    with database.get_cursor() as cur:
        return cur.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def get_user(user_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public(row) if row else None


def list_users():
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT u.*, (SELECT COUNT(*) FROM campaign_members m WHERE m.user_id = u.id) AS campaigns "
            "FROM users u ORDER BY (u.role = 'gm') DESC, lower(u.display_name), u.id").fetchall()
    out = []
    for r in rows:
        u = _public(r)
        u["campaigns"] = r["campaigns"]
        out.append(u)
    return out


def _active_gm_count(cur, excluding=None):
    sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'gm' AND disabled = 0"
    params = ()
    if excluding is not None:
        sql += " AND id != ?"
        params = (excluding,)
    return cur.execute(sql, params).fetchone()["n"]


def create_user(username, password, role="player", display_name="", must_change=True):
    username = clean_username(username)
    if role not in ROLES:
        raise ValueError("Role must be gm or player.")
    validate_password(password)
    pw_hash = hash_password(password)
    display = _clean_display(display_name, username)
    with database.get_cursor() as cur:
        if cur.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("That username is already taken.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO users (username, display_name, role, pw_hash, must_change) VALUES (?, ?, ?, ?, ?)",
                    (username, display, role, pw_hash, 1 if must_change else 0))
        return cur.lastrowid


def bootstrap_first_gm(username, password):
    """Turns accounts on: only works while there are no accounts at all."""
    if accounts_enabled():
        raise ValueError("Accounts are already set up.")
    return create_user(username, password, role="gm", must_change=False)


def update_user(user_id, display_name=None, role=None, disabled=None):
    user = get_user(user_id)
    if not user:
        raise ValueError("Unknown user.")
    new_role = user["role"] if role is None else role
    new_disabled = user["disabled"] if disabled is None else bool(disabled)
    if new_role not in ROLES:
        raise ValueError("Role must be gm or player.")
    lost_gm = user["role"] == "gm" and not user["disabled"] and (new_role != "gm" or new_disabled)
    with database.get_cursor() as cur:
        if lost_gm and _active_gm_count(cur, excluding=user_id) == 0:
            raise ValueError("There has to be at least one active GM account.")
    display = user["display_name"] if display_name is None else _clean_display(display_name, user["username"])
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET display_name = ?, role = ?, disabled = ? WHERE id = ?",
                    (display, new_role, 1 if new_disabled else 0, user_id))
        if new_disabled or new_role != user["role"]:
            cur.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if new_role == "gm":
            # a GM sees everything; memberships and grants would only be clutter
            cur.execute("DELETE FROM campaign_members WHERE user_id = ?", (user_id,))
            cur.execute("DELETE FROM user_game_access WHERE user_id = ?", (user_id,))
            cur.execute("UPDATE map_tokens SET user_id = NULL WHERE user_id = ?", (user_id,))


def delete_user(user_id):
    user = get_user(user_id)
    if not user:
        raise ValueError("Unknown user.")
    with database.get_cursor() as cur:
        if user["role"] == "gm" and not user["disabled"] and _active_gm_count(cur, excluding=user_id) == 0:
            raise ValueError("There has to be at least one active GM account.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM users WHERE id = ?", (user_id,))   # sessions, memberships, notes cascade


def set_password(user_id, new_password, must_change=False, keep_token=None):
    """Replaces a password and ends every other session of that person (the one
    named by keep_token, normally the one making the change, stays)."""
    validate_password(new_password)
    pw_hash = hash_password(new_password)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET pw_hash = ?, must_change = ? WHERE id = ?",
                    (pw_hash, 1 if must_change else 0, user_id))
        if keep_token:
            cur.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _token_hash(keep_token)))
        else:
            cur.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def reset_password(user_id):
    """A new temporary password (returned once, in plain text, for the GM to pass on)."""
    if not get_user(user_id):
        raise ValueError("Unknown user.")
    temp = generate_password()
    set_password(user_id, temp, must_change=True)
    return temp


def authenticate(username, password):
    """The user dict for a correct username + password, else None."""
    name = (username or "").strip()
    with database.get_cursor() as cur:
        row = cur.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
    if not row:
        _burn_time()
        return None
    ok = verify_password(password or "", row["pw_hash"])
    if not ok or row["disabled"]:
        return None
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET last_login = ? WHERE id = ?", (_stamp(_now()), row["id"]))
    return _public(row)


# ------------------------------------------------------------- sessions
def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    now = _now()
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE expires_at < ?", (_stamp(now),))
        cur.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (_token_hash(token), user_id, _stamp(now), _stamp(now), _stamp(now + timedelta(days=SESSION_DAYS))))
    return token


def user_for_token(token):
    """The signed-in user for a session cookie value, or None (unknown, expired,
    or the account was disabled). Quietly extends the session while it's in use."""
    if not token or len(token) > 200:
        return None
    now = _now()
    with database.get_cursor() as cur:
        row = cur.execute(
            "SELECT s.last_seen, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
    if not row or row["disabled"] or row["expires_at"] < _stamp(now):
        return None
    if row["last_seen"] < _stamp(now - timedelta(minutes=1)):
        with database.get_cursor(commit=True) as cur:
            cur.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                        (_stamp(now), _stamp(now + timedelta(days=SESSION_DAYS)), _token_hash(token)))
    return _public(row)


def end_session(token):
    if token:
        with database.get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


# ---------------------------------------------------- sign-in throttling
class LoginThrottle:
    """Slows down password guessing. Failures are remembered per client address and
    per (name, address) for a window; past the limit that key is refused until the
    oldest failure ages out. In memory on purpose: a restart is a free pass, but it
    keeps the database free of attacker-controlled rows."""

    IP_LIMIT = 10
    PAIR_LIMIT = 5
    WINDOW = 15 * 60

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._fails = {}
        self._sweep_at = 5000

    def _recent(self, key, now):
        times = [t for t in self._fails.get(key, ()) if now - t < self.WINDOW]
        if times:
            self._fails[key] = times
        else:
            self._fails.pop(key, None)
        return times

    def _keys(self, username, ip):
        return (("ip", ip or "?", self.IP_LIMIT), ("pair", f"{(username or '').strip().lower()}|{ip or '?'}", self.PAIR_LIMIT))

    def retry_after(self, username, ip):
        """Seconds to wait before another attempt (0 = go ahead)."""
        now = self._clock()
        wait = 0
        with self._lock:
            for kind, key, limit in self._keys(username, ip):
                times = self._recent((kind, key), now)
                if len(times) >= limit:
                    wait = max(wait, int(self.WINDOW - (now - times[0])) + 1)
        return wait

    def record_failure(self, username, ip):
        now = self._clock()
        with self._lock:
            for kind, key, _limit in self._keys(username, ip):
                self._fails.setdefault((kind, key), []).append(now)
            if len(self._fails) > self._sweep_at:     # an attacker rotating names must not grow this forever
                for k in list(self._fails):
                    self._recent(k, now)
                if len(self._fails) > 20000:          # still too many inside one window: start over
                    self._fails.clear()
                self._sweep_at = max(5000, 2 * len(self._fails))      # sweeping stays cheap on average

    def record_success(self, username, ip):
        with self._lock:
            self._fails.pop(("pair", self._keys(username, ip)[1][1]), None)


throttle = LoginThrottle()


# ---------------------------------------------------------- memberships
def _campaign_row(campaign_id):
    with database.get_cursor() as cur:
        return cur.execute("SELECT id, game_key, title FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()


def add_member(campaign_id, user_id):
    campaign = _campaign_row(campaign_id)
    if not campaign:
        raise ValueError("Unknown campaign.")
    user = get_user(user_id)
    if not user:
        raise ValueError("Unknown user.")
    if user["role"] != "player":
        raise ValueError("GM accounts already see every campaign - only players are added to one.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT OR IGNORE INTO campaign_members (campaign_id, user_id) VALUES (?, ?)", (campaign_id, user_id))
        # the campaign's game shows up in their list (library access still has to be ticked)
        cur.execute("INSERT OR IGNORE INTO user_game_access (user_id, game_key, library) VALUES (?, ?, 0)",
                    (user_id, campaign["game_key"]))


def remove_member(campaign_id, user_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM campaign_members WHERE campaign_id = ? AND user_id = ?", (campaign_id, user_id))
        # a token they could move stays on the map but is no longer theirs
        cur.execute("UPDATE map_tokens SET user_id = NULL WHERE user_id = ? AND map_id IN "
                    "(SELECT id FROM maps WHERE campaign_id = ?)", (user_id, campaign_id))


def members(campaign_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT u.id, u.username, u.display_name, u.disabled FROM campaign_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.campaign_id = ? ORDER BY lower(u.display_name), u.id",
            (campaign_id,)).fetchall()
    return [{"id": r["id"], "username": r["username"], "display_name": r["display_name"] or r["username"],
             "disabled": bool(r["disabled"])} for r in rows]


def is_member(user_id, campaign_id):
    if not user_id or not campaign_id:
        return False
    with database.get_cursor() as cur:
        return cur.execute("SELECT 1 FROM campaign_members WHERE campaign_id = ? AND user_id = ?",
                           (campaign_id, user_id)).fetchone() is not None


def campaigns_for_user(user_id):
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT c.id, c.game_key, c.title, c.pitch, c.status FROM campaign_members m "
            "JOIN campaigns c ON c.id = m.campaign_id WHERE m.user_id = ? ORDER BY lower(c.title), c.id",
            (user_id,)).fetchall()
    return [dict(r) for r in rows]


def maps_for_campaign(campaign_id):
    """The maps attached to a campaign, as a player may list them (title and id only)."""
    with database.get_cursor() as cur:
        rows = cur.execute("SELECT id, title FROM maps WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall()
    return [{"id": r["id"], "title": r["title"]} for r in rows]


# ---------------------------------------------------------- game access
def set_game_access(user_id, game_key, enabled, library=False):
    """Switch a game on or off for a player, and (separately) its library."""
    user = get_user(user_id)
    if not user:
        raise ValueError("Unknown user.")
    if user["role"] != "player":
        raise ValueError("GM accounts already have every game.")
    game_key = (game_key or "").strip()
    if not game_key or len(game_key) > 80:
        raise ValueError("Unknown game.")
    with database.get_cursor(commit=True) as cur:
        if not enabled:
            cur.execute("DELETE FROM user_game_access WHERE user_id = ? AND game_key = ?", (user_id, game_key))
        else:
            cur.execute("INSERT INTO user_game_access (user_id, game_key, library) VALUES (?, ?, ?) "
                        "ON CONFLICT(user_id, game_key) DO UPDATE SET library = excluded.library",
                        (user_id, game_key, 1 if library else 0))


def game_access(user_id):
    """{game_key: {"library": bool}} for everything switched on for this person."""
    with database.get_cursor() as cur:
        rows = cur.execute("SELECT game_key, library FROM user_game_access WHERE user_id = ? ORDER BY game_key",
                           (user_id,)).fetchall()
    return {r["game_key"]: {"library": bool(r["library"])} for r in rows}


def library_allowed(user_id, game_key):
    if not user_id or not game_key:
        return False
    with database.get_cursor() as cur:
        row = cur.execute("SELECT library FROM user_game_access WHERE user_id = ? AND game_key = ?",
                          (user_id, game_key)).fetchone()
    return bool(row and row["library"])


def library_games(user_id):
    """Game keys whose library this person may use."""
    return [k for k, v in game_access(user_id).items() if v["library"]]


# ---------------------------------------------------------- player notes
def get_note(campaign_id, user_id):
    with database.get_cursor() as cur:
        row = cur.execute("SELECT body, updated_at FROM player_notes WHERE campaign_id = ? AND user_id = ?",
                          (campaign_id, user_id)).fetchone()
    return {"body": row["body"], "updated_at": row["updated_at"]} if row else {"body": "", "updated_at": None}


def save_note(campaign_id, user_id, body):
    body = (body or "").replace("\r\n", "\n")
    if len(body) > MAX_NOTE:
        raise ValueError(f"Notes can hold up to {MAX_NOTE:,} characters - that one is {len(body):,}.")
    if not is_member(user_id, campaign_id):
        raise ValueError("You're not in that campaign.")
    with database.get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO player_notes (campaign_id, user_id, body, updated_at) VALUES (?, ?, ?, datetime('now')) "
                    "ON CONFLICT(campaign_id, user_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
                    (campaign_id, user_id, body))


def notes_for_campaign(campaign_id):
    """Every member's notebook (for the GM), including empty ones."""
    with database.get_cursor() as cur:
        rows = cur.execute(
            "SELECT u.id, u.username, u.display_name, COALESCE(n.body, '') AS body, n.updated_at "
            "FROM campaign_members m JOIN users u ON u.id = m.user_id "
            "LEFT JOIN player_notes n ON n.campaign_id = m.campaign_id AND n.user_id = m.user_id "
            "WHERE m.campaign_id = ? ORDER BY lower(u.display_name), u.id", (campaign_id,)).fetchall()
    return [{"user_id": r["id"], "name": r["display_name"] or r["username"], "body": r["body"],
             "updated_at": r["updated_at"]} for r in rows]


# ------------------------------------------------------------------ CLI
def _cli(argv):
    import getpass

    database.init_db()
    if len(argv) == 1 and argv[0] == "list":
        for u in list_users():
            flags = ", ".join(x for x in ("disabled" if u["disabled"] else "", "temporary password" if u["must_change"] else "") if x)
            print(f"{u['username']:<20} {u['role']:<7} {flags}")
        return 0
    if len(argv) == 2 and argv[0] == "create-gm":
        password = getpass.getpass("Password (at least %d characters): " % MIN_PASSWORD)
        if password != getpass.getpass("Again: "):
            print("Those didn't match.")
            return 1
        try:
            create_user(argv[1], password, role="gm", must_change=False)
        except ValueError as e:
            print(e)
            return 1
        print(f"GM account '{argv[1]}' created.")
        return 0
    if len(argv) == 2 and argv[0] == "reset-password":
        with database.get_cursor() as cur:
            row = cur.execute("SELECT id FROM users WHERE username = ?", (argv[1],)).fetchone()
        if not row:
            print("No account with that name. 'python -m app.accounts list' shows them.")
            return 1
        print(f"Temporary password for '{argv[1]}': {reset_password(row['id'])}")
        print("They'll be asked to choose a new one at the next sign-in.")
        return 0
    print(__doc__.split("Command line", 1)[1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
