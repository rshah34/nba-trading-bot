"""API usage log. DB/network-free."""

from types import SimpleNamespace

from nba_bot import usage


def _response(inp, out, cache_read=0):
    return SimpleNamespace(usage=SimpleNamespace(
        input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0))


def test_cost_uses_the_models_price_and_is_unknown_for_unlisted_models():
    assert usage.cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == 0.60
    assert usage.cost_usd("claude-sonnet-5", 4000, 2000) == 0.028
    assert usage.cost_usd("some-future-model", 10, 10) is None


def test_cache_writes_cost_more_and_reads_cost_a_tenth():
    plain = usage.cost_usd("claude-sonnet-5-5", 10_000, 0)
    assert usage.cost_usd("claude-sonnet-5-5", 0, 0, cache_write=10_000) == plain * 1.25
    assert abs(usage.cost_usd("claude-sonnet-5-5", 0, 0, cache_read=10_000) - plain * 0.1) < 1e-12


def test_record_and_summarize_round_trip(tmp_path):
    path = tmp_path / "api-usage.jsonl"
    assert usage.record("research", "claude-sonnet-5", _response(4000, 2000), path) == 0.028
    usage.record("research", "claude-sonnet-5", _response(3000, 1000, cache_read=1000), path)
    usage.record("news", "claude-haiku-5-5", _response(100_000, 10_000), path)
    report = usage.summarize(30, path)
    research = report["groups"]["research / claude-sonnet-5"]
    assert (research["calls"], research["input_tokens"], research["output_tokens"]) == (2, 8000, 3000)
    # The 1,000 cached tokens bill at a tenth: (3,000 + 100) × $2/M + 1,000 × $10/M.
    assert report["total_usd"] == round(0.028 + 0.0162 + 0.015, 4)
    assert len(report["by_day"]) == 1


def test_recording_never_raises(tmp_path):
    assert usage.record("news", "claude-sonnet-5", object(), tmp_path / "x.jsonl") is None
    assert usage.summarize(30, tmp_path / "missing.jsonl")["total_usd"] == 0
