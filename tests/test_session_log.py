"""Session log: storage, where a line goes, recap/export/summary use, and the endpoints."""
import glob
import os

import pytest
from fastapi.testclient import TestClient

from app import campaign_ai, campaign_export, campaigns, config, database, llm, session_log


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def camp(db, games_config):
    return campaigns.create_campaign("rifts", "Shadows over Chi-Town")


@pytest.fixture
def sess(camp):
    return campaigns.add_session(camp)


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    seen = {}

    def fake(system_prompt, user_prompt, max_tokens=800):
        seen["system"], seen["user"] = system_prompt, user_prompt
        return "The party met Marlowe and survived the ambush."

    monkeypatch.setattr(llm, "generate", fake)
    return seen


# ---------------------------------------------------------------- storage

def test_add_list_update_delete(camp, sess):
    a = session_log.add_entry(camp, "  Met   Marlowe  ", sess)
    b = session_log.add_entry(camp, "Ambush at the docks", sess, kind="combat")
    entries = session_log.list_entries(camp, sess)
    assert [e["text"] for e in entries] == ["Met Marlowe", "Ambush at the docks"]
    assert entries[1]["kind"] == "combat"

    session_log.update_entry(a, "text", "Met Marlowe Vance")
    session_log.update_entry(a, "kind", "scene")
    first = session_log.get_entry(a)
    assert (first["text"], first["kind"]) == ("Met Marlowe Vance", "scene")

    session_log.delete_entry(b)
    assert [e["id"] for e in session_log.list_entries(camp, sess)] == [a]


def test_validation(camp, sess):
    with pytest.raises(ValueError):
        session_log.add_entry(camp, "   ", sess)
    with pytest.raises(ValueError):
        session_log.add_entry(camp, "x", sess, kind="bogus")
    with pytest.raises(ValueError):
        session_log.add_entry(99999, "x")
    entry = session_log.add_entry(camp, "ok", sess)
    for field, value in (("text", ""), ("kind", "nope"), ("campaign_id", "5")):
        with pytest.raises(ValueError):
            session_log.update_entry(entry, field, value)


def test_text_is_clamped(camp, sess):
    entry = session_log.add_entry(camp, "x" * 5000, sess)
    assert len(session_log.get_entry(entry)["text"]) == session_log.MAX_TEXT


def test_a_session_from_another_campaign_is_refused(camp, sess):
    other = campaigns.create_campaign("rifts", "Other")
    with pytest.raises(ValueError):
        session_log.add_entry(other, "leak", sess)
    with pytest.raises(ValueError):
        session_log.add_entry(camp, "leak", 99999)


def test_entry_without_a_session_belongs_to_the_campaign(camp, sess):
    session_log.add_entry(camp, "loose line")
    session_log.add_entry(camp, "in session", sess)
    assert len(session_log.list_entries(camp)) == 2
    assert [e["text"] for e in session_log.list_entries(camp, sess)] == ["in session"]
    assert session_log.counts_by_session(camp) == {sess: 1}


def test_deleting_a_session_or_campaign_removes_its_lines(camp, sess):
    session_log.add_entry(camp, "gone with the session", sess)
    campaigns.delete_session(sess)
    assert session_log.list_entries(camp) == []
    sess2 = campaigns.add_session(camp)
    session_log.add_entry(camp, "gone with the campaign", sess2)
    campaigns.delete_campaign(camp)
    with database.get_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM campaign_session_log").fetchone()[0] == 0


def test_logging_counts_as_campaign_activity(camp, sess):
    old = "2000-01-01 00:00:00"
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (old, camp))
    entry = session_log.add_entry(camp, "something", sess)
    assert campaigns.get_campaign(camp)["updated_at"] != old
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (old, camp))
    session_log.update_entry(entry, "text", "something else")
    assert campaigns.get_campaign(camp)["updated_at"] != old


# ---------------------------------------------------------------- where a line goes

def test_default_session_is_the_lowest_unplayed(camp):
    assert session_log.default_session(camp) is None
    s1, s2, s3 = (campaigns.add_session(camp) for _ in range(3))
    campaigns.update_session_field(s1, "status", "played")
    assert session_log.default_session(camp)["id"] == s2
    campaigns.update_session_field(s2, "status", "played")
    campaigns.update_session_field(s3, "status", "played")
    assert session_log.default_session(camp)["id"] == s3      # all played: the latest


def test_log_target_is_the_most_recent_unfinished_campaign_of_the_game(db, games_config):
    assert session_log.log_target("rifts") is None
    old = campaigns.create_campaign("rifts", "Old one")
    new = campaigns.create_campaign("rifts", "New one")
    campaigns.create_campaign("dnd5e", "Other game")
    with database.get_cursor(commit=True) as cur:
        cur.execute("UPDATE campaigns SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (old,))
    assert session_log.log_target("rifts")[0]["id"] == new
    campaigns.update_campaign_field(new, "status", "completed")
    assert session_log.log_target("rifts")[0]["id"] == old
    campaigns.update_campaign_field(old, "status", "completed")
    assert session_log.log_target("rifts") is None


# ---------------------------------------------------------------- turning a log into text

def test_entries_as_text_tags_non_notes(camp, sess):
    session_log.add_entry(camp, "Met Marlowe", sess)
    session_log.add_entry(camp, "300 gp", sess, kind="loot")
    text = session_log.session_log_text(sess)
    assert text == "- Met Marlowe\n- [Loot] 300 gp"
    assert session_log.session_log_text(99999) == ""


def test_summary_without_ai_is_the_plain_list(camp, sess, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    session_log.add_entry(camp, "Met Marlowe", sess)
    assert session_log.summarize(sess) == {"summary": "- Met Marlowe", "ai": False}


def test_summary_with_ai_uses_the_log_only(camp, sess, ai_on):
    session_log.add_entry(camp, "Met Marlowe", sess)
    session_log.add_entry(camp, "Ambush", sess, kind="combat")
    res = session_log.summarize(sess)
    assert res == {"summary": "The party met Marlowe and survived the ambush.", "ai": True}
    assert "- Met Marlowe" in ai_on["user"] and "[Combat] Ambush" in ai_on["user"]
    assert "do not invent" in ai_on["system"].lower()


def test_summary_falls_back_when_the_model_fails_or_is_blank(camp, sess, ai_on, monkeypatch):
    session_log.add_entry(camp, "Met Marlowe", sess)

    def boom(*a, **k):
        raise llm.LLMError("down")

    monkeypatch.setattr(llm, "generate", boom)
    assert session_log.summarize(sess) == {"summary": "- Met Marlowe", "ai": False}
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "   ")
    assert session_log.summarize(sess)["ai"] is False
    assert session_log.summarize(sess, use_ai=False)["ai"] is False


def test_summary_errors(camp, sess):
    with pytest.raises(ValueError):
        session_log.summarize(sess)          # nothing logged
    with pytest.raises(ValueError):
        session_log.summarize(99999)


# ---------------------------------------------------------------- recap & export

def test_recap_uses_the_log_when_there_is_no_written_summary(camp, ai_on):
    s1 = campaigns.add_session(camp)
    campaigns.update_session_field(s1, "status", "played")
    campaigns.update_session_field(s1, "summary", "Written summary wins.")
    session_log.add_entry(camp, "log line that is ignored", s1)
    s2 = campaigns.add_session(camp)            # not marked played, only logged
    session_log.add_entry(camp, "Met Marlowe at the docks", s2)
    s3 = campaigns.add_session(camp)            # nothing at all
    used = campaign_ai.played_summaries(camp)
    assert [s["session_number"] for s in used] == [1, 2]
    assert used[0]["summary"] == "Written summary wins." and not used[0].get("from_log")
    assert used[1]["summary"] == "- Met Marlowe at the docks" and used[1]["from_log"]

    res = campaign_ai.generate_recap(camp)
    assert res["sessions_used"] == [1, 2]
    assert "Met Marlowe at the docks" in ai_on["user"]
    assert "log line that is ignored" not in ai_on["user"]


def test_unplayed_session_with_only_a_summary_is_still_left_out(camp):
    s = campaigns.add_session(camp)
    campaigns.update_session_field(s, "summary", "notes written early")
    assert campaign_ai.played_summaries(camp) == []


def test_recap_limit_counts_log_sessions_too(camp):
    for i in range(campaign_ai.RECAP_SESSIONS + 2):
        s = campaigns.add_session(camp)
        session_log.add_entry(camp, f"line {i + 1}", s)
    used = campaign_ai.played_summaries(camp)
    assert len(used) == campaign_ai.RECAP_SESSIONS
    assert used[-1]["session_number"] == campaign_ai.RECAP_SESSIONS + 2


def test_export_includes_the_log_for_the_gm_only(camp, sess):
    campaigns.update_session_field(sess, "status", "played")
    campaigns.update_session_field(sess, "summary", "Wrote this up.")
    session_log.add_entry(camp, "The vault code is 1234", sess, kind="clue")
    gm = campaign_export.to_markdown(camp)
    assert "**Session log:**" in gm and "- [Clue] The vault code is 1234" in gm
    assert gm.index("Wrote this up.") < gm.index("**Session log:**")
    player = campaign_export.to_markdown(camp, player_safe=True)
    assert "vault code" not in player and "Session log" not in player


def test_export_log_without_a_summary(camp, sess):
    session_log.add_entry(camp, "Only a log line", sess)
    assert "**Session log:**\n\n- Only a log line" in campaign_export.to_markdown(camp)


# ---------------------------------------------------------------- endpoints

def test_log_endpoints_round_trip(client, camp, sess):
    res = client.post(f"/api/campaigns/{camp}/log", data={"text": "Met Marlowe", "kind": "scene", "session_id": sess})
    assert res.status_code == 200
    entry = res.json()["entry"]
    assert entry["text"] == "Met Marlowe" and entry["kind"] == "scene" and entry["session_id"] == sess

    listed = client.get(f"/api/campaigns/{camp}/log", params={"session_id": sess}).json()["entries"]
    assert [e["id"] for e in listed] == [entry["id"]]

    upd = client.post(f"/api/campaigns/log/{entry['id']}/update", data={"field": "text", "value": "Met Marlowe Vance"})
    assert upd.json()["entry"]["text"] == "Met Marlowe Vance"
    assert client.post(f"/api/campaigns/log/{entry['id']}/delete").json() == {"ok": True}
    assert client.get(f"/api/campaigns/{camp}/log").json()["entries"] == []


def test_log_endpoint_errors(client, camp, sess):
    assert client.post(f"/api/campaigns/{camp}/log", data={"text": " "}).status_code == 400
    assert client.post(f"/api/campaigns/{camp}/log", data={"text": "x", "kind": "zzz"}).status_code == 400
    assert client.post("/api/campaigns/99999/log", data={"text": "x"}).status_code == 400
    assert client.get("/api/campaigns/99999/log").status_code == 404
    assert client.get(f"/api/campaigns/{camp}/log", params={"session_id": "abc"}).status_code == 400
    assert client.post("/api/campaigns/log/99999/update", data={"field": "text", "value": "x"}).status_code == 404
    entry = session_log.add_entry(camp, "x", sess)
    assert client.post(f"/api/campaigns/log/{entry}/update", data={"field": "kind", "value": "bad"}).status_code == 400


def test_quick_log_goes_to_the_games_active_campaign(client, camp, sess):
    res = client.post("/api/log", data={"game": "rifts", "text": "1d20+5 = 17", "kind": "roll"})
    body = res.json()
    assert res.status_code == 200 and body["campaign_title"] == "Shadows over Chi-Town"
    assert body["session_number"] == 1
    entry = session_log.list_entries(camp, sess)[0]
    assert (entry["text"], entry["kind"]) == ("1d20+5 = 17", "roll")


def test_quick_log_with_nowhere_to_go(client, db, games_config):
    res = client.post("/api/log", data={"game": "rifts", "text": "x"})
    assert res.status_code == 400 and "campaign" in res.json()["error"]
    assert client.post("/api/log", data={"game": "", "text": "x"}).status_code == 400


def test_quick_log_works_before_any_session_exists(client, camp):
    res = client.post("/api/log", data={"game": "rifts", "text": "table talk"})
    assert res.status_code == 200 and res.json()["session_number"] is None
    assert [e["text"] for e in session_log.list_entries(camp)] == ["table talk"]


def test_quick_log_rejects_blank_text(client, camp):
    assert client.post("/api/log", data={"game": "rifts", "text": "  "}).status_code == 400


def test_summary_endpoint(client, camp, sess, monkeypatch):
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    assert client.post(f"/api/campaigns/sessions/{sess}/log/summary").status_code == 400
    session_log.add_entry(camp, "Met Marlowe", sess)
    res = client.post(f"/api/campaigns/sessions/{sess}/log/summary")
    assert res.json() == {"summary": "- Met Marlowe", "ai": False}
    monkeypatch.setattr(llm, "is_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "AI text")
    assert client.post(f"/api/campaigns/sessions/{sess}/log/summary").json()["ai"] is True
    assert client.post(f"/api/campaigns/sessions/{sess}/log/summary", data={"plain": 1}).json()["ai"] is False


# ---------------------------------------------------------------- pages

def test_campaign_page_shows_the_log_card(client, camp, sess):
    session_log.add_entry(camp, "Met Marlowe", sess)
    html = client.get(f"/campaigns/{camp}").text
    assert 'id="session-log-card"' in html and 'id="log-form"' in html
    assert f'<option value="{sess}" selected>Session 1 (1)</option>' in html


def test_campaign_page_without_sessions_says_so(client, camp):
    html = client.get(f"/campaigns/{camp}").text
    assert "Add a session below to start logging" in html and 'id="log-form"' not in html


def test_dice_and_table_pages_offer_logging_only_in_a_game(client, camp):
    assert 'id="log-roll-btn"' in client.get("/tools/dice?game=rifts").text
    assert 'id="log-roll-btn"' not in client.get("/tools/dice").text
    from app import random_tables
    random_tables.create_table("rifts", "Rumours", "d4", "1: a\n2: b\n3: c\n4: d")
    assert "log-btn" in client.get("/tools/tables?game=rifts").text
    assert 'class="secondary log-btn"' not in client.get("/tools/tables").text
    assert "logToSession" in client.get("/static/app.js").text


def test_templates_have_no_doubled_unicode_escapes():
    """'\\\\u2192' in a template shows up on the page as the literal text \\u2192."""
    for path in glob.glob(os.path.join(config.APP_DIR, "templates", "*.html")):
        with open(path, encoding="utf-8") as f:
            assert "\\\\u" not in f.read(), os.path.basename(path)
