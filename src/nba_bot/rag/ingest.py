"""News ingestion: RSS entries -> dedupe -> chunk -> embed (Voyage) -> news_chunks.

Articles are deduped on their URL (news_articles.url is UNIQUE), so re-running
only embeds genuinely new articles and never double-charges the embedding API.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.data import embeddings, news_feeds
from nba_bot.db.models import NewsArticle, NewsChunk

log = logging.getLogger("nba_bot.news")

_MAX_CHUNK_CHARS = 800
# ~6k tokens a request, under Voyage's lowest per-minute token limit.
_EMBED_BATCH_CHARS = 24_000
_RATE_LIMIT_WAIT = 61.0


def chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split text into <= max_chars pieces on sentence boundaries.

    RSS summaries are short, so this is usually one chunk; the packing only kicks
    in for the occasional long summary, and keeps the module ready for fuller text.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def sync_news(session: Session, feed_urls: tuple[str, ...] = news_feeds.NBA_RSS_FEEDS) -> dict:
    """Fetch NBA news, store new articles + their embedded chunks. Idempotent per URL."""
    entries = [e for e in news_feeds.fetch_entries(feed_urls) if e.url]

    # Skip URLs already ingested so we only fetch article pages (for the full
    # title) for genuinely new entries — syncs run several times a day.
    urls = [e.url for e in entries]
    already = (
        set(session.execute(select(NewsArticle.url).where(NewsArticle.url.in_(urls))).scalars())
        if urls
        else set()
    )

    new_articles = 0
    for entry in entries:
        if entry.url in already:
            continue
        # ESPN truncates RSS titles with an ellipsis; only then is the page worth a fetch.
        title = (news_feeds.resolve_full_title(entry.url, entry.title)
                 if news_feeds.is_truncated(entry.title) else entry.title)
        body = f"{title}. {entry.summary}".strip() if entry.summary else title
        team_ids = news_feeds.tag_team_ids(body)

        stmt = (
            pg_insert(NewsArticle)
            .values(
                source=entry.source,
                url=entry.url,
                title=title,
                # Some feeds carry no date; first-seen time is the best available.
                published_at=entry.published_at or datetime.now(timezone.utc),
                team_ids=team_ids or None,
                raw_text=entry.summary or None,
            )
            .on_conflict_do_nothing(index_elements=[NewsArticle.url])
            .returning(NewsArticle.id)
        )
        if session.execute(stmt).scalar_one_or_none() is not None:
            new_articles += 1
    # Articles are committed before embedding: the signal extractor needs them even
    # if the embedding API is down or rate-limited.
    session.commit()

    return {"entries": len(entries), "new_articles": new_articles,
            "chunks_embedded": embed_pending(session)}


def embed_pending(session: Session, lookback_days: int = 3, max_seconds: float = 240) -> int:
    """Chunk + embed recent articles that have no chunks yet (new, or a failed run's).

    Embeds newest-first in small batches, committing each, and gives up for this run
    when rate-limited past `max_seconds` — whatever is left is picked up next run.
    (Voyage without a payment method allows ~10k tokens a minute, far below a day of
    full-text articles.)
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    articles = session.execute(
        select(NewsArticle)
        .outerjoin(NewsChunk, NewsChunk.article_id == NewsArticle.id)
        .where(NewsArticle.fetched_at >= since, NewsChunk.id.is_(None))
        .order_by(NewsArticle.published_at.desc().nulls_last())
    ).scalars().all()

    batches: list[list[tuple[int, str]]] = [[]]
    size = 0
    for a in articles:
        body = f"{a.title}. {a.raw_text}".strip() if a.raw_text else (a.title or "")
        chunks = [(a.id, c) for c in chunk_text(body)]
        n = sum(len(c) for _, c in chunks)
        if batches[-1] and size + n > _EMBED_BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].extend(chunks)  # an article's chunks stay together: all or none
        size += n

    deadline = time.monotonic() + max_seconds
    embedded = 0
    for batch in (b for b in batches if b):
        vectors = None
        for attempt in (1, 2):
            try:
                vectors = embeddings.embed_once([chunk for _, chunk in batch])
                break
            except Exception as exc:  # noqa: BLE001
                wait = _RATE_LIMIT_WAIT if attempt == 1 else None
                if wait is None or time.monotonic() + wait > deadline:
                    log.warning("embedding paused after %d chunk(s): %s", embedded, exc)
                    return embedded
                time.sleep(wait)
        for (article_id, chunk), vector in zip(batch, vectors):
            session.add(NewsChunk(article_id=article_id, chunk_text=chunk, embedding=vector))
        session.commit()
        embedded += len(batch)
    return embedded
