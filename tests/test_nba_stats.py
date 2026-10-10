"""Pairing team-game rows into games. Network-free."""

import pandas as pd

from nba_bot.data.nba_stats import pair_games


def _rows(*rows):
    return pd.DataFrame(
        [{"GAME_ID": gid, "GAME_DATE": "2026-01-15", "TEAM_ID": tid, "TEAM_ABBREVIATION": abbr,
          "MATCHUP": matchup, "PTS": pts} for gid, tid, abbr, matchup, pts in rows]
    )


def test_pair_games_reads_home_and_away_from_the_matchup():
    games = pair_games(_rows(
        ("1", 10, "BOS", "BOS vs. NYK", 110),
        ("1", 20, "NYK", "NYK @ BOS", 101),
    ))
    assert games.iloc[0].to_dict() | {"game_date": None, "status": None} == {
        "game_id": "1", "game_date": None, "home_team_id": 10, "away_team_id": 20,
        "home_score": 110, "away_score": 101, "status": None,
    }


def test_pair_games_keeps_neutral_site_games():
    # Overseas and NBA Cup semifinal games list both teams under the same "A @ B".
    games = pair_games(_rows(
        ("2", 30, "MEM", "MEM @ ORL", 111),
        ("2", 40, "ORL", "MEM @ ORL", 118),
    ))
    assert len(games) == 1
    assert (games.iloc[0]["home_team_id"], games.iloc[0]["away_team_id"]) == (40, 30)
    assert (games.iloc[0]["home_score"], games.iloc[0]["away_score"]) == (118, 111)
