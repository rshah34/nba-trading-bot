"""Load a completed NBA season into the DB for backtesting.

Everything here is reconstructable point-in-time from the schedule + final
scores: game results, rest days / back-to-backs, and per-team margins (which
feed `recent_form`). Injuries, news, and odds are NOT loaded — they aren't
available historically, so a backtest measures the stats+rest signal only.
One `get_season_games` call; no per-game box-score fetches.
"""

from __future__ import annotations

import time
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.agents.data_agent import store_player_box_score, sync_teams
from nba_bot.data import nba_stats
from nba_bot.db.models import Game, PlayerGameStats, TeamGameStats


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


def backfill_player_stats(session: Session, season: str, limit: int | None = None,
                          sleep: float = 0.5) -> dict:
    """Fetch per-player box scores for a season's final games and store them
    (via data_agent.store_player_box_score). Idempotent: skips games already
    ingested, so a failed run resumes on re-run. `sleep` throttles nba.com calls.
    """
    final_ids = session.execute(
        select(Game.game_id)
        .where(Game.season == season, Game.status == "final", Game.home_score.is_not(None))
        .order_by(Game.game_date, Game.game_id)
    ).scalars().all()
    done = set(session.execute(select(PlayerGameStats.game_id).distinct()).scalars().all())
    todo = [gid for gid in final_ids if gid not in done]
    if limit is not None:
        todo = todo[:limit]

    games_done, rows, failed = 0, 0, 0
    for gid in todo:
        try:
            rows += store_player_box_score(session, gid)
        except Exception:  # noqa: BLE001 — one flaky game shouldn't abort a long backfill
            failed += 1
            continue
        games_done += 1
        if sleep:
            time.sleep(sleep)

    return {"season": season, "games_ingested": games_done, "player_rows": rows,
            "failed": failed, "pending_this_run": len(todo)}
