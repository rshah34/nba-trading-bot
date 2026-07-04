"""Injury reports from ESPN's public site API.

nba_api / stats.nba.com do not expose an injury report endpoint, so this hits
ESPN's (undocumented but stable) site API directly.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"

_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))


@_retry
def get_injuries() -> list[dict]:
    """Returns one dict per team: {espn_team_id, team_name, injuries: [{player_name, status, reason}]}."""
    resp = httpx.get(ESPN_INJURIES_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    teams = []
    for team in data.get("injuries", []):
        entries = [
            {
                "player_name": inj.get("athlete", {}).get("displayName"),
                "status": inj.get("status"),
                "reason": inj.get("shortComment") or inj.get("longComment"),
            }
            for inj in team.get("injuries", [])
        ]
        teams.append(
            {
                "espn_team_id": team.get("id"),
                "team_name": team.get("displayName"),
                "injuries": entries,
            }
        )
    return teams
