from __future__ import annotations

import pytest
from httpx import AsyncClient
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_news_returns_articles(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/news/AAPL")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert data["cached"] is False
    assert len(data["articles"]) > 0

    first_article = data["articles"][0]
    assert isinstance(first_article["title"], str)
    assert first_article["title"]
    assert isinstance(first_article["url"], str)
    assert first_article["url"]


@pytest.mark.asyncio
async def test_news_cached(async_client: AsyncClient) -> None:
    first = await async_client.get("/api/news/AAPL")
    second = await async_client.get("/api/news/AAPL")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True


@pytest.mark.asyncio
async def test_news_persists_articles_with_timestamps(
    async_client: AsyncClient,
    postgres_container,
) -> None:
    response = await async_client.get("/api/news/AAPL")

    assert response.status_code == 200
    assert len(response.json()["articles"]) > 0

    engine = create_async_engine(postgres_container.async_url, echo=False)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    """
                    SELECT COUNT(*) AS article_count
                    FROM news_articles
                    WHERE ticker = :ticker
                      AND created_at IS NOT NULL
                      AND updated_at IS NOT NULL
                    """
                ),
                {"ticker": "AAPL"},
            )
            article_count = result.scalar_one()
    finally:
        await engine.dispose()

    assert article_count > 0


@pytest.mark.asyncio
async def test_news_article_fields(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/news/AAPL")

    assert response.status_code == 200
    data = response.json()

    for article in data["articles"]:
        assert isinstance(article["title"], str)
        assert isinstance(article["url"], str)
        assert isinstance(article["published_at"], str) or article["published_at"] is None
        assert isinstance(article["source"], str) or article["source"] is None


@pytest.mark.asyncio
async def test_news_ticker_uppercased(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/news/aapl")

    assert response.status_code == 200
    assert response.json()["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_news_different_ticker(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/news/TSLA")

    assert response.status_code == 200
    assert response.json()["ticker"] == "TSLA"
