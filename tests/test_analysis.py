"""Analysis Agent tests: market math + consensus aggregation. Network/DB-free."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from nba_bot.agents.analysis_agent import (
    american_to_implied_prob,
    devig_two_way,
    market_line_from_odds,
)


def _odds(book, captured, home_ml=None, away_ml=None, spread_home=None):
    return SimpleNamespace(
        sportsbook=book,
        captured_at=captured,
        home_moneyline=home_ml,
        away_moneyline=away_ml,
        spread_home=spread_home,
    )


def test_american_to_implied_prob():
    assert american_to_implied_prob(-180) == pytest.approx(180 / 280)
    assert american_to_implied_prob(150) == pytest.approx(0.4)
    assert american_to_implied_prob(100) == pytest.approx(0.5)
    assert american_to_implied_prob(-110) == pytest.approx(110 / 210)


def test_devig_two_way_removes_vig():
    # Two -110 sides each imply ~0.524; de-vigged they must sum to 1.0.
    p = american_to_implied_prob(-110)
    home, away = devig_two_way(p, p)
    assert home == pytest.approx(0.5)
    assert away == pytest.approx(0.5)
    assert home + away == pytest.approx(1.0)


def test_market_line_consensus_across_books():
    t = datetime(2026, 11, 3, tzinfo=timezone.utc)
    rows = [
        _odds("dk", t, home_ml=-150, away_ml=130, spread_home=-3.5),
        _odds("fd", t, home_ml=-140, away_ml=120, spread_home=-3.0),
    ]
    line = market_line_from_odds(rows)
    assert line.n_books == 2
    assert line.home_margin == pytest.approx(3.25)  # (-(-3.5) + -(-3.0)) / 2
    # de-vigged home prob is averaged and strictly between the raw vigged prob and 0.5
    assert 0.55 < line.home_win_prob < 0.60


def test_market_line_uses_latest_snapshot_per_book():
    early = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    late = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
    rows = [
        _odds("dk", early, home_ml=200, away_ml=-250, spread_home=6.0),   # stale, should be ignored
        _odds("dk", late, home_ml=-150, away_ml=130, spread_home=-3.5),   # latest wins
    ]
    line = market_line_from_odds(rows)
    assert line.n_books == 1
    assert line.home_margin == pytest.approx(3.5)  # from the late snapshot only


def test_market_line_empty_is_safe():
    line = market_line_from_odds([])
    assert line.n_books == 0
    assert line.home_win_prob is None
    assert line.home_margin is None
