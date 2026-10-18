"""Player-prop projections: a blind, statistical distribution for every player stat.

Where the game model asks an LLM for one win probability, props need thousands of
numbers a night, each with a full distribution (P(over 24.5) is useless without the
spread). So this is plain statistics, and — unlike the game model — it can be
backtested on a whole season for free.

The projection for one player-stat is

    mean = minutes × per-minute rate × opponent factor × teammate/news adjustment

* **Minutes** move fast (role changes, injuries around him), so they use a short
  exponential half-life. **Per-minute rates** are a slower-moving skill, so they use
  a long one. Splitting the two is the single biggest win over "last 10 average".
* **Opponent factor**: what this opponent allows in that stat relative to the league,
  shrunk by sample size.
* **Minutes budget**: a team has ~225 rotation minutes a night. Sum the projected
  minutes of the rotation players actually available; if that is short of the budget
  (players out), everyone else absorbs the slack, and if it is over (players back,
  offseason additions) everyone gives some up. Bench players move almost 1:1 with the
  ratio, starters much less. This is the largest single effect in the model — up to
  ±25–35% on a bench player's line — and it is why availability news matters so much.
* **Stat budget**: the same idea per stat. A team's points, rebounds and assists
  have to come from somebody, so when the available rotation's projections add up
  to less than the team usually produces (its scorer or playmaker is out), the rest
  pick it up, bench first. The best of the context effects: pooled error −0.4% on
  the held-out season, and it removed the model's overconfidence in exactly the
  games where teammates are out.
* **Game context**: a player back from a layoff is eased in (the largest miss
  before this: −5% to −18% of minutes after 2 to 9+ games out); the second night
  of a back-to-back costs ~2.5% of production; a heavy favourite's starters sit
  late; home court is worth ~1% of scoring. Each is small on its own. Measured on
  2024-25 and confirmed on 2025-26 — all four together took pooled error from
  2.721 to 2.705 with the rotation known.

Tested and rejected: a per-player with/without split for an absent teammate. Even
with perfect availability knowledge it made projections *worse* than no adjustment
(a handful of games per split is noise), where the pooled budgets validated
out-of-sample. Also tested: widening the distribution whenever minutes look
uncertain. Once the means above were fixed the spread was already honest (squared
standardized error ≈ 1.0 in every bucket), so φ is only widened for a thin log, a
layoff, or a debut.

Counting stats are over-dispersed, so the distribution is negative binomial with
variance = φ × mean, where φ blends the player's own variance/mean ratio with a
league prior per market. Everything is point-in-time (games strictly before `as_of`)
and never sees a betting line.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from nba_bot.data.nba_stats import is_preseason
from nba_bot.db.models import Game, PlayerGameStats, TeamGameStats

BASE_STATS = ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov")
# Shot counts ride along for fantasy scoring; they are not projected on their own.
SHOOTING = ("fgm", "fga", "ftm", "fta")

# Odds API market key -> the box-score stats it sums.
MARKETS: dict[str, tuple[str, ...]] = {
    "player_points": ("pts",),
    "player_rebounds": ("reb",),
    "player_assists": ("ast",),
    "player_threes": ("fg3m",),
    "player_steals": ("stl",),
    "player_blocks": ("blk",),
    "player_turnovers": ("tov",),
    "player_points_rebounds_assists": ("pts", "reb", "ast"),
    "player_points_rebounds": ("pts", "reb"),
    "player_points_assists": ("pts", "ast"),
    "player_rebounds_assists": ("reb", "ast"),
    "player_blocks_steals": ("blk", "stl"),
}

MARKET_LABELS: dict[str, str] = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PM",
    "player_steals": "STL",
    "player_blocks": "BLK",
    "player_turnovers": "TOV",
    "player_points_rebounds_assists": "PTS+REB+AST",
    "player_points_rebounds": "PTS+REB",
    "player_points_assists": "PTS+AST",
    "player_rebounds_assists": "REB+AST",
    "player_blocks_steals": "BLK+STL",
}

# League-wide predictive variance/mean per market: Σ(actual − projection)² / Σ projection
# over every projected player-game of 2025-26 (`nba-bot props-backtest` prints these).
# The prior a thin player sample is shrunk toward.
PHI_PRIOR: dict[str, float] = {
    "player_points": 3.1,
    "player_rebounds": 1.4,
    "player_assists": 1.3,
    "player_threes": 1.2,
    "player_steals": 1.1,
    "player_blocks": 1.15,
    "player_turnovers": 1.05,
    "player_points_rebounds_assists": 3.1,
    "player_points_rebounds": 3.1,
    "player_points_assists": 3.0,
    "player_rebounds_assists": 1.6,
    "player_blocks_steals": 1.15,
}

# Team box column feeding each stat's "allowed by opponent" factor.
_TEAM_COLUMN = {"pts": "points", "reb": "rebounds", "ast": "assists", "stl": "steals",
                "blk": "blocks", "fg3m": "fg3m", "tov": "turnovers"}

# Minutes budget. A player counts toward the rotation at ROTATION_MIN projected
# minutes. The league-median sum of those was ~225 in 2025-26 (181/257 at the
# 10th/90th percentile — teams are rarely "normal"); 234 is the target that leaves
# the adjusted projections unbiased, because capped starters absorb less of a
# shortfall than a squeeze takes away.
ROTATION_MIN = 12.0
ROTATION_BUDGET = 234.0
_BUDGET_CLIP = (0.75, 1.4)
BUDGET_UP_NOTE = "more minutes and shots with teammates out"
# Elasticity of a player's minutes / stat means to the budget ratio r, as
# multiplier = r ** beta, by projected minutes and by direction (r < 1 squeezes,
# r > 1 frees minutes). From the 2025-26 actual/projected ratios: bench players track
# the ratio almost 1:1, starters are capped by how many minutes a night exists —
# though their *usage* still rises when teammates sit. Interpolated between anchors.
_ROLE_ANCHORS = (19.5, 27.0, 33.0)            # bench, mid-rotation, starter
_BETA_MINUTES = {"down": (0.9, 0.6, 0.4), "up": (0.7, 0.25, 0.0)}
_BETA_STATS = {"down": (1.0, 0.8, 0.5), "up": (1.0, 0.45, 0.3)}

# Game context: small, role-dependent effects of the schedule and the matchup. Each
# was measured as actual ÷ projected on 2024-25 and held up on 2025-26; anchors are
# (bench, mid-rotation, starter) like the budget's.
_HOME_EDGE = {"pts": 0.008, "ast": 0.011, "fg3m": 0.017, "blk": 0.03}   # ± home / away
_B2B_MULT = 0.975                   # production on the second night of a back-to-back
HOME_COURT = 2.5                    # points, for the expected margin
_BLOWOUT_FROM = 6.0                 # expected margin (points) where minutes start to move
_BLOWOUT_PER_POINT = (0.004, 0.0, -0.005)   # bench plays more, a favourite's starters less
# A player back from a layoff is eased in: minutes multiplier by team games missed.
_LAYOFF = ((9, 0.83), (4, 0.92), (2, 0.955), (1, 0.985))
_DEBUT_MULT = 0.80                  # first game for a new team
# ...and he is harder to call: his outcomes spread wider than his history says.
_LAYOFF_SPREAD = 1.08               # two or more games missed
_DEBUT_SPREAD = 1.25
# So is a player with a short log: φ is widened by up to this much at `min_games`,
# fading to nothing at _THIN_LOG games.
_THIN_SPREAD = 0.15
_THIN_LOG = 20
# Stat budget: a team's production has to come from somebody. The share of a team's
# typical total that its rotation is projected for, and how much of a shortfall or
# surplus each role absorbs (multiplier = ratio ** elasticity).
_ROTATION_SHARE = {"pts": 0.97, "reb": 0.963, "ast": 0.98, "stl": 0.958, "blk": 0.955,
                   "fg3m": 0.976, "tov": 0.95}
_STAT_ELASTICITY = {"pts": (1.0, 0.5, 0.4), "reb": (0.7, 0.3, 0.1), "ast": (1.05, 0.6, 0.4),
                    "stl": (0.5, 0.3, 0.0), "blk": (0.4, 0.2, 0.25), "fg3m": (0.7, 0.3, 0.1),
                    "tov": (0.95, 0.6, 0.45)}
_STAT_RATIO_CLIP = (0.8, 1.3)
_FORM_MIN_GAMES = 5                 # team games this season before its totals are trusted


_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


def normalize_name(name: str) -> str:
    """Fold a player name so sportsbook, injury-report and box-score spellings match.

    Strips accents (Dončić -> doncic), suffixes (Jr./III), and punctuation.
    """
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = _SUFFIXES.sub("", s.lower())
    s = re.sub(r"[^a-z ]", "", s)
    return " ".join(s.split())


@dataclass(frozen=True)
class Params:
    # Half-lives and opp_weight were grid-searched on 2025-26 (`props-backtest`).
    hl_minutes: float = 4.0        # half-life (games) for the minutes projection
    hl_rate: float = 20.0          # half-life (games) for per-minute rates
    opp_weight: float = 1.0        # share of the opponent's allowed-vs-league gap applied
    opp_shrink_k: float = 10.0     # games of shrinkage on the opponent factor
    min_games: int = 8             # fewer prior games than this -> no projection
    log_window: int = 120          # games of history considered
    phi_window: int = 40           # games used for the player's own variance/mean
    phi_prior_k: float = 15.0      # pseudo-games of the league φ prior


DEFAULT_PARAMS = Params()


@dataclass(frozen=True)
class GameLine:
    """One player's box line in one game."""

    game_id: str
    game_date: date
    season: str
    team_id: int
    opp_id: int
    minutes: float
    pts: int = 0
    reb: int = 0
    ast: int = 0
    stl: int = 0
    blk: int = 0
    fg3m: int = 0
    tov: int = 0
    fgm: int = 0
    fga: int = 0
    ftm: int = 0
    fta: int = 0

    def value(self, market: str) -> int:
        return sum(getattr(self, s) for s in MARKETS[market])


@dataclass
class Adjustment:
    """Multiplicative tweaks layered on the base projection (teammates out, news)."""

    minutes_mult: float = 1.0
    stat_mults: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    spread_mult: float = 1.0        # widens the distribution (φ) without moving the mean

    def mult(self, stat: str) -> float:
        return self.stat_mults.get(stat, 1.0)


@dataclass(frozen=True)
class Context:
    """What is known about one team's game before tip, beyond its players' logs."""

    game_date: date
    team_id: int
    home: bool
    expected_margin: float = 0.0                    # points this team is expected to win by
    team_totals: dict[str, float] | None = None     # typical team total per stat vs. this opponent
    team_dates: tuple[date, ...] = ()               # the team's earlier games this season
    # The team's games before this one that haven't been played yet: non-zero when
    # projecting further ahead than its next game.
    unplayed_before: int = 0


@dataclass
class Projection:
    minutes: float
    means: dict[str, float]          # per base stat
    phis: dict[str, float]           # per market: variance / mean
    games: int
    notes: list[str] = field(default_factory=list)

    def mean(self, market: str) -> float:
        return sum(self.means[s] for s in MARKETS[market])

    def probs(self, market: str, line: float) -> tuple[float, float, float]:
        """(P(over), P(under), P(push)) for a line in this market."""
        return over_under_probs(self.mean(market), self.phis[market], line)


# --------------------------------------------------------------------------- #
# Distribution.
# --------------------------------------------------------------------------- #
def _pmf(mean: float, phi: float, kmax: int) -> list[float]:
    """P(X = 0..kmax) for a count with the given mean and variance/mean ratio.

    Negative binomial when over-dispersed (φ > 1), Poisson otherwise — a count can't
    be modelled as under-dispersed here, and Poisson is the conservative floor.
    """
    if mean <= 0:
        return [1.0] + [0.0] * kmax
    out = []
    if phi <= 1.02:
        log_p = -mean
        for k in range(kmax + 1):
            if k:
                log_p += math.log(mean / k)
            out.append(math.exp(log_p))
        return out
    r = mean / (phi - 1)
    p = 1 / phi
    log_p = r * math.log(p)
    for k in range(kmax + 1):
        if k:
            log_p += math.log((k - 1 + r) / k * (1 - p))
        out.append(math.exp(log_p))
    return out


def over_under_probs(mean: float, phi: float, line: float) -> tuple[float, float, float]:
    """(P(X > line), P(X < line), P(X == line)). Push is non-zero only on whole lines."""
    whole = float(line).is_integer()
    top = int(line) if whole else math.floor(line)
    pmf = _pmf(mean, phi, max(top, 0))
    if whole:
        push = pmf[top] if top >= 0 else 0.0
        under = sum(pmf[:top]) if top > 0 else 0.0
    else:
        push = 0.0
        under = sum(pmf[: top + 1]) if top >= 0 else 0.0
    return max(0.0, 1.0 - under - push), under, push


# Single-stat guidance. The bot may only have a line for a combo market (one market
# is all the free odds plan buys), but the projection covers every stat. For each of
# these it reports the lines at which the model alone makes the over, or the under,
# at least GUIDE_PROB likely — for the bettor to compare with what their book hangs.
GUIDE_STATS = (("pts", "player_points"), ("reb", "player_rebounds"), ("ast", "player_assists"))
GUIDE_PROB = 0.57   # comfortably above the ~53.5% a -115 price needs to break even


def stat_guide(proj: Projection, bet_market: str) -> dict[str, dict]:
    """{stat: {proj, over_at, under_at}} — take the over if the book's line is
    `over_at` or lower, the under if it is `under_at` or higher. A bound is None
    when no half-point line clears GUIDE_PROB. Raw model: with no line to compare
    against there is no market blend and no second book behind these."""
    guide = {}
    for stat, market in GUIDE_STATS:
        if market == bet_market:
            continue
        mean, phi = proj.means[stat], proj.phis[market]
        lines = [k + 0.5 for k in range(80)]
        overs = [ln for ln in lines if over_under_probs(mean, phi, ln)[0] >= GUIDE_PROB]
        unders = [ln for ln in lines if over_under_probs(mean, phi, ln)[1] >= GUIDE_PROB]
        guide[stat] = {"proj": round(mean, 1), "over_at": max(overs, default=None),
                       "under_at": min(unders, default=None)}
    return guide


# --------------------------------------------------------------------------- #
# Projection (pure).
# --------------------------------------------------------------------------- #
def _weights(n: int, half_life: float) -> list[float]:
    """Exponential recency weights for a chronological log (last = most recent)."""
    return [0.5 ** ((n - 1 - i) / half_life) for i in range(n)]


def shooting_means(log: list[GameLine], pts_mean: float,
                   params: Params = DEFAULT_PARAMS) -> dict[str, float]:
    """Projected makes and attempts, for fantasy scoring: the player's own shots per
    point (recency-weighted like his rates) times his projected points."""
    log = log[-params.log_window:]
    w = _weights(len(log), params.hl_rate)
    pts = sum(wi * g.pts for wi, g in zip(w, log))
    if pts <= 0:
        return dict.fromkeys(SHOOTING, 0.0)
    return {s: pts_mean * sum(wi * getattr(g, s) for wi, g in zip(w, log)) / pts for s in SHOOTING}


def _phi(values: list[int], prior: float, prior_k: float) -> float:
    """Player variance/mean, shrunk toward the league prior by sample size."""
    n = len(values)
    if n < 2:
        return prior
    m = sum(values) / n
    if m <= 0:
        return prior
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return max(1.0, (n * (var / m) + prior_k * prior) / (n + prior_k))


def project(
    log: list[GameLine],
    *,
    opp_factors: dict[str, float] | None = None,
    adjustment: Adjustment | None = None,
    params: Params = DEFAULT_PARAMS,
) -> Projection | None:
    """Project one player's next game from his chronological log (prior games only)."""
    if len(log) < params.min_games:
        return None
    log = log[-params.log_window:]
    n = len(log)
    adj = adjustment or Adjustment()
    opp = opp_factors or {}

    wm = _weights(n, params.hl_minutes)
    minutes = sum(w * g.minutes for w, g in zip(wm, log)) / sum(wm)

    wr = _weights(n, params.hl_rate)
    weighted_min = sum(w * g.minutes for w, g in zip(wr, log))
    if weighted_min <= 0:
        return None

    means: dict[str, float] = {}
    for s in BASE_STATS:
        rate = sum(w * getattr(g, s) for w, g in zip(wr, log)) / weighted_min
        means[s] = minutes * rate * opp.get(s, 1.0) * adj.mult(s)

    recent = log[-params.phi_window:]
    spread = adj.spread_mult * (1 + _THIN_SPREAD * max(0.0, 1 - n / _THIN_LOG))
    phis = {
        m: spread * _phi([g.value(m) for g in recent], PHI_PRIOR[m], params.phi_prior_k)
        for m in MARKETS
    }
    return Projection(
        minutes=round(minutes * adj.minutes_mult, 1),
        means=means,
        phis=phis,
        games=n,
        notes=list(adj.notes),
    )


def combine_adjustments(*adjs: Adjustment | None) -> Adjustment:
    """Multiply adjustments together, clipping the total so stacked signals can't
    run away (a projection that moves >50% on context alone is a data problem)."""
    out = Adjustment()
    for a in adjs:
        if a is None:
            continue
        out.minutes_mult *= a.minutes_mult
        for s, m in a.stat_mults.items():
            out.stat_mults[s] = out.stat_mults.get(s, 1.0) * m
        out.notes.extend(a.notes)
        out.spread_mult *= a.spread_mult
    out.minutes_mult = min(max(out.minutes_mult, 0.5), 1.5)
    out.stat_mults = {s: min(max(m, 0.5), 1.5) for s, m in out.stat_mults.items()}
    return out


# --------------------------------------------------------------------------- #
# Minutes budget.
# --------------------------------------------------------------------------- #
def budget_ratio(rotation_minutes: list[float]) -> float:
    """Budget ÷ the projected minutes of the available rotation, clipped.

    >1 means minutes are going spare (players out) and the rest absorb them; <1
    means more rotation players than minutes and everyone is squeezed.
    """
    total = sum(m for m in rotation_minutes if m >= ROTATION_MIN)
    if total <= 0:
        return 1.0
    return min(max(ROTATION_BUDGET / total, _BUDGET_CLIP[0]), _BUDGET_CLIP[1])


def _interp(minutes: float, values: tuple[float, float, float]) -> float:
    lo, mid, hi = _ROLE_ANCHORS
    if minutes <= lo:
        return values[0]
    if minutes >= hi:
        return values[2]
    if minutes <= mid:
        return values[0] + (values[1] - values[0]) * (minutes - lo) / (mid - lo)
    return values[1] + (values[2] - values[1]) * (minutes - mid) / (hi - mid)


def budget_adjustment(projected_minutes: float, ratio: float) -> Adjustment:
    """Role-dependent share of the budget ratio one player absorbs."""
    if abs(ratio - 1) < 0.02:
        return Adjustment()
    direction = "up" if ratio > 1 else "down"
    minutes_mult = ratio ** _interp(projected_minutes, _BETA_MINUTES[direction])
    stat_mult = ratio ** _interp(projected_minutes, _BETA_STATS[direction])
    return Adjustment(
        minutes_mult=minutes_mult,
        stat_mults=dict.fromkeys(BASE_STATS, stat_mult),
        notes=[BUDGET_UP_NOTE if ratio > 1 else "fewer minutes in a full rotation"]
        if abs(ratio - 1) >= 0.05 else [],
    )


def layoff_adjustment(log: list[GameLine], ctx: Context) -> Adjustment | None:
    """A player back from a layoff, or new to the team, is eased in. Minutes and
    stats scale together: his per-minute rates hold up, his minutes don't.

    Only for the team's next game: when there are games in between, his debut or
    return comes in one of those, not this one."""
    if ctx.unplayed_before:
        return None
    last = log[-1]
    if last.team_id != ctx.team_id:
        mult, spread, note = _DEBUT_MULT, _DEBUT_SPREAD, "first game for a new team"
    else:
        missed = sum(d > last.game_date for d in ctx.team_dates)
        mult = next((m for n, m in _LAYOFF if missed >= n), 1.0)
        spread = _LAYOFF_SPREAD if missed >= 2 else 1.0
        note = f"eased back after missing {missed} game{'s' if missed != 1 else ''}"
    if mult == 1.0:
        return None
    return Adjustment(mult, dict.fromkeys(BASE_STATS, mult), [note], spread)


def context_adjustment(projected_minutes: float, ctx: Context) -> Adjustment:
    """Home court, a back-to-back, and a likely blowout, by role."""
    role, tired, notes = 1.0, 1.0, []
    if ctx.team_dates and (ctx.game_date - ctx.team_dates[-1]).days == 1:
        tired = _B2B_MULT
        notes.append("second night of a back-to-back")
    over = min(abs(ctx.expected_margin) - _BLOWOUT_FROM, 8.0)
    if over > 0:
        per_point = _interp(projected_minutes, _BLOWOUT_PER_POINT)
        if per_point > 0 or ctx.expected_margin > 0:   # an underdog's starters keep playing
            role *= 1 + per_point * over
            if per_point * over < -0.015:
                notes.append("heavy favourite: starters may sit late")
    home = 1 if ctx.home else -1
    return Adjustment(
        minutes_mult=role,
        stat_mults={s: role * tired * (1 + home * _HOME_EDGE.get(s, 0.0)) for s in BASE_STATS},
        notes=notes,
    )


def _recent_average(log: list[GameLine], attr: str) -> float:
    recent = log[-5:]
    return sum(getattr(g, attr) for g in recent) / len(recent) if recent else 0.0


def _share_stats(
    final: dict[int, Projection], rotation: dict[int, Projection],
    unprojected: list[list[GameLine]], team_totals: dict[str, float],
) -> None:
    """Stat budget: scale each stat toward what the team typically produces.

    When the rotation's projections add up to less than the team's usual total
    (the players who normally supply it are out), the rest pick it up — bench
    players nearly in full, starters about half. In place.
    """
    extras = [lg for lg in unprojected if _recent_average(lg, "minutes") >= ROTATION_MIN]
    for s in BASE_STATS:
        total = sum(p.means[s] for p in rotation.values() if p.minutes >= ROTATION_MIN)
        total += sum(_recent_average(lg, s) for lg in extras)
        if total <= 0:
            continue
        ratio = _ROTATION_SHARE[s] * team_totals[s] / total
        ratio = min(max(ratio, _STAT_RATIO_CLIP[0]), _STAT_RATIO_CLIP[1])
        for p in final.values():
            p.means[s] *= ratio ** _interp(p.minutes, _STAT_ELASTICITY[s])


def project_team(
    logs: dict[int, list[GameLine]],
    *,
    opp_factors: dict[str, float] | None = None,
    extra: dict[int, Adjustment] | None = None,
    context: Context | None = None,
    use_budget: bool = True,
    budget_from: dict[int, list[GameLine]] | None = None,
    fallback_minutes: dict[int, float] | None = None,
    params: Params = DEFAULT_PARAMS,
) -> dict[int, Projection]:
    """Project every available player on one team, sharing one minutes budget and
    one stat budget.

    `logs` holds each available player's prior games. `extra` carries per-player
    news adjustments; they apply before the budget so a player on a minutes limit
    frees minutes for the rest. `context` adds what is known about the game itself
    (venue, rest, expected margin, layoffs, the team's typical totals). `budget_from`
    overrides which players define the budgets (the backtest's "assume last game's
    rotation" mode); by default it is the players being projected.

    A player too new to project still takes minutes from everyone else, so he counts
    toward the budget at his short-sample average, or at `fallback_minutes` (e.g.
    preseason) when he has no games at all. Leaving rookies out makes a rotation look
    short-handed and inflates every teammate.
    """
    fallback_minutes = fallback_minutes or {}
    pre = dict(extra or {})
    if context is not None:
        for pid, log in logs.items():
            news = pre.get(pid)
            if not log or (news is not None and news.minutes_mult < 1):
                continue   # the news already says how limited he is
            layoff = layoff_adjustment(log, context)
            if layoff is not None:
                pre[pid] = combine_adjustments(news, layoff)

    def first_pass(source: dict[int, list[GameLine]]) -> dict[int, Projection]:
        out = {}
        for pid, log in source.items():
            proj = project(log, opp_factors=opp_factors, adjustment=pre.get(pid), params=params)
            if proj is not None:
                out[pid] = proj
        return out

    base = first_pass(logs)
    source = logs if budget_from is None else budget_from
    pool = base if budget_from is None else first_pass(budget_from)
    ratio = 1.0
    if use_budget:
        pool_minutes = [p.minutes for p in pool.values()]
        pool_minutes += [
            _recent_average(log, "minutes") if log else fallback_minutes.get(pid, 0.0)
            for pid, log in source.items() if pid not in pool
        ]
        ratio = budget_ratio(pool_minutes)

    def finalize(pid: int, log: list[GameLine], proj: Projection) -> Projection:
        limited = pre.get(pid)
        # A player already held back doesn't also soak up spare minutes.
        capped = limited is not None and limited.minutes_mult < 1 and ratio > 1
        return project(
            log, opp_factors=opp_factors, params=params,
            adjustment=combine_adjustments(
                limited,
                None if capped else budget_adjustment(proj.minutes, ratio),
                context_adjustment(proj.minutes, context) if context is not None else None,
            ),
        )

    final = {pid: finalize(pid, logs[pid], proj) for pid, proj in base.items()}
    if use_budget and context is not None and context.team_totals:
        rotation = final if budget_from is None else {
            pid: finalize(pid, budget_from[pid], proj) for pid, proj in pool.items()
        }
        _share_stats(final, rotation, [lg for pid, lg in source.items() if pid not in pool],
                     context.team_totals)
    return final


# --------------------------------------------------------------------------- #
# In-memory snapshot of the box-score history (shared by live + backtest).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GameInfo:
    game_id: str
    game_date: date
    season: str
    home_id: int
    away_id: int


@dataclass
class PropsData:
    """Everything the projection needs, loaded once. ~27k rows a season, so holding
    it in memory keeps the live path and the backtest on identical code."""

    logs: dict[int, list[GameLine]]                 # player -> chronological real games played
    names: dict[int, str]
    current_team: dict[int, int]                    # latest team, preseason included
    last_seen: dict[int, date]                      # latest appearance, preseason included
    team_games: dict[int, list[GameInfo]]           # team -> chronological real games
    team_box: dict[tuple[str, int], dict[str, float]]  # (game, team) -> stat totals
    # Average preseason minutes in the newest season: the only read on the role of a
    # player with no regular-season history (rookies).
    exhibition_minutes: dict[int, float] = field(default_factory=dict)
    _name_index: dict[str, list[int]] | None = field(default=None, repr=False)

    def name_index(self) -> dict[str, list[int]]:
        """Normalized name -> player ids, for matching sportsbook and injury-report
        names (two players can share one). Built on first use."""
        if self._name_index is None:
            index: dict[str, list[int]] = defaultdict(list)
            for pid, name in self.names.items():
                index[normalize_name(name)].append(pid)
            self._name_index = dict(index)
        return self._name_index

    def log_before(self, player_id: int, as_of: date) -> list[GameLine]:
        return [g for g in self.logs.get(player_id, []) if g.game_date < as_of]

    def opponent_factors(
        self, as_of: date, season: str, params: Params = DEFAULT_PARAMS
    ) -> dict[int, dict[str, float]]:
        """team -> {stat: multiplier} for what that team allows vs. the league so far
        this season. 1.0 everywhere before any games are played."""
        allowed: dict[int, dict[str, float]] = defaultdict(lambda: dict.fromkeys(BASE_STATS, 0.0))
        counts: dict[int, int] = defaultdict(int)
        seen: set[str] = set()
        for games in self.team_games.values():
            for g in games:
                if g.season != season or g.game_date >= as_of or g.game_id in seen:
                    continue
                seen.add(g.game_id)
                home, away = self.team_box.get((g.game_id, g.home_id)), self.team_box.get(
                    (g.game_id, g.away_id)
                )
                if home is None or away is None:
                    continue
                for team, opp_box in ((g.home_id, away), (g.away_id, home)):
                    for s in BASE_STATS:
                        allowed[team][s] += opp_box[s]
                    counts[team] += 1

        total_n = sum(counts.values())
        if not total_n:
            return {}
        league = {s: sum(a[s] for a in allowed.values()) / total_n for s in BASE_STATS}
        out: dict[int, dict[str, float]] = {}
        for team, n in counts.items():
            shrink = n / (n + params.opp_shrink_k)
            out[team] = {
                s: 1 + params.opp_weight * shrink * (allowed[team][s] / n / league[s] - 1)
                if league[s] > 0 else 1.0
                for s in BASE_STATS
            }
        return out


@dataclass(frozen=True)
class TeamForm:
    """One team's season so far, as of a date."""

    margin: float                   # average point margin, shrunk toward 0
    totals: dict[str, float] | None  # per-game team totals; None until there are enough games
    dates: tuple[date, ...]


def team_form(data: PropsData, as_of: date, season: str) -> dict[int, TeamForm]:
    """Every team's form from this season's games before `as_of`."""
    out: dict[int, TeamForm] = {}
    for team, games in data.team_games.items():
        prior = [g for g in games if g.season == season and g.game_date < as_of]
        margin, sums, n = 0.0, dict.fromkeys(BASE_STATS, 0.0), 0
        for g in prior:
            mine = data.team_box.get((g.game_id, team))
            theirs = data.team_box.get((g.game_id, g.away_id if team == g.home_id else g.home_id))
            if mine is None or theirs is None:
                continue
            margin += mine["pts"] - theirs["pts"]
            for s in BASE_STATS:
                sums[s] += mine[s]
            n += 1
        out[team] = TeamForm(
            margin=margin / (n + 10),
            totals={s: v / n for s, v in sums.items()} if n >= _FORM_MIN_GAMES else None,
            dates=tuple(g.game_date for g in prior),
        )
    return out


def game_context(
    form: dict[int, TeamForm], team_id: int, opp_id: int, home: bool, game_date: date,
    opp_factors: dict[str, float] | None, unplayed_before: int = 0,
) -> Context:
    """The pre-game context for one team. Blind to the betting line: the expected
    margin comes from point differentials, not from a spread."""
    mine, theirs = form.get(team_id), form.get(opp_id)
    margin = (mine.margin if mine else 0.0) - (theirs.margin if theirs else 0.0)
    totals = None
    if mine is not None and mine.totals is not None:
        totals = {s: v * (opp_factors or {}).get(s, 1.0) for s, v in mine.totals.items()}
    return Context(
        game_date=game_date, team_id=team_id, home=home,
        expected_margin=margin + (HOME_COURT if home else -HOME_COURT),
        team_totals=totals, team_dates=mine.dates if mine else (),
        unplayed_before=unplayed_before,
    )


def load_props_data(session: Session) -> PropsData:
    """Load every stored box score into a PropsData snapshot."""
    games = session.execute(
        select(Game.game_id, Game.game_date, Game.season, Game.home_team_id, Game.away_team_id)
        .where(Game.status == "final")
        .order_by(Game.game_date, Game.game_id)
    ).all()
    info = {g.game_id: GameInfo(g.game_id, g.game_date, g.season, g.home_team_id, g.away_team_id)
            for g in games}

    team_games: dict[int, list[GameInfo]] = defaultdict(list)
    for g in info.values():
        if is_preseason(g.game_id):
            continue
        team_games[g.home_id].append(g)
        team_games[g.away_id].append(g)

    team_box: dict[tuple[str, int], dict[str, float]] = {}
    for row in session.execute(select(TeamGameStats).where(TeamGameStats.fga.is_not(None))).scalars():
        team_box[(row.game_id, row.team_id)] = {
            s: float(getattr(row, col) or 0) for s, col in _TEAM_COLUMN.items()
        }

    logs: dict[int, list[GameLine]] = defaultdict(list)
    names: dict[int, str] = {}
    latest: dict[int, tuple[date, int]] = {}
    newest_season = max((g.season for g in info.values()), default="")
    exhibition: dict[int, list[float]] = defaultdict(list)
    for r in session.execute(select(PlayerGameStats)).scalars():
        g = info.get(r.game_id)
        if g is None:
            continue
        names[r.player_id] = r.player_name
        minutes = float(r.minutes or 0)
        if minutes <= 0:
            continue
        # Preseason says where a player is *now* (116 changed teams this offseason),
        # but its minutes and usage are exhibition noise, so it never enters a log.
        if r.player_id not in latest or g.game_date >= latest[r.player_id][0]:
            latest[r.player_id] = (g.game_date, r.team_id)
        if is_preseason(r.game_id):
            if g.season == newest_season:
                exhibition[r.player_id].append(minutes)
            continue
        logs[r.player_id].append(
            GameLine(
                game_id=r.game_id, game_date=g.game_date, season=g.season, team_id=r.team_id,
                opp_id=g.away_id if r.team_id == g.home_id else g.home_id, minutes=minutes,
                pts=r.points or 0, reb=r.rebounds or 0, ast=r.assists or 0, stl=r.steals or 0,
                blk=r.blocks or 0, fg3m=r.fg3m or 0, tov=r.turnovers or 0,
                fgm=r.fgm or 0, fga=r.fga or 0, ftm=r.ftm or 0, fta=r.fta or 0,
            )
        )
    for log in logs.values():
        log.sort(key=lambda g: (g.game_date, g.game_id))

    return PropsData(
        logs=dict(logs),
        names=names,
        current_team={pid: team for pid, (_d, team) in latest.items()},
        last_seen={pid: seen for pid, (seen, _team) in latest.items()},
        team_games=dict(team_games),
        team_box=team_box,
        exhibition_minutes={pid: sum(m) / len(m) for pid, m in exhibition.items()},
    )
