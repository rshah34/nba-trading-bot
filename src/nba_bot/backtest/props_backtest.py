"""Backtest the prop projection model over a completed season. No API spend.

What this can and can't tell you:

* It CAN measure projection accuracy (MAE vs. naive averages) and whether the
  predicted distributions are honest (when the model says 60% over, does it go over
  60% of the time?). Those are the properties sizing a bet depends on.
* It CANNOT measure edge against a sportsbook — no historical prop lines exist for
  free. The "line" used here is the player's own trailing-10 average, which is a far
  softer opponent than a real book. Real edge is only visible live, through CLV.

The minutes and stat budgets need to know who is available, which no historical
injury report can tell us. `availability` brackets it:

* `none`      — no availability knowledge at all (the floor). Schedule, venue and
                layoff effects still apply; they don't depend on tonight's rotation.
* `last_game` — assume the rotation that played the team's previous game. Knowable
                before tip; what a pipeline with a stale injury report achieves.
* `oracle`    — the rotation that actually played tonight. Mildly look-ahead, so a
                ceiling, never a track record. Live results land between
                `last_game` and `oracle`, depending on how good the news feed is.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date

from nba_bot.features.props import (
    DEFAULT_PARAMS,
    MARKETS,
    Context,
    GameInfo,
    GameLine,
    Params,
    Projection,
    PropsData,
    game_context,
    over_under_probs,
    project_team,
    team_form,
)

_BINS = [(0.0, 0.3), (0.3, 0.4), (0.4, 0.45), (0.45, 0.5), (0.5, 0.55), (0.55, 0.6),
         (0.6, 0.7), (0.7, 1.01)]


@dataclass
class MarketStats:
    n: int = 0
    abs_model: float = 0.0
    abs_l10: float = 0.0
    abs_season: float = 0.0
    bias: float = 0.0
    sq_err: float = 0.0
    sum_mean: float = 0.0
    brier: float = 0.0
    brier_naive: float = 0.0
    bins: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0.0, 0]))

    def summary(self) -> dict:
        n = self.n or 1
        return {
            "n": self.n,
            "mae_model": round(self.abs_model / n, 3),
            "mae_l10": round(self.abs_l10 / n, 3),
            "mae_season": round(self.abs_season / n, 3),
            "bias": round(self.bias / n, 3),
            # Empirical predictive variance/mean — the value PHI_PRIOR should carry.
            "phi": round(self.sq_err / self.sum_mean, 2) if self.sum_mean else None,
            "brier": round(self.brier / n, 4),
            "brier_naive": round(self.brier_naive / n, 4),
        }


def calibration(stats: dict[str, MarketStats]) -> list[dict]:
    """Pooled reliability table across markets: predicted P(over) vs. how often it hit."""
    pooled: dict[tuple[float, float], list] = {b: [0, 0.0, 0] for b in _BINS}
    for st in stats.values():
        for b, (n, psum, hits) in st.bins.items():
            pooled[b][0] += n
            pooled[b][1] += psum
            pooled[b][2] += hits
    return [
        {"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": n,
         "predicted": round(psum / n, 3), "actual": round(hits / n, 3)}
        for (lo, hi), (n, psum, hits) in pooled.items() if n
    ]


@dataclass
class TeamGame:
    """One team's side of one game, projected as the model would have before tip."""

    game: GameInfo
    team_id: int
    opp_id: int
    context: Context
    opp_factors: dict[str, float] | None
    logs: dict[int, list[GameLine]]       # each player's games before this one
    actual: dict[int, GameLine]           # what each player did in it
    projections: dict[int, Projection]
    budget_from: dict[int, list[GameLine]] | None


def replay(
    data: PropsData,
    season: str,
    *,
    params: Params = DEFAULT_PARAMS,
    use_opponent: bool = True,
    availability: str = "oracle",
    start: date | None = None,
) -> Iterator[TeamGame]:
    """Project both teams in every game of a season, in date order, point-in-time."""
    # (game, team) -> the lines of the players who actually played.
    played: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    for pid, log in data.logs.items():
        for idx, g in enumerate(log):
            if g.season == season:
                played[(g.game_id, g.team_id)].append((pid, idx))

    games = sorted(
        {g.game_id: g for gs in data.team_games.values() for g in gs if g.season == season}.values(),
        key=lambda g: (g.game_date, g.game_id),
    )
    factors: dict[int, dict[str, float]] = {}
    form: dict = {}
    factors_date: date | None = None
    for game in games:
        if start and game.game_date < start:
            continue
        if game.game_date != factors_date:
            factors = data.opponent_factors(game.game_date, season, params) if use_opponent else {}
            form = team_form(data, game.game_date, season)
            factors_date = game.game_date

        for team_id, opp_id in ((game.home_id, game.away_id), (game.away_id, game.home_id)):
            tonight = played.get((game.game_id, team_id), [])
            logs = {pid: data.logs[pid][:idx] for pid, idx in tonight}
            budget_from = None
            if availability == "last_game":
                previous = [g for g in data.team_games.get(team_id, [])
                            if g.season == season and g.game_date < game.game_date][-1:]
                budget_from = {
                    pid: data.log_before(pid, game.game_date)
                    for g in previous for pid, _ in played.get((g.game_id, team_id), [])
                } or None
            context = game_context(form, team_id, opp_id, team_id == game.home_id,
                                   game.game_date, factors.get(opp_id))
            projections = project_team(
                logs, opp_factors=factors.get(opp_id), params=params, context=context,
                use_budget=availability != "none", budget_from=budget_from, season=season,
            )
            yield TeamGame(game, team_id, opp_id, context, factors.get(opp_id), logs,
                           {pid: data.logs[pid][idx] for pid, idx in tonight}, projections,
                           budget_from)


def run_backtest(
    data: PropsData,
    season: str,
    *,
    params: Params = DEFAULT_PARAMS,
    use_opponent: bool = True,
    availability: str = "oracle",
    min_minutes: float = 15.0,
    start: date | None = None,
) -> dict[str, MarketStats]:
    """Project every rotation player in every game of a season and score it."""
    stats: dict[str, MarketStats] = {m: MarketStats() for m in MARKETS}
    for tg in replay(data, season, params=params, use_opponent=use_opponent,
                     availability=availability, start=start):
        for pid, proj in tg.projections.items():
            if pid in tg.actual and proj.minutes >= min_minutes:
                _score(stats, proj, tg.logs[pid], tg.actual[pid], season)
    return stats


def _score(stats, proj, prior: list[GameLine], actual_line: GameLine, season: str) -> None:
    last10 = prior[-10:]
    this_season = [g for g in prior if g.season == season] or prior
    for market in MARKETS:
        st = stats[market]
        actual = actual_line.value(market)
        mean = proj.mean(market)
        l10 = sum(g.value(market) for g in last10) / len(last10)
        season_avg = sum(g.value(market) for g in this_season) / len(this_season)

        st.n += 1
        st.abs_model += abs(actual - mean)
        st.abs_l10 += abs(actual - l10)
        st.abs_season += abs(actual - season_avg)
        st.bias += mean - actual
        st.sq_err += (actual - mean) ** 2
        st.sum_mean += mean

        line = math.floor(l10) + 0.5
        over = actual > line
        p = min(max(proj.probs(market, line)[0], 1e-4), 1 - 1e-4)
        p_naive = min(max(over_under_probs(l10, proj.phis[market], line)[0], 1e-4), 1 - 1e-4)
        st.brier += (p - over) ** 2
        st.brier_naive += (p_naive - over) ** 2
        for lo, hi in _BINS:
            if lo <= p < hi:
                b = st.bins[(lo, hi)]
                b[0] += 1
                b[1] += p
                b[2] += int(over)
                break
