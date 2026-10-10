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


def test_record_and_summarize_round_trip(tmp_path):
    path = tmp_path / "api-usage.jsonl"
    usage.record("game", "claude-sonnet-5", _response(4000, 2000), path)
    usage.record("game", "claude-sonnet-5", _response(3000, 1000, cache_read=1000), path)
    usage.record("news", "claude-haiku-5-5", _response(100_000, 10_000), path)
    report = usage.summarize(30, path)
    game = report["groups"]["game / claude-sonnet-5"]
    assert (game["calls"], game["input_tokens"], game["output_tokens"]) == (2, 8000, 3000)
    assert report["total_usd"] == round(0.028 + 0.018 + 0.015, 4)
    assert len(report["by_day"]) == 1


def test_recording_never_raises(tmp_path):
    usage.record("game", "claude-sonnet-5", object(), tmp_path / "x.jsonl")  # no .usage
    assert usage.summarize(30, tmp_path / "missing.jsonl")["total_usd"] == 0
