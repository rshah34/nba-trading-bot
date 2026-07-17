"""On-off impact: how a team actually performs with vs. without a given player.

Quantifies a player's value from **with/without splits** rather than reputation —
the signal the market is slowest on for non-star absences. Point-in-time: only
games strictly before `as_of` are used.

Three things make this honest rather than misleading:

1. **Roster span.** A player traded in midseason has no rows for his new team's
   earlier games; naively those look like "the team without him". We only count
   games from his first appearance for that team onward.
2. **Regime.** If a player has been out for weeks, the team's *recent form is
   already the without-him form* — surfacing an on-off delta on top of it makes
   the model penalize the team twice. Every result is labelled `newly_out`
   (actionable — form overstates the team), `long_term_out` (already priced in),
   `returning` (form understates the team), or `available`.
3. **Uncertainty.** Game-level splits are noisy and confounded (injuries cluster;
   opponent quality differs between splits). Real on-off needs possession-level
   lineup data we don't have. So we require a minimum sample, **shrink** the delta
   toward zero by sample size, and always surface N and the average opponent
   quality faced in each split so the model can discount accordingly.

`played` means a box-score row with minutes > 0 — a 0:00 row is stored, so
row-existence alone would wrongly count as an appearance.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nba_bot.db.models import Game, PlayerGameStats, TeamGameStats
from nba_bot.features.four_factors import _poss

# Below this many games without the player, the split is too noisy to report.
_MIN_GAMES_WITHOUT = 3
# ...and below this many games WITH him there's no meaningful baseline to compare to
# (a 1-game "with" net rating is one game's noise, not a player's value).
_MIN_GAMES_WITH = 5
# Deep-bench absences don't move a line; require a real rotation role.
_MIN_MPG = 15.0
# Shrinkage strength: delta * n / (n + K). K≈5 keeps a 3-game sample heavily discounted.
_SHRINK_K = 5
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


@dataclass
class OnOffImpact:
    player: str
    regime: str                      # newly_out | long_term_out | returning | available
    mpg: float | None                # minutes per game when he plays (role size)
    games_with: int
    games_without: int
    net_rtg_with: float | None       # points per 100 possessions differential
    net_rtg_without: float | None
    delta_shrunk: float | None       # (with − without), regularized by sample size
    opp_quality_with: float | None   # avg opponent season margin faced (confounder context)
    opp_quality_without: float | None


def normalize_name(name: str) -> str:
    """Fold a player name for matching ESPN injury reports to box-score names.

    Strips accents (Dončić -> doncic), suffixes (Jr./III), and punctuation, so the
    two sources join reliably.
    """
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = _SUFFIXES.sub("", s.lower())
    s = re.sub(r"[^a-z ]", "", s)
    return " ".join(s.split())


def _team_games(session: Session, team_id: int, season: str, as_of: date) -> list[tuple[str, date, int]]:
    """(game_id, game_date, opponent_id) for a team's final games before as_of."""
    rows = session.execute(
        select(Game.game_id, Game.game_date, Game.home_team_id, Game.away_team_id)
        .where(
            Game.season == season,
            Game.status == "final",
            Game.game_date < as_of,
            or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
        )
        .order_by(Game.game_date)
    ).all()
    return [(gid, d, away if home == team_id else home) for gid, d, home, away in rows]


def _appearances(session: Session, team_id: int, player_id: int, game_ids: list[str]) -> set[str]:
    """Game ids the player actually played in (minutes > 0)."""
    if not game_ids:
        return set()
    return set(
        session.execute(
            select(PlayerGameStats.game_id).where(
                PlayerGameStats.player_id == player_id,
                PlayerGameStats.team_id == team_id,
                PlayerGameStats.game_id.in_(game_ids),
                PlayerGameStats.minutes > 0,
            )
        ).scalars().all()
    )


def _avg_minutes(session: Session, team_id: int, player_id: int, game_ids: list[str]) -> float | None:
    """Minutes per game in the games he played — his role size."""
    if not game_ids:
        return None
    val = session.execute(
        select(func.avg(PlayerGameStats.minutes)).where(
            PlayerGameStats.player_id == player_id,
            PlayerGameStats.team_id == team_id,
            PlayerGameStats.game_id.in_(game_ids),
            PlayerGameStats.minutes > 0,
        )
    ).scalar_one_or_none()
    return round(float(val), 1) if val is not None else None


def _net_rating(session: Session, team_id: int, game_ids: list[str]) -> tuple[float | None, int]:
    """Team net rating (pts per 100 poss differential) over a set of games.

    Aggregated as (Σ team pts − Σ opp pts) / Σ possessions — the correct way to
    combine per-game rates. Games without raw counts (pre-migration-004, not yet
    backfilled) are skipped. Returns (net_rating, n_games_used).
    """
    if not game_ids:
        return None, 0
    own = session.execute(
        select(TeamGameStats).where(
            TeamGameStats.game_id.in_(game_ids),
            TeamGameStats.team_id == team_id,
            TeamGameStats.fga.is_not(None),
        )
    ).scalars().all()
    if not own:
        return None, 0

    ids = [r.game_id for r in own]
    opp_rows = session.execute(
        select(TeamGameStats).where(
            TeamGameStats.game_id.in_(ids),
            TeamGameStats.team_id != team_id,
            TeamGameStats.fga.is_not(None),
        )
    ).scalars().all()
    opp_by_game = {r.game_id: r for r in opp_rows}

    t_pts = o_pts = poss = 0.0
    n = 0
    for r in own:
        o = opp_by_game.get(r.game_id)
        if o is None:
            continue
        tp = _poss(r.fga or 0, r.oreb or 0, r.turnovers or 0, r.fta or 0)
        op = _poss(o.fga or 0, o.oreb or 0, o.turnovers or 0, o.fta or 0)
        poss += (tp + op) / 2  # the two estimates should be near-equal; average is standard
        t_pts += r.points or 0
        o_pts += o.points or 0
        n += 1

    if n == 0 or poss <= 0:
        return None, n
    return round((t_pts - o_pts) / poss * 100, 1), n


def team_avg_margin_map(session: Session, season: str, as_of: date) -> dict[int, float]:
    """team_id -> season-to-date average margin before as_of. Used as a cheap
    opponent-strength proxy so a split's schedule luck is visible."""
    rows = session.execute(
        select(TeamGameStats.team_id, func.avg(TeamGameStats.plus_minus))
        .join(Game, TeamGameStats.game_id == Game.game_id)
        .where(
            Game.season == season,
            Game.status == "final",
            Game.game_date < as_of,
            TeamGameStats.plus_minus.is_not(None),
        )
        .group_by(TeamGameStats.team_id)
    ).all()
    return {tid: float(m) for tid, m in rows if m is not None}


def _avg_opp_quality(opp_ids: list[int], margins: dict[int, float]) -> float | None:
    vals = [margins[o] for o in opp_ids if o in margins]
    return round(sum(vals) / len(vals), 1) if vals else None


def _classify_regime(
    session: Session, team_id: int, player_id: int, games: list[tuple[str, date, int]],
    is_injured: bool,
) -> str:
    """Label the absence relative to recent form, so the model never double-counts.

    Uses the team's most recent games: played in the last 3 => the absence is NEW
    (recent form still includes him and overstates the team). Absent from the last
    5 => long-term (recent form already reflects life without him).
    """
    last5 = [g for g, _, _ in games[-5:]]
    last3 = [g for g, _, _ in games[-3:]]
    played5 = _appearances(session, team_id, player_id, last5)
    played3 = _appearances(session, team_id, player_id, last3)

    if is_injured:
        return "newly_out" if played3 else "long_term_out"
    # Not on the injury report: if he's missed the recent stretch, he's coming back.
    return "returning" if not played5 else "available"


def player_on_off(
    session: Session,
    team_id: int,
    player_id: int,
    player_name: str,
    season: str,
    as_of: date,
    is_injured: bool,
    margins: dict[int, float] | None = None,
) -> OnOffImpact:
    """With/without split for one player on one team, point-in-time."""
    games = _team_games(session, team_id, season, as_of)
    if not games:
        return OnOffImpact(player_name, "available", None, 0, 0, None, None, None, None, None)

    all_ids = [g for g, _, _ in games]
    played = _appearances(session, team_id, player_id, all_ids)

    # Roster span: ignore games before his first appearance for this team (a
    # midseason acquisition never "played without" his new team in October).
    first_date = min((d for g, d, _ in games if g in played), default=None)
    if first_date is None:
        return OnOffImpact(player_name, "available", None, 0, 0, None, None, None, None, None)
    eligible = [(g, d, o) for g, d, o in games if d >= first_date]

    with_ids = [g for g, _, _ in eligible if g in played]
    without_ids = [g for g, _, _ in eligible if g not in played]
    regime = _classify_regime(session, team_id, player_id, games, is_injured)
    mpg = _avg_minutes(session, team_id, player_id, with_ids)

    # Both sides need a real sample: too few games WITHOUT and the split is noise;
    # too few WITH and there's no baseline to compare against.
    if len(without_ids) < _MIN_GAMES_WITHOUT or len(with_ids) < _MIN_GAMES_WITH:
        return OnOffImpact(player_name, regime, mpg, len(with_ids), len(without_ids),
                           None, None, None, None, None)

    net_with, n_with = _net_rating(session, team_id, with_ids)
    net_without, n_without = _net_rating(session, team_id, without_ids)

    delta = None
    if net_with is not None and net_without is not None and n_without:
        # Shrink toward zero by sample size — a 3-game split should not read as fact.
        delta = round((net_with - net_without) * n_without / (n_without + _SHRINK_K), 1)

    margins = margins if margins is not None else team_avg_margin_map(session, season, as_of)
    with_set, without_set = set(with_ids), set(without_ids)
    return OnOffImpact(
        player=player_name,
        regime=regime,
        mpg=mpg,
        games_with=len(with_ids),
        games_without=len(without_ids),
        net_rtg_with=net_with,
        net_rtg_without=net_without,
        delta_shrunk=delta,
        opp_quality_with=_avg_opp_quality([o for g, _, o in eligible if g in with_set], margins),
        opp_quality_without=_avg_opp_quality([o for g, _, o in eligible if g in without_set], margins),
    )


def oracle_absences(
    session: Session, game_id: str, team_id: int, season: str, as_of: date, recent_n: int = 5
) -> list[dict]:
    """BACKTEST ONLY: derive who was out for a completed game from its own box score.

    No historical injury-report data exists, so a backtest otherwise can't know
    availability at all. For a finished game we *do* know who played, so treating
    "played recently but absent from this game's box" as OUT approximates what the
    pre-game report would have said.

    Two honest caveats, and why this exists anyway: it peeks at the game itself, and
    it assumes availability was known *perfectly* at tip (reality has game-time
    decisions). So it measures the on-off feature's **ceiling** — if the feature
    can't help even with perfect availability knowledge, it won't help live. Never
    report a run using this as a real track record.
    """
    games = _team_games(session, team_id, season, as_of)
    recent_ids = [g for g, _, _ in games[-recent_n:]]
    if not recent_ids:
        return []

    roster = session.execute(
        select(PlayerGameStats.player_name)
        .where(
            PlayerGameStats.team_id == team_id,
            PlayerGameStats.game_id.in_(recent_ids),
            PlayerGameStats.minutes > 0,
        )
        .distinct()
    ).scalars().all()
    played_now = set(
        session.execute(
            select(PlayerGameStats.player_name).where(
                PlayerGameStats.game_id == game_id,
                PlayerGameStats.team_id == team_id,
                PlayerGameStats.minutes > 0,
            )
        ).scalars().all()
    )
    return [
        {"player": name, "status": "out", "reason": "did not play (oracle availability)"}
        for name in roster
        if name not in played_now
    ]


def _name_to_id(session: Session, team_id: int, season: str, as_of: date) -> dict[str, tuple[int, str]]:
    """normalized name -> (player_id, display name) for players on this team."""
    rows = session.execute(
        select(PlayerGameStats.player_id, PlayerGameStats.player_name)
        .join(Game, PlayerGameStats.game_id == Game.game_id)
        .where(PlayerGameStats.team_id == team_id, Game.season == season, Game.game_date < as_of)
        .distinct()
    ).all()
    return {normalize_name(name): (pid, name) for pid, name in rows}


def team_on_off(
    session: Session,
    team_id: int,
    season: str,
    as_of: date,
    injuries: list[dict],
    contributors: list[dict] | None = None,
    top_k: int = 3,
) -> list[OnOffImpact]:
    """On-off for a team's notable absences (and returners) as of a cutoff.

    `injuries` are the point-in-time injury rows ({'player': name, ...}); matched to
    box-score player ids by normalized name. `contributors` (from `player_form`) are
    additionally checked so a key player who quietly missed the recent stretch but is
    NOT on the report surfaces as `returning`. Unmatched injury names are skipped.
    """
    lookup = _name_to_id(session, team_id, season, as_of)
    margins = team_avg_margin_map(session, season, as_of)

    injured_norm = {normalize_name(i["player"]) for i in injuries}
    candidates: dict[str, bool] = {n: True for n in injured_norm if n in lookup}
    for c in contributors or []:
        n = normalize_name(c["name"])
        if n in lookup and n not in candidates:
            candidates[n] = False  # not injured; may be a returner

    out: list[OnOffImpact] = []
    for norm, is_injured in candidates.items():
        pid, display = lookup[norm]
        impact = player_on_off(session, team_id, pid, display, season, as_of, is_injured, margins)
        # Keep only informative absences: an 'available' player carries no news, a
        # thin sample carries no signal, and a deep-bench absence doesn't move a game.
        if (
            impact.regime != "available"
            and impact.delta_shrunk is not None
            and (impact.mpg or 0) >= _MIN_MPG
        ):
            out.append(impact)

    out.sort(key=lambda i: abs(i.delta_shrunk or 0), reverse=True)
    return out[:top_k]
