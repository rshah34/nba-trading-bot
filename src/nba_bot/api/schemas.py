"""Pydantic response models for the API. Kept separate from the ORM models so the
wire format is explicit and decoupled from the database schema.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class TeamRef(BaseModel):
    team_id: int
    abbreviation: str
    full_name: str


class PredictionSummary(BaseModel):
    """One prediction with its matchup and the edge vs. the market."""

    game_id: str
    game_date: date
    season: str
    status: str
    home: TeamRef
    away: TeamRef
    home_score: int | None
    away_score: int | None
    model_version: str
    as_of: datetime
    predicted_home_win_prob: float
    market_home_win_prob: float | None
    # predicted minus market, positive = model likes the home side more than the book.
    edge: float | None
    predicted_spread: float | None
    market_spread: float | None


class PredictionDetail(PredictionSummary):
    """A single prediction, plus the model's reasoning and the context it saw."""

    reasoning: str | None
    context_used: dict | None


class ModelInfo(BaseModel):
    model_version: str
    n_predictions: int
    latest_as_of: datetime | None


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


class BetRow(BaseModel):
    """One recorded paper bet with its matchup and (once settled) CLV + P&L."""

    game_id: str
    game_date: date
    status: str
    home: TeamRef
    away: TeamRef
    home_score: int | None
    away_score: int | None
    model_version: str
    side: str                    # 'home' | 'away'
    edge: float
    model_prob: float
    market_prob: float
    decimal_odds: float
    stake_fraction: float
    settled: bool
    won: bool | None
    pnl: float | None
    clv: float | None            # prob points beaten vs. the close (north-star metric)
    closing_decimal_odds: float | None


class BetsSummary(BaseModel):
    """Paper-trade track record over settled bets (+ how many are still open)."""

    n_bets: int                  # settled
    n_pending: int               # placed but not yet settled
    win_rate: float | None
    roi: float | None
    final_bankroll: float
    avg_clv: float | None
    clv_positive_rate: float | None
