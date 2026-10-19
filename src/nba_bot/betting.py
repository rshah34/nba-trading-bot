"""Bet-decision layer: turn a calibrated win probability + market odds into a
sized bet, and judge it by closing-line value (CLV).

Design stance: the market sees everything our models see and more, so we do NOT try
to beat Vegas at prediction across the board. We bet only the specific spots where our honestly-calibrated probability disagrees
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


# The price has to leave this much room under the probability we size on, or the bet
# isn't worth making. It is also what "playable to" means in the text.
PRICE_MARGIN = 0.005


def playable_odds(bet_prob: float) -> float:
    """The worst decimal price at which a bet at this probability is still worth making."""
    return 1 / max(bet_prob - PRICE_MARGIN, 1e-6)


@dataclass
class BetDecision:
    side: str                    # "home" | "away" | "none"
    edge: float                  # bet_prob − de-vigged market prob on the bet side
    stake_fraction: float        # fraction of bankroll to wager (fractional Kelly, capped)
    bet_prob: float              # model blended toward the market — what we size on
    market_prob: float           # de-vigged market prob for the side bet
    decimal_odds: float | None   # odds taken on the bet side
    expected_value: float        # EV per unit staked (>0 required to bet)
    model_prob: float            # the model's own probability for the side bet
    reason: str
    sportsbook: str | None = None       # where the price was taken, when books are named
    playable_odds: float | None = None  # the worst price still worth taking


def decide_bet_decimal(
    model_home_prob: float,
    home_decimal: float,
    away_decimal: float,
    *,
    prices: dict[str, tuple[str, float]] | None = None,
    market_weight: float = 0.0,
    min_edge: float = 0.04,
    max_edge: float | None = None,
    kelly_multiplier: float = 0.25,
    max_stake: float = 0.05,
) -> BetDecision:
    """Decide whether/how to bet one game from decimal odds (the live primitive).

    De-vigs the two-way market to a fair probability and compares it to the model's
    calibrated probability, optionally blended toward the market by `market_weight`
    (the line is itself a strong forecast, so the blend is the honest number to size
    on). Bets the side whose edge clears `min_edge`; only one side can. An edge
    above `max_edge` is no bet either: a disagreement that large means the model is
    missing something the market knows. Stake = fractional Kelly (×
    `kelly_multiplier`), capped at `max_stake` of bankroll.

    The two odds passed in are the market (its consensus). `prices` is what can
    actually be had: {side: (sportsbook, decimal odds)} at the bettor's own books.
    With it, the bet is priced there, and there is no bet if the side isn't offered
    or the price leaves no room.
    """
    fair_home, fair_away = devig_two_way(1 / home_decimal, 1 / away_decimal)
    bet_home = market_weight * fair_home + (1 - market_weight) * model_home_prob
    home_edge = bet_home - fair_home

    if home_edge > min_edge:
        side, edge, p, fair, odds, raw = (
            "home", home_edge, bet_home, fair_home, home_decimal, model_home_prob)
    elif -home_edge > min_edge:  # away_edge == -home_edge
        side, edge, p, fair, odds, raw = (
            "away", -home_edge, 1 - bet_home, fair_away, away_decimal, 1 - model_home_prob)
    else:
        return BetDecision("none", round(home_edge, 4), 0.0, round(bet_home, 4),
                           round(fair_home, 4), None, 0.0, round(model_home_prob, 4),
                           f"edge {home_edge:+.1%} within ±{min_edge:.0%} threshold — no bet")

    if max_edge is not None and edge > max_edge:
        return BetDecision("none", round(edge, 4), 0.0, round(p, 4), round(fair, 4), None, 0.0,
                           round(raw, 4), f"edge {edge:+.1%} is too large to trust — no bet")

    book = None
    if prices is not None:
        book, odds = prices.get(side, (None, 0.0))
        if odds < playable_odds(p):
            return BetDecision("none", round(edge, 4), 0.0, round(p, 4), round(fair, 4), None,
                               0.0, round(raw, 4), "no playable price at your books — no bet")

    stake = max(0.0, min(kelly_fraction(p, odds) * kelly_multiplier, max_stake))
    ev = p * (odds - 1) - (1 - p)
    return BetDecision(side, round(edge, 4), round(stake, 4), round(p, 4), round(fair, 4),
                       round(odds, 3), round(ev, 4), round(raw, 4),
                       f"bet {side}: model {raw:.1%} vs market {fair:.1%}, edge {edge:+.1%}",
                       book, round(playable_odds(p), 3))


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


# --------------------------------------------------------------------------- #
# Player props.
# --------------------------------------------------------------------------- #
@dataclass
class PropQuote:
    """One book's two-way price on one player line."""

    sportsbook: str
    line: float
    over_decimal: float
    under_decimal: float


@dataclass
class PropDecision:
    side: str                  # "over" | "under"
    sportsbook: str            # the book with the best price on that side
    line: float
    decimal_odds: float
    n_books: int               # books behind the consensus at this line
    model_prob: float          # raw model probability of the side winning
    market_prob: float         # de-vigged consensus probability of the side
    bet_prob: float            # model blended toward the market — what we size on
    edge: float                # bet_prob − market_prob
    expected_value: float      # EV per unit staked at the price taken
    stake_fraction: float
    playable_odds: float       # the worst price still worth taking at this line
    playable_line: float       # the worst line still worth taking at this price


# How far a prop bet's probability leans on the market rather than the model.
PROP_MARKET_WEIGHT = 0.5
# A book whose line is further than this from the one being priced isn't quoting the
# same bet, and is left out of the consensus.
_MAX_LINE_GAP = 1.5


def decide_prop(
    quotes: list[PropQuote],
    probs_at,
    *,
    books: frozenset[str] | None = None,
    market_weight: float = PROP_MARKET_WEIGHT,
    min_edge: float = 0.03,
    max_edge: float = 0.10,
    min_books: int = 2,
    kelly_multiplier: float = 0.25,
    max_stake: float = 0.02,
) -> PropDecision | None:
    """Pick the best bet (if any) on one player-market across books and lines.

    `probs_at(line)` returns the model's (P(over), P(under), P(push)) at a line.
    `books` names the books the bettor can use: only their lines are bet, at the
    best of their prices. Every book quoted still informs the consensus.

    Three guards, each there because the model is unproven against real books:

    * **Blend toward the market.** The line is itself a strong forecast, so we size
      on `market_weight` × market + the rest × model rather than on the raw model.
    * **Consensus, then best price.** Fair probability is the de-vigged average
      across at least `min_books` books. A book hanging a nearby line still counts:
      its probability is carried to the line being priced with the model's own
      distribution, so a book that is a point off the rest shows up as the edge it is.
    * **Too-good-to-be-true cap.** A blended edge above `max_edge` (a raw model
      gap twice that) almost always means the model is missing something — an
      injury, a role change — not that several books are that wrong at once.

    Stake is fractional Kelly on the blended probability, push-aware, capped.
    """
    usable = [q for q in quotes if q.over_decimal > 1 and q.under_decimal > 1]
    mine = [q for q in usable if books is None or q.sportsbook in books]

    def model_over(line: float) -> tuple[float, float]:
        """(P(over) given no push, P(no push)) — a two-way price ignores the push."""
        over, _under, push = probs_at(line)
        return (over / (1 - push), 1 - push) if push < 1 else (0.5, 0.0)

    best: PropDecision | None = None
    for line in sorted({q.line for q in mine}):
        over_here, live = model_over(line)
        if live <= 0:
            continue
        nearest: dict[str, PropQuote] = {}
        for q in usable:
            cur = nearest.get(q.sportsbook)
            if abs(q.line - line) <= _MAX_LINE_GAP and (
                    cur is None or abs(q.line - line) < abs(cur.line - line)):
                nearest[q.sportsbook] = q
        if len(nearest) < min_books:
            continue
        fair = [
            devig_two_way(1 / q.over_decimal, 1 / q.under_decimal)[0]
            + (over_here - model_over(q.line)[0] if q.line != line else 0.0)
            for q in nearest.values()
        ]
        fair_over = min(max(sum(fair) / len(fair), 0.01), 0.99)

        for side, model, market in (
            ("over", over_here, fair_over),
            ("under", 1 - over_here, 1 - fair_over),
        ):
            bet_prob = market_weight * market + (1 - market_weight) * model
            edge = bet_prob - market
            if edge < min_edge or edge > max_edge:
                continue
            quote = max((q for q in mine if q.line == line),
                        key=lambda q: q.over_decimal if side == "over" else q.under_decimal)
            odds = quote.over_decimal if side == "over" else quote.under_decimal
            if odds < playable_odds(bet_prob):
                continue
            win, lose = bet_prob * live, (1 - bet_prob) * live
            ev = win * (odds - 1) - lose
            stake = min(max(ev / (odds - 1), 0.0) * kelly_multiplier, max_stake)

            # How far the line can move against us and still be a bet at this price:
            # the market and the model shift together, so only the price has to hold.
            def side_prob(ln: float, side: str = side) -> float:
                over = model_over(ln)[0]
                return over if side == "over" else 1 - over

            playable_line, step = line, 0.5 if side == "over" else -0.5
            while (abs(playable_line + step - line) <= _MAX_LINE_GAP and odds >= playable_odds(
                    bet_prob + side_prob(playable_line + step) - side_prob(line))):
                playable_line += step

            d = PropDecision(side, quote.sportsbook, line, round(odds, 3), len(nearest),
                             round(model, 4), round(market, 4), round(bet_prob, 4),
                             round(edge, 4), round(ev, 4), round(stake, 4),
                             round(playable_odds(bet_prob), 3), playable_line)
            if best is None or d.expected_value > best.expected_value:
                best = d
    return best


def settle_prop(side: str, line: float, actual: float, decimal_odds: float,
                stake_fraction: float) -> tuple[str, float]:
    """(result, pnl) for a prop bet. A whole-number line landed exactly is a push."""
    if actual == line:
        return "push", 0.0
    won = (actual > line) == (side == "over")
    return ("won", round(stake_fraction * (decimal_odds - 1), 4)) if won else (
        "lost", round(-stake_fraction, 4))
