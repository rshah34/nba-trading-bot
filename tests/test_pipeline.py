"""Pipeline run-summary logging. DB/network-free."""

import json

from nba_bot import pipeline

# A run result shaped like run_daily's return value.
_OK = {
    "ingest": {"ok": True, "result": {}},
    "odds": {"ok": True, "result": {}},
    "predict": {"ok": True, "result": []},
    "bets": {"ok": True, "result": {"placed": 0}},
}
_MIXED = {**_OK, "odds": {"ok": False, "error": "HTTPError: 429"}}


def test_summarize_run_reduces_to_step_flags():
    assert pipeline.summarize_run(_OK) == {
        "ingest": True, "odds": True, "predict": True, "bets": True
    }
    assert pipeline.summarize_run(_MIXED)["odds"] is False


def test_append_run_log_writes_jsonl_and_flags_failures(tmp_path):
    ok_entry = pipeline.append_run_log("daily", _OK, log_dir=tmp_path)
    bad_entry = pipeline.append_run_log("daily", _MIXED, log_dir=tmp_path)

    assert ok_entry["ok"] is True and bad_entry["ok"] is False
    assert ok_entry["phase"] == "daily"

    lines = (tmp_path / "pipeline-runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2  # appended, not overwritten
    parsed = json.loads(lines[0])
    assert parsed["steps"]["ingest"] is True and "ts" in parsed


def _stub_agents(monkeypatch, teams):
    """Replace every agent call with a recorder; returns the list of calls made."""
    calls = []

    def rec(name, result=None):
        return lambda *a, **k: calls.append(name) or result

    monkeypatch.setattr(pipeline, "_slate_teams", lambda session, on: teams)
    monkeypatch.setattr(pipeline, "load_props_data", rec("load"))
    for agent, fn, result in [
        (pipeline.data_agent, "run_nightly", {}),
        (pipeline.data_agent, "mark_closing_lines", 0),
        (pipeline.evaluation_agent, "run_evaluation", {}),
        (pipeline.betting_agent, "settle_bets", {}),
        (pipeline.props_agent, "settle_prop_bets", {}),
        (pipeline.news_agent, "sync_news", {}),
        (pipeline.news_agent, "extract_signals", {}),
        (pipeline.analysis_agent, "run_predictions", []),
        (pipeline.data_agent, "sync_odds", {}),
        (pipeline.props_agent, "sync_prop_odds", {}),
        (pipeline.picks, "make_picks", {"picks": []}),
        (pipeline.digest, "best_bets", "No bets today"),
        (pipeline.notify, "send", "ok"),
    ]:
        monkeypatch.setattr(agent, fn, rec(fn, result))
    return calls


def test_run_daily_settles_before_it_prices_and_texts_last(monkeypatch):
    calls = _stub_agents(monkeypatch, teams={1, 2})
    result = pipeline.run_daily(session=None)

    assert all(pipeline.summarize_run(result).values())
    assert calls == [
        "run_nightly", "mark_closing_lines", "run_evaluation", "settle_bets",
        "settle_prop_bets", "sync_news", "load", "extract_signals", "run_predictions",
        "sync_odds", "sync_prop_odds", "make_picks", "best_bets", "send",
    ]


def test_run_daily_with_no_games_only_settles(monkeypatch):
    calls = _stub_agents(monkeypatch, teams=set())
    result = pipeline.run_daily(session=None)

    assert "notify" not in result and "predict" not in result
    assert calls[-1] == "sync_news" and "sync_odds" not in calls
