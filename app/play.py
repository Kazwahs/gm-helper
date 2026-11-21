"""What a player sees about a campaign they belong to.

Deliberately narrower than the GM's "player-safe export": that export is something
the GM reviews before handing out, so it can list every NPC, location and faction.
A signed-in player gets no such review, and a name alone can spoil a twist, so this
page shows only what the GM has clearly put in front of the party:

  * the premise and tone the GM wrote for the campaign
  * the secrets and clues the party has actually found (status 'revealed')
  * a summary for each session that has been played
  * the maps attached to the campaign (the live player view of each)

Everything else - plot threads, NPCs, locations, factions, clocks, ties, prep
notes, raw session logs, hidden secrets - stays GM-only until the GM adds a way to
share it.
"""
from . import accounts, campaign_world, campaigns
from .library import games as games_module


def recap(campaign_id):
    detail = campaigns.get_campaign_detail(campaign_id)
    if not detail:
        return None
    c = detail["campaign"]
    game = games_module.get_game(c["game_key"])
    learned = [x["text"] for x in campaign_world.list_secrets(campaign_id) if x["status"] == "revealed"]
    played = [s for s in reversed(detail["sessions"]) if s["status"] == "played"]   # oldest first
    return {
        "id": c["id"],
        "title": c["title"],
        "game_key": c["game_key"],
        "game_name": game["name"] if game else c["game_key"],
        "pitch": (c["pitch"] or "").strip(),
        "tone": (c["tone"] or "").strip(),
        "learned": learned,
        "sessions": [{"number": s["session_number"], "title": (s["title"] or "").strip(),
                      "date": (s["date"] or "").strip(), "summary": (s["summary"] or "").strip()} for s in played],
        "maps": accounts.maps_for_campaign(campaign_id),
    }


def home(user_id):
    """The player landing page: their campaigns and the games/libraries open to them."""
    names = {g["key"]: g["name"] for g in games_module.all_game_choices()}
    campaigns_list = []
    for c in accounts.campaigns_for_user(user_id):
        campaigns_list.append({"id": c["id"], "title": c["title"], "status": c["status"],
                               "game_name": names.get(c["game_key"], c["game_key"])})
    libraries = [{"key": k, "name": names.get(k, k)} for k in accounts.library_games(user_id)]
    libraries.sort(key=lambda g: g["name"].lower())
    return {"campaigns": campaigns_list, "libraries": libraries}
