"""News ingestion: RSS entries -> dedupe -> chunk -> embed (Voyage) -> news_chunks.

Articles are deduped on their URL (news_articles.url is UNIQUE), so re-running
only embeds genuinely new articles and never double-charges the embedding API.
"""

from __future__ import annotations

import re

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.data import embeddings, news_feeds
from nba_bot.db.models import NewsArticle, NewsChunk

_MAX_CHUNK_CHARS = 800


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
    entries = news_feeds.fetch_entries(feed_urls)

    new_articles = 0
    pending: list[tuple[int, str]] = []  # (article_id, chunk_text)
    for entry in entries:
        if not entry.url:
            continue
        body = f"{entry.title}. {entry.summary}".strip() if entry.summary else entry.title
        team_ids = news_feeds.tag_team_ids(body)

        stmt = (
            pg_insert(NewsArticle)
            .values(
                source=entry.source,
                url=entry.url,
                title=entry.title,
                published_at=entry.published_at,
                team_ids=team_ids or None,
                raw_text=entry.summary or None,
            )
            .on_conflict_do_nothing(index_elements=[NewsArticle.url])
            .returning(NewsArticle.id)
        )
        article_id = session.execute(stmt).scalar_one_or_none()
        if article_id is None:
            continue  # URL already ingested
        new_articles += 1
        for chunk in chunk_text(body):
            pending.append((article_id, chunk))

    session.flush()

    embedded = 0
    if pending:
        vectors = embeddings.embed_documents([chunk for _, chunk in pending])
        for (article_id, chunk), vector in zip(pending, vectors):
            session.add(NewsChunk(article_id=article_id, chunk_text=chunk, embedding=vector))
            embedded += 1

    session.commit()
    return {"entries": len(entries), "new_articles": new_articles, "chunks_embedded": embedded}
