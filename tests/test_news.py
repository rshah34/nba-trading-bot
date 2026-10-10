"""News feed tests: team tagging, HTML stripping, title resolution. Network-free."""

from nba_bot.data import news_feeds
from nba_bot.data.news_feeds import (
    _clean_title,
    _meta_content,
    _strip_html,
    resolve_full_title,
    tag_team_ids,
)


def _team_id(nickname: str) -> int:
    from nba_api.stats.static import teams as static_teams

    return next(t["id"] for t in static_teams.get_teams() if t["nickname"] == nickname)


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
