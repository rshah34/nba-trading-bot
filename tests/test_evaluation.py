"""Evaluation Agent tests: metric computation. DB-free."""

import math

import pytest

from nba_bot.agents.evaluation_agent import compute_metrics


def test_perfect_confident_correct_prediction():
    # Predicted home 90%, home won by 10.
    m = compute_metrics(0.90, home_score=110, away_score=100)
    assert m["actual_home_win"] is True
    assert m["correctly_picked_winner"] is True
    assert m["brier_score"] == pytest.approx((0.90 - 1) ** 2)
    assert m["log_loss"] == pytest.approx(-math.log(0.90), abs=1e-6)


def test_confident_but_wrong_is_penalized_heavily():
    # Predicted home 95%, home LOST.
    m = compute_metrics(0.95, home_score=95, away_score=100)
    assert m["actual_home_win"] is False
    assert m["correctly_picked_winner"] is False
    assert m["brier_score"] == pytest.approx(0.95**2)
    # log-loss on a confident miss is large
    assert m["log_loss"] > 2.5


def test_log_loss_finite_at_probability_one():
    # p=1.0 must be clamped so log-loss stays finite even when wrong.
    m = compute_metrics(1.0, home_score=90, away_score=100)
    assert math.isfinite(m["log_loss"])
    assert m["log_loss"] > 10


def test_edge_vs_close_and_beat_spread_when_closing_available():
    # Model 0.62 home, home favored by 3.5 at close, home wins by 6 (covers).
    m = compute_metrics(
        0.62, home_score=112, away_score=106,
        predicted_home_margin=5.0, closing_home_win_prob=0.58, closing_home_margin=3.5,
    )
    assert m["edge_vs_close"] == pytest.approx(0.04)
    # model_likes_home (5.0 > 3.5) and home covered (6 > 3.5) -> beat spread
    assert m["beat_spread"] is True


def test_beat_spread_model_likes_away_and_is_right():
    # Model predicts home margin 1.0 but close has home -3.5: model likes AWAY.
    # Home wins by only 2 (does NOT cover 3.5) -> away side wins -> model beat spread.
    m = compute_metrics(
        0.45, home_score=102, away_score=100,
        predicted_home_margin=1.0, closing_home_win_prob=0.58, closing_home_margin=3.5,
    )
    assert m["beat_spread"] is True


def test_push_and_missing_closing_line_are_none():
    push = compute_metrics(
        0.5, home_score=103, away_score=100,
        predicted_home_margin=1.0, closing_home_margin=3.0,  # actual margin == line
    )
    assert push["beat_spread"] is None

    no_close = compute_metrics(0.6, home_score=110, away_score=100)
    assert no_close["edge_vs_close"] is None
    assert no_close["beat_spread"] is None
