"""User accounts (app/accounts.py): passwords, users, sessions, sign-in throttling,
campaign membership, game/library grants, player notes and token owners."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import accounts, backup, campaigns, database, map_tokens, maps_store

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240"><rect width="320" height="240"/></svg>'
PW = "correct horse battery"


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch):
    """The real cost (16 MB, tens of ms per hash) is checked once below; everything else
    uses a tiny one so the suite stays fast. Stored hashes carry their own parameters."""
    monkeypatch.setattr(accounts, "_N", 2 ** 4)


@pytest.fixture
def gm(db):
    return accounts.create_user("zak", PW, role="gm", display_name="Zak", must_change=False)


@pytest.fixture
def player(db):
    return accounts.create_user("aria", PW, role="player", display_name="Aria", must_change=False)


@pytest.fixture
def campaign(db):
    return campaigns.create_campaign("rifts", "Shadows over Chi-Town")


# ------------------------------------------------------------ passwords
def test_real_hash_parameters_are_the_interactive_scrypt_cost(monkeypatch):
    monkeypatch.undo()                      # undo the cheap_scrypt fixture
    stored = accounts.hash_password(PW)
    assert stored.startswith("scrypt$16384$8$1$")
    assert accounts.verify_password(PW, stored)


def test_password_round_trip_and_salting():
    a, b = accounts.hash_password(PW), accounts.hash_password(PW)
    assert a != b                           # a fresh salt every time
    assert accounts.verify_password(PW, a) and accounts.verify_password(PW, b)
    assert not accounts.verify_password(PW + "x", a)
    assert not accounts.verify_password("", a)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$x$y", "md5$1$2$3$4$5", "scrypt$a$b$c$d$e", "scrypt$16$8$1$!!$!!"])
def test_a_malformed_stored_hash_never_verifies(stored):
    assert accounts.verify_password(PW, stored) is False


def test_password_rules():
    accounts.validate_password("x" * accounts.MIN_PASSWORD)
    for bad in ("short", "x" * (accounts.MIN_PASSWORD - 1), " " + "x" * 12, "x" * 12 + " ", "x" * (accounts.MAX_PASSWORD + 1), None):
        with pytest.raises(ValueError):
            accounts.validate_password(bad)


def test_generated_passwords_are_valid_readable_and_different():
    seen = {accounts.generate_password() for _ in range(20)}
    assert len(seen) == 20
    for p in seen:
        accounts.validate_password(p)
        assert len(p) == 14 and p.count("-") == 2
        assert not set(p) & set("0o1li")    # nothing easily misread over a phone call


@pytest.mark.parametrize("name", ["", "a", "-bad", "has space", "semi;colon", "x" * 33, "ünï", "a/b"])
def test_bad_usernames(name, db):
    with pytest.raises(ValueError):
        accounts.create_user(name, PW)


# ---------------------------------------------------------------- users
def test_create_user_defaults_and_uniqueness_ignores_case(db):
    uid = accounts.create_user("Aria", PW)
    u = accounts.get_user(uid)
    assert (u["username"], u["display_name"], u["role"], u["must_change"], u["disabled"]) == ("Aria", "Aria", "player", True, False)
    assert "pw_hash" not in u
    with pytest.raises(ValueError, match="taken"):
        accounts.create_user("aria", PW)
    with pytest.raises(ValueError):
        accounts.create_user("bob", PW, role="wizard")
    with pytest.raises(ValueError):
        accounts.create_user("bob", "short")


def test_accounts_enabled_only_once_a_user_exists(db):
    assert accounts.accounts_enabled() is False
    accounts.create_user("zak", PW, role="gm")
    assert accounts.accounts_enabled() is True


def test_bootstrap_first_gm_only_works_on_an_empty_database(db):
    uid = accounts.bootstrap_first_gm("zak", PW)
    u = accounts.get_user(uid)
    assert u["role"] == "gm" and u["must_change"] is False
    with pytest.raises(ValueError, match="already set up"):
        accounts.bootstrap_first_gm("evil", PW)


def test_authenticate(gm):
    assert accounts.authenticate("zak", PW)["username"] == "zak"
    assert accounts.authenticate("ZAK", PW) is not None            # usernames ignore case
    assert accounts.authenticate("zak", "wrong password") is None
    assert accounts.authenticate("nobody", PW) is None
    assert accounts.authenticate("", "") is None
    assert accounts.get_user(gm)["last_login"]


def test_a_disabled_account_cannot_sign_in(gm, player):
    accounts.update_user(player, disabled=True)
    assert accounts.authenticate("aria", PW) is None
    accounts.update_user(player, disabled=False)
    assert accounts.authenticate("aria", PW) is not None


def test_list_users_puts_gms_first_and_counts_campaigns(gm, player, campaign):
    accounts.add_member(campaign, player)
    users = accounts.list_users()
    assert [u["username"] for u in users] == ["zak", "aria"]
    assert users[1]["campaigns"] == 1


def test_there_must_always_be_an_active_gm(db):
    only = accounts.create_user("zak", PW, role="gm")
    with pytest.raises(ValueError, match="at least one"):
        accounts.update_user(only, role="player")
    with pytest.raises(ValueError, match="at least one"):
        accounts.update_user(only, disabled=True)
    with pytest.raises(ValueError, match="at least one"):
        accounts.delete_user(only)
    second = accounts.create_user("bea", PW, role="gm")
    accounts.update_user(only, role="player")             # fine now: someone else is GM
    with pytest.raises(ValueError, match="at least one"):
        accounts.delete_user(second)
    accounts.update_user(only, role="gm")
    accounts.delete_user(second)
    assert [u["username"] for u in accounts.list_users()] == ["zak"]


def test_a_disabled_gm_does_not_count_as_the_last_gm(db):
    a = accounts.create_user("zak", PW, role="gm")
    b = accounts.create_user("bea", PW, role="gm")
    accounts.update_user(b, disabled=True)
    with pytest.raises(ValueError, match="at least one"):
        accounts.update_user(a, disabled=True)


def test_making_someone_a_gm_drops_their_player_grants(gm, player, campaign):
    accounts.add_member(campaign, player)
    accounts.set_game_access(player, "rifts", True, library=True)
    accounts.update_user(player, role="gm")
    assert accounts.members(campaign) == []
    assert accounts.game_access(player) == {}


def test_display_name_is_cleaned_and_falls_back_to_the_username(db):
    uid = accounts.create_user("aria", PW, display_name="  Aria   the   Bold  ")
    assert accounts.get_user(uid)["display_name"] == "Aria the Bold"
    accounts.update_user(uid, display_name="   ")
    assert accounts.get_user(uid)["display_name"] == "aria"
    accounts.update_user(uid, display_name="x" * 100)
    assert len(accounts.get_user(uid)["display_name"]) == accounts.MAX_NAME


def test_unknown_user_errors(db):
    for call in (lambda: accounts.update_user(999, role="gm"), lambda: accounts.delete_user(999),
                 lambda: accounts.reset_password(999)):
        with pytest.raises(ValueError, match="Unknown user"):
            call()


# ------------------------------------------------------------- passwords (set/reset)
def test_set_password_ends_other_sessions_but_keeps_the_current_one(gm):
    here, elsewhere = accounts.create_session(gm), accounts.create_session(gm)
    accounts.set_password(gm, "a brand new secret", keep_token=here)
    assert accounts.user_for_token(here) is not None
    assert accounts.user_for_token(elsewhere) is None
    assert accounts.authenticate("zak", "a brand new secret") is not None
    assert accounts.authenticate("zak", PW) is None


def test_reset_password_gives_a_temporary_one_and_signs_them_out(gm, player):
    token = accounts.create_session(player)
    temp = accounts.reset_password(player)
    assert accounts.user_for_token(token) is None
    assert accounts.authenticate("aria", PW) is None
    user = accounts.authenticate("aria", temp)
    assert user and user["must_change"] is True


# ------------------------------------------------------------- sessions
def test_session_lifecycle(gm):
    token = accounts.create_session(gm)
    assert len(token) >= 40
    assert accounts.user_for_token(token)["id"] == gm
    # only a hash of the token is stored
    with database.get_cursor() as cur:
        stored = [r["token_hash"] for r in cur.execute("SELECT token_hash FROM sessions").fetchall()]
    assert stored and token not in stored
    accounts.end_session(token)
    assert accounts.user_for_token(token) is None


@pytest.mark.parametrize("token", [None, "", "nope", "x" * 500])
def test_garbage_tokens_are_just_signed_out(token, db):
    assert accounts.user_for_token(token) is None


def test_sessions_expire_and_slide_while_in_use(gm, monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = {"now": start}
    monkeypatch.setattr(accounts, "_now", lambda: clock["now"])
    token = accounts.create_session(gm)
    clock["now"] = start + timedelta(days=10)
    assert accounts.user_for_token(token) is not None             # used on day 10: extended to day 24
    clock["now"] = start + timedelta(days=20)
    assert accounts.user_for_token(token) is not None             # would have expired on day 14 otherwise
    clock["now"] = start + timedelta(days=20 + accounts.SESSION_DAYS + 1)
    assert accounts.user_for_token(token) is None


def test_creating_a_session_sweeps_out_expired_ones(gm, monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = {"now": start}
    monkeypatch.setattr(accounts, "_now", lambda: clock["now"])
    accounts.create_session(gm)
    clock["now"] = start + timedelta(days=accounts.SESSION_DAYS + 2)
    accounts.create_session(gm)
    with database.get_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1


def test_disabling_or_deleting_someone_ends_their_sessions(gm, player):
    t1 = accounts.create_session(player)
    accounts.update_user(player, disabled=True)
    assert accounts.user_for_token(t1) is None
    accounts.update_user(player, disabled=False)
    t2 = accounts.create_session(player)
    accounts.delete_user(player)
    assert accounts.user_for_token(t2) is None


def test_changing_someones_role_ends_their_sessions(gm, player):
    token = accounts.create_session(player)
    accounts.update_user(player, role="gm")
    assert accounts.user_for_token(token) is None


# ----------------------------------------------------------- throttling
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_throttle_locks_a_name_from_one_address_after_five_failures():
    clock = Clock()
    t = accounts.LoginThrottle(clock=clock)
    for _ in range(4):
        t.record_failure("aria", "10.0.0.5")
    assert t.retry_after("aria", "10.0.0.5") == 0
    t.record_failure("aria", "10.0.0.5")
    wait = t.retry_after("aria", "10.0.0.5")
    assert 0 < wait <= accounts.LoginThrottle.WINDOW + 1
    assert t.retry_after("Aria", "10.0.0.5") > 0                  # case doesn't dodge it
    assert t.retry_after("aria", "10.0.0.6") == 0                 # a different address is unaffected
    assert t.retry_after("bob", "10.0.0.5") == 0                  # so is a different name


def test_throttle_also_limits_one_address_trying_many_names():
    t = accounts.LoginThrottle(clock=Clock())
    for i in range(accounts.LoginThrottle.IP_LIMIT):
        t.record_failure(f"user{i}", "10.0.0.9")
    assert t.retry_after("someone-new", "10.0.0.9") > 0
    assert t.retry_after("someone-new", "10.0.0.10") == 0


def test_throttle_forgives_after_the_window_and_after_a_success():
    clock = Clock()
    t = accounts.LoginThrottle(clock=clock)
    for _ in range(5):
        t.record_failure("aria", "1.1.1.1")
    clock.t += accounts.LoginThrottle.WINDOW + 1
    assert t.retry_after("aria", "1.1.1.1") == 0
    for _ in range(4):
        t.record_failure("aria", "1.1.1.1")
    t.record_success("aria", "1.1.1.1")
    t.record_failure("aria", "1.1.1.1")
    assert t.retry_after("aria", "1.1.1.1") == 0                  # the success cleared the earlier four


def test_throttle_memory_stays_bounded_when_names_are_rotated():
    clock = Clock()
    t = accounts.LoginThrottle(clock=clock)
    for i in range(3000):
        t.record_failure(f"n{i}", f"ip{i}")
        clock.t += 0.01
    clock.t += accounts.LoginThrottle.WINDOW + 1     # everything so far has aged out
    for i in range(3000, 7000):
        t.record_failure(f"n{i}", f"ip{i}")
    assert len(t._fails) < 10000                     # the 6,000 old keys were swept: 14,000 were added in all
    assert ("ip", "ip0") not in t._fails


def test_throttle_has_a_hard_ceiling_even_inside_one_window():
    t = accounts.LoginThrottle(clock=Clock())
    for i in range(12000):                           # 24,000 keys, all still recent
        t.record_failure(f"n{i}", f"ip{i}")
    assert len(t._fails) <= 20000


# ----------------------------------------------------------- membership
def test_membership_and_the_game_it_switches_on(gm, player, campaign):
    assert accounts.is_member(player, campaign) is False
    accounts.add_member(campaign, player)
    accounts.add_member(campaign, player)                         # adding twice is harmless
    assert accounts.is_member(player, campaign) is True
    assert [m["username"] for m in accounts.members(campaign)] == ["aria"]
    assert accounts.game_access(player) == {"rifts": {"library": False}}
    assert [c["title"] for c in accounts.campaigns_for_user(player)] == ["Shadows over Chi-Town"]
    accounts.remove_member(campaign, player)
    assert accounts.is_member(player, campaign) is False
    assert accounts.game_access(player) == {"rifts": {"library": False}}      # the game stays listed


def test_only_players_join_campaigns_and_both_ends_must_exist(gm, player, campaign):
    with pytest.raises(ValueError, match="already see every campaign"):
        accounts.add_member(campaign, gm)
    with pytest.raises(ValueError, match="Unknown campaign"):
        accounts.add_member(999, player)
    with pytest.raises(ValueError, match="Unknown user"):
        accounts.add_member(campaign, 999)


def test_is_member_is_false_for_missing_ids(db):
    assert accounts.is_member(None, 1) is False
    assert accounts.is_member(1, None) is False


def test_deleting_a_campaign_or_a_user_cleans_up_memberships(gm, player, campaign):
    accounts.add_member(campaign, player)
    campaigns.delete_campaign(campaign)
    assert accounts.campaigns_for_user(player) == []
    other = campaigns.create_campaign("rifts", "Other")
    accounts.add_member(other, player)
    accounts.delete_user(player)
    assert accounts.members(other) == []


def test_maps_for_campaign_lists_only_its_own(gm, campaign):
    other = campaigns.create_campaign("rifts", "Other")
    mine = maps_store.save_map("rifts", "dungeon", "fantasy", "Mine", SVG, campaign_id=campaign)
    maps_store.save_map("rifts", "dungeon", "fantasy", "Theirs", SVG, campaign_id=other)
    maps_store.save_map("rifts", "dungeon", "fantasy", "Loose", SVG)
    assert accounts.maps_for_campaign(campaign) == [{"id": mine, "title": "Mine"}]


# ---------------------------------------------------------- game access
def test_game_and_library_grants(gm, player):
    assert accounts.library_allowed(player, "rifts") is False
    accounts.set_game_access(player, "rifts", True)
    assert accounts.library_allowed(player, "rifts") is False     # seeing a game isn't library access
    accounts.set_game_access(player, "rifts", True, library=True)
    accounts.set_game_access(player, "dnd5e", True)
    assert accounts.library_allowed(player, "rifts") is True
    assert accounts.library_games(player) == ["rifts"]
    assert accounts.game_access(player) == {"dnd5e": {"library": False}, "rifts": {"library": True}}
    accounts.set_game_access(player, "rifts", False, library=True)   # switching a game off removes its library too
    assert accounts.library_allowed(player, "rifts") is False
    assert list(accounts.game_access(player)) == ["dnd5e"]


def test_grants_reject_bad_targets(gm, player):
    with pytest.raises(ValueError, match="already have every game"):
        accounts.set_game_access(gm, "rifts", True)
    with pytest.raises(ValueError, match="Unknown user"):
        accounts.set_game_access(999, "rifts", True)
    for key in ("", "   ", "x" * 81):
        with pytest.raises(ValueError, match="Unknown game"):
            accounts.set_game_access(player, key, True)
    assert accounts.library_allowed(None, "rifts") is False
    assert accounts.library_allowed(player, "") is False


# -------------------------------------------------------- player notes
def test_player_notes(gm, player, campaign):
    assert accounts.get_note(campaign, player) == {"body": "", "updated_at": None}
    with pytest.raises(ValueError, match="not in that campaign"):
        accounts.save_note(campaign, player, "hello")
    accounts.add_member(campaign, player)
    accounts.save_note(campaign, player, "Line one\r\nLine two")
    assert accounts.get_note(campaign, player)["body"] == "Line one\nLine two"
    accounts.save_note(campaign, player, "replaced")
    assert accounts.get_note(campaign, player)["body"] == "replaced"
    with pytest.raises(ValueError, match="Notes can hold"):
        accounts.save_note(campaign, player, "x" * (accounts.MAX_NOTE + 1))
    assert accounts.get_note(campaign, player)["body"] == "replaced"


def test_the_gm_sees_every_members_notebook_even_empty_ones(gm, player, campaign):
    friend = accounts.create_user("bob", PW, display_name="Bob")
    accounts.add_member(campaign, player)
    accounts.add_member(campaign, friend)
    accounts.save_note(campaign, player, "my secret plan")
    notes = {n["name"]: n["body"] for n in accounts.notes_for_campaign(campaign)}
    assert notes == {"Aria": "my secret plan", "Bob": ""}


def test_notes_are_per_campaign(gm, player, campaign):
    other = campaigns.create_campaign("rifts", "Other")
    accounts.add_member(campaign, player)
    accounts.add_member(other, player)
    accounts.save_note(campaign, player, "one")
    assert accounts.get_note(other, player)["body"] == ""


# ---------------------------------------------------------- token owners
@pytest.fixture
def table(gm, player, campaign):
    accounts.add_member(campaign, player)
    map_id = maps_store.save_map("rifts", "dungeon", "fantasy", "Crypt", SVG, campaign_id=campaign)
    return {"map": map_id, "token": map_tokens.add_token(map_id, "Aria", 10, 10)}


def test_a_token_can_be_handed_to_a_member_and_taken_back(table, player):
    map_tokens.update_token(table["token"], "user_id", str(player))
    assert map_tokens.get_token(table["token"])["user_id"] == player
    map_tokens.update_token(table["token"], "user_id", "")
    assert map_tokens.get_token(table["token"])["user_id"] is None


def test_handing_over_a_token_bumps_the_live_version(table, player):
    with database.get_cursor() as cur:
        v0 = cur.execute("SELECT live_v FROM maps WHERE id = ?", (table["map"],)).fetchone()["live_v"]
    map_tokens.update_token(table["token"], "user_id", str(player))
    with database.get_cursor() as cur:
        assert cur.execute("SELECT live_v FROM maps WHERE id = ?", (table["map"],)).fetchone()["live_v"] == v0 + 1


def test_a_token_cannot_go_to_a_non_member_or_junk(table):
    stranger = accounts.create_user("eve", PW)
    for value in (str(stranger), "999", "abc", "1.5"):
        with pytest.raises(ValueError, match="campaign's players"):
            map_tokens.update_token(table["token"], "user_id", value)


def test_a_map_with_no_campaign_cannot_hand_out_tokens(player):
    map_id = maps_store.save_map("rifts", "dungeon", "fantasy", "Loose", SVG)
    token = map_tokens.add_token(map_id, "X", 1, 1)
    with pytest.raises(ValueError, match="Attach this map"):
        map_tokens.update_token(token, "user_id", str(player))


def test_removing_a_player_takes_their_tokens_back(table, player, campaign):
    map_tokens.update_token(table["token"], "user_id", str(player))
    accounts.remove_member(campaign, player)
    assert map_tokens.get_token(table["token"])["user_id"] is None


def test_deleting_a_user_leaves_their_token_on_the_map(table, player):
    map_tokens.update_token(table["token"], "user_id", str(player))
    accounts.delete_user(player)
    assert map_tokens.get_token(table["token"])["user_id"] is None


def test_other_token_fields_still_validate(table):
    with pytest.raises(ValueError, match="can't be changed"):
        map_tokens.update_token(table["token"], "map_id", "5")
    with pytest.raises(ValueError, match="isn't on the map"):
        map_tokens.update_token(9999, "user_id", "")


# ---------------------------------------------------------------- backups
def test_user_data_backups_keep_accounts_but_never_sessions(gm, player):
    accounts.create_session(gm)
    path = backup.make_backup("user-data")
    try:
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        conn.close()
    finally:
        backup.remove_file(path)
    with database.get_cursor() as cur:        # the live database is untouched
        assert cur.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1


# -------------------------------------------------------------------- CLI
def test_cli_list_and_reset(gm, player, capsys):
    assert accounts._cli(["list"]) == 0
    assert "zak" in capsys.readouterr().out
    assert accounts._cli(["reset-password", "aria"]) == 0
    out = capsys.readouterr().out
    temp = out.split(": ", 1)[1].splitlines()[0]
    assert accounts.authenticate("aria", temp)["must_change"] is True
    assert accounts._cli(["reset-password", "ghost"]) == 1
    assert accounts._cli(["bogus"]) == 2
