"""Aggregate a backtest run into the metrics that tell the story: calibration
(does a predicted 70% win ~70% of the time?), accuracy, Brier/log-loss, and a
few context splits (home back-to-backs, confident picks).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from nba_bot.db.models import Game, Prediction, PredictionEvaluation


def calibration_table(pairs: list[tuple[float, bool]], n_bins: int = 5) -> list[dict]:
    """Bucket (predicted_home_win_prob, actual_home_win) pairs into probability
    bins and compare average predicted probability to the realized win rate.
    """
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for prob, actual in pairs:
        idx = min(int(prob * n_bins), n_bins - 1)
        bins[idx].append((prob, actual))

    rows = []
    for i, bucket in enumerate(bins):
        if not bucket:
            continue
        avg_pred = sum(p for p, _ in bucket) / len(bucket)
        actual_rate = sum(1 for _, a in bucket if a) / len(bucket)
        rows.append({
            "bin": f"{i / n_bins:.1f}-{(i + 1) / n_bins:.1f}",
            "n": len(bucket),
            "avg_predicted": round(avg_pred, 3),
            "actual_win_rate": round(actual_rate, 3),
        })
    return rows


def build_report(session: Session, model_version: str, n_bins: int = 5) -> dict:
    """Assemble the backtest report for all evaluated predictions of a model_version."""
    rows = session.execute(
        select(
            Prediction.predicted_home_win_prob,
            PredictionEvaluation.actual_home_win,
            PredictionEvaluation.brier_score,
            PredictionEvaluation.log_loss,
            PredictionEvaluation.correctly_picked_winner,
            Game.is_back_to_back_home,
        )
        .join(PredictionEvaluation, PredictionEvaluation.prediction_id == Prediction.id)
        .join(Game, Prediction.game_id == Game.game_id)
        .where(Prediction.model_version == model_version)
    ).all()

    if not rows:
        return {"model_version": model_version, "n": 0}

    n = len(rows)
    pairs = [(float(r.predicted_home_win_prob), r.actual_home_win) for r in rows]
    hits = sum(1 for r in rows if r.correctly_picked_winner)

    home_won = sum(1 for r in rows if r.actual_home_win)
    confident = [r for r in rows if abs(float(r.predicted_home_win_prob) - 0.5) >= 0.15]
    confident_hits = sum(1 for r in confident if r.correctly_picked_winner)
    b2b = [r for r in rows if r.is_back_to_back_home]
    b2b_hits = sum(1 for r in b2b if r.correctly_picked_winner)

    return {
        "model_version": model_version,
        "n": n,
        "winner_accuracy": round(hits / n, 3),
        "mean_brier": round(sum(float(r.brier_score) for r in rows) / n, 4),
        "mean_log_loss": round(sum(float(r.log_loss) for r in rows) / n, 4),
        "home_win_rate_actual": round(home_won / n, 3),
        "accuracy_confident_picks": (
            {"n": len(confident), "accuracy": round(confident_hits / len(confident), 3)}
            if confident else None
        ),
        "accuracy_home_back_to_back": (
            {"n": len(b2b), "accuracy": round(b2b_hits / len(b2b), 3)} if b2b else None
        ),
        "calibration": calibration_table(pairs, n_bins),
    }
