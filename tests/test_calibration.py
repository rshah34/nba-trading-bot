"""Platt calibration: recovery, overconfidence correction, CV. Pure functions."""

import random

from nba_bot.features.calibration import (
    apply_platt,
    brier,
    cross_val_metrics,
    fit_platt,
)


def _make_overconfident(n=2000, seed=0):
    """Synthesize predictions whose TRUE win prob is a shrunk version of the stated
    one — i.e. a systematically overconfident model, exactly our observed failure."""
    rng = random.Random(seed)
    raw, outcomes = [], []
    for _ in range(n):
        p = rng.uniform(0.05, 0.95)
        true_p = 0.5 + (p - 0.5) * 0.6  # overconfident: stated spread 1.67x the real one
        raw.append(p)
        outcomes.append(1 if rng.random() < true_p else 0)
    return raw, outcomes


def test_apply_shrinks_overconfident_probabilities_toward_half():
    raw, outcomes = _make_overconfident()
    params = fit_platt(raw, outcomes)
    assert params.a < 1.0  # learns to shrink
    # a confident 0.85 prediction should move toward 0.5
    assert 0.5 < apply_platt(0.85, params) < 0.85


def test_calibration_improves_brier_on_overconfident_data():
    raw, outcomes = _make_overconfident()
    params = fit_platt(raw, outcomes)
    cal = [apply_platt(p, params) for p in raw]
    assert brier(cal, outcomes) < brier(raw, outcomes)


def test_fit_recovers_identity_on_already_calibrated_data():
    # If stated prob == true prob, Platt should stay near a=1, b=0 (no-op).
    rng = random.Random(1)
    raw = [rng.uniform(0.05, 0.95) for _ in range(3000)]
    outcomes = [1 if rng.random() < p else 0 for p in raw]
    params = fit_platt(raw, outcomes)
    assert abs(params.a - 1.0) < 0.2
    assert abs(params.b) < 0.2


def test_cross_val_metrics_reports_out_of_sample():
    raw, outcomes = _make_overconfident()
    cv = cross_val_metrics(raw, outcomes, k=5)
    assert cv["n"] == len(raw)
    assert cv["cal_brier"] < cv["raw_brier"]  # OOS gain on genuinely miscalibrated data
