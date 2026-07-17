"""Pipeline run-summary logging. DB/network-free."""

import json

from nba_bot import pipeline

# A phase result shaped like run_pregame/run_postgame return values.
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
    ok_entry = pipeline.append_run_log("pregame", _OK, log_dir=tmp_path)
    bad_entry = pipeline.append_run_log("pregame", _MIXED, log_dir=tmp_path)

    assert ok_entry["ok"] is True and bad_entry["ok"] is False
    assert ok_entry["phase"] == "pregame"

    lines = (tmp_path / "pipeline-runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2  # appended, not overwritten
    parsed = json.loads(lines[0])
    assert parsed["steps"]["ingest"] is True and "ts" in parsed
