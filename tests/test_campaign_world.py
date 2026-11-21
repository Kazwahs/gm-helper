"""Secrets & clues, progress clocks, faction standing, ties between NPCs and factions."""
import pytest
from fastapi.testclient import TestClient

from app import campaign_export, campaign_world, campaigns, database, session_log


@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def camp(db, games_config):
    return campaigns.create_campaign("rifts", "Shadows over Chi-Town")


@pytest.fixture
def other(db, games_config):
    return campaigns.create_campaign("dnd5e", "Another Table")


@pytest.fixture
def sess(camp):
    return campaigns.add_session(camp, "The docks")


def _count(table, **where):
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = ?" for k in where)
    with database.get_cursor() as cur:
        return cur.execute(sql, tuple(where.values())).fetchone()["n"]


# ---------------------------------------------------------------- secrets

def test_add_and_list_secrets(camp, sess):
    a = campaign_world.add_secret(camp, "  The   mayor is a   vampire ", source="the ledger", session_id=sess)
    b = campaign_world.add_secret(camp, "There is a tunnel under the tavern")
    rows = campaign_world.list_secrets(camp)
    assert [r["id"] for r in rows] == [a, b]
    assert rows[0]["text"] == "The mayor is a vampire"
    assert rows[0]["source"] == "the ledger"
    assert rows[0]["session_id"] == sess and rows[0]["planned_number"] == 1
    assert rows[1]["session_id"] is None
    assert all(r["status"] == "hidden" for r in rows)


def test_secret_needs_text_and_known_campaign(camp):
    with pytest.raises(ValueError):
        campaign_world.add_secret(camp, "   ")
    with pytest.raises(ValueError):
        campaign_world.add_secret(9999, "Nobody's campaign")


def test_secret_text_is_capped(camp):
    sid = campaign_world.add_secret(camp, "x" * 5000)
    assert len(campaign_world.get_secret(sid)["text"]) == campaign_world.MAX_TEXT


def test_secret_session_must_belong_to_campaign(camp, other):
    foreign = campaigns.add_session(other)
    with pytest.raises(ValueError):
        campaign_world.add_secret(camp, "Leaky", session_id=foreign)
    with pytest.raises(ValueError):
        campaign_world.add_secret(camp, "Leaky", session_id="abc")
    sid = campaign_world.add_secret(camp, "Fine")
    with pytest.raises(ValueError):
        campaign_world.update_secret(sid, "session_id", foreign)
    assert campaign_world.get_secret(sid)["session_id"] is None


def test_update_secret_fields(camp, sess):
    sid = campaign_world.add_secret(camp, "Old")
    campaign_world.update_secret(sid, "text", "New text")
    campaign_world.update_secret(sid, "source", "the tavern keeper")
    campaign_world.update_secret(sid, "session_id", sess)
    row = campaign_world.get_secret(sid)
    assert (row["text"], row["source"], row["session_id"]) == ("New text", "the tavern keeper", sess)
    campaign_world.update_secret(sid, "session_id", "")
    assert campaign_world.get_secret(sid)["session_id"] is None
    with pytest.raises(ValueError):
        campaign_world.update_secret(sid, "text", "  ")
    with pytest.raises(ValueError):
        campaign_world.update_secret(sid, "status", "revealed")      # only reveal() changes that
    with pytest.raises(ValueError):
        campaign_world.update_secret(99999, "text", "x")


def test_reveal_writes_a_clue_line_into_the_log(camp, sess):
    sid = campaign_world.add_secret(camp, "The mayor is a vampire", session_id=sess)
    row = campaign_world.reveal(sid)
    assert row["status"] == "revealed"
    assert row["revealed_session_id"] == sess and row["revealed_at"]
    entries = session_log.list_entries(camp, sess)
    assert [(e["kind"], e["text"]) for e in entries] == [("clue", "Revealed: The mayor is a vampire")]


def test_reveal_goes_to_the_chosen_session_first(camp, sess):
    later = campaigns.add_session(camp, "Later")
    sid = campaign_world.add_secret(camp, "Prepped for one, found in another", session_id=sess)
    row = campaign_world.reveal(sid, session_id=later)
    assert row["revealed_session_id"] == later
    assert len(session_log.list_entries(camp, later)) == 1
    assert session_log.list_entries(camp, sess) == []


def test_reveal_without_any_session_uses_the_current_one(camp, sess):
    sid = campaign_world.add_secret(camp, "Unplanned")
    row = campaign_world.reveal(sid)
    assert row["revealed_session_id"] == session_log.default_session(camp)["id"]
    assert len(session_log.list_entries(camp)) == 1


def test_reveal_with_no_sessions_still_works(camp):
    sid = campaign_world.add_secret(camp, "No sessions exist yet")
    row = campaign_world.reveal(sid)
    assert row["status"] == "revealed" and row["revealed_session_id"] is None
    assert [e["text"] for e in session_log.list_entries(camp)] == ["Revealed: No sessions exist yet"]


def test_reveal_twice_logs_once(camp, sess):
    sid = campaign_world.add_secret(camp, "Once", session_id=sess)
    campaign_world.reveal(sid)
    campaign_world.reveal(sid)
    assert len(session_log.list_entries(camp, sess)) == 1


def test_reveal_refuses_a_session_of_another_campaign(camp, other):
    foreign = campaigns.add_session(other)
    sid = campaign_world.add_secret(camp, "Careful")
    with pytest.raises(ValueError):
        campaign_world.reveal(sid, session_id=foreign)
    assert campaign_world.get_secret(sid)["status"] == "hidden"
    assert session_log.list_entries(other) == []


def test_hide_again_keeps_the_log_line(camp, sess):
    sid = campaign_world.add_secret(camp, "Oops", session_id=sess)
    campaign_world.reveal(sid)
    row = campaign_world.hide_again(sid)
    assert row["status"] == "hidden" and row["revealed_session_id"] is None and row["revealed_at"] is None
    assert len(session_log.list_entries(camp, sess)) == 1      # it did happen at the table


def test_list_puts_hidden_before_revealed(camp):
    a = campaign_world.add_secret(camp, "A")
    b = campaign_world.add_secret(camp, "B")
    c = campaign_world.add_secret(camp, "C")
    campaign_world.reveal(a)
    assert [r["id"] for r in campaign_world.list_secrets(camp)] == [b, c, a]


def test_deleting_a_session_keeps_the_secret(camp, sess):
    sid = campaign_world.add_secret(camp, "Survives", session_id=sess)
    campaign_world.reveal(sid)
    campaigns.delete_session(sess)
    row = campaign_world.get_secret(sid)
    assert row and row["session_id"] is None and row["revealed_session_id"] is None


def test_delete_secret(camp):
    sid = campaign_world.add_secret(camp, "Gone soon")
    campaign_world.delete_secret(sid)
    assert campaign_world.get_secret(sid) is None
    campaign_world.delete_secret(sid)    # idempotent


def test_import_from_session_prep(camp, sess):
    second = campaigns.add_session(camp, "Second")
    campaigns.update_session_field(sess, "secrets_clues", "- The mayor is a vampire\n* A tunnel under the tavern\n\n3. Marlowe lied")
    campaigns.update_session_field(second, "secrets_clues", "The mayor is a vampire\nThe key is in the well")
    result = campaign_world.import_from_sessions(camp)
    assert result == {"added": 4, "skipped": 1}
    rows = campaign_world.list_secrets(camp)
    assert [r["text"] for r in rows] == ["The mayor is a vampire", "A tunnel under the tavern", "Marlowe lied", "The key is in the well"]
    assert [r["session_id"] for r in rows] == [sess, sess, sess, second]
    # running it again adds nothing
    assert campaign_world.import_from_sessions(camp) == {"added": 0, "skipped": 5}
    assert len(campaign_world.list_secrets(camp)) == 4


def test_import_with_nothing_to_import(camp, sess):
    assert campaign_world.import_from_sessions(camp) == {"added": 0, "skipped": 0}


def test_import_stays_inside_its_campaign(camp, other):
    mine, theirs = campaigns.add_session(camp), campaigns.add_session(other)
    campaigns.update_session_field(theirs, "secrets_clues", "Not mine")
    assert campaign_world.import_from_sessions(camp) == {"added": 0, "skipped": 0}
    assert campaign_world.list_secrets(camp) == []


# ----------------------------------------------------------------- clocks

def test_add_clock_and_defaults(camp):
    cid = campaign_world.add_clock(camp, "  The ritual   completes ")
    clock = campaign_world.list_clocks(camp)[0]
    assert clock["id"] == cid and clock["name"] == "The ritual completes"
    assert (clock["filled"], clock["segments"], clock["full"]) == (0, 6, False)
    assert clock["faction_id"] is None and clock["faction_name"] is None


def test_clock_validation(camp, other):
    foreign = campaigns.add_faction(other, "Outsiders")
    with pytest.raises(ValueError):
        campaign_world.add_clock(camp, "  ")
    for bad in (1, 13, 0, -4, "x", None, ""):
        with pytest.raises(ValueError):
            campaign_world.add_clock(camp, "Bad", segments=bad)
    with pytest.raises(ValueError):
        campaign_world.add_clock(camp, "Cross-campaign", faction_id=foreign)
    with pytest.raises(ValueError):
        campaign_world.add_clock(9999, "Nowhere")
    assert campaign_world.list_clocks(camp) == []
    campaign_world.add_clock(camp, "Smallest", segments=2)
    campaign_world.add_clock(camp, "Biggest", segments="12")
    assert [c["segments"] for c in campaign_world.list_clocks(camp)] == [2, 12]


def test_advance_clamps_and_reports_the_fill(camp):
    cid = campaign_world.add_clock(camp, "Timer", segments=4)
    assert campaign_world.advance_clock(cid)["filled"] == 1
    r = campaign_world.advance_clock(cid, 2)
    assert r["filled"] == 3 and r["just_filled"] is False
    r = campaign_world.advance_clock(cid, 5)         # clamps at full
    assert r["filled"] == 4
    assert r["just_filled"] is True
    r = campaign_world.advance_clock(cid, 1)         # already full: not "just filled" again
    assert r["filled"] == 4 and r["just_filled"] is False
    r = campaign_world.advance_clock(cid, -99)       # clamps at empty
    assert r["filled"] == 0 and r["just_filled"] is False
    with pytest.raises(ValueError):
        campaign_world.advance_clock(cid, "lots")
    with pytest.raises(ValueError):
        campaign_world.advance_clock(99999)


def test_full_clocks_sort_last(camp):
    a = campaign_world.add_clock(camp, "A", segments=2)
    b = campaign_world.add_clock(camp, "B", segments=3)
    campaign_world.advance_clock(a, 2)
    clocks = campaign_world.list_clocks(camp)
    assert [c["id"] for c in clocks] == [b, a]
    assert [c["full"] for c in clocks] == [False, True]


def test_update_clock_fields(camp):
    faction = campaigns.add_faction(camp, "The Cult")
    cid = campaign_world.add_clock(camp, "Old name", segments=6)
    campaign_world.advance_clock(cid, 5)
    campaign_world.update_clock(cid, "name", "New name")
    campaign_world.update_clock(cid, "note", "The portal opens")
    campaign_world.update_clock(cid, "faction_id", faction)
    clock = campaign_world.list_clocks(camp)[0]
    assert (clock["name"], clock["note"], clock["faction_name"]) == ("New name", "The portal opens", "The Cult")
    campaign_world.update_clock(cid, "faction_id", "")
    assert campaign_world.get_clock(cid)["faction_id"] is None
    with pytest.raises(ValueError):
        campaign_world.update_clock(cid, "name", " ")
    with pytest.raises(ValueError):
        campaign_world.update_clock(cid, "filled", 1)       # use advance_clock
    with pytest.raises(ValueError):
        campaign_world.update_clock(99999, "name", "x")


def test_shrinking_a_clock_never_leaves_it_over_full(camp):
    cid = campaign_world.add_clock(camp, "Shrinks", segments=10)
    campaign_world.advance_clock(cid, 9)
    campaign_world.update_clock(cid, "segments", 4)
    clock = campaign_world.get_clock(cid)
    assert (clock["segments"], clock["filled"]) == (4, 4)
    with pytest.raises(ValueError):
        campaign_world.update_clock(cid, "segments", 20)
    assert campaign_world.get_clock(cid)["segments"] == 4


def test_deleting_a_faction_keeps_its_clock(camp):
    faction = campaigns.add_faction(camp, "The Cult")
    cid = campaign_world.add_clock(camp, "Ritual", faction_id=faction)
    campaigns.delete_faction(faction)
    clock = campaign_world.get_clock(cid)
    assert clock and clock["faction_id"] is None


def test_deleting_the_campaign_removes_everything(camp, sess):
    f = campaigns.add_faction(camp, "The Cult")
    n = campaigns.add_npc(camp, "Marlowe")
    campaign_world.add_secret(camp, "Secret", session_id=sess)
    campaign_world.add_clock(camp, "Clock", faction_id=f)
    campaign_world.add_relationship(camp, f"npc:{n}", f"faction:{f}", "ally")
    campaigns.delete_campaign(camp)
    for table in ("campaign_secrets", "campaign_clocks", "campaign_relationships"):
        assert _count(table) == 0, table


def test_delete_clock(camp):
    cid = campaign_world.add_clock(camp, "Short lived")
    campaign_world.delete_clock(cid)
    assert campaign_world.get_clock(cid) is None


# --------------------------------------------------------------- standing

def test_faction_standing_defaults_to_neutral_and_validates(camp):
    fid = campaigns.add_faction(camp, "The Guild")
    assert campaigns.list_factions(camp)[0]["standing"] == "neutral"
    campaigns.update_faction_field(fid, "standing", "Hostile")
    assert campaigns.list_factions(camp)[0]["standing"] == "hostile"
    with pytest.raises(ValueError):
        campaigns.update_faction_field(fid, "standing", "adored")
    with pytest.raises(ValueError):
        campaigns.update_faction_field(fid, "standing", "")
    assert campaigns.list_factions(camp)[0]["standing"] == "hostile"
    for choice in campaigns.STANDINGS:
        campaigns.update_faction_field(fid, "standing", choice)
        assert campaigns.list_factions(camp)[0]["standing"] == choice


# ---------------------------------------------------------- relationships

def test_add_and_list_relationships(camp):
    guild = campaigns.add_faction(camp, "The Guild")
    marlowe = campaigns.add_npc(camp, "Marlowe")
    rid = campaign_world.add_relationship(camp, f"npc:{marlowe}", f"faction:{guild}", "employer", "pays in gold")
    (tie,) = campaign_world.list_relationships(camp)
    assert tie["id"] == rid
    assert (tie["a_name"], tie["b_name"], tie["kind"]) == ("Marlowe", "The Guild", "employer")
    assert tie["phrase"] == "Marlowe employs The Guild"
    assert tie["note"] == "pays in gold"
    assert campaign_world.relationships_of(camp, "faction", guild) == ["Marlowe employs The Guild"]
    assert campaign_world.relationships_of(camp, "npc", marlowe) == ["Marlowe employs The Guild"]
    assert campaign_world.relationships_of(camp, "npc", 9999) == []


def test_tie_is_replaced_not_duplicated(camp):
    a, b = campaigns.add_npc(camp, "Ana"), campaigns.add_npc(camp, "Ben")
    first = campaign_world.add_relationship(camp, f"npc:{a}", f"npc:{b}", "ally")
    second = campaign_world.add_relationship(camp, f"npc:{a}", f"npc:{b}", "enemy", "falling out")
    assert first == second
    (tie,) = campaign_world.list_relationships(camp)
    assert (tie["kind"], tie["note"]) == ("enemy", "falling out")
    # the reverse direction is a different statement ("A owes B" vs "B owes A")
    campaign_world.add_relationship(camp, f"npc:{b}", f"npc:{a}", "owes")
    assert len(campaign_world.list_relationships(camp)) == 2


def test_relationship_validation(camp, other):
    a, b = campaigns.add_npc(camp, "Ana"), campaigns.add_npc(camp, "Ben")
    foreign = campaigns.add_npc(other, "Stranger")
    foreign_faction = campaigns.add_faction(other, "Foreign Guild")
    bad = [
        (f"npc:{a}", f"npc:{a}", "ally"),                 # itself
        (f"npc:{a}", f"npc:{foreign}", "ally"),           # another campaign's NPC
        (f"npc:{a}", f"faction:{foreign_faction}", "ally"),
        (f"npc:{a}", "npc:9999", "ally"),                 # unknown
        (f"npc:{a}", "", "ally"),
        (f"npc:{a}", "dragon:1", "ally"),                 # unknown kind of thing
        (f"npc:{a}", "npc:abc", "ally"),
        (f"npc:{a}", f"npc:{b}", "frenemy"),              # unknown tie
    ]
    for x, y, kind in bad:
        with pytest.raises(ValueError):
            campaign_world.add_relationship(camp, x, y, kind)
    assert campaign_world.list_relationships(camp) == []
    # an NPC and a faction can share an id number without being confused
    f = campaigns.add_faction(camp, "Same Number")
    campaign_world.add_relationship(camp, f"npc:{a}", f"faction:{f}", "fears")
    assert campaign_world.list_relationships(camp)[0]["b_name"] == "Same Number"


def test_removing_an_npc_or_faction_removes_its_ties(camp):
    guild = campaigns.add_faction(camp, "The Guild")
    a, b = campaigns.add_npc(camp, "Ana"), campaigns.add_npc(camp, "Ben")
    campaign_world.add_relationship(camp, f"npc:{a}", f"npc:{b}", "ally")
    campaign_world.add_relationship(camp, f"npc:{a}", f"faction:{guild}", "fears")
    campaign_world.add_relationship(camp, f"faction:{guild}", f"npc:{b}", "employer")
    campaigns.delete_npc(a)
    assert [t["phrase"] for t in campaign_world.list_relationships(camp)] == ["The Guild employs Ben"]
    assert _count("campaign_relationships") == 1
    campaigns.delete_faction(guild)
    assert _count("campaign_relationships") == 0


def test_an_npc_and_a_faction_with_the_same_id_keep_their_own_ties(camp):
    # ids are per table, so NPC 1 and faction 1 are different things: deleting one must not touch the other's ties
    npc = campaigns.add_npc(camp, "Ana")
    friend = campaigns.add_npc(camp, "Ben")
    faction = campaigns.add_faction(camp, "The Guild")
    assert npc == faction == 1
    campaign_world.add_relationship(camp, f"faction:{faction}", f"npc:{friend}", "ally")
    campaign_world.add_relationship(camp, f"npc:{npc}", f"npc:{friend}", "rival")
    campaigns.delete_npc(npc)
    assert [t["phrase"] for t in campaign_world.list_relationships(camp)] == ["The Guild allied with Ben"]


def test_entity_choices_and_payload(camp, sess):
    guild = campaigns.add_faction(camp, "The Guild")
    marlowe = campaigns.add_npc(camp, "Marlowe")
    campaign_world.add_secret(camp, "S")
    campaign_world.add_clock(camp, "C")
    payload = campaign_world.payload(camp)
    assert payload["entities"] == [
        {"value": f"faction:{guild}", "name": "The Guild", "kind": "faction"},
        {"value": f"npc:{marlowe}", "name": "Marlowe", "kind": "npc"},
    ]
    assert len(payload["secrets"]) == 1 and len(payload["clocks"]) == 1 and payload["relationships"] == []
    assert payload["sessions"] == [{"id": sess, "number": 1, "title": "The docks"}]
    assert {k["value"] for k in payload["tie_kinds"]} == set(campaign_world.TIE_KINDS)
    assert all(k["label"] for k in payload["tie_kinds"])


# ----------------------------------------------------------------- export

def _populate(camp, sess):
    guild = campaigns.add_faction(camp, "The Guild", goal="Own the docks")
    campaigns.update_faction_field(guild, "standing", "unfriendly")
    marlowe = campaigns.add_npc(camp, "Marlowe")
    campaign_world.add_relationship(camp, f"npc:{marlowe}", f"faction:{guild}", "fears", "owes them money")
    cid = campaign_world.add_clock(camp, "Guild takes the docks", segments=4, faction_id=guild, note="Fire in the harbour")
    campaign_world.advance_clock(cid, 2)
    hidden = campaign_world.add_secret(camp, "HIDDENTRUTH the mayor is a vampire", source="ledger", session_id=sess)
    out = campaign_world.add_secret(camp, "KNOWNFACT the tunnel exists")
    campaign_world.reveal(out, session_id=sess)
    return hidden


def test_gm_export_includes_everything(camp, sess):
    _populate(camp, sess)
    text = campaign_export.to_markdown(camp)
    assert "**Standing with the party:** unfriendly" in text
    assert "## Ties" in text and "Marlowe fears The Guild - owes them money" in text
    assert "## Clocks" in text and "**Guild takes the docks**: 2 of 4 - The Guild - Fire in the harbour" in text
    assert "## Secrets & clues" in text
    assert "- [ ] HIDDENTRUTH the mayor is a vampire (source: ledger)" in text
    assert "- [x] KNOWNFACT the tunnel exists (revealed in session 1)" in text


def test_player_handout_only_shows_what_was_revealed(camp, sess):
    _populate(camp, sess)
    text = campaign_export.to_markdown(camp, player_safe=True)
    assert "HIDDENTRUTH" not in text
    assert "## What the party has learned" in text and "- KNOWNFACT the tunnel exists" in text
    for gm_only in ("Standing with the party", "## Ties", "## Clocks", "Fire in the harbour", "owes them money", "## Secrets & clues"):
        assert gm_only not in text, gm_only


def test_player_handout_without_revealed_secrets_has_no_section(camp, sess):
    campaign_world.add_secret(camp, "STILLHIDDEN")
    text = campaign_export.to_markdown(camp, player_safe=True)
    assert "STILLHIDDEN" not in text and "What the party has learned" not in text


# -------------------------------------------------------------- endpoints

def test_world_endpoint(client, camp):
    campaign_world.add_secret(camp, "A secret")
    res = client.get(f"/api/campaigns/{camp}/world")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"secrets", "clocks", "relationships", "entities", "sessions", "tie_kinds"}
    assert body["secrets"][0]["text"] == "A secret"
    assert client.get("/api/campaigns/9999/world").status_code == 404


def test_secret_endpoints_round_trip(client, camp, sess):
    res = client.post(f"/api/campaigns/{camp}/secrets", data={"text": "Hidden door", "source": "map", "session_id": str(sess)})
    assert res.status_code == 200
    sid = res.json()["id"]
    assert client.post(f"/api/campaigns/secrets/{sid}/update", data={"field": "text", "value": "Hidden vault"}).status_code == 200
    assert client.get(f"/api/campaigns/{camp}/world").json()["secrets"][0]["text"] == "Hidden vault"
    res = client.post(f"/api/campaigns/secrets/{sid}/reveal", data={"session_id": str(sess)})
    assert res.status_code == 200 and res.json()["status"] == "revealed"
    assert [e["kind"] for e in session_log.list_entries(camp, sess)] == ["clue"]
    assert client.post(f"/api/campaigns/secrets/{sid}/hide").json()["status"] == "hidden"
    assert client.post(f"/api/campaigns/secrets/{sid}/delete").status_code == 200
    assert client.get(f"/api/campaigns/{camp}/world").json()["secrets"] == []


def test_secret_endpoint_errors_are_400_with_a_message(client, camp, other):
    foreign = campaigns.add_session(other)
    res = client.post(f"/api/campaigns/{camp}/secrets", data={"text": "  "})
    assert res.status_code == 400 and "secret" in res.json()["error"].lower()
    res = client.post(f"/api/campaigns/{camp}/secrets", data={"text": "x", "session_id": str(foreign)})
    assert res.status_code == 400
    assert client.post("/api/campaigns/secrets/9999/update", data={"field": "text", "value": "x"}).status_code == 400
    assert client.post("/api/campaigns/secrets/9999/reveal").status_code == 400
    assert client.post("/api/campaigns/secrets/9999/hide").status_code == 400
    assert client.post("/api/campaigns/9999/secrets", data={"text": "x"}).status_code == 404
    assert client.post("/api/campaigns/9999/secrets/import").status_code == 404


def test_import_endpoint(client, camp, sess):
    campaigns.update_session_field(sess, "secrets_clues", "- One\n- Two")
    res = client.post(f"/api/campaigns/{camp}/secrets/import")
    assert res.status_code == 200 and res.json() == {"added": 2, "skipped": 0}


def test_clock_endpoints_round_trip(client, camp):
    res = client.post(f"/api/campaigns/{camp}/clocks", data={"name": "Doom", "segments": "4"})
    assert res.status_code == 200
    cid = res.json()["id"]
    res = client.post(f"/api/campaigns/clocks/{cid}/advance", data={"delta": "4"})
    assert res.status_code == 200 and res.json()["just_filled"] is True and res.json()["filled"] == 4
    res = client.post(f"/api/campaigns/clocks/{cid}/update", data={"field": "segments", "value": "3"})
    assert res.status_code == 200 and res.json()["filled"] == 3
    assert client.post(f"/api/campaigns/clocks/{cid}/delete").status_code == 200
    assert client.get(f"/api/campaigns/{camp}/world").json()["clocks"] == []


def test_clock_endpoint_errors(client, camp):
    assert client.post(f"/api/campaigns/{camp}/clocks", data={"name": "", "segments": "4"}).status_code == 400
    assert client.post(f"/api/campaigns/{camp}/clocks", data={"name": "X", "segments": "99"}).status_code == 400
    assert client.post("/api/campaigns/clocks/9999/advance", data={"delta": "1"}).status_code == 400
    assert client.post("/api/campaigns/clocks/9999/update", data={"field": "name", "value": "x"}).status_code == 400
    assert client.post("/api/campaigns/9999/clocks", data={"name": "X"}).status_code == 404


def test_relationship_endpoints_round_trip(client, camp):
    a, b = campaigns.add_npc(camp, "Ana"), campaigns.add_npc(camp, "Ben")
    res = client.post(f"/api/campaigns/{camp}/relationships", data={"a": f"npc:{a}", "b": f"npc:{b}", "kind": "rival"})
    assert res.status_code == 200
    ties = client.get(f"/api/campaigns/{camp}/world").json()["relationships"]
    assert [t["phrase"] for t in ties] == ["Ana rival of Ben"]
    assert client.post(f"/api/campaigns/relationships/{res.json()['id']}/delete").status_code == 200
    assert client.get(f"/api/campaigns/{camp}/world").json()["relationships"] == []
    res = client.post(f"/api/campaigns/{camp}/relationships", data={"a": f"npc:{a}", "b": f"npc:{a}", "kind": "ally"})
    assert res.status_code == 400
    assert client.post("/api/campaigns/9999/relationships", data={"a": "npc:1", "b": "npc:2"}).status_code == 404


def test_faction_standing_endpoint(client, camp):
    fid = campaigns.add_faction(camp, "The Guild")
    assert client.post(f"/api/campaigns/factions/{fid}/update", data={"field": "standing", "value": "allied"}).status_code == 200
    assert campaigns.list_factions(camp)[0]["standing"] == "allied"
    assert client.post(f"/api/campaigns/factions/{fid}/update", data={"field": "standing", "value": "nope"}).status_code == 400


def test_campaign_page_has_the_new_cards(client, camp):
    campaigns.add_faction(camp, "The Guild")
    html = client.get(f"/campaigns/{camp}").text
    for needle in ('id="secrets-card"', 'id="clocks-card"', 'id="ties-card"', "campaign_world.js", 'data-field="standing"'):
        assert needle in html, needle
    for choice in campaigns.STANDINGS:
        assert f'value="{choice}"' in html


def test_export_endpoint_carries_the_new_sections(client, camp, sess):
    _populate(camp, sess)
    gm = client.get(f"/campaigns/{camp}/export.md").text
    player = client.get(f"/campaigns/{camp}/export.md?player_safe=1").text
    assert "HIDDENTRUTH" in gm and "HIDDENTRUTH" not in player
    assert "KNOWNFACT" in player
