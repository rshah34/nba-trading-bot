"""Bet-decision math: odds conversion, Kelly, edge/side selection, CLV, sim."""

from nba_bot.betting import (
    american_to_decimal,
    clv,
    decide_bet,
    kelly_fraction,
    simulate_paper_trade,
)


def test_american_to_decimal():
    assert american_to_decimal(100) == 2.0        # even money
    assert abs(american_to_decimal(-110) - (1 + 100 / 110)) < 1e-9
    assert american_to_decimal(200) == 3.0        # +200 -> 3.0


def test_kelly_fraction():
    # p=0.6 at even money (b=1): f = (0.6*2 - 1)/1 = 0.2
    assert round(kelly_fraction(0.6, 2.0), 4) == 0.2
    # no edge -> non-positive
    assert kelly_fraction(0.5, 2.0) == 0.0


def test_decide_bet_takes_the_edge_side_and_sizes_fractionally():
    # Market -110/-110 => fair ~50/50. Model loves home at 0.60 => ~10% edge.
    d = decide_bet(0.60, -110, -110, min_edge=0.04, kelly_multiplier=0.25, max_stake=0.05)
    assert d.side == "home"
    assert d.edge > 0.09
    assert 0 < d.stake_fraction <= 0.05      # capped
    assert d.expected_value > 0


def test_decide_bet_picks_away_when_model_fades_home():
    d = decide_bet(0.35, -110, -110)
    assert d.side == "away"
    assert d.bet_prob == 0.65                 # 1 - model home prob
    assert d.edge > 0.09


def test_decide_bet_no_bet_when_within_threshold():
    # Model agrees with the market -> no edge -> no bet.
    d = decide_bet(0.52, -110, -110, min_edge=0.04)
    assert d.side == "none"
    assert d.stake_fraction == 0.0


def test_decide_bet_respects_the_vig_via_devig():
    # Heavy favorite -300/+250: fair home ~0.74. Model at 0.75 is NOT a real edge.
    d = decide_bet(0.75, -300, 250, min_edge=0.04)
    assert d.side == "none"


def test_clv_positive_when_price_beat_the_close():
    # Took +2.10 decimal, closed at +1.90 => our side's implied prob rose => positive CLV.
    assert clv(2.10, 1.90) > 0
    assert clv(1.90, 2.10) < 0


def test_simulate_paper_trade_tracks_bankroll_and_clv():
    bets = [
        {"stake_fraction": 0.04, "decimal_odds": 2.0, "won": True, "clv": 0.02},
        {"stake_fraction": 0.03, "decimal_odds": 1.9, "won": False, "clv": 0.01},
        {"stake_fraction": 0.05, "decimal_odds": 2.5, "won": True, "clv": -0.01},
    ]
    res = simulate_paper_trade(bets, starting_bankroll=1.0)
    assert res.n_bets == 3
    assert res.win_rate == round(2 / 3, 3)
    assert res.avg_clv == round((0.02 + 0.01 - 0.01) / 3, 4)
    assert res.clv_positive_rate == round(2 / 3, 3)


def test_simulate_paper_trade_empty():
    res = simulate_paper_trade([])
    assert res.n_bets == 0 and res.final_bankroll == 1.0
