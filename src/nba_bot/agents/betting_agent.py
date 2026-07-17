"""Betting agent: the DB-touching orchestration around the pure `betting` math.

Pre-tip it records a sized paper bet for each game where the calibrated model
probability disagrees enough with the market. After games resolve it settles each
bet with CLV (vs. the closing line) and P&L. Calibration params (if fitted) are
applied to the raw probability first, so edge and sizing use honest numbers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

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


def record_bets(
    session: Session,
    game_date: date,
    model_version: str | None = None,
    *,
    min_edge: float = 0.04,
    kelly_multiplier: float = 0.25,
    max_stake: float = 0.05,
) -> dict:
    """For each predicted game on a date, apply calibration, compare to the current
    market, and record a sized bet where the edge clears the threshold. Idempotent —
    the first pre-tip bet per (game, model) stands (on-conflict-do-nothing)."""
    model_version = model_version or settings.analysis_model
    params = load_calibration(session, model_version)

    rows = session.execute(
        select(Prediction, Game)
        .join(Game, Prediction.game_id == Game.game_id)
        .where(Game.game_date == game_date, Prediction.model_version == model_version)
    ).all()

    placed = 0
    for pred, game in rows:
        raw = float(pred.predicted_home_win_prob)
        cal_home = apply_platt(raw, params) if params else raw
        odds_rows = session.execute(select(Odds).where(Odds.game_id == game.game_id)).scalars().all()
        cons = consensus_decimal_odds(odds_rows)
        if cons is None:
            continue  # no market to bet into
        d = decide_bet_decimal(cal_home, cons[0], cons[1], min_edge=min_edge,
                               kelly_multiplier=kelly_multiplier, max_stake=max_stake)
        if d.side == "none" or d.stake_fraction <= 0:
            continue
        res = session.execute(
            pg_insert(Bet)
            .values(game_id=game.game_id, prediction_id=pred.id, model_version=model_version,
                    side=d.side, model_prob=d.bet_prob, market_prob=d.market_prob, edge=d.edge,
                    stake_fraction=d.stake_fraction, decimal_odds=d.decimal_odds)
            .on_conflict_do_nothing(constraint="bets_game_model_key")
            .returning(Bet.id)
        )
        # RETURNING yields a row only on an actual insert (empty on conflict) — robust
        # where ON CONFLICT DO NOTHING reports rowcount -1 under psycopg.
        if res.first() is not None:
            placed += 1
    session.commit()
    return {"date": str(game_date), "model_version": model_version, "placed": placed}


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
