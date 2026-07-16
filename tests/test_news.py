"""News RAG tests: chunking, team tagging, HTML stripping, embeddings client.

Network-free: the Voyage call is monkeypatched; RSS/DB are not exercised here.
"""

from nba_bot.data import embeddings, news_feeds
from nba_bot.data.news_feeds import (
    _clean_title,
    _meta_content,
    _strip_html,
    resolve_full_title,
    tag_team_ids,
)
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


def test_tag_team_ids_matches_unique_city_names():
    # City mentions the nickname misses ("Ja Morant to Portland") should still tag.
    assert tag_team_ids("Ja Morant switches numbers with Portland") == [_team_id("Trail Blazers")]
    assert tag_team_ids("Washington resets around its young core") == [_team_id("Wizards")]


def test_tag_team_ids_skips_ambiguous_shared_city():
    # "Los Angeles" maps to both Lakers and Clippers, so the city alone tags neither.
    assert tag_team_ids("A big night in Los Angeles") == []
    # The nickname still disambiguates.
    assert tag_team_ids("The Los Angeles Lakers won") == [_team_id("Lakers")]


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


def test_clean_title_strips_trailing_ellipsis():
    assert _clean_title("The Wizards' sta...") == "The Wizards' sta"
    assert _clean_title("A pull quote…") == "A pull quote"
    assert _clean_title("Full clean headline") == "Full clean headline"
    assert _clean_title(None) == ""


def test_meta_content_extracts_og_title_either_attr_order():
    html = '<meta property="og:title" content="How the Wizards win"/>'
    assert _meta_content(html, "og:title") == "How the Wizards win"
    # content before property should also parse
    html2 = "<meta content='Reverse order' name='og:title'>"
    assert _meta_content(html2, "og:title") == "Reverse order"
    assert _meta_content("<html></html>", "og:title") is None
    # An apostrophe inside a double-quoted value must not truncate the match.
    html3 = '<meta property="og:title" content="NBA won\'t fine the Bucks\' front office">'
    assert _meta_content(html3, "og:title") == "NBA won't fine the Bucks' front office"


def test_resolve_full_title_prefers_og_title(monkeypatch):
    html = '<meta property="og:title" content="How Dybantsa and Young lift the Wizards">'
    monkeypatch.setattr(news_feeds, "_fetch_article_html", lambda url: html)
    title = resolve_full_title("http://x/story", fallback="The Wizards' sta...")
    assert title == "How Dybantsa and Young lift the Wizards"


def test_resolve_full_title_falls_back_when_page_fetch_fails(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(news_feeds, "_fetch_article_html", boom)
    # A failed fetch must not drop the article — fall back to the cleaned RSS title.
    assert resolve_full_title("http://x/story", fallback="Lakers deal center...") == "Lakers deal center"
