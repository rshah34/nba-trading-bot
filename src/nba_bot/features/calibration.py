"""Platt calibration for the model's win probabilities.

Every backtest run shows the same failure: the model is systematically
overconfident (says ~0.68 when the real rate is ~0.62). Calibration learns a
monotone map from raw probability → corrected probability that matches observed
frequencies, so downstream edge and bet-sizing use honest numbers.

Platt scaling fits a 2-parameter logistic on the raw probability's logit:

    calibrated = sigmoid(a * logit(raw) + b)

`a < 1` shrinks toward 0.5 (fixes overconfidence); `b` corrects a systematic
home/away lean. Two parameters is deliberate — with only hundreds of resolved
predictions, isotonic regression would overfit. Fit uses Platt's target smoothing
for robustness on small samples. Pure-Python (no numpy dep); the fit is a handful
of Newton steps over a few hundred points.

Always judge calibration by `cross_val_metrics` (out-of-sample) — fitting and
scoring on the same data would flatter it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

_EPS = 1e-12


@dataclass
class PlattParams:
    a: float
    b: float
    n: int  # predictions the fit was trained on


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1 + ez)


def fit_platt(raw_probs: list[float], outcomes: list[int], iters: int = 100) -> PlattParams:
    """Fit `sigmoid(a*logit(p)+b)` to outcomes by Newton's method (log-loss)."""
    xs = [_logit(p) for p in raw_probs]
    n_pos = sum(outcomes)
    n_neg = len(outcomes) - n_pos
    # Platt target smoothing — pull labels off {0,1} so the fit can't run to ±∞.
    hi = (n_pos + 1) / (n_pos + 2)
    lo = 1 / (n_neg + 2)
    ts = [hi if y == 1 else lo for y in outcomes]

    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, t in zip(xs, ts):
            q = _sigmoid(a * x + b)
            d = q - t
            w = q * (1 - q)
            ga += d * x
            gb += d
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < _EPS:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a -= da
        b -= db
        if abs(da) + abs(db) < 1e-9:
            break
    return PlattParams(a=a, b=b, n=len(outcomes))


def apply_platt(raw_prob: float, params: PlattParams) -> float:
    return round(_sigmoid(params.a * _logit(raw_prob) + params.b), 4)


def brier(probs: list[float], outcomes: list[int]) -> float:
    return round(sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(outcomes), 4)


def log_loss(probs: list[float], outcomes: list[int]) -> float:
    eps = 1e-15
    total = sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
        for p, y in zip(probs, outcomes)
    )
    return round(-total / len(outcomes), 4)


def cross_val_metrics(raw_probs: list[float], outcomes: list[int], k: int = 5, seed: int = 0) -> dict:
    """k-fold out-of-sample raw vs. calibrated Brier & log-loss.

    Fit Platt on k−1 folds, apply to the held-out fold, and score there — so the
    improvement (if any) is genuine generalization, not curve-fitting.
    """
    n = len(raw_probs)
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    folds = [idx[i::k] for i in range(k)]

    cal_oos = [0.0] * n
    for i in range(k):
        test = folds[i]
        train = [j for j in idx if j not in set(test)]
        params = fit_platt([raw_probs[j] for j in train], [outcomes[j] for j in train])
        for j in test:
            cal_oos[j] = apply_platt(raw_probs[j], params)

    return {
        "n": n,
        "k": k,
        "raw_brier": brier(raw_probs, outcomes),
        "cal_brier": brier(cal_oos, outcomes),
        "raw_log_loss": log_loss(raw_probs, outcomes),
        "cal_log_loss": log_loss(cal_oos, outcomes),
    }
