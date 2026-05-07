from __future__ import annotations

import pytest
from httpx import AsyncClient


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
