"""API tests: route wiring and the pure helpers behind the dashboard. DB/network-free."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from nba_bot.api import app as api
from nba_bot.api import views
from nba_bot.features.props import GameLine, project


def _log(n, minutes=30.0, pts=20, reb=5, ast=4):
    return [GameLine(game_id=f"g{i}", game_date=date(2026, 1, 1) + timedelta(days=2 * i),
                     season="2025-26", team_id=1, opp_id=2, minutes=minutes, pts=pts, reb=reb, ast=ast)
            for i in range(n)]


def test_health_needs_no_database():
    resp = TestClient(api.app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_routes_are_registered():
    paths = {r.path for r in api.app.routes}
    assert {"/health", "/slate", "/games/{game_id}", "/players", "/players/{player_id}", "/teams",
            "/teams/{team_id}", "/news", "/picks", "/models", "/backtest"} <= paths


def test_averages_over_a_season_and_a_recent_window():
    log = _log(10, minutes=20, pts=10) + _log(5, minutes=30, pts=22)
    season = views.averages(log)
    assert (season["gp"], season["pts"], season["min"]) == (15, 14.0, 23.3)
    assert views.averages(log, 5) == {"gp": 5, "min": 30.0, "pts": 22.0, "reb": 5.0, "ast": 4.0,
                                      "fg3m": 0.0, "stl": 0.0, "blk": 0.0, "tov": 0.0}
    assert views.averages([]) is None


def test_ladder_is_a_falling_probability_at_every_half_point():
    proj = project(_log(30, pts=24))
    ladder = views._ladder(proj, "player_points")
    lines, probs = [row[0] for row in ladder], [row[1] for row in ladder]
    assert lines[0] == 0.5 and all(b - a == 1 for a, b in zip(lines, lines[1:]))
    assert probs == sorted(probs, reverse=True)          # a higher line is harder to clear
    assert probs[0] > 0.99 and probs[-1] < 0.05          # and the ladder spans the range
    assert lines[-1] > 24                                # well past the projection
