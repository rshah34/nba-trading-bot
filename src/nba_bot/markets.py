"""Market math: convert betting odds to fair (de-vigged) probabilities and a
consensus line across sportsbooks. Shared by the Analysis Agent (edge at
prediction time) and the Evaluation Agent (closing-line value / CLV).
"""

from __future__ import annotations

from dataclasses import dataclass

from nba_bot.db.models import Odds


def american_to_implied_prob(odds: int) -> float:
    """Convert American moneyline odds to an implied win probability (incl. vig)."""
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


def devig_two_way(p_home: float, p_away: float) -> tuple[float, float]:
    """Normalize a two-way implied-probability pair to remove the bookmaker vig."""
    total = p_home + p_away
    if total <= 0:
        return 0.5, 0.5
    return p_home / total, p_away / total


@dataclass
class MarketLine:
    home_win_prob: float | None  # de-vigged, consensus across books
    home_margin: float | None  # positive = home favored by N points
    n_books: int


def market_line_from_odds(odds_rows: list[Odds]) -> MarketLine:
    """Consensus market view from a set of odds snapshots (latest per book).

    Averages de-vigged moneyline probabilities and the point spread across books.
    Spread is expressed as home margin (positive = home favored), i.e. -spread_home.
    """
    latest_by_book: dict[str, Odds] = {}
    for row in odds_rows:
        cur = latest_by_book.get(row.sportsbook)
        if cur is None or row.captured_at > cur.captured_at:
            latest_by_book[row.sportsbook] = row

    probs: list[float] = []
    margins: list[float] = []
    for row in latest_by_book.values():
        if row.home_moneyline is not None and row.away_moneyline is not None:
            p_home = american_to_implied_prob(row.home_moneyline)
            p_away = american_to_implied_prob(row.away_moneyline)
            probs.append(devig_two_way(p_home, p_away)[0])
        if row.spread_home is not None:
            margins.append(-float(row.spread_home))

    return MarketLine(
        home_win_prob=sum(probs) / len(probs) if probs else None,
        home_margin=sum(margins) / len(margins) if margins else None,
        n_books=len(latest_by_book),
    )
