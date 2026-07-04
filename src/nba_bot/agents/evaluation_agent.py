"""Evaluation Agent: after games resolve, score each prediction against the
actual outcome and the closing line. Produces the metrics that make the project
defensible — calibration (Brier, log-loss), pick accuracy, and closing-line
value (CLV): did the model's estimate beat where the market closed?

Runs over predictions whose game is final and that don't yet have an evaluation,
so it is idempotent — re-running only scores newly-resolved games.
"""

from __future__ import annotations

import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nba_bot.db.models import Game, Odds, Prediction, PredictionEvaluation
from nba_bot.markets import market_line_from_odds

_EPS = 1e-6  # clamp probabilities away from 0/1 so log-loss stays finite


def compute_metrics(
    pred_home_win_prob: float,
    home_score: int,
    away_score: int,
    *,
    predicted_home_margin: float | None = None,
    closing_home_win_prob: float | None = None,
    closing_home_margin: float | None = None,
) -> dict:
    """Score one prediction against the final result and (optionally) the closing line.

    - brier_score / log_loss: probabilistic calibration (lower is better)
    - correctly_picked_winner: was the >50% side the actual winner
    - edge_vs_close (CLV): model prob minus the de-vigged closing prob
    - beat_spread: did the model's against-the-spread side cover (None on a push
      or when no closing spread is available)
    """
    actual_home_win = home_score > away_score
    outcome = 1.0 if actual_home_win else 0.0
    p = min(max(pred_home_win_prob, _EPS), 1 - _EPS)

    brier = (p - outcome) ** 2
    log_loss = -(outcome * math.log(p) + (1 - outcome) * math.log(1 - p))
    correctly_picked_winner = (p >= 0.5) == actual_home_win

    edge_vs_close = None
    if closing_home_win_prob is not None:
        edge_vs_close = p - closing_home_win_prob

    beat_spread = None
    if predicted_home_margin is not None and closing_home_margin is not None:
        actual_margin = home_score - away_score
        if actual_margin != closing_home_margin:  # otherwise a push -> None
            home_covered = actual_margin > closing_home_margin
            model_likes_home = predicted_home_margin > closing_home_margin
            beat_spread = home_covered if model_likes_home else not home_covered

    return {
        "actual_home_win": actual_home_win,
        "brier_score": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "correctly_picked_winner": correctly_picked_winner,
        "edge_vs_close": round(edge_vs_close, 6) if edge_vs_close is not None else None,
        "beat_spread": beat_spread,
    }


def _unevaluated_predictions(session: Session) -> list[Prediction]:
    """Predictions whose game is final and that don't yet have an evaluation."""
    stmt = (
        select(Prediction)
        .join(Game, Prediction.game_id == Game.game_id)
        .outerjoin(PredictionEvaluation, PredictionEvaluation.prediction_id == Prediction.id)
        .where(
            Game.status == "final",
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
            PredictionEvaluation.id.is_(None),
        )
    )
    return list(session.execute(stmt).scalars().all())


def run_evaluation(session: Session) -> dict:
    """Evaluate every newly-resolved prediction; return how many were scored."""
    evaluated = 0
    for pred in _unevaluated_predictions(session):
        game = session.get(Game, pred.game_id)
        closing_rows = session.execute(
            select(Odds).where(Odds.game_id == pred.game_id, Odds.is_closing_line.is_(True))
        ).scalars().all()
        closing = market_line_from_odds(list(closing_rows))

        metrics = compute_metrics(
            float(pred.predicted_home_win_prob),
            game.home_score,
            game.away_score,
            predicted_home_margin=float(pred.predicted_spread)
            if pred.predicted_spread is not None
            else None,
            closing_home_win_prob=closing.home_win_prob,
            closing_home_margin=closing.home_margin,
        )
        session.add(PredictionEvaluation(prediction_id=pred.id, **metrics))
        evaluated += 1

    session.commit()
    return {"evaluated": evaluated, **summary(session)}


def summary(session: Session) -> dict:
    """Aggregate metrics over all evaluations so far (the track record)."""
    count, brier, log_loss, clv = session.execute(
        select(
            func.count(PredictionEvaluation.id),
            func.avg(PredictionEvaluation.brier_score),
            func.avg(PredictionEvaluation.log_loss),
            func.avg(PredictionEvaluation.edge_vs_close),
        )
    ).one()
    hits = session.execute(
        select(func.count()).where(PredictionEvaluation.correctly_picked_winner.is_(True))
    ).scalar_one()
    return {
        "n_evaluations": count or 0,
        "mean_brier": round(float(brier), 4) if brier is not None else None,
        "mean_log_loss": round(float(log_loss), 4) if log_loss is not None else None,
        "winner_hit_rate": round(hits / count, 4) if count else None,
        "mean_clv": round(float(clv), 4) if clv is not None else None,
    }
