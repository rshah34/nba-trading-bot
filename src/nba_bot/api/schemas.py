"""Pydantic response models for the API. Kept separate from the ORM models so the
wire format is explicit and decoupled from the database schema.
"""

from __future__ import annotations

from pydantic import BaseModel


class CalibrationBin(BaseModel):
    bin: str
    n: int
    avg_predicted: float
    actual_win_rate: float


class SplitAccuracy(BaseModel):
    n: int
    accuracy: float


class BacktestReport(BaseModel):
    """Aggregate metrics for all evaluated predictions of a model version."""

    model_version: str
    n: int
    winner_accuracy: float | None = None
    mean_brier: float | None = None
    mean_log_loss: float | None = None
    home_win_rate_actual: float | None = None
    accuracy_confident_picks: SplitAccuracy | None = None
    accuracy_home_back_to_back: SplitAccuracy | None = None
    calibration: list[CalibrationBin] = []
