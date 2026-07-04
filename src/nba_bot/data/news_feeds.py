"""ESPN NBA news via RSS (feedparser) + tagging articles to team ids.

RSS is free and keyless. Each article is tagged with the team ids it mentions
by scanning its text for team nicknames, so retrieval can later filter news down
to the two teams in a matchup.

Nickname matching is a deliberate simplification: nicknames are unique across the
30 teams and word-boundary anchored, but a few double as common words ("Heat",
"Magic", "Thunder"), so occasional false-positive tags are possible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import feedparser
import httpx
from nba_api.stats.static import teams as static_teams
from tenacity import retry, stop_after_attempt, wait_exponential

NBA_RSS_FEEDS = ("https://www.espn.com/espn/rss/nba/news",)

_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))


@dataclass
class FeedEntry:
    source: str
    url: str
    title: str
    summary: str
    published_at: datetime | None


@lru_cache(maxsize=1)
def _team_patterns() -> list[tuple[re.Pattern, int]]:
    patterns: list[tuple[re.Pattern, int]] = []
    for t in static_teams.get_teams():
        names = {t["nickname"]}
        # Multiword nickname (e.g. "Trail Blazers") should also match its common
        # short form ("Blazers").
        if " " in t["nickname"]:
            names.add(t["nickname"].split()[-1])
        for name in names:
            patterns.append((re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), t["id"]))
    return patterns


def tag_team_ids(text: str) -> list[int]:
    """Return the sorted team ids mentioned by nickname in the text."""
    ids = {tid for pattern, tid in _team_patterns() if pattern.search(text)}
    return sorted(ids)


def _strip_html(value: str | None) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


def _published_at(entry) -> datetime | None:
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


@_retry
def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    # Fetch over httpx (certifi trust store) and parse the bytes, rather than
    # letting feedparser fetch via urllib — urllib fails on machines with a
    # self-signed cert injected into the TLS chain (corporate proxies).
    resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=15)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def fetch_entries(feed_urls: tuple[str, ...] = NBA_RSS_FEEDS) -> list[FeedEntry]:
    """Fetch and normalize entries from the configured NBA RSS feeds."""
    entries: list[FeedEntry] = []
    for url in feed_urls:
        parsed = _fetch_feed(url)
        source = parsed.feed.get("title", url)
        for e in parsed.entries:
            entries.append(
                FeedEntry(
                    source=source,
                    url=e.get("link", ""),
                    title=_strip_html(e.get("title")),
                    summary=_strip_html(e.get("summary")),
                    published_at=_published_at(e),
                )
            )
    return entries
