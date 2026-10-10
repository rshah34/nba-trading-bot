"""FastAPI app exposing predictions, edges, and backtest metrics as JSON.

Read-only: the pipeline (data/analysis/evaluation agents) writes to Postgres;
this layer only serves what's there, so the dashboard can stay a thin client.
Run locally with: `uv run nba-bot serve` (or `uvicorn nba_bot.api.app:app`).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from nba_bot.agents import betting_agent
from nba_bot.api import schemas
from nba_bot.backtest.report import build_report
from nba_bot.db.engine import SessionLocal
from nba_bot.db.models import Bet, Game, Prediction, Team

app = FastAPI(
    title="NBA Trading Bot API",
    description="Model win probabilities, market edges, and backtest metrics.",
    version="0.1.0",
)

# Allow a separately-hosted dashboard (any origin) to read the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def _edge(pred: float, market: float | None) -> float | None:
    return round(pred - market, 4) if market is not None else None


def _build_summary(pred: Prediction, game: Game, home: Team, away: Team) -> schemas.PredictionSummary:
    market = float(pred.market_home_win_prob) if pred.market_home_win_prob is not None else None
    predicted = float(pred.predicted_home_win_prob)
    return schemas.PredictionSummary(
        game_id=pred.game_id,
        game_date=game.game_date,
        season=game.season,
        status=game.status,
        home=schemas.TeamRef(team_id=home.team_id, abbreviation=home.abbreviation, full_name=home.full_name),
        away=schemas.TeamRef(team_id=away.team_id, abbreviation=away.abbreviation, full_name=away.full_name),
        home_score=game.home_score,
        away_score=game.away_score,
        model_version=pred.model_version,
        as_of=pred.as_of,
        predicted_home_win_prob=predicted,
        market_home_win_prob=market,
        edge=_edge(predicted, market),
        predicted_spread=float(pred.predicted_spread) if pred.predicted_spread is not None else None,
        market_spread=float(pred.market_spread) if pred.market_spread is not None else None,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/models", response_model=list[schemas.ModelInfo])
def list_models(session: Session = Depends(get_session)) -> list[schemas.ModelInfo]:
    """Model versions present in the store, most-predicted first."""
    rows = session.execute(
        select(
            Prediction.model_version,
            func.count().label("n"),
            func.max(Prediction.as_of).label("latest"),
        )
        .group_by(Prediction.model_version)
        .order_by(func.count().desc())
    ).all()
    return [
        schemas.ModelInfo(model_version=r.model_version, n_predictions=r.n, latest_as_of=r.latest)
        for r in rows
    ]


@app.get("/predictions", response_model=list[schemas.PredictionSummary])
def list_predictions(
    session: Session = Depends(get_session),
    model_version: str | None = Query(None, description="Filter to one model version."),
    upcoming: bool = Query(False, description="Only games not yet final."),
    limit: int = Query(50, ge=1, le=200),
) -> list[schemas.PredictionSummary]:
    """Recent predictions with their matchup and edge vs. the market."""
    home = aliased(Team)
    away = aliased(Team)
    stmt = (
        select(Prediction, Game, home, away)
        .join(Game, Prediction.game_id == Game.game_id)
        .join(home, Game.home_team_id == home.team_id)
        .join(away, Game.away_team_id == away.team_id)
        .order_by(Game.game_date.desc(), Prediction.as_of.desc())
        .limit(limit)
    )
    if model_version:
        stmt = stmt.where(Prediction.model_version == model_version)
    if upcoming:
        stmt = stmt.where(Game.status != "final")

    return [_build_summary(pred, game, h, a) for pred, game, h, a in session.execute(stmt).all()]


@app.get("/predictions/{game_id}", response_model=schemas.PredictionDetail)
def get_prediction(
    game_id: str,
    session: Session = Depends(get_session),
    model_version: str | None = Query(None),
) -> schemas.PredictionDetail:
    """Full detail for one game's prediction, including the model's reasoning."""
    home = aliased(Team)
    away = aliased(Team)
    stmt = (
        select(Prediction, Game, home, away)
        .join(Game, Prediction.game_id == Game.game_id)
        .join(home, Game.home_team_id == home.team_id)
        .join(away, Game.away_team_id == away.team_id)
        .where(Prediction.game_id == game_id)
        .order_by(Prediction.as_of.desc())
        .limit(1)
    )
    if model_version:
        stmt = stmt.where(Prediction.model_version == model_version)

    row = session.execute(stmt).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No prediction for game {game_id}")

    pred, game, h, a = row
    summary = _build_summary(pred, game, h, a)
    return schemas.PredictionDetail(
        **summary.model_dump(),
        reasoning=pred.reasoning,
        context_used=pred.context_used,
    )


def _bet_row(bet: Bet, game: Game, home: Team, away: Team) -> schemas.BetRow:
    def f(v):
        return float(v) if v is not None else None
    return schemas.BetRow(
        game_id=bet.game_id, game_date=game.game_date, status=game.status,
        home=schemas.TeamRef(team_id=home.team_id, abbreviation=home.abbreviation, full_name=home.full_name),
        away=schemas.TeamRef(team_id=away.team_id, abbreviation=away.abbreviation, full_name=away.full_name),
        home_score=game.home_score, away_score=game.away_score,
        model_version=bet.model_version, side=bet.side, edge=float(bet.edge),
        model_prob=float(bet.model_prob), market_prob=float(bet.market_prob),
        decimal_odds=float(bet.decimal_odds), stake_fraction=float(bet.stake_fraction),
        settled=bet.settled_at is not None, won=bet.won, pnl=f(bet.pnl), clv=f(bet.clv),
        closing_decimal_odds=f(bet.closing_decimal_odds),
    )


@app.get("/bets", response_model=list[schemas.BetRow])
def list_bets(
    session: Session = Depends(get_session),
    model_version: str | None = Query(None, description="Filter to one model version."),
    limit: int = Query(100, ge=1, le=500),
) -> list[schemas.BetRow]:
    """Recorded paper bets (most recent first) — the bet log."""
    home = aliased(Team)
    away = aliased(Team)
    stmt = (
        select(Bet, Game, home, away)
        .join(Game, Bet.game_id == Game.game_id)
        .join(home, Game.home_team_id == home.team_id)
        .join(away, Game.away_team_id == away.team_id)
        .order_by(Bet.decided_at.desc())
        .limit(limit)
    )
    if model_version:
        stmt = stmt.where(Bet.model_version == model_version)
    return [_bet_row(b, g, h, a) for b, g, h, a in session.execute(stmt).all()]


@app.get("/bets/summary", response_model=schemas.BetsSummary)
def bets_summary(
    session: Session = Depends(get_session),
    model_version: str | None = Query(None),
) -> schemas.BetsSummary:
    """Paper-trade track record (CLV, ROI, record) over settled bets, plus open count."""
    rec = betting_agent.track_record(session, model_version)
    pending_stmt = select(func.count()).select_from(Bet).where(Bet.settled_at.is_(None))
    if model_version:
        pending_stmt = pending_stmt.where(Bet.model_version == model_version)
    n_pending = session.execute(pending_stmt).scalar_one()
    return schemas.BetsSummary(**rec, n_pending=n_pending)


@app.get("/backtest", response_model=schemas.BacktestReport)
def backtest(
    session: Session = Depends(get_session),
    model_version: str | None = Query(None, description="Defaults to the most-predicted model."),
) -> schemas.BacktestReport:
    """Calibration and scoring metrics over all evaluated predictions of a model."""
    if model_version is None:
        model_version = session.execute(
            select(Prediction.model_version)
            .group_by(Prediction.model_version)
            .order_by(func.count().desc())
            .limit(1)
        ).scalar_one_or_none()
    if model_version is None:
        raise HTTPException(status_code=404, detail="No predictions in the store yet.")

    return schemas.BacktestReport(**build_report(session, model_version))
