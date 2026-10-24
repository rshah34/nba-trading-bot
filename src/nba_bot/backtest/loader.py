"""Load a completed NBA season into the DB for backtesting.

Everything here is reconstructable point-in-time from the schedule + final
scores: game results, rest days / back-to-backs, and per-team margins (which
feed `recent_form`). Injuries, news, and odds are NOT loaded — they aren't
available historically, so a backtest measures the stats+rest signal only.
Three requests a season: the games, then every player's and team's box score.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.agents.data_agent import sync_teams
from nba_bot.data import nba_stats
from nba_bot.db.models import Game, PlayerGameStats, TeamGameStats


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


SEASON_TYPES = ("Regular Season", "PlayIn", "Playoffs")


def load_season(session: Session, season: str, season_type: str = "Regular Season") -> dict:
    """Populate games + team_game_stats for one kind of game in a season."""
    sync_teams(session)  # ensure the 30 teams exist (static, no network)

    paired = nba_stats.pair_games(nba_stats.get_season_games(season, season_type))
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
    return {"season": season, "type": season_type, "games": games,
            "team_game_stat_rows": stats_rows}


def load_box_scores(session: Session, season: str, season_type: str = "Regular Season") -> dict:
    """Player and team box scores for a loaded season, from two bulk requests.

    Matches what the per-game box score stores, with one exception handled here: the
    season log's team turnovers include team turnovers (shot-clock violations and the
    like), so the team's count is summed from its players instead."""
    players, teams = nba_stats.get_season_box_scores(season, season_type)
    known = set(session.execute(select(Game.game_id).where(Game.season == season)).scalars())
    players = players[players["GAME_ID"].isin(known)]
    turnovers = players.groupby(["GAME_ID", "TEAM_ID"])["TOV"].sum()

    player_rows = 0
    for r in players.itertuples(index=False):
        minutes = round(float(r.MIN), 2)
        if minutes <= 0:
            continue
        session.execute(
            pg_insert(PlayerGameStats).values(
                game_id=r.GAME_ID, player_id=int(r.PLAYER_ID), player_name=r.PLAYER_NAME,
                team_id=int(r.TEAM_ID), minutes=minutes, points=int(r.PTS), rebounds=int(r.REB),
                assists=int(r.AST), steals=int(r.STL), blocks=int(r.BLK), turnovers=int(r.TOV),
                fgm=int(r.FGM), fga=int(r.FGA), fg3m=int(r.FG3M), fg3a=int(r.FG3A),
                ftm=int(r.FTM), fta=int(r.FTA), plus_minus=float(r.PLUS_MINUS),
            ).on_conflict_do_nothing(index_elements=[PlayerGameStats.game_id,
                                                     PlayerGameStats.player_id])
        )
        player_rows += 1

    team_rows = 0
    for r in teams[teams["GAME_ID"].isin(known)].itertuples(index=False):
        box = {"fgm": int(r.FGM), "fga": int(r.FGA), "fg3m": int(r.FG3M), "fg3a": int(r.FG3A),
               "ftm": int(r.FTM), "fta": int(r.FTA), "oreb": int(r.OREB), "dreb": int(r.DREB),
               "rebounds": int(r.REB), "assists": int(r.AST), "steals": int(r.STL),
               "blocks": int(r.BLK),
               "turnovers": int(turnovers.get((r.GAME_ID, r.TEAM_ID), r.TOV)),
               "fg_pct": r.FG_PCT, "fg3_pct": r.FG3_PCT, "ft_pct": r.FT_PCT}
        session.execute(
            pg_insert(TeamGameStats).values(game_id=r.GAME_ID, team_id=int(r.TEAM_ID), **box)
            .on_conflict_do_update(index_elements=[TeamGameStats.game_id, TeamGameStats.team_id],
                                   set_=box)
        )
        team_rows += 1
    session.commit()
    return {"season": season, "type": season_type, "player_rows": player_rows,
            "team_rows": team_rows}
