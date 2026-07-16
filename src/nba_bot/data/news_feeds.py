"""ESPN NBA news via RSS (feedparser) + tagging articles to team ids.

RSS is free and keyless. Each article is tagged with the team ids it mentions
by scanning its text for team nicknames, so retrieval can later filter news down
to the two teams in a matchup.

Matching is a deliberate simplification: articles are tagged by team nickname
*and* city (word-boundary anchored). Cities catch mentions the nickname misses
("Ja Morant to Portland" tags the Blazers), but a city shared by two teams
("Los Angeles" → Lakers and Clippers) is ambiguous, so city matching is used only
for cities unique to one team; the nickname disambiguates the rest. A few names
double as common words ("Heat", "Magic", "Thunder", "Washington"), so occasional
false-positive tags are possible.
"""

from __future__ import annotations

import re
from collections import Counter
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
    teams = static_teams.get_teams()
    # A city shared by two teams (only "Los Angeles") can't identify one, so tag by
    # city only when it maps to a single team.
    city_counts = Counter(t["city"] for t in teams)

    patterns: list[tuple[re.Pattern, int]] = []
    for t in teams:
        names = {t["nickname"]}
        # Multiword nickname (e.g. "Trail Blazers") should also match its common
        # short form ("Blazers").
        if " " in t["nickname"]:
            names.add(t["nickname"].split()[-1])
        if city_counts[t["city"]] == 1:
            names.add(t["city"])
        for name in names:
            patterns.append((re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), t["id"]))
    return patterns


def tag_team_ids(text: str) -> list[int]:
    """Return the sorted team ids mentioned by nickname in the text."""
    ids = {tid for pattern, tid in _team_patterns() if pattern.search(text)}
    return sorted(ids)


def _strip_html(value: str | None) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


_TRAILING_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*$")
# ESPN page <title> tags carry a " - ESPN" suffix; og:title does not.
_TITLE_SITE_SUFFIX = re.compile(r"\s*[-|]\s*ESPN\s*$", re.IGNORECASE)


def _clean_title(title: str | None) -> str:
    """Drop a trailing ellipsis (ESPN truncates RSS titles) and surrounding space."""
    return _TRAILING_ELLIPSIS.sub("", title or "").strip()


def _meta_content(html: str, prop: str) -> str | None:
    """Value of a <meta property=prop content=...> tag, tolerant of attribute order."""
    tag = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*>', html, re.IGNORECASE
    )
    if not tag:
        return None
    # Backreference the opening quote so an apostrophe inside a double-quoted value
    # (content="Bucks' season") doesn't end the match early.
    content = re.search(r'content=(["\'])(.*?)\1', tag.group(0), re.IGNORECASE | re.DOTALL)
    return _strip_html(content.group(2)) if content else None


@_retry
def _fetch_article_html(url: str) -> str:
    resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=15)
    resp.raise_for_status()
    return resp.text


def resolve_full_title(url: str, fallback: str) -> str:
    """The RSS <title> is truncated with an ellipsis by ESPN, so fetch the article
    page and use its full headline (og:title, then <title>). Falls back to the
    cleaned RSS title if the page can't be fetched or exposes no usable title —
    a page fetch failure must never drop the article.
    """
    try:
        html = _fetch_article_html(url)
    except Exception:
        return _clean_title(fallback)
    title = _meta_content(html, "og:title")
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = _TITLE_SITE_SUFFIX.sub("", _strip_html(m.group(1))) if m else None
    return _clean_title(title) or _clean_title(fallback)


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
