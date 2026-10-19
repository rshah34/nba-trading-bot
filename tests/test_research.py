"""Research agent tests. DB/network-free."""

from datetime import date, datetime, timedelta, timezone

from nba_bot.features.props import GameInfo, GameLine, PropsData
from nba_bot.research import table

SEASON = "2025-26"
A, B = 1610612738, 1610612743     # Boston, and Denver for the altitude flag


def _season(games: int = 16, sit: tuple[int, int] | None = None) -> PropsData:
    """Two teams playing each other `games` times, home and away in turn, with a
    back-to-back at game 12. `sit` = (player, game) keeps one player out of one game."""
    day, dates = date(2025, 10, 22), []
    for i in range(games):
        dates.append(day)
        day += timedelta(days=1 if i == 10 else 2)
    infos = [GameInfo(f"g{i}", d, SEASON, A if i % 2 == 0 else B, B if i % 2 == 0 else A)
             for i, d in enumerate(dates)]
    logs, names, team_box = {}, {}, {}
    for team, base in ((A, 100), (B, 200)):
        for k in range(9):
            pid = base + k
            names[pid] = f"p{pid}"
            logs[pid] = [
                GameLine(g.game_id, g.game_date, SEASON, team, B if team == A else A,
                         minutes=34.0 - 2 * k, pts=24 - 2 * k, reb=6, ast=4, fg3m=2,
                         stl=1, blk=1, tov=2, fgm=8, fga=17, ftm=4, fta=5)
                for i, g in enumerate(infos) if sit != (pid, i)
            ]
        for g in infos:
            team_box[(g.game_id, team)] = {"pts": 110.0, "reb": 44.0, "ast": 25.0, "stl": 8.0,
                                           "blk": 5.0, "fg3m": 13.0, "tov": 14.0}
    return PropsData(
        logs=logs, names=names,
        current_team={pid: (A if pid < 200 else B) for pid in logs},
        last_seen={pid: log[-1].game_date for pid, log in logs.items()},
        team_games={A: infos, B: infos}, team_box=team_box,
    )


def test_table_rows_carry_the_schedule_and_outcomes():
    df = table.build(_season(), seasons=(SEASON,))
    assert len(df) and set(df.season) == {SEASON}
    assert (df.games_played >= 8).all()          # the model needs 8 games to project
    b2b = df[df.team_game_number == 12]
    assert b2b.b2b.all() and b2b.rest_days.eq(0).all()
    assert df[df.b2b == False].rest_days.ge(1).all()   # noqa: E712
    # Boston's road games are at Denver: altitude.
    assert df[(df.team_id == A) & ~df.home].at_altitude.all()
    assert not df[df.team_id == B].at_altitude.any()
    pra = df.proj_pts + df.proj_reb + df.proj_ast
    assert (abs(df.proj_pra - pra) < 1e-9).all()
    assert (df.actual_pra == df.actual_pts + df.actual_reb + df.actual_ast).all()


def test_table_counts_who_is_missing_from_last_game():
    df = table.build(_season(sit=(100, 13)), seasons=(SEASON,))
    tonight = df[(df.team_id == A) & (df.team_game_number == 14)]
    assert 100 not in set(tonight.player_id)
    assert tonight.players_out.eq(1).all() and tonight.minutes_out.gt(30).all()
    # His minutes went to teammates, through the minutes budget.
    assert tonight.budget_minutes_mult.gt(1).all()
    assert df[(df.team_id == A) & (df.team_game_number == 13)].players_out.eq(0).all()


# --------------------------------------------------------------------------- #
# Tools and the rules a finding has to pass.
# --------------------------------------------------------------------------- #
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from nba_bot.research import tools  # noqa: E402


def _table(planted: float = 0.88, seed: int = 1) -> pd.DataFrame:
    """Two seasons of fake player-games where the model is right on average, except
    that on back-to-backs real scoring is `planted` × the projection."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in (tools.EXPLORE, tools.CONFIRM):
        for player in range(150):
            level = rng.uniform(6, 26)
            for _ in range(40):
                b2b = rng.random() < 0.2
                # home is a coin flip here: pure noise.
                row = {"season": season, "player_id": player, "b2b": b2b,
                       "home": bool(rng.random() < 0.5), "rest_days": 0 if b2b else 1,
                       "role": "starter" if level > 15 else "bench"}
                for t, scale in (("pts", 1.0), ("reb", 0.4), ("ast", 0.3), ("fg3m", 0.1),
                                 ("stl", 0.05), ("blk", 0.04), ("tov", 0.1)):
                    proj = level * scale
                    truth = proj * (planted if b2b and t == "pts" else 1.0)
                    row[f"proj_{t}"], row[f"actual_{t}"] = proj, float(rng.poisson(truth))
                row["proj_pra"] = row["proj_pts"] + row["proj_reb"] + row["proj_ast"]
                row["actual_pra"] = row["actual_pts"] + row["actual_reb"] + row["actual_ast"]
                for t in table.TARGETS:
                    row[f"phi_{t}"] = 1.0
                    row[f"line_{t}"] = np.floor(row[f"proj_{t}"]) + 0.5
                    row[f"over_{t}"] = row[f"actual_{t}"] > row[f"line_{t}"]
                rows.append(row)
    return pd.DataFrame(rows)


B2B = [{"field": "b2b", "op": "==", "value": True}]
NOISE = [{"field": "home", "op": "==", "value": True}]


@pytest.fixture(scope="module")
def ws_table():
    return _table()


def test_measure_finds_the_planted_bias(ws_table):
    ws = tools.Workspace(ws_table)
    b2b = ws.measure(B2B)["overall"]["pts"]
    assert b2b["bias"] > 0 and b2b["ci95"][0] > 0          # over-projected, clearly
    rest = ws.measure([{"field": "b2b", "op": "==", "value": False}])["overall"]["pts"]
    assert rest["ci95"][0] < 0 < rest["ci95"][1]            # right on average elsewhere
    split = ws.measure(by="b2b")["by"]
    assert set(split) == {"True", "False"}


def test_try_fix_fits_the_multiplier_and_helps(ws_table):
    ws = tools.Workspace(ws_table)
    fix = ws.try_fix(B2B, "pts")
    assert 0.84 < fix["multiplier"] < 0.92
    assert abs(fix["in_slice"]["after"]["pts"]["bias"]) < 0.05
    assert fix["whole_model"]["mae_after"] < fix["whole_model"]["mae_before"]


def test_a_real_effect_passes_confirmation(ws_table):
    ws = tools.Workspace(ws_table)
    m = ws.try_fix(B2B, "pts")["multiplier"]
    result = ws.confirm("starters score less on back-to-backs", B2B, "pts", m)
    assert result.verdict == "pass", result.reasons
    assert ws.confirms_left == tools.CONFIRMS_PER_RUN - 1


def test_noise_fails_confirmation():
    clean = _table(planted=1.0, seed=2)      # nothing to find
    for m in (0.97, 1.03):
        # The minimum-gain gate is set aside here so the held-back checks are what decide.
        ws = tools.Workspace(clean, min_gain=-1)
        result = ws.confirm("home court moves scoring", NOISE, "pts", m)
        assert result.verdict == "fail", (m, result.reasons)


def test_a_fix_too_small_to_matter_never_reaches_the_held_back_season():
    ws = tools.Workspace(_table(planted=1.0, seed=2))
    with pytest.raises(tools.ToolError, match="not worth a held-back look"):
        ws.confirm("home court moves scoring", NOISE, "pts", 0.99)
    assert ws.confirms_left == tools.CONFIRMS_PER_RUN and ws.confirmations == []
    assert ws.describe()["rules"]["confirm_needs"].startswith("a whole-model MAE gain")


def test_the_bar_rises_with_every_confirmation_ever_run(ws_table):
    fresh = tools.Workspace(ws_table).confirm("x", B2B, "pts", 0.9)
    worn = tools.Workspace(ws_table, prior_confirmations=40).confirm("x", B2B, "pts", 0.9)
    assert worn.threshold_z > fresh.threshold_z


def test_tools_refuse_what_would_leak_or_waste_the_held_back_season(ws_table):
    ws = tools.Workspace(ws_table, confirms_per_run=1)
    with pytest.raises(tools.ToolError, match="post-game"):
        ws.measure([{"field": "actual_pts", "op": ">", "value": 20}])
    tiny = [{"field": "rest_days", "op": ">=", "value": 3}]
    with pytest.raises(tools.ToolError, match="too small"):
        ws.confirm("one player", tiny, "pts", 0.9)
    assert ws.confirms_left == 1                     # a refusal costs nothing
    with pytest.raises(tools.ToolError, match="multiplier"):
        ws.confirm("huge", B2B, "pts", 0.5)
    ws.confirm("x", B2B, "pts", 0.9)
    with pytest.raises(tools.ToolError, match="no confirmations left"):
        ws.confirm("y", B2B, "pts", 0.9)


# --------------------------------------------------------------------------- #
# The agent loop, with a scripted stand-in for Claude.
# --------------------------------------------------------------------------- #
from types import SimpleNamespace  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from nba_bot.db.models import ResearchFinding, ResearchRun  # noqa: E402
from nba_bot.research import agent  # noqa: E402


class _ScriptedClaude:
    """Replies with the given tool calls, one turn each, and records every request."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})  # as sent
        calls = self.turns.pop(0)
        content = [SimpleNamespace(type="tool_use", id=f"t{len(self.requests)}{i}", name=n, input=a)
                   for i, (n, a) in enumerate(calls)]
        return SimpleNamespace(content=content, stop_reason="tool_use", model=agent.MODEL,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    agent_tables = [ResearchRun.__table__, ResearchFinding.__table__]
    ResearchRun.metadata.create_all(engine, tables=agent_tables)
    with Session(engine) as session:
        yield session


def _run(db, ws_table, turns, monkeypatch, cost=0.01, budget=2.0, mode="confirm"):
    monkeypatch.setattr(agent.usage, "record", lambda *a, **k: cost)
    claude = _ScriptedClaude(turns)
    run = agent.start_run(db, focus=None, budget=budget, mode=mode)
    return agent.run_research(db, run, ws_table, client=claude), claude


def test_a_run_explores_confirms_records_and_finishes(db, ws_table, monkeypatch):
    run, claude = _run(db, ws_table, [
        [("describe", {})],
        [("measure", {"where": B2B}), ("try_fix", {"where": B2B, "stat": "pts"})],
        [("confirm", {"hypothesis": "tired legs", "rationale": "back-to-backs", "where": B2B,
                      "stat": "pts", "multiplier": 0.88})],
        [("finish", {"summary": "Back-to-backs are over-projected.",
                     "explored": [{"idea": "home court", "outcome": "nothing there"}]})],
    ], monkeypatch)
    assert (run.status, run.turns, float(run.cost_usd)) == ("done", 4, 0.04)
    assert run.explored == [{"idea": "home court", "outcome": "nothing there"}]
    finding = db.query(ResearchFinding).one()
    assert finding.verdict == "pass" and finding.slice == B2B and finding.run_id == run.id
    # Both calls of turn 2 were answered together, in one message.
    results = claude.requests[2]["messages"][-1]["content"]
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]
    # The next run sees the idea, and the season is one confirmation more worn.
    assert agent.confirmations_so_far(db) == 1
    assert any("tired legs" in line and "PASSED" in line for line in agent.prior_ideas(db))
    assert any("home court" in line for line in agent.prior_ideas(db))


def test_bad_calls_come_back_as_errors_and_the_run_goes_on(db, ws_table, monkeypatch):
    run, claude = _run(db, ws_table, [
        [("measure", {"where": [{"field": "actual_pts", "op": ">", "value": 1}]})],
        [("try_fix", {"where": B2B})],                  # no stat
        [("finish", {"summary": "nothing", "explored": []})],
    ], monkeypatch)
    assert run.status == "done"
    first_error = claude.requests[1]["messages"][-1]["content"][0]
    assert first_error["is_error"] and "post-game" in first_error["content"]
    assert claude.requests[2]["messages"][-1]["content"][0]["is_error"]


def test_the_budget_warns_then_stops(db, ws_table, monkeypatch):
    run, claude = _run(db, ws_table, [[("describe", {})]] * 5, monkeypatch, cost=0.45, budget=1.0)
    assert run.status == "out_of_budget" and run.turns == 3
    warning = claude.requests[2]["messages"][-1]
    assert warning["role"] == "system" and "Budget nearly spent" in warning["content"]


def test_an_explore_only_run_never_sees_confirm_and_keeps_its_candidates(db, ws_table, monkeypatch):
    candidate = {"hypothesis": "tired legs", "rationale": "back-to-backs", "where": B2B,
                 "stat": "pts", "multiplier": 0.88, "evidence": "+1.6 points, clear of zero"}
    run, claude = _run(db, ws_table, [
        [("try_fix", {"where": B2B, "stat": "pts"})],
        [("confirm", {"hypothesis": "x", "rationale": "y", "where": B2B, "stat": "pts",
                      "multiplier": 0.88})],                     # not offered: must not run
        [("finish", {"summary": "one lead", "explored": [], "candidates": [candidate]})],
    ], monkeypatch, mode="explore")
    assert "confirm" not in {t["name"] for t in claude.requests[0]["tools"]}
    assert "explore-only" in claude.requests[0]["messages"][0]["content"]
    assert run.prompt == claude.requests[0]["messages"][0]["content"]   # what the page shows
    assert db.query(ResearchFinding).count() == 0 and agent.confirmations_so_far(db) == 0
    assert run.candidates == [candidate]
    assert [line.split(":")[0] for line in (e["line"] for e in run.log)] == [
        "try_fix", "confirm", "finish"]
    assert any("candidate from an explore-only run" in line for line in agent.prior_ideas(db))


def test_a_run_starts_only_within_the_limits(db):
    now = datetime(2026, 10, 19, 18, tzinfo=timezone.utc)
    assert "budget" in agent.why_not_start(db, 0, now)
    assert "budget" in agent.why_not_start(db, agent.MAX_BUDGET + 1, now)
    assert agent.why_not_start(db, 2, now) is None
    run = agent.start_run(db, focus=None, budget=2, mode="explore")
    run.started_at = now - timedelta(minutes=5)
    db.commit()
    assert "already going" in agent.why_not_start(db, 2, now)
    # A run left "running" by a server that died is closed out, not waited on forever.
    run.started_at, run.cost_usd = now - timedelta(hours=2), 3.9
    db.commit()
    assert "today's research spend" in agent.why_not_start(db, 2, now)
    assert run.status == "error" and "Interrupted" in run.summary
    assert agent.why_not_start(db, 1, now) is None
