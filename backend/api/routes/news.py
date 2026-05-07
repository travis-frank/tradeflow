from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import cache_get, cache_set, news_key
from core.config import get_settings
from core.data_provider import get_provider
from core.metrics import api_call, cache_hit, cache_miss, record_latency
from db.session import get_db

log = structlog.get_logger()
router: APIRouter = APIRouter()


class NewsArticle(BaseModel):
    title: str
    url: str
    published_at: str | None
    source: str | None


class NewsResponse(BaseModel):
    ticker: str
    cached: bool
    articles: list[NewsArticle]


def _to_iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(int(value), tz=UTC).replace(tzinfo=None).isoformat()

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None).isoformat()
        return value.isoformat()

    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).replace(tzinfo=None).isoformat()
        return parsed.isoformat()
    except ValueError:
        return None


def _to_db_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


async def _persist_news_articles(
    session: AsyncSession,
    ticker: str,
    articles: list[dict[str, Any]],
) -> None:
    if not articles:
        log.info("news articles persisted", ticker=ticker, attempted=0, persisted=0)
        return

    statement: sa.TextClause = sa.text(
        """
        INSERT INTO news_articles (
            ticker, title, url, source, published_at, summary, sentiment, sentiment_score
        )
        VALUES (
            :ticker, :title, :url, :source, :published_at, :summary, :sentiment, :sentiment_score
        )
        ON CONFLICT DO NOTHING
        """
    )

    inserted_count: int = 0

    try:
        for article in articles:
            title: str | None = article.get("title")
            url: str | None = article.get("url")
            if not title or not url:
                continue

            published_at_iso: str | None = _to_iso_timestamp(article.get("published_at"))

            result = await session.execute(
                statement,
                {
                    "ticker": ticker,
                    "title": title,
                    "url": url,
                    "source": article.get("source"),
                    "published_at": _to_db_datetime(published_at_iso),
                    "summary": None,
                    "sentiment": None,
                    "sentiment_score": None,
                },
            )

            if result.rowcount and result.rowcount > 0:
                inserted_count += int(result.rowcount)
    except Exception:
        await session.rollback()
        log.exception("failed to persist news articles", ticker=ticker, attempted=len(articles))
        return

    log.info(
        "news articles persisted",
        ticker=ticker,
        attempted=len(articles),
        persisted=inserted_count,
    )


@router.get("/{ticker}", response_model=NewsResponse)
async def get_news(
    ticker: str,
    session: AsyncSession = Depends(get_db),
) -> NewsResponse:
    started: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = news_key(normalized_ticker)

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("news")
        if isinstance(cached_data, dict):
            try:
                response = NewsResponse(**cached_data, cached=True)
                record_latency("news", (perf_counter() - started) * 1000)
                return response
            except ValidationError:
                log.warning("invalid cached news payload", ticker=normalized_ticker)
        else:
            log.warning("unexpected cached news payload type", ticker=normalized_ticker)

    cache_miss("news")
    provider = get_provider()

    try:
        raw_articles: list[dict] = await provider.get_news(normalized_ticker)
    except Exception:
        log.exception("failed to fetch news", ticker=normalized_ticker)
        raise HTTPException(status_code=502, detail="Failed to fetch news data")

    api_call("yfinance", normalized_ticker)

    normalized_articles: list[dict[str, Any]] = []
    for article in raw_articles:
        title: str | None = article.get("title")
        url: str | None = article.get("url")
        if not title or not url:
            continue

        normalized_articles.append(
            {
                "title": title,
                "url": url,
                "published_at": _to_iso_timestamp(article.get("published_at")),
                "source": article.get("source"),
            }
        )

    await _persist_news_articles(session, normalized_ticker, normalized_articles)

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "articles": normalized_articles,
    }
    await cache_set(key, payload, settings.cache_ttl_news)
    record_latency("news", (perf_counter() - started) * 1000)
    return NewsResponse(**payload, cached=False)
