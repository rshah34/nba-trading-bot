"""Betting agent: the DB-touching orchestration around the pure `betting` math.

Pre-tip it proposes a sized paper bet for each game where the game model's
probability disagrees enough with the market (which of those get recorded is the
daily picks' call — see `picks.py`). After games resolve it settles each
bet with CLV (vs. the closing line) and P&L.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot import betting
from nba_bot.betting import decide_bet_decimal, simulate_paper_trade
from nba_bot.config import my_books
from nba_bot.db.models import Bet, Game, Odds, Prediction
from nba_bot.features.games import GAME_MODEL


def _latest_moneylines(odds_rows: list[Odds], closing_only: bool = False) -> dict[str, tuple[float, float]]:
    """sportsbook -> (home, away) decimal odds from each book's newest two-way moneyline."""
    rows = [r for r in odds_rows if r.is_closing_line] if closing_only else odds_rows
    latest: dict[str, Odds] = {}
    for r in rows:
        cur = latest.get(r.sportsbook)
        if cur is None or r.captured_at > cur.captured_at:
            latest[r.sportsbook] = r
    return {
        book: (betting.american_to_decimal(r.home_moneyline),
               betting.american_to_decimal(r.away_moneyline))
        for book, r in latest.items()
        if r.home_moneyline is not None and r.away_moneyline is not None
    }


def consensus_decimal_odds(odds_rows: list[Odds], closing_only: bool = False) -> tuple[float, float] | None:
    """Average decimal odds per side across books (latest snapshot per book).

    Averaging happens in decimal space (payouts), not American. Returns
    (home_decimal, away_decimal), or None if no usable two-way moneyline is present.
    """
    lines = _latest_moneylines(odds_rows, closing_only)
    if not lines:
        return None
    return (sum(h for h, _ in lines.values()) / len(lines),
            sum(a for _, a in lines.values()) / len(lines))


def best_prices(odds_rows: list[Odds], books: frozenset[str]) -> dict[str, tuple[str, float]]:
    """{side: (sportsbook, decimal odds)} — the best current price on each side at
    the named books. A side none of them offers is absent."""
    out: dict[str, tuple[str, float]] = {}
    for book, (home, away) in _latest_moneylines(odds_rows).items():
        if book not in books:
            continue
        for side, odds in (("home", home), ("away", away)):
            if side not in out or odds > out[side][1]:
                out[side] = (book, odds)
    return out


def game_candidates(
    session: Session,
    game_date: date,
    *,
    as_of: datetime,
    model_version: str | None = None,
    market_weight: float = 0.75,
    min_edge: float = 0.025,
    max_edge: float = 0.0625,
    kelly_multiplier: float = 0.25,
    max_stake: float = 0.03,
) -> list[dict]:
    """Game-winner bets worth making right now, best edge first. Nothing is recorded.

    For each predicted game that hasn't tipped and isn't already bet: blend the
    model's probability toward the current market, and keep it if the edge clears
    the threshold. The market is every book's consensus; the bet is priced
    at the best of your own books (`MY_BOOKS`), and skipped if none has a playable
    price.

    The blend leans on the market three to one: game lines are the sharpest market
    there is, and the market sees everything this model does and more. So a game
    only qualifies when the model disagrees with the price by 10 to 25 points — less
    is noise, more means the model is missing something.
    """
    model_version = model_version or GAME_MODEL
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
        if game.tipoff_at is not None and game.tipoff_at <= as_of:
            continue
        odds_rows = session.execute(select(Odds).where(Odds.game_id == game.game_id)).scalars().all()
        cons = consensus_decimal_odds(odds_rows)
        if cons is None:
            continue  # no market to bet into
        d = decide_bet_decimal(
            float(pred.predicted_home_win_prob), cons[0], cons[1],
            prices=best_prices(odds_rows, my_books()),
            market_weight=market_weight, min_edge=min_edge, max_edge=max_edge,
            kelly_multiplier=kelly_multiplier, max_stake=max_stake,
        )
        if d.side == "none" or d.stake_fraction <= 0:
            continue
        factors = (pred.context_used or {}).get("key_factors") or []
        out.append({"kind": "game", "game_id": game.game_id, "prediction_id": pred.id,
                    "model_version": model_version, "decision": d, "edge": d.edge,
                    "reason": factors[0] if factors else None})
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
                    stake_fraction=d.stake_fraction, decimal_odds=d.decimal_odds,
                    sportsbook=d.sportsbook, playable_odds=d.playable_odds, reason=c["reason"])
            .on_conflict_do_nothing(constraint="bets_game_model_key")
            .returning(Bet.id)
        )
        # RETURNING yields a row only on an actual insert (empty on conflict) — robust
        # where ON CONFLICT DO NOTHING reports rowcount -1 under psycopg.
        placed += res.first() is not None
    session.commit()
    return placed


def settle_bets(session: Session) -> dict:
    """Score unsettled bets whose game is final: P&L, and CLV — how far the market's
    fair probability of our side moved toward us between the bet and the close."""
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
            fair = betting.devig_two_way(1 / closing[0], 1 / closing[1])
            bet.closing_decimal_odds = round(closing[0] if bet.side == "home" else closing[1], 3)
            bet.clv = round(fair[0 if bet.side == "home" else 1] - float(bet.market_prob), 4)
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
