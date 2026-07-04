"""News RAG tests: chunking, team tagging, HTML stripping, embeddings client.

Network-free: the Voyage call is monkeypatched; RSS/DB are not exercised here.
"""

from nba_bot.data import embeddings, news_feeds
from nba_bot.data.news_feeds import _strip_html, tag_team_ids
from nba_bot.rag.ingest import chunk_text


def _team_id(nickname: str) -> int:
    from nba_api.stats.static import teams as static_teams

    return next(t["id"] for t in static_teams.get_teams() if t["nickname"] == nickname)


def test_chunk_text_short_and_empty():
    assert chunk_text("A short summary.") == ["A short summary."]
    assert chunk_text("   ") == []


def test_chunk_text_splits_long_text_on_sentences():
    sentence = "This is a sentence about the game. "
    chunks = chunk_text(sentence * 40, max_chars=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # no text is lost
    assert "".join(c.replace(" ", "") for c in chunks) == (sentence * 40).replace(" ", "")


def test_tag_team_ids_matches_nicknames():
    ids = tag_team_ids("Lakers rally past the Celtics in overtime")
    assert set(ids) == {_team_id("Lakers"), _team_id("Celtics")}


def test_tag_team_ids_handles_blazers_short_form_and_none():
    assert tag_team_ids("The Blazers signed a guard") == [_team_id("Trail Blazers")]
    assert tag_team_ids("A generic sports headline") == []


def test_strip_html():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert _strip_html(None) == ""


def test_embeddings_client_batches_and_orders(monkeypatch):
    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            # return out of order to prove we re-sort by index
            return {"data": [
                {"index": 1, "embedding": [0.2]},
                {"index": 0, "embedding": [0.1]},
            ]}

    def fake_post(url, headers, json, timeout):
        calls["input_type"] = json["input_type"]
        calls["model"] = json["model"]
        return FakeResp()

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)

    vecs = embeddings.embed_documents(["a", "b"])
    assert vecs == [[0.1], [0.2]]  # re-ordered to match inputs
    assert calls["input_type"] == "document"

    q = embeddings.embed_query("hello")
    assert q == [0.1]
    assert calls["input_type"] == "query"


def test_feed_entry_is_dataclass():
    e = news_feeds.FeedEntry(source="ESPN", url="u", title="t", summary="s", published_at=None)
    assert e.url == "u" and e.title == "t"
