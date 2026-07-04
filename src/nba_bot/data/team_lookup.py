"""Resolve free-text team names (as used by The Odds API) to nba_api team ids.

The Odds API and nba_api use identical full names for all 30 teams
(e.g. "Los Angeles Clippers", "Boston Celtics"), so a direct full_name lookup
suffices. Names are matched case-insensitively to tolerate any upstream drift.
"""

from __future__ import annotations

from functools import lru_cache

from nba_api.stats.static import teams as static_teams


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


@lru_cache(maxsize=1)
def _name_to_id() -> dict[str, int]:
    return {_normalize(t["full_name"]): t["id"] for t in static_teams.get_teams()}


def resolve_team_id(name: str | None) -> int | None:
    """Return the nba_api team id for a team's full name, or None if unrecognized."""
    if not name:
        return None
    return _name_to_id().get(_normalize(name))
