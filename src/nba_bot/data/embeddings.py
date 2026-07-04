"""Voyage AI embeddings (https://docs.voyageai.com/).

Called over httpx to avoid pulling in the voyageai SDK. Used to embed news
chunks (input_type='document') at ingest time and search queries
(input_type='query') at retrieval time. voyage-3 returns 1024-dim vectors,
matching news_chunks.embedding.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from nba_bot.config import settings

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_MAX_BATCH = 128  # Voyage caps inputs per request

_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))


@_retry
def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    resp = httpx.post(
        VOYAGE_URL,
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
        json={"model": settings.embedding_model, "input": texts, "input_type": input_type},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    # Response order isn't guaranteed; align to the input order via each item's index.
    return [item["embedding"] for item in sorted(data, key=lambda d: d["index"])]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed stored content (news chunks), batched to respect Voyage's input cap."""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), _MAX_BATCH):
        vectors.extend(_embed(texts[i : i + _MAX_BATCH], "document"))
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return _embed([text], "query")[0]
