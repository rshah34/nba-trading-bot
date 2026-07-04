"""Data Agent: nightly ingestion of teams, today's games, injuries, and box scores
for completed games. Run via `nba-bot ingest` (see cli/main.py).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.data import injuries as injuries_client
from nba_bot.data import nba_stats
from nba_bot.db.models import Game, Injury, Team, TeamGameStats


def sync_teams(session: Session) -> int:
    rows = nba_stats.get_all_teams()
    count = 0
    for t in rows:
        stmt = (
            pg_insert(Team)
            .values(team_id=t["id"], abbreviation=t["abbreviation"], full_name=t["full_name"])
            .on_conflict_do_update(
                index_elements=[Team.team_id],
                set_={"abbreviation": t["abbreviation"], "full_name": t["full_name"]},
            )
        )
        session.execute(stmt)
        count += 1
    session.commit()
    return count


def _last_game_date_before(session: Session, team_id: int, before: date) -> date | None:
    stmt = (
        select(Game.game_date)
        .where(
            Game.status == "final",
            Game.game_date < before,
            (Game.home_team_id == team_id) | (Game.away_team_id == team_id),
        )
        .order_by(Game.game_date.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def sync_games_for_date(session: Session, game_date: date) -> int:
    df = nba_stats.get_scoreboard(game_date)
    count = 0
    for _, g in df.iterrows():
        home_last = _last_game_date_before(session, int(g["home_team_id"]), game_date)
        away_last = _last_game_date_before(session, int(g["away_team_id"]), game_date)
        home_rest = (game_date - home_last).days - 1 if home_last else None
        away_rest = (game_date - away_last).days - 1 if away_last else None

        stmt = (
            pg_insert(Game)
            .values(
                game_id=g["game_id"],
                season=_season_for_date(game_date),
                game_date=g["game_date"],
                home_team_id=int(g["home_team_id"]),
                away_team_id=int(g["away_team_id"]),
                home_score=int(g["home_score"]) if g["home_score"] is not None else None,
                away_score=int(g["away_score"]) if g["away_score"] is not None else None,
                status=g["status"],
                home_rest_days=home_rest,
                away_rest_days=away_rest,
                is_back_to_back_home=home_rest == 0,
                is_back_to_back_away=away_rest == 0,
            )
            .on_conflict_do_update(
                index_elements=[Game.game_id],
                set_={
                    "home_score": int(g["home_score"]) if g["home_score"] is not None else None,
                    "away_score": int(g["away_score"]) if g["away_score"] is not None else None,
                    "status": g["status"],
                },
            )
        )
        session.execute(stmt)
        count += 1
    session.commit()
    return count


def _season_for_date(d: date) -> str:
    # NBA season labeled by its starting year; season flips in October.
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def sync_injuries(session: Session) -> int:
    teams_by_name = {t.full_name: t.team_id for t in session.execute(select(Team)).scalars()}
    count = 0
    for team in injuries_client.get_injuries():
        team_id = teams_by_name.get(team["team_name"])
        if team_id is None:
            continue
        for inj in team["injuries"]:
            if not inj["player_name"]:
                continue
            session.add(
                Injury(
                    team_id=team_id,
                    player_name=inj["player_name"],
                    status=inj["status"] or "unknown",
                    reason=inj["reason"],
                )
            )
            count += 1
    session.commit()
    return count


def backfill_box_scores(session: Session, lookback_days: int = 3) -> int:
    """Fetch team box scores for recently completed games that don't have stats yet."""
    cutoff = date.today() - timedelta(days=lookback_days)
    stmt = (
        select(Game.game_id)
        .outerjoin(TeamGameStats, Game.game_id == TeamGameStats.game_id)
        .where(Game.status == "final", Game.game_date >= cutoff, TeamGameStats.game_id.is_(None))
    )
    game_ids = session.execute(stmt).scalars().all()

    count = 0
    for game_id in game_ids:
        df = nba_stats.get_team_box_score(game_id)
        for _, row in df.iterrows():
            session.add(
                TeamGameStats(
                    game_id=game_id,
                    team_id=int(row["TEAM_ID"]),
                    points=row["PTS"],
                    fg_pct=row["FG_PCT"],
                    fg3_pct=row["FG3_PCT"],
                    ft_pct=row["FT_PCT"],
                    rebounds=row["REB"],
                    assists=row["AST"],
                    turnovers=row["TO"],
                    plus_minus=row["PLUS_MINUS"],
                )
            )
            count += 1
        session.commit()
    return count


def run_nightly(session: Session) -> dict:
    """Full nightly ingestion: teams, today's + tomorrow's games, injuries, recent box scores."""
    teams = sync_teams(session)
    games_today = sync_games_for_date(session, date.today())
    games_tomorrow = sync_games_for_date(session, date.today() + timedelta(days=1))
    injuries = sync_injuries(session)
    box_scores = backfill_box_scores(session)
    return {
        "teams": teams,
        "games_today": games_today,
        "games_tomorrow": games_tomorrow,
        "injuries": injuries,
        "box_score_rows": box_scores,
    }
