"""Wrapper around nba_api for teams, games, and box scores.

Note: stats.nba.com/cdn.nba.com aggressively block traffic from datacenter/cloud
IP ranges (Akamai bot protection). These calls are expected to work from a normal
residential connection but may 403/timeout from cloud CI or hosted dev sandboxes.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from nba_api.stats.endpoints import boxscoretraditionalv2, leaguegamefinder, scoreboardv2
from nba_api.stats.static import teams as static_teams
from tenacity import retry, stop_after_attempt, wait_exponential

_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))


def get_all_teams() -> list[dict]:
    """Static lookup, no network call. Returns id/full_name/abbreviation/etc for all 30 teams."""
    return static_teams.get_teams()


@_retry
def get_scoreboard(game_date: date) -> pd.DataFrame:
    """Games scheduled/played on a given date, with home/away team ids and final scores."""
    sb = scoreboardv2.ScoreboardV2(game_date=game_date.strftime("%Y-%m-%d"))
    header = sb.game_header.get_data_frame()
    line_score = sb.line_score.get_data_frame()

    scores = line_score.set_index(["GAME_ID", "TEAM_ID"])["PTS"]

    rows = []
    for _, g in header.iterrows():
        home_id, away_id = g["HOME_TEAM_ID"], g["VISITOR_TEAM_ID"]
        rows.append(
            {
                "game_id": g["GAME_ID"],
                "game_date": game_date,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_score": scores.get((g["GAME_ID"], home_id)),
                "away_score": scores.get((g["GAME_ID"], away_id)),
                "status": "final" if g["GAME_STATUS_ID"] == 3 else "scheduled",
            }
        )
    return pd.DataFrame(rows)


@_retry
def get_season_games(season: str, season_type: str = "Regular Season") -> pd.DataFrame:
    """Historical games for a season (e.g. '2023-24'), one row per team-game.

    Used for backtesting: pairs of rows sharing GAME_ID represent one game.
    """
    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable=season,
        season_type_nullable=season_type,
        league_id_nullable="00",
    )
    return finder.get_data_frames()[0]


def pair_games(season_games: pd.DataFrame) -> pd.DataFrame:
    """Collapse the team-game rows from get_season_games into one row per game
    with home/away team ids and scores, using MATCHUP ('vs.' = home, '@' = away).
    """
    rows = []
    for game_id, group in season_games.groupby("GAME_ID"):
        if len(group) != 2:
            continue
        home = group[group["MATCHUP"].str.contains("vs.")]
        away = group[group["MATCHUP"].str.contains("@")]
        if home.empty or away.empty:
            continue
        home, away = home.iloc[0], away.iloc[0]
        rows.append(
            {
                "game_id": game_id,
                "game_date": home["GAME_DATE"],
                "home_team_id": home["TEAM_ID"],
                "away_team_id": away["TEAM_ID"],
                "home_score": home["PTS"],
                "away_score": away["PTS"],
                "status": "final",
            }
        )
    return pd.DataFrame(rows)


@_retry
def get_team_box_score(game_id: str) -> pd.DataFrame:
    """Per-team traditional box score stats for a single game."""
    box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
    return box.team_stats.get_data_frame()
