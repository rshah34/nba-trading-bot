"""Dean Oliver's Four Factors, point-in-time, offense and defense.

Computed from the team_game_stats raw counts added in migration 004. For a team's
last N final games before as_of, we aggregate the team's own rows (offense) and the
opponents' rows in those same games (defense) — a team's defensive factors are
literally what its opponents did against it. Rate stats are combined as
sum(numerator)/sum(denominator) across the window (the correct way to aggregate
per-game rates), not a mean of per-game ratios.

Requires the raw counts to be populated; games lacking them (e.g. a season not yet
retrofitted via `nba-bot backfill-team-box`) are skipped, and a team with no
qualifying games returns games=0 with all-None factors so callers can omit it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from nba_bot.db.models import Game, TeamGameStats


@dataclass
class FourFactors:
    games: int
    pace: float | None          # possessions per game (regulation ≈ pace)
    # Offense — what the team does
    efg: float | None           # effective FG%
    tov_pct: float | None       # turnover rate (lower is better)
    oreb_pct: float | None      # offensive rebound rate
    ft_rate: float | None       # FTA / FGA (free-throw generation)
    # Defense — what the team allows/forces (from the opponents' rows)
    def_efg: float | None       # opponent eFG% allowed (lower is better)
    def_tov_pct: float | None   # opponent TOV% forced (higher is better)
    dreb_pct: float | None      # defensive rebound rate: own DREB / (own DREB + opp OREB)
    def_ft_rate: float | None   # opponent FTA / FGA allowed (lower is better)


def _poss(fga: float, oreb: float, tov: float, fta: float) -> float:
    """Standard possession estimate: FGA − OREB + TOV + 0.44·FTA."""
    return fga - oreb + tov + 0.44 * fta


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def team_four_factors(session: Session, team_id: int, as_of: date, n: int = 10) -> FourFactors:
    """Four Factors over a team's last `n` final games strictly before `as_of`."""
    game_ids = session.execute(
        select(TeamGameStats.game_id)
        .join(Game, TeamGameStats.game_id == Game.game_id)
        .where(
            TeamGameStats.team_id == team_id,
            Game.status == "final",
            Game.game_date < as_of,
            TeamGameStats.fga.is_not(None),  # skip games not yet backfilled with raw counts
        )
        .order_by(Game.game_date.desc())
        .limit(n)
    ).scalars().all()
    if not game_ids:
        return FourFactors(0, *([None] * 9))

    own = session.execute(
        select(TeamGameStats).where(
            TeamGameStats.game_id.in_(game_ids), TeamGameStats.team_id == team_id
        )
    ).scalars().all()
    opp = session.execute(
        select(TeamGameStats).where(
            TeamGameStats.game_id.in_(game_ids), TeamGameStats.team_id != team_id
        )
    ).scalars().all()

    def s(rows, attr: str) -> int:
        return sum(getattr(r, attr) or 0 for r in rows)

    t_fga, t_fgm, t_fg3m = s(own, "fga"), s(own, "fgm"), s(own, "fg3m")
    t_fta, t_oreb, t_dreb, t_tov = s(own, "fta"), s(own, "oreb"), s(own, "dreb"), s(own, "turnovers")
    o_fga, o_fgm, o_fg3m = s(opp, "fga"), s(opp, "fgm"), s(opp, "fg3m")
    o_fta, o_oreb, o_dreb, o_tov = s(opp, "fta"), s(opp, "oreb"), s(opp, "dreb"), s(opp, "turnovers")

    t_poss = _poss(t_fga, t_oreb, t_tov, t_fta)
    o_poss = _poss(o_fga, o_oreb, o_tov, o_fta)
    n_games = len(game_ids)

    return FourFactors(
        games=n_games,
        pace=round(t_poss / n_games, 1) if n_games else None,
        efg=_ratio(t_fgm + 0.5 * t_fg3m, t_fga),
        tov_pct=_ratio(t_tov, t_poss),
        oreb_pct=_ratio(t_oreb, t_oreb + o_dreb),
        ft_rate=_ratio(t_fta, t_fga),
        def_efg=_ratio(o_fgm + 0.5 * o_fg3m, o_fga),
        def_tov_pct=_ratio(o_tov, o_poss),
        dreb_pct=_ratio(t_dreb, t_dreb + o_oreb),
        def_ft_rate=_ratio(o_fta, o_fga),
    )
