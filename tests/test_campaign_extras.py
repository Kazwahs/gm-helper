"""Tests for the campaign extras: location<->map links, NPC faction/location,
the AI recap + prep suggestions (model stubbed), Markdown export (GM and
player-safe) and their routes."""
import pytest
from fastapi.testclient import TestClient

from app import campaign_ai, campaign_export, campaigns, llm, maps_store


@pytest.fixture
def camp(db):
    return campaigns.create_campaign("rifts", "The Sunken Crown", "A drowned kingdom stirs.", "Gothic, damp")


def _map(campaign_id=None, title="Harbor"):
    return maps_store.save_map("rifts", "town", "fantasy", title, "<svg/>", campaign_id=campaign_id)


# ----------------------------------------------------------- NPC links
def test_npc_can_be_linked_to_a_faction_and_location(camp):
    faction = campaigns.add_faction(camp, "Tide Cult")
    place = campaigns.add_location(camp, "Drowned Chapel")
    npc = campaigns.add_npc(camp, "Mara", faction_id=faction, location_id=place)
    row = campaigns.list_npcs(camp)[0]
    assert row["id"] == npc and row["faction_name"] == "Tide Cult" and row["location_name"] == "Drowned Chapel"


def test_npc_links_can_be_changed_and_cleared(camp):
    f = campaigns.add_faction(camp, "Tide Cult")
    npc = campaigns.add_npc(camp, "Mara")
    campaigns.update_npc_field(npc, "faction_id", str(f))
    assert campaigns.list_npcs(camp)[0]["faction_name"] == "Tide Cult"
    campaigns.update_npc_field(npc, "faction_id", "")
    assert campaigns.list_npcs(camp)[0]["faction_id"] is None


def test_npc_cannot_link_to_another_campaigns_faction(camp):
    other = campaigns.create_campaign("rifts", "Other")
    foreign = campaigns.add_faction(other, "Foreign Faction")
    npc = campaigns.add_npc(camp, "Mara")
    with pytest.raises(ValueError):
        campaigns.update_npc_field(npc, "faction_id", str(foreign))
    with pytest.raises(ValueError):
        campaigns.add_npc(camp, "Bob", faction_id=foreign)
    with pytest.raises(ValueError):
        campaigns.update_npc_field(npc, "location_id", "not-a-number")


def test_deleting_a_faction_or_location_unlinks_npcs(camp):
    f = campaigns.add_faction(camp, "Tide Cult")
    l = campaigns.add_location(camp, "Chapel")
    campaigns.add_npc(camp, "Mara", faction_id=f, location_id=l)
    campaigns.delete_faction(f)
    campaigns.delete_location(l)
    npc = campaigns.list_npcs(camp)[0]
    assert npc["faction_id"] is None and npc["location_id"] is None


# ----------------------------------------------------------- map links
def test_location_links_to_a_map_and_shows_its_title(camp):
    m = _map(camp, "Harbor Town")
    loc = campaigns.add_location(camp, "Harbor", map_id=m)
    assert campaigns.locations_tree(camp)[0]["map_title"] == "Harbor Town"
    assert campaigns.locations_for_map(m) == [
        {"id": loc, "name": "Harbor", "campaign_id": camp, "campaign_title": "The Sunken Crown"}]


def test_location_map_link_rules(camp):
    other = campaigns.create_campaign("rifts", "Other")
    foreign = _map(other)
    free = _map(None, "Unattached")
    loc = campaigns.add_location(camp, "Harbor")
    with pytest.raises(ValueError):
        campaigns.update_location_field(loc, "map_id", str(foreign))   # another campaign's map
    campaigns.update_location_field(loc, "map_id", str(free))          # unattached is fine
    assert campaigns.locations_tree(camp)[0]["map_id"] == free
    with pytest.raises(ValueError):
        campaigns.update_location_field(loc, "map_id", "9999")         # no such map
    campaigns.update_location_field(loc, "map_id", "")
    assert campaigns.locations_tree(camp)[0]["map_id"] is None


def test_deleting_a_map_clears_the_link(camp):
    m = _map(camp)
    campaigns.add_location(camp, "Harbor", map_id=m)
    maps_store.delete_map(m)
    assert campaigns.locations_tree(camp)[0]["map_id"] is None


def test_attaching_a_map_to_another_campaign_drops_stale_links(camp):
    other = campaigns.create_campaign("rifts", "Other")
    m = _map(None)
    campaigns.add_location(camp, "Harbor", map_id=m)
    maps_store.set_campaign(m, other)
    campaigns.unlink_map_from_other_campaigns(m, other)
    assert campaigns.locations_tree(camp)[0]["map_id"] is None


def test_parent_location_must_be_in_the_same_campaign(camp):
    other = campaigns.create_campaign("rifts", "Other")
    foreign = campaigns.add_location(other, "Elsewhere")
    with pytest.raises(ValueError):
        campaigns.add_location(camp, "Here", parent_id=foreign)
    mine = campaigns.add_location(camp, "Here")
    with pytest.raises(ValueError):
        campaigns.update_location_field(mine, "parent_id", str(foreign))


# ------------------------------------------------------------------- AI
@pytest.fixture
def ai_on(monkeypatch):
    calls = []
    state = {"answer": "Previously, the party reached the drowned gate."}
    monkeypatch.setattr(llm, "is_enabled", lambda: True)

    def fake(system_prompt, user_prompt, max_tokens=1600):
        calls.append({"system": system_prompt, "user": user_prompt})
        return state["answer"]

    monkeypatch.setattr(llm, "generate", fake)
    return {"calls": calls, "state": state}


def _played(camp, summary, title="", status="played"):
    sid = campaigns.add_session(camp, title)
    campaigns.update_session_field(sid, "status", status)
    campaigns.update_session_field(sid, "summary", summary)
    return sid


def test_recap_uses_only_played_sessions_with_summaries(camp, ai_on):
    _played(camp, "The party met Mara at the chapel.", "Chapel")
    _played(camp, "PLANNED SECRET - must not leak", status="planned")
    _played(camp, "   ")
    res = campaign_ai.generate_recap(camp)
    prompt = ai_on["calls"][0]["user"]
    assert res["recap"].startswith("Previously") and res["sessions_used"] == [1]
    assert "Session 1 - Chapel" in prompt and "met Mara" in prompt
    assert "must not leak" not in prompt and "The Sunken Crown" in prompt
    assert "do not reveal" in ai_on["calls"][0]["system"]


def test_recap_keeps_only_the_latest_sessions_oldest_first(camp, ai_on):
    for i in range(1, 9):
        _played(camp, f"summary-{i}")
    campaign_ai.generate_recap(camp)
    prompt = ai_on["calls"][0]["user"]
    assert "summary-4" in prompt and "summary-3" not in prompt  # latest 5 sessions: 4-8
    assert prompt.index("summary-4") < prompt.index("summary-8")


def test_recap_errors(camp, ai_on, monkeypatch):
    with pytest.raises(campaign_ai.CampaignAIError) as e:
        campaign_ai.generate_recap(camp)           # nothing played yet
    assert "Played" in str(e.value) and ai_on["calls"] == []
    with pytest.raises(campaign_ai.CampaignAIError) as e:
        campaign_ai.generate_recap(99999)
    assert e.value.status == 404
    _played(camp, "something happened")
    monkeypatch.setattr(llm, "generate", lambda *a, **k: "  ")
    with pytest.raises(campaign_ai.CampaignAIError) as e:
        campaign_ai.generate_recap(camp)
    assert e.value.status == 502
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    with pytest.raises(campaign_ai.CampaignAIError) as e:
        campaign_ai.generate_recap(camp)
    assert "Settings" in str(e.value)


def test_prep_prompt_includes_open_threads_cast_and_factions(camp, ai_on):
    campaigns.add_thread(camp, "The Rising Tide", "Water is climbing", "Town floods", "active")
    campaigns.add_thread(camp, "Old Debt", status="resolved")
    f = campaigns.add_faction(camp, "Tide Cult", "Raise the crown")
    campaigns.add_npc(camp, "Mara", "priestess", "rival", "wants the crown", "she drowned her sister",
                      faction_id=f)
    ai_on["state"]["answer"] = ("STRONG START:\nThe bell tolls underwater.\nSCENES:\n- Flooded market\n"
                                "SECRETS AND CLUES:\n- A wet ledger")
    res = campaign_ai.suggest_prep(camp)
    prompt = ai_on["calls"][0]["user"]
    assert "The Rising Tide" in prompt and "Old Debt" not in prompt  # resolved threads are left out
    assert "Mara" in prompt and "[Tide Cult]" in prompt and "Raise the crown" in prompt
    assert res["strong_start"] == "The bell tolls underwater."
    assert res["scenes"] == "- Flooded market" and res["secrets_clues"] == "- A wet ledger"


def test_prep_needs_some_campaign_material(db, ai_on):
    empty = campaigns.create_campaign("rifts", "Blank")
    with pytest.raises(campaign_ai.CampaignAIError):
        campaign_ai.suggest_prep(empty)
    assert ai_on["calls"] == []


def test_parse_prep_handles_markdown_headings_and_unstructured_text():
    parsed = campaign_ai.parse_prep("**STRONG START:** A fire.\n\n## Scenes\n- One\n- Two\n"
                                    "Scenes of chaos continue.\nSecrets & Clues:\n- X")
    assert parsed["strong_start"] == "A fire."
    assert parsed["scenes"] == "- One\n- Two\nScenes of chaos continue."
    assert parsed["secrets_clues"] == "- X"
    assert campaign_ai.parse_prep("just some prose") == {"strong_start": "", "scenes": "", "secrets_clues": ""}


# --------------------------------------------------------------- export
@pytest.fixture
def rich(camp):
    campaigns.add_thread(camp, "The Rising Tide", "Water climbs", "Town floods", "active")
    f = campaigns.add_faction(camp, "Tide Cult", "Raise the crown", "Hidden in the docks")
    harbor = campaigns.add_location(camp, "Harbor", "Busy docks", map_id=_map(camp, "Harbor Map"))
    campaigns.add_location(camp, "Chapel", "Half sunk", parent_id=harbor)
    campaigns.add_npc(camp, "Mara", "priestess", "villain", "the crown", "drowned her sister", "tall",
                      faction_id=f, location_id=harbor)
    _played(camp, "The party reached the gate.", "The Gate")
    nxt = campaigns.add_session(camp, "Planned One")
    campaigns.update_session_field(nxt, "strong_start", "SECRET OPENING")
    campaigns.update_session_field(nxt, "secrets_clues", "SECRET CLUE")
    return camp


def test_gm_export_has_everything(rich):
    md = campaign_export.to_markdown(rich, game_name="Rifts")
    assert md.startswith("# The Sunken Crown\n") and "*Rifts - Status: planning*" in md
    for needle in ("## Premise", "A drowned kingdom stirs.", "## Tone", "## Plot threads", "Town floods",
                   "### Mara - priestess (villain)", "**Secret:** drowned her sister", "**Faction:** Tide Cult",
                   "**Location:** Harbor", "**Goal:** Raise the crown", "(map: Harbor Map)",
                   "SECRET OPENING", "SECRET CLUE", "The party reached the gate."):
        assert needle in md, needle
    assert "  - **Chapel**" in md  # nested under Harbor


def test_sessions_export_oldest_first(rich):
    md = campaign_export.to_markdown(rich)
    assert md.index("### Session 1") < md.index("### Session 2")


def test_player_safe_export_leaves_out_gm_only_material(rich):
    md = campaign_export.to_markdown(rich, player_safe=True)
    for secret in ("SECRET OPENING", "SECRET CLUE", "drowned her sister", "the crown", "Raise the crown",
                   "Town floods", "Plot threads", "villain", "Status:", "Planned One", "Hidden in the docks"):
        assert secret not in md, secret
    for shown in ("A drowned kingdom stirs.", "Mara - priestess", "Tide Cult", "Harbor", "The party reached the gate."):
        assert shown in md, shown


def test_export_of_a_missing_campaign_is_none(db):
    assert campaign_export.to_markdown(12345) is None


def test_slugify():
    assert campaign_export.slugify("The Sunken Crown!") == "the-sunken-crown"
    assert campaign_export.slugify("???") == "campaign"


# ---------------------------------------------------------------- routes
@pytest.fixture
def client(db, games_config):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_export_route_downloads_markdown(client, rich):
    res = client.get(f"/campaigns/{rich}/export.md")
    assert res.status_code == 200 and "text/markdown" in res.headers["content-type"]
    assert 'filename="the-sunken-crown.md"' in res.headers["content-disposition"]
    assert "SECRET CLUE" in res.text
    safe = client.get(f"/campaigns/{rich}/export.md?player_safe=1")
    assert "SECRET CLUE" not in safe.text and "the-sunken-crown-handout.md" in safe.headers["content-disposition"]
    assert client.get("/campaigns/9999/export.md").status_code == 404


def test_detail_page_renders_link_controls(client, rich):
    html = client.get(f"/campaigns/{rich}").text
    assert 'data-field="faction_id"' in html and 'data-field="map_id"' in html
    assert "Open map" in html and "Player handout" in html


def test_recap_and_prep_routes(client, rich, ai_on, monkeypatch):
    res = client.post(f"/api/campaigns/{rich}/recap")
    assert res.status_code == 200 and "recap" in res.json()
    ai_on["state"]["answer"] = "STRONG START:\nGo.\nSCENES:\n- a\nSECRETS AND CLUES:\n- b"
    res = client.post(f"/api/campaigns/{rich}/prep-suggestions")
    assert res.status_code == 200 and res.json()["strong_start"] == "Go."
    monkeypatch.setattr(llm, "is_enabled", lambda: False)
    assert client.post(f"/api/campaigns/{rich}/recap").status_code == 400
    assert client.post("/api/campaigns/9999/prep-suggestions").status_code == 400


def test_link_endpoints_round_trip(client, camp):
    fid = client.post(f"/api/campaigns/{camp}/factions", data={"name": "Cult"}).json()["id"]
    lid = client.post(f"/api/campaigns/{camp}/locations", data={"name": "Chapel", "parent_id": "", "map_id": ""}).json()["id"]
    nid = client.post(f"/api/campaigns/{camp}/npcs",
                      data={"name": "Mara", "faction_id": str(fid), "location_id": str(lid)}).json()["id"]
    assert campaigns.list_npcs(camp)[0]["faction_name"] == "Cult"
    bad = client.post(f"/api/campaigns/npcs/{nid}/update", data={"field": "faction_id", "value": "9999"})
    assert bad.status_code == 400 and "error" in bad.json()
    m = _map(camp)
    ok = client.post(f"/api/campaigns/locations/{lid}/update", data={"field": "map_id", "value": str(m)})
    assert ok.status_code == 200
    assert f"/maps/{m}" in client.get(f"/campaigns/{camp}").text
    assert "Chapel" in client.get(f"/maps/{m}").text  # "Linked from" on the map page
