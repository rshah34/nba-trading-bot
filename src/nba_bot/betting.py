"""Bet-decision layer: turn a calibrated win probability + market odds into a
sized bet, and judge it by closing-line value (CLV).

Design stance, earned the hard way (see the calibration/A-B findings): our model is
~coin-flip at *predicting* games, so we do NOT try to beat Vegas at prediction. We
bet only the specific spots where our honestly-calibrated probability disagrees
enough with the de-vigged market price to clear a conservative edge threshold; we
size by FRACTIONAL Kelly (full Kelly on an uncertain edge is a fast road to ruin);
and we judge ourselves primarily by **CLV** — did we get a better number than the
close — because short-run P&L is mostly variance.

Everything here is pure/odds-math so it is fully testable and reused by both the
live pipeline (decide bets pre-tip) and the paper-trade report (score them after).
"""

from __future__ import annotations

from dataclasses import dataclass

from nba_bot.markets import devig_two_way


def american_to_decimal(american: int) -> float:
    """American moneyline → decimal odds (total return per unit staked)."""
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def kelly_fraction(prob: float, decimal_odds: float) -> float:
    """Full-Kelly stake fraction for a wager. Negative = no +EV bet exists."""
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    return (prob * decimal_odds - 1) / b


@dataclass
class BetDecision:
    side: str                    # "home" | "away" | "none"
    edge: float                  # calibrated prob − de-vigged market prob on the bet side
    stake_fraction: float        # fraction of bankroll to wager (fractional Kelly, capped)
    bet_prob: float              # model prob for the side bet
    market_prob: float           # de-vigged market prob for the side bet
    decimal_odds: float | None   # odds taken on the bet side
    expected_value: float        # EV per unit staked (>0 required to bet)
    reason: str


def decide_bet_decimal(
    model_home_prob: float,
    home_decimal: float,
    away_decimal: float,
    *,
    min_edge: float = 0.04,
    kelly_multiplier: float = 0.25,
    max_stake: float = 0.05,
) -> BetDecision:
    """Decide whether/how to bet one game from decimal odds (the live primitive).

    De-vigs the two-way market to a fair probability, compares it to the model's
    calibrated probability, and bets the side whose edge clears `min_edge`. Because
    de-vigged probs and model probs each sum to 1, only one side can carry a positive
    edge. Stake = fractional Kelly (× `kelly_multiplier`), capped at `max_stake` of
    bankroll. Conservative by default — the model barely out-discriminates a coin.
    """
    fair_home, fair_away = devig_two_way(1 / home_decimal, 1 / away_decimal)
    home_edge = model_home_prob - fair_home

    if home_edge > min_edge:
        side, edge, p, fair, odds = ("home", home_edge, model_home_prob, fair_home, home_decimal)
    elif -home_edge > min_edge:  # away_edge == -home_edge
        side, edge, p, fair, odds = ("away", -home_edge, 1 - model_home_prob, fair_away, away_decimal)
    else:
        return BetDecision("none", round(home_edge, 4), 0.0, model_home_prob,
                           round(fair_home, 4), None, 0.0,
                           f"edge {home_edge:+.1%} within ±{min_edge:.0%} threshold — no bet")

    stake = max(0.0, min(kelly_fraction(p, odds) * kelly_multiplier, max_stake))
    ev = p * (odds - 1) - (1 - p)
    return BetDecision(side, round(edge, 4), round(stake, 4), round(p, 4), round(fair, 4),
                       round(odds, 3), round(ev, 4),
                       f"bet {side}: model {p:.1%} vs market {fair:.1%}, edge {edge:+.1%}")


def decide_bet(
    model_home_prob: float,
    home_american: int,
    away_american: int,
    **kwargs,
) -> BetDecision:
    """Decide a bet from American moneyline odds — thin wrapper over the decimal primitive."""
    return decide_bet_decimal(
        model_home_prob, american_to_decimal(home_american), american_to_decimal(away_american),
        **kwargs,
    )


def clv(bet_decimal_odds: float, closing_decimal_odds: float) -> float:
    """Closing-line value in probability points: fair implied prob of our side at the
    close minus at the odds we took. Positive = we beat the close (got a better price).
    The single most reliable evidence of a real edge — it doesn't need the game to resolve.
    """
    return round(1 / closing_decimal_odds - 1 / bet_decimal_odds, 4)


@dataclass
class PaperTradeResult:
    n_bets: int
    win_rate: float | None
    roi: float | None            # profit / total staked
    final_bankroll: float
    avg_clv: float | None        # mean CLV across bets — the north-star metric
    clv_positive_rate: float | None  # share of bets that beat the close


def simulate_paper_trade(bets: list[dict], starting_bankroll: float = 1.0) -> PaperTradeResult:
    """Replay a list of settled bets into a bankroll curve + CLV summary.

    Each bet: {stake_fraction, decimal_odds, won: bool, clv: float}. Stake compounds
    off the running bankroll (fractional-Kelly style). CLV is averaged separately —
    it's the durable signal; the P&L curve is the noisy one.
    """
    if not bets:
        return PaperTradeResult(0, None, None, starting_bankroll, None, None)

    bankroll = starting_bankroll
    staked_total = profit_total = 0.0
    wins = 0
    clvs = [b["clv"] for b in bets if b.get("clv") is not None]
    for b in bets:
        stake = bankroll * b["stake_fraction"]
        staked_total += stake
        if b["won"]:
            pnl = stake * (b["decimal_odds"] - 1)
            wins += 1
        else:
            pnl = -stake
        profit_total += pnl
        bankroll += pnl

    return PaperTradeResult(
        n_bets=len(bets),
        win_rate=round(wins / len(bets), 3),
        roi=round(profit_total / staked_total, 4) if staked_total else None,
        final_bankroll=round(bankroll, 4),
        avg_clv=round(sum(clvs) / len(clvs), 4) if clvs else None,
        clv_positive_rate=round(sum(1 for c in clvs if c > 0) / len(clvs), 3) if clvs else None,
    )
