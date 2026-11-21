"""Home page "Pick up where you left off" and the triggers that keep a
campaign's last-activity time honest."""
import pytest
from fastapi.testclient import TestClient

from app import bookmarks, campaigns, database, maps_store, recent

OLD = "2000-01-01 00:00:00"


def _age(campaign_id):
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (OLD, campaign_id))


def _updated(campaign_id):
    return campaigns.get_campaign(campaign_id)["updated_at"]


@pytest.fixture
def camp(db, games_config):
    return campaigns.create_campaign("rifts", "Shadows over Chi-Town")


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- triggers

@pytest.mark.parametrize("touch", [
    lambda c: campaigns.add_thread(c, "A thread"),
    lambda c: campaigns.add_npc(c, "Bob"),
    lambda c: campaigns.add_location(c, "Town"),
    lambda c: campaigns.add_faction(c, "Guild"),
    lambda c: campaigns.add_session(c),
])
def test_adding_anything_marks_the_campaign_active(camp, touch):
    _age(camp)
    touch(camp)
    assert _updated(camp) != OLD


def test_editing_children_marks_the_campaign_active(camp):
    npc = campaigns.add_npc(camp, "Bob")
    thread = campaigns.add_thread(camp, "T")
    loc = campaigns.add_location(camp, "L")
    fac = campaigns.add_faction(camp, "F")
    sess = campaigns.add_session(camp)
    for edit in (
        lambda: campaigns.update_npc_field(npc, "notes", "x"),
        lambda: campaigns.update_thread_field(thread, "stakes", "x"),
        lambda: campaigns.update_location_field(loc, "description", "x"),
        lambda: campaigns.update_faction_field(fac, "goal", "x"),
        lambda: campaigns.update_session_field(sess, "summary", "x"),
    ):
        _age(camp)
        edit()
        assert _updated(camp) != OLD


def test_touching_one_campaign_leaves_others_alone(camp):
    other = campaigns.create_campaign("rifts", "Other")
    _age(camp); _age(other)
    campaigns.add_npc(camp, "Bob")
    assert _updated(camp) != OLD and _updated(other) == OLD


def test_recently_edited_campaign_sorts_first(camp):
    other = campaigns.create_campaign("rifts", "Other")
    _age(camp); _age(other)
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaigns SET updated_at = '2001-01-01 00:00:00' WHERE id = ?", (other,))
    assert campaigns.list_campaigns()[0]["id"] == other
    campaigns.add_npc(camp, "Bob")
    assert campaigns.list_campaigns()[0]["id"] == camp


def test_deleting_a_campaign_with_children_still_works(camp):
    campaigns.add_npc(camp, "Bob"); campaigns.add_session(camp); campaigns.add_thread(camp, "T")
    campaigns.delete_campaign(camp)
    assert campaigns.get_campaign(camp) is None


def test_existing_databases_pick_up_the_triggers(db):
    """init_db runs on every start, so an older database gains them without a migration step."""
    with database.get_cursor(commit=True) as cur:
        cur.execute("DROP TRIGGER trg_campaign_npcs_insert_touch")
    database.init_db()
    with database.get_cursor() as cur:
        names = {r["name"] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert "trg_campaign_npcs_insert_touch" in names


# ---------------------------------------------------------------- recent_campaigns

def test_next_session_is_the_lowest_unplayed_one(camp):
    s1, s2, s3 = (campaigns.add_session(camp, title=f"S{i}") for i in (1, 2, 3))
    campaigns.update_session_field(s1, "status", "played")
    campaigns.update_session_field(s3, "strong_start", "A stranger arrives")
    info = recent.recent_campaigns()[0]
    assert info["next_session"]["number"] == 2 and info["next_session"]["title"] == "S2"
    assert info["sessions_played"] == 1
    assert info["game_name"] == "Rifts"


def test_next_session_reports_how_much_is_prepped(camp):
    s = campaigns.add_session(camp)
    campaigns.update_session_field(s, "strong_start", "Hook")
    campaigns.update_session_field(s, "secrets_clues", "  \n ")
    prepped = recent.recent_campaigns()[0]["next_session"]["prepped"]
    assert prepped == {"strong_start": True, "scenes": False, "secrets_clues": False}


def test_long_strong_start_is_shortened(camp):
    s = campaigns.add_session(camp)
    campaigns.update_session_field(s, "strong_start", "word " * 100)
    text = recent.recent_campaigns()[0]["next_session"]["strong_start"]
    assert len(text) <= 140 and text.endswith("...")


def test_no_next_session_when_everything_is_played_or_absent(camp):
    assert recent.recent_campaigns()[0]["next_session"] is None
    s = campaigns.add_session(camp)
    campaigns.update_session_field(s, "status", "played")
    assert recent.recent_campaigns()[0]["next_session"] is None


def test_only_the_most_recent_campaigns_are_listed(db, games_config):
    for i in range(7):
        campaigns.create_campaign("rifts", f"C{i}")
    assert len(recent.recent_campaigns()) == recent.LIMIT


def test_unknown_game_key_falls_back_to_the_key(db, games_config):
    c = campaigns.create_campaign("rifts", "X")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaigns SET game_key = 'gone' WHERE id = ?", (c,))
    assert recent.recent_campaigns()[0]["game_name"] == "gone"


# ---------------------------------------------------------------- maps / bookmarks

def _save_map(title="Crypt"):
    return maps_store.save_map("rifts", "dungeon", "fantasy", title, "<svg></svg>", seed=1)


def test_recent_maps_newest_first(db, games_config):
    a, b = _save_map("A"), _save_map("B")
    assert [m["id"] for m in recent.recent_maps()][:2] == [b, a]


def test_recent_bookmarks_join_book_title(db, a_book):
    col = bookmarks.get_or_create_collection("Prep")
    bookmarks.add_bookmark(col, a_book, 7, "Dragon lair")
    got = recent.recent_bookmarks()
    assert got[0]["page_number"] == 7 and got[0]["note"] == "Dragon lair" and got[0]["book_title"]


def test_activity_flag_false_when_nothing_exists(db, games_config):
    assert recent.home_activity()["any"] is False


# ---------------------------------------------------------------- the page

def test_home_hides_the_section_when_empty(client):
    assert 'id="pick-up"' not in client.get("/").text


def test_home_shows_campaign_map_and_bookmark_cards(client, a_book):
    c = campaigns.create_campaign("rifts", "Shadows")
    s = campaigns.add_session(c, title="The Heist")
    campaigns.update_session_field(s, "strong_start", "Alarm bells ring")
    _save_map("Crypt of Ash")
    bookmarks.add_bookmark(bookmarks.get_or_create_collection("P"), a_book, 3, "Boss stats")
    html = client.get("/").text
    assert 'id="pick-up"' in html
    assert f'href="/campaigns/{c}"' in html and "Shadows" in html
    assert "Next: session 1" in html and "The Heist" in html and "Alarm bells ring" in html
    assert "Crypt of Ash" in html and "Boss stats" in html
    assert f"/books/{a_book}/file#page=3" in html


def test_home_escapes_user_text(client):
    campaigns.create_campaign("rifts", "<script>alert(1)</script>")
    html = client.get("/").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
