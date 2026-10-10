"""Betting agent: the DB-touching orchestration around the pure `betting` math.

Pre-tip it proposes a sized paper bet for each game where the calibrated model
probability disagrees enough with the market (which of those get recorded is the
daily picks' call — see `picks.py`). After games resolve it settles each
bet with CLV (vs. the closing line) and P&L. Calibration params (if fitted) are
applied to the raw probability first, so edge and sizing use honest numbers.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot import betting
from nba_bot.betting import decide_bet_decimal, simulate_paper_trade
from nba_bot.config import settings
from nba_bot.db.models import Bet, CalibrationParams, Game, Odds, Prediction
from nba_bot.features.calibration import PlattParams, apply_platt


def consensus_decimal_odds(odds_rows: list[Odds], closing_only: bool = False) -> tuple[float, float] | None:
    """Average decimal odds per side across books (latest snapshot per book).

    Averaging happens in decimal space (payouts), not American. Returns
    (home_decimal, away_decimal), or None if no usable two-way moneyline is present.
    """
    rows = [r for r in odds_rows if r.is_closing_line] if closing_only else odds_rows
    latest: dict[str, Odds] = {}
    for r in rows:
        cur = latest.get(r.sportsbook)
        if cur is None or r.captured_at > cur.captured_at:
            latest[r.sportsbook] = r

    homes, aways = [], []
    for r in latest.values():
        if r.home_moneyline is not None and r.away_moneyline is not None:
            homes.append(betting.american_to_decimal(r.home_moneyline))
            aways.append(betting.american_to_decimal(r.away_moneyline))
    if not homes:
        return None
    return sum(homes) / len(homes), sum(aways) / len(aways)


def load_calibration(session: Session, model_version: str) -> PlattParams | None:
    row = session.get(CalibrationParams, model_version)
    return PlattParams(a=float(row.a), b=float(row.b), n=row.n) if row else None


def save_calibration(session: Session, model_version: str, params: PlattParams) -> None:
    session.execute(
        pg_insert(CalibrationParams)
        .values(model_version=model_version, a=params.a, b=params.b, n=params.n,
                fitted_at=datetime.now(timezone.utc))
        .on_conflict_do_update(
            index_elements=[CalibrationParams.model_version],
            set_={"a": params.a, "b": params.b, "n": params.n,
                  "fitted_at": datetime.now(timezone.utc)},
        )
    )
    session.commit()


def game_candidates(
    session: Session,
    game_date: date,
    *,
    as_of: datetime,
    window_hours: float | None = None,
    model_version: str | None = None,
    market_weight: float = 0.75,
    min_edge: float = 0.025,
    max_edge: float = 0.0625,
    kelly_multiplier: float = 0.25,
    max_stake: float = 0.03,
) -> list[dict]:
    """Game-winner bets worth making right now, best edge first. Nothing is recorded.

    For each predicted game that hasn't tipped and isn't already bet: calibrate the
    model's probability, blend it toward the current market, and keep it if the edge
    clears the threshold.

    The blend leans on the market three to one: game lines are the sharpest market
    there is, and this model tested at roughly a coin flip against them. So a game
    only qualifies when the model disagrees with the price by 10 to 25 points — less
    is noise, more means the model is missing something.
    """
    model_version = model_version or settings.analysis_model
    params = load_calibration(session, model_version)
    already = set(session.execute(
        select(Bet.game_id).where(Bet.model_version == model_version)
    ).scalars())
    rows = session.execute(
        select(Prediction, Game)
        .join(Game, Prediction.game_id == Game.game_id)
        .where(Game.game_date == game_date, Game.status == "scheduled",
               Prediction.model_version == model_version)
        .order_by(Prediction.as_of.desc())
    ).all()

    out: list[dict] = []
    seen: set[str] = set()
    for pred, game in rows:
        if game.game_id in seen or game.game_id in already:
            continue
        seen.add(game.game_id)  # newest prediction per game
        if game.tipoff_at is not None:
            if game.tipoff_at <= as_of:
                continue
            if window_hours is not None and game.tipoff_at > as_of + timedelta(hours=window_hours):
                continue
        cons = consensus_decimal_odds(
            session.execute(select(Odds).where(Odds.game_id == game.game_id)).scalars().all()
        )
        if cons is None:
            continue  # no market to bet into
        raw = float(pred.predicted_home_win_prob)
        d = decide_bet_decimal(
            apply_platt(raw, params) if params else raw, cons[0], cons[1],
            market_weight=market_weight, min_edge=min_edge, max_edge=max_edge,
            kelly_multiplier=kelly_multiplier, max_stake=max_stake,
        )
        if d.side == "none" or d.stake_fraction <= 0:
            continue
        out.append({"kind": "game", "game_id": game.game_id, "prediction_id": pred.id,
                    "model_version": model_version, "decision": d, "edge": d.edge})
    out.sort(key=lambda c: c["edge"], reverse=True)
    return out


def record_game_bets(session: Session, chosen: list[dict], as_of: datetime) -> int:
    """Record the chosen game bets. One bet per (game, model) ever; returns how many
    were new."""
    placed = 0
    for c in chosen:
        d = c["decision"]
        res = session.execute(
            pg_insert(Bet)
            .values(game_id=c["game_id"], prediction_id=c["prediction_id"],
                    model_version=c["model_version"], decided_at=as_of, side=d.side,
                    model_prob=d.model_prob, market_prob=d.market_prob, edge=d.edge,
                    stake_fraction=d.stake_fraction, decimal_odds=d.decimal_odds)
            .on_conflict_do_nothing(constraint="bets_game_model_key")
            .returning(Bet.id)
        )
        # RETURNING yields a row only on an actual insert (empty on conflict) — robust
        # where ON CONFLICT DO NOTHING reports rowcount -1 under psycopg.
        placed += res.first() is not None
    session.commit()
    return placed


def settle_bets(session: Session) -> dict:
    """Score unsettled bets whose game is final: CLV vs. the closing line and P&L."""
    rows = session.execute(
        select(Bet, Game)
        .join(Game, Bet.game_id == Game.game_id)
        .where(Bet.settled_at.is_(None), Game.status == "final", Game.home_score.is_not(None))
    ).all()

    settled = 0
    for bet, game in rows:
        home_won = game.home_score > game.away_score
        won = (bet.side == "home" and home_won) or (bet.side == "away" and not home_won)
        stake, odds = float(bet.stake_fraction), float(bet.decimal_odds)
        bet.won = won
        bet.pnl = round(stake * (odds - 1) if won else -stake, 4)

        closing = consensus_decimal_odds(
            session.execute(select(Odds).where(Odds.game_id == bet.game_id)).scalars().all(),
            closing_only=True,
        )
        if closing is not None:
            close_side = closing[0] if bet.side == "home" else closing[1]
            bet.closing_decimal_odds = round(close_side, 3)
            bet.clv = betting.clv(odds, close_side)
        bet.settled_at = datetime.now(timezone.utc)
        settled += 1

    session.commit()
    return {"settled": settled}


def track_record(session: Session, model_version: str | None = None) -> dict:
    """Paper-trade summary over settled bets (CLV is the north star)."""
    stmt = select(Bet).where(Bet.settled_at.is_not(None))
    if model_version:
        stmt = stmt.where(Bet.model_version == model_version)
    bets = session.execute(stmt).scalars().all()

    data = [
        {"stake_fraction": float(b.stake_fraction), "decimal_odds": float(b.decimal_odds),
         "won": bool(b.won), "clv": float(b.clv) if b.clv is not None else None}
        for b in bets
    ]
    res = simulate_paper_trade(data)
    return {
        "n_bets": res.n_bets, "win_rate": res.win_rate, "roi": res.roi,
        "final_bankroll": res.final_bankroll, "avg_clv": res.avg_clv,
        "clv_positive_rate": res.clv_positive_rate,
    }
