"""API tests: edge math, ORM->schema mapping, and route wiring. DB/network-free."""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from nba_bot.api import app as api
from nba_bot.db.models import Game, Prediction, Team


def _team(team_id: int, abbr: str, name: str) -> Team:
    return Team(team_id=team_id, abbreviation=abbr, full_name=name)


def test_edge_is_signed_difference_and_rounded():
    assert api._edge(0.71, 0.60) == 0.11
    assert api._edge(0.40, 0.55) == -0.15


def test_edge_is_none_without_market():
    assert api._edge(0.71, None) is None


def test_build_summary_maps_orm_and_casts_decimals():
    game = Game(
        game_id="0022500001",
        season="2025-26",
        game_date=datetime(2026, 1, 15).date(),
        home_team_id=1,
        away_team_id=2,
        home_score=110,
        away_score=104,
        status="final",
    )
    pred = Prediction(
        game_id="0022500001",
        model_version="test-v1",
        as_of=datetime(2026, 1, 15, 18, tzinfo=timezone.utc),
        # SQLAlchemy Numeric columns hand back Decimal; the schema must expose float.
        predicted_home_win_prob=Decimal("0.70"),
        market_home_win_prob=Decimal("0.62"),
        predicted_spread=Decimal("6.5"),
        market_spread=Decimal("4.0"),
    )
    s = api._build_summary(pred, game, _team(1, "BOS", "Boston Celtics"), _team(2, "MIA", "Miami Heat"))

    assert s.home.abbreviation == "BOS"
    assert s.away.full_name == "Miami Heat"
    assert isinstance(s.predicted_home_win_prob, float) and s.predicted_home_win_prob == 0.70
    assert s.edge == 0.08
    assert s.status == "final" and s.home_score == 110


def test_health_needs_no_database():
    resp = TestClient(api.app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_routes_are_registered():
    paths = {r.path for r in api.app.routes}
    assert {"/health", "/models", "/predictions", "/predictions/{game_id}", "/backtest"} <= paths
