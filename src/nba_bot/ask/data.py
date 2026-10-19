"""Builds `ask.team_games`: one row per team per final game, with both sides' record
and rank going into it and whether it came after the All-Star break.

Point-in-time on purpose: "against top-10 teams" means teams that were top 10 when
they played, not where they finished. Rebuilt whenever there are final games it
doesn't have yet (`refresh_if_stale`), which takes about a second.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import column, delete, func, insert, select, table
from sqlalchemy.orm import Session

from nba_bot.data.nba_stats import is_preseason
from nba_bot.db.models import Game, Team
from nba_bot.research.table import all_star_break

TEAM_GAMES = table(
    "team_games",
    *(column(c) for c in (
        "season", "season_type", "game_id", "game_date", "team", "opponent", "home",
        "team_score", "opp_score", "margin", "won", "rest_days", "back_to_back",
        "after_all_star_break", "team_wins_before", "team_losses_before", "opp_wins_before",
        "opp_losses_before", "team_rank_before", "opp_rank_before")),
    schema="ask",
)
_TYPES = {"001": "preseason", "002": "regular", "004": "playoffs", "005": "play-in"}


def season_type(game_id: str) -> str:
    return _TYPES.get(game_id[:3], "other")


def _final_games(session: Session) -> list[Game]:
    return session.execute(
        select(Game).where(Game.status == "final", Game.home_score.is_not(None),
                           Game.away_score.is_not(None))
        .order_by(Game.game_date, Game.game_id)
    ).scalars().all()


def build_rows(games: list[Game], abbr: dict[int, str]) -> list[dict]:
    """The table's rows. Records and ranks count regular-season games only; ranks are
    by point margin per game, 1 = best, among teams that have played."""
    rows = []
    by_season: dict[str, list[Game]] = defaultdict(list)
    for g in games:
        by_season[g.season].append(g)
    for season, season_games in by_season.items():
        regular = [g for g in season_games if not is_preseason(g.game_id)]
        brk = all_star_break([g.game_date for g in regular])
        wins: dict[int, int] = defaultdict(int)
        losses: dict[int, int] = defaultdict(int)
        points: dict[int, int] = defaultdict(int)
        ranks: dict[int, int] = {}
        ranked_on = None
        for g in season_games:
            pre = is_preseason(g.game_id)
            if not pre and g.game_date != ranked_on:
                played = [t for t in set(wins) | set(losses) if wins[t] + losses[t]]
                order = sorted(played, key=lambda t: points[t] / (wins[t] + losses[t]), reverse=True)
                ranks, ranked_on = {t: i + 1 for i, t in enumerate(order)}, g.game_date
            for team, opp, home in ((g.home_team_id, g.away_team_id, True),
                                    (g.away_team_id, g.home_team_id, False)):
                mine, theirs = (g.home_score, g.away_score) if home else (g.away_score, g.home_score)
                rest = g.home_rest_days if home else g.away_rest_days
                rows.append({
                    "season": season, "season_type": season_type(g.game_id),
                    "game_id": g.game_id, "game_date": g.game_date,
                    "team": abbr[team], "opponent": abbr[opp], "home": home,
                    "team_score": mine, "opp_score": theirs, "margin": mine - theirs,
                    "won": mine > theirs, "rest_days": rest,
                    "back_to_back": None if rest is None else rest == 0,
                    "after_all_star_break": None if pre else (brk is not None and g.game_date >= brk),
                    "team_wins_before": None if pre else wins[team],
                    "team_losses_before": None if pre else losses[team],
                    "opp_wins_before": None if pre else wins[opp],
                    "opp_losses_before": None if pre else losses[opp],
                    "team_rank_before": None if pre else ranks.get(team),
                    "opp_rank_before": None if pre else ranks.get(opp),
                })
            if not pre:
                home_won = g.home_score > g.away_score
                for team, won, diff in ((g.home_team_id, home_won, g.home_score - g.away_score),
                                        (g.away_team_id, not home_won, g.away_score - g.home_score)):
                    wins[team] += won
                    losses[team] += not won
                    points[team] += diff
    return rows


def refresh(session: Session) -> int:
    abbr = dict(session.execute(select(Team.team_id, Team.abbreviation)).all())
    rows = build_rows(_final_games(session), abbr)
    session.execute(delete(TEAM_GAMES))
    if rows:
        session.execute(insert(TEAM_GAMES), rows)
    session.commit()
    return len(rows)


def refresh_if_stale(session: Session) -> bool:
    """Rebuild when final games exist that the table doesn't have."""
    finals = session.execute(
        select(func.count()).select_from(Game).where(
            Game.status == "final", Game.home_score.is_not(None), Game.away_score.is_not(None))
    ).scalar_one()
    have = session.execute(select(func.count()).select_from(TEAM_GAMES)).scalar_one()
    if have == 2 * finals:
        return False
    refresh(session)
    return True
