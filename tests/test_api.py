"""API tests: route wiring and the pure helpers behind the dashboard. DB/network-free."""

from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

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
    assert {"/health", "/slate", "/slate/players", "/games/{game_id}", "/directory", "/players", "/players/{player_id}", "/teams",
            "/teams/{team_id}", "/news", "/picks", "/game-model", "/research"} <= paths


def test_averages_over_a_season_and_a_recent_window():
    log = _log(10, minutes=20, pts=10) + _log(5, minutes=30, pts=22)
    season = views.averages(log)
    assert (season["gp"], season["pts"], season["min"]) == (15, 14.0, 23.3)
    assert views.averages(log, 5) == {"gp": 5, "min": 30.0, "pts": 22.0, "reb": 5.0, "ast": 4.0,
                                      "fg3m": 0.0, "stl": 0.0, "blk": 0.0, "tov": 0.0,
                                      "fgm": 0.0, "fga": 0.0, "ftm": 0.0, "fta": 0.0}
    assert views.averages([]) is None


def test_directory_lists_a_player_who_missed_the_newest_season(monkeypatch):
    current = _log(3, pts=10)
    older = [replace(g, season="2024-25", pts=25) for g in _log(4)]
    data = SimpleNamespace(
        names={7: "Played Lately", 8: "Out All Year", 9: "Preseason Rookie"},
        logs={7: current, 8: older},
        current_team={7: 1, 8: 1},
        team_games={1: current + older},
    )
    monkeypatch.setattr(views, "props_data", lambda session: data)
    monkeypatch.setattr(views, "team_map", lambda session: {1: {"id": 1, "abbr": "DAL", "name": "Dallas"}})

    out = views.directory(None)

    assert out["season"] == "2025-26"
    assert [(p["name"], p["team"], p["last_season"], p["pts"]) for p in out["players"]] == [
        ("Played Lately", "DAL", "2025-26", 10.0),
        ("Out All Year", "DAL", "2024-25", 25.0),
        ("Preseason Rookie", None, None, None),
    ]


def test_players_index_shows_the_team_he_played_for_that_season(monkeypatch):
    # Traded since: last season's numbers belong under last season's team.
    data = SimpleNamespace(names={7: "Moved On"}, logs={7: _log(3)}, current_team={7: 2},
                           team_games={1: _log(3)})
    monkeypatch.setattr(views, "props_data", lambda session: data)
    monkeypatch.setattr(views, "team_map", lambda session: {
        1: {"id": 1, "abbr": "TOR", "name": "Toronto"}, 2: {"id": 2, "abbr": "LAC", "name": "LA"}})

    row = views.players_index(None, "2025-26")["players"][0]

    assert (row["team"], row["team_id"]) == ("TOR", 1)


def test_ladder_is_a_falling_probability_at_every_half_point():
    proj = project(_log(30, pts=24))
    ladder = views._ladder(proj, "player_points")
    lines, probs = [row[0] for row in ladder], [row[1] for row in ladder]
    assert lines[0] == 0.5 and all(b - a == 1 for a, b in zip(lines, lines[1:]))
    assert probs == sorted(probs, reverse=True)          # a higher line is harder to clear
    assert probs[0] > 0.99 and probs[-1] < 0.05          # and the ladder spans the range
    assert lines[-1] > 24                                # well past the projection


def test_injury_return_date_is_read_or_left_empty():
    from datetime import date

    from nba_bot.data.injuries import _return_date

    assert _return_date({"details": {"returnDate": "2026-10-13"}}) == date(2026, 10, 13)
    assert _return_date({"details": {}}) is None and _return_date({}) is None
    assert _return_date({"details": {"returnDate": "soon"}}) is None


def test_a_research_finding_is_condensed_for_the_page():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    side = lambda bias: {"rows": 900, "players": 120,  # noqa: E731
                         "pts": {"bias": bias, "ci95": [bias - 0.2, bias + 0.2], "mae": 4.9}}
    f = SimpleNamespace(
        id=7, run_id=2, created_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
        hypothesis="tired legs", rationale="back-to-backs", stat="pts", multiplier=0.97,
        slice=[{"field": "b2b", "op": "==", "value": True}], verdict="pass", applied_at=None,
        explore={"in_slice": {"before": side(0.5)}},
        confirm={"in_slice": {"before": side(0.4)},
                 "whole_model": {"mae_before": 2.245, "mae_after": 2.244,
                                 "brier_before": 0.2247, "brier_after": 0.2246}},
        reasons=["held-back bias +0.400"],
    )
    row = views._finding(f)
    assert row["slice"] == ["back-to-back"] and row["held"]["bias"] == 0.4
    assert row["explore"]["rows"] == 900 and row["mae"] == [2.245, 2.244]


def test_slices_read_as_plain_words():
    assert views._slice_words([
        {"field": "home", "op": "==", "value": False},
        {"field": "role", "op": "in", "value": ["starter", "star"]},
        {"field": "rest_days", "op": ">=", "value": 2},
        {"field": "expected_margin", "op": "between", "value": [-3, 3]},
    ]) == ["away", "role: starter or star", "rest days ≥ 2", "expected margin -3–3"]
    assert views._slice_words([]) == ["every player-game"]


def test_research_runs_start_only_from_this_machine():
    from fastapi.testclient import TestClient

    resp = TestClient(api.app).post("/research/runs", json={"budget": 1})
    assert resp.status_code == 403            # the test client is not localhost


def test_qualified_follows_the_nbas_seventy_percent_rule():
    assert views.qualified(58, 82) and not views.qualified(57, 82)
    assert views.qualified(2, 2) and not views.qualified(1, 2)   # 70% of 2 rounds up to 2
    assert not views.qualified(0, 0)
