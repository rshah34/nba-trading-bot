"""FastAPI app serving the dashboard: tonight's slate, players, teams, news, the
picks and their record, and the live game model's accuracy.

Read-only: the pipeline writes to Postgres; this layer only serves what's there, so
the dashboard stays a thin client. Run locally with `uv run nba-bot serve`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from nba_bot.api import schemas, views
from nba_bot.backtest.report import build_report
from nba_bot.config import settings
from nba_bot.db.engine import SessionLocal

app = FastAPI(
    title="NBA Trading Bot API",
    description="Slate, player and team data, projections, news reports, picks, and model metrics.",
    version="0.2.0",
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


def _found(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return value


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/slate")
def slate(
    session: Session = Depends(get_session),
    on: date | None = Query(None, alias="date", description="Defaults to the next day with games."),
) -> dict:
    """One day's games with the model's and the market's view of each, plus that day's picks."""
    return views.slate(session, on)


@app.get("/slate/players")
def slate_players(
    session: Session = Depends(get_session),
    on: date | None = Query(None, alias="date", description="Defaults to the next day with games."),
) -> dict:
    """Every projected player on an upcoming date: availability, minutes, projections."""
    return views.slate_players(session, on)


@app.get("/games/{game_id}")
def game(game_id: str, session: Session = Depends(get_session)) -> dict:
    """A game in full: both teams' projected (and, once played, actual) box scores,
    injuries, news reports and picks."""
    return _found(views.game_detail(session, game_id), "game")


@app.get("/directory")
def directory(session: Session = Depends(get_session)) -> dict:
    """Every team and player on record, for the search box."""
    return views.directory(session)


@app.get("/players")
def players(
    session: Session = Depends(get_session),
    season: str | None = Query(None, description="e.g. 2025-26. Defaults to the newest."),
) -> dict:
    """Every player's averages for a season: full season, last 10 and last 5."""
    return views.players_index(session, season)


@app.get("/players/{player_id}")
def player(player_id: int, session: Session = Depends(get_session)) -> dict:
    """A player's game log, next-game projection (with P(over) at every line), news and status."""
    return _found(views.player_detail(session, player_id), "player")


@app.get("/teams")
def teams(
    session: Session = Depends(get_session),
    season: str | None = Query(None),
) -> dict:
    """Records and per-game production for and against every team."""
    return views.teams_index(session, season)


@app.get("/teams/{team_id}")
def team(team_id: int, session: Session = Depends(get_session)) -> dict:
    """A team's roster, recent results, injuries and news reports."""
    return _found(views.team_detail(session, team_id), "team")


@app.get("/news")
def news(
    session: Session = Depends(get_session),
    team_id: int | None = Query(None),
) -> list[dict]:
    """Active player reports, consolidated across articles, strongest first."""
    return views.news(session, team_id)


@app.get("/picks")
def picks(session: Session = Depends(get_session)) -> dict:
    """Every recorded pick (game winners and props) with the running records."""
    return views.picks(session)


@app.get("/game-model", response_model=schemas.BacktestReport)
def game_model(session: Session = Depends(get_session)) -> schemas.BacktestReport:
    """How the game model now in use has done on the games it predicted live:
    accuracy, Brier, log-loss and calibration. Empty until its first games are graded."""
    return schemas.BacktestReport(**build_report(session, settings.analysis_model))
