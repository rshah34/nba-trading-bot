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

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.agents.data_agent import store_box_score, sync_teams
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
                # Rest days too: they depend on the whole schedule, so a reload that
                # adds a game must correct the games after it.
                set_={"home_score": home_score, "away_score": away_score,
                      "status": "final" if home_score is not None else "scheduled",
                      "home_rest_days": home_rest, "away_rest_days": away_rest,
                      "is_back_to_back_home": home_rest == 0,
                      "is_back_to_back_away": away_rest == 0},
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


# After a failed fetch, wait this long before the next game; stop after this many in a row.
_BACKOFF_SECONDS = 45
_GIVE_UP_AFTER = 4


def backfill_box_scores(session: Session, season: str, limit: int | None = None,
                        sleep: float = 0.5) -> dict:
    """Fetch box scores for a season's final games that are missing them.

    One V3 call per game fills both the player lines and the team raw counts, so
    this is the single backfill for a freshly loaded season. Resumable: a game that
    already has both is skipped. `sleep` is a courtesy pause between nba.com calls.
    """
    have_players = select(PlayerGameStats.game_id)
    have_team_box = select(TeamGameStats.game_id).where(TeamGameStats.fga.is_not(None))
    stmt = (
        select(Game.game_id)
        .where(
            Game.season == season, Game.status == "final", Game.home_score.is_not(None),
            or_(Game.game_id.not_in(have_players), Game.game_id.not_in(have_team_box)),
        )
        .order_by(Game.game_date)
    )
    if limit:
        stmt = stmt.limit(limit)
    pending = session.execute(stmt).scalars().all()

    fetched = failed = streak = 0
    for gid in pending:
        try:
            store_box_score(session, gid)
            fetched, streak = fetched + 1, 0
        except Exception:  # noqa: BLE001 — nba.com times out under load; the game stays pending
            session.rollback()
            failed, streak = failed + 1, streak + 1
            if streak == _GIVE_UP_AFTER:
                break  # it is refusing us; a later run picks up where this one stopped
            time.sleep(_BACKOFF_SECONDS)
        time.sleep(sleep)
    return {"games_fetched": fetched, "failed": failed, "pending": len(pending) - fetched}
