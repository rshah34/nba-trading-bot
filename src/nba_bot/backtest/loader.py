"""Load a completed NBA season into the DB for backtesting.

Everything here is reconstructable point-in-time from the schedule + final
scores: game results, rest days / back-to-backs, and per-team margins (which
feed `recent_form`). Injuries, news, and odds are NOT loaded — they aren't
available historically, so a backtest measures the stats+rest signal only.
One `get_season_games` call; no per-game box-score fetches.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.agents.data_agent import sync_teams
from nba_bot.data import nba_stats
from nba_bot.db.models import Game, TeamGameStats


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def load_season(session: Session, season: str) -> dict:
    """Populate games + team_game_stats for a season. Returns row counts."""
    sync_teams(session)  # ensure the 30 teams exist (static, no network)

    paired = nba_stats.pair_games(nba_stats.get_season_games(season))
    paired = paired.assign(_d=paired["game_date"].map(_as_date)).sort_values("_d")

    last_played: dict[int, date] = {}
    games = 0
    stats_rows = 0
    for _, g in paired.iterrows():
        gdate = g["_d"]
        home_id, away_id = int(g["home_team_id"]), int(g["away_team_id"])
        home_score = int(g["home_score"]) if g["home_score"] is not None else None
        away_score = int(g["away_score"]) if g["away_score"] is not None else None

        home_rest = (gdate - last_played[home_id]).days - 1 if home_id in last_played else None
        away_rest = (gdate - last_played[away_id]).days - 1 if away_id in last_played else None
        last_played[home_id] = gdate
        last_played[away_id] = gdate

        session.execute(
            pg_insert(Game)
            .values(
                game_id=g["game_id"],
                season=season,
                game_date=gdate,
                home_team_id=home_id,
                away_team_id=away_id,
                home_score=home_score,
                away_score=away_score,
                status="final" if home_score is not None else "scheduled",
                home_rest_days=home_rest,
                away_rest_days=away_rest,
                is_back_to_back_home=home_rest == 0,
                is_back_to_back_away=away_rest == 0,
            )
            .on_conflict_do_update(
                index_elements=[Game.game_id],
                set_={"home_score": home_score, "away_score": away_score,
                      "status": "final" if home_score is not None else "scheduled"},
            )
        )
        games += 1

        # Per-team margin (plus_minus) drives recent_form; other stats unavailable here.
        if home_score is not None:
            for team_id, pm in ((home_id, home_score - away_score), (away_id, away_score - home_score)):
                pts = home_score if team_id == home_id else away_score
                session.execute(
                    pg_insert(TeamGameStats)
                    .values(game_id=g["game_id"], team_id=team_id, points=pts, plus_minus=pm)
                    .on_conflict_do_update(
                        index_elements=[TeamGameStats.game_id, TeamGameStats.team_id],
                        set_={"points": pts, "plus_minus": pm},
                    )
                )
                stats_rows += 1

    session.commit()
    return {"season": season, "games": games, "team_game_stat_rows": stats_rows}
