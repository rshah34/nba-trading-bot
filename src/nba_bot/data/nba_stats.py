"""Wrapper around nba_api for teams, games, and box scores.

Note: stats.nba.com/cdn.nba.com aggressively block traffic from datacenter/cloud
IP ranges (Akamai bot protection). These calls are expected to work from a normal
residential connection but may 403/timeout from cloud CI or hosted dev sandboxes.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from nba_api.stats.endpoints import (
    boxscoretraditionalv3,
    leaguegamefinder,
    scheduleleaguev2,
    scoreboardv3,
)
from nba_api.stats.static import teams as static_teams
from tenacity import retry, stop_after_attempt, wait_exponential

_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))


def get_all_teams() -> list[dict]:
    """Static lookup, no network call. Returns id/full_name/abbreviation/etc for all 30 teams."""
    return static_teams.get_teams()


# nba.com game ids encode the game type in their first three digits.
PRESEASON_PREFIX = "001"


def is_preseason(game_id: str) -> bool:
    """Preseason games (id prefix 001) are exhibition minutes — never treat them
    as evidence about a team's or player's regular-season level."""
    return str(game_id).startswith(PRESEASON_PREFIX)


def _parse_utc(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@_retry
def get_scoreboard(game_date: date) -> pd.DataFrame:
    """Games scheduled/played on a given date, with home/away team ids, final
    scores, and the tip-off instant.

    Uses ScoreboardV3 — V2 went stale for 2026-27 (finished games stay at status 1
    with an empty line score, so nothing would ever resolve). V3 has no explicit
    home/visitor ids; `gameCode` is `YYYYMMDD/AWYHOM`, so the home side is the
    team whose tricode ends the code.
    """
    sb = scoreboardv3.ScoreboardV3(game_date=game_date.strftime("%Y-%m-%d"))
    frames = sb.get_data_frames()
    header, teams = frames[1], frames[2]

    rows = []
    for _, g in header.iterrows():
        sides = teams[teams["gameId"] == g["gameId"]]
        if len(sides) != 2:
            continue
        home_code = str(g["gameCode"])[-3:]
        home = sides[sides["teamTricode"] == home_code]
        away = sides[sides["teamTricode"] != home_code]
        if home.empty or away.empty:
            continue
        home, away = home.iloc[0], away.iloc[0]
        final = int(g["gameStatus"]) == 3
        rows.append(
            {
                "game_id": g["gameId"],
                "game_date": game_date,
                "home_team_id": int(home["teamId"]),
                "away_team_id": int(away["teamId"]),
                # A not-yet-played game reports score 0, not null.
                "home_score": int(home["score"]) if final else None,
                "away_score": int(away["score"]) if final else None,
                "status": "final" if final else "scheduled",
                "tipoff_at": _parse_utc(g["gameTimeUTC"]),
            }
        )
    return pd.DataFrame(rows)


@_retry
def get_schedule(season: str) -> pd.DataFrame:
    """Every game on the league's schedule for a season (e.g. '2026-27'), played or
    not: one row per game with the date (US Eastern, as the league dates games),
    the teams, the tip-off instant, and whether it is final."""
    df = scheduleleaguev2.ScheduleLeagueV2(season=season, league_id="00").get_data_frames()[0]
    return pd.DataFrame({
        "game_id": df["gameId"],
        "game_date": df["gameDateEst"].map(lambda v: _parse_utc(v).date()),
        "home_team_id": df["homeTeam_teamId"].astype(int),
        "away_team_id": df["awayTeam_teamId"].astype(int),
        "tipoff_at": df["gameDateTimeUTC"].map(_parse_utc),
        "final": df["gameStatus"].astype(int) == 3,
    })


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
    with home/away team ids and scores.

    The home team is read from MATCHUP: "BOS vs. NYK" names it first, "NYK @ BOS"
    names it last. A neutral-site game (overseas, NBA Cup semifinal) gives both teams
    the same "A @ B" string, so the row's own side can't be told from "vs."/"@" alone.
    """
    rows = []
    for game_id, group in season_games.groupby("GAME_ID"):
        if len(group) != 2:
            continue
        matchup = group.iloc[0]["MATCHUP"]
        home_abbr = matchup.split(" vs. ")[0] if " vs. " in matchup else matchup.split(" @ ")[-1]
        is_home = group["TEAM_ABBREVIATION"] == home_abbr
        if is_home.sum() != 1:
            continue
        home, away = group[is_home].iloc[0], group[~is_home].iloc[0]
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


def parse_minutes(value) -> float | None:
    """Parse a V3 'MM:SS' minutes string to float minutes; None/empty = DNP."""
    if not value or ":" not in str(value):
        return None
    mm, ss = str(value).split(":")[:2]
    return round(int(mm) + int(ss) / 60, 2)


@_retry
def get_box_score_v3(game_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-player and per-team traditional box scores for a game, from one fetch.

    Uses BoxScoreTraditionalV3 — V2 stopped publishing data as of the 2025-26
    season and returns empty frames. One API call yields both frames, so the
    caller can populate player_game_stats and team_game_stats without a second
    round-trip. Returns (player_stats, team_stats).
    """
    box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
    return box.player_stats.get_data_frame(), box.team_stats.get_data_frame()
