"""Retrieve news chunks relevant to a matchup, for the Analysis Agent.

Cosine-KNN over news_chunks (pgvector HNSW index), filtered to articles that
mention either team and were published within a recency window ending at the
game date — so a backtest never retrieves news from after the game.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nba_bot.data import embeddings
from nba_bot.db.models import NewsArticle, NewsChunk


@dataclass
class RetrievedChunk:
    chunk_text: str
    title: str | None
    url: str
    source: str
    published_at: datetime | None
    distance: float


def retrieve_relevant_news(
    session: Session,
    query: str,
    team_ids: list[int],
    as_of: date,
    lookback_days: int = 7,
    k: int = 8,
) -> list[RetrievedChunk]:
    """Return up to k news chunks closest to the query, for the given teams/window."""
    query_vec = embeddings.embed_query(query)
    since = as_of - timedelta(days=lookback_days)
    distance = NewsChunk.embedding.cosine_distance(query_vec)

    stmt = (
        select(
            NewsChunk.chunk_text,
            NewsArticle.title,
            NewsArticle.url,
            NewsArticle.source,
            NewsArticle.published_at,
            distance.label("distance"),
        )
        .join(NewsArticle, NewsChunk.article_id == NewsArticle.id)
        .where(or_(*[NewsArticle.team_ids.any(tid) for tid in team_ids]))
        .where(
            (NewsArticle.published_at.is_(None)) | (NewsArticle.published_at >= since)
        )
        .order_by(distance)
        .limit(k)
    )

    return [
        RetrievedChunk(
            chunk_text=row.chunk_text,
            title=row.title,
            url=row.url,
            source=row.source,
            published_at=row.published_at,
            distance=float(row.distance),
        )
        for row in session.execute(stmt).all()
    ]
