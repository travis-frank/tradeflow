from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_current_crypto_price(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/crypto/current/BTC-USD")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "BTC-USD"
    assert data["price"] > 0
    assert data["currency"] == "USD"
    assert data["cached"] is False


@pytest.mark.asyncio
async def test_current_crypto_price_cached(async_client: AsyncClient) -> None:
    first = await async_client.get("/api/crypto/current/BTC-USD")
    second = await async_client.get("/api/crypto/current/BTC-USD")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True


@pytest.mark.asyncio
async def test_historical_crypto_prices(async_client: AsyncClient) -> None:
    response = await async_client.get(
        "/api/crypto/historical/BTC-USD",
        params={"start": "2024-01-01", "end": "2024-01-07"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "BTC-USD"
    assert len(data["bars"]) > 0

    first_bar = data["bars"][0]
    assert "time" in first_bar
    assert "open" in first_bar
    assert "high" in first_bar
    assert "low" in first_bar
    assert "close" in first_bar
    assert "volume" in first_bar


@pytest.mark.asyncio
async def test_crypto_ticker_uppercased(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/crypto/current/btc-usd")

    assert response.status_code == 200
    assert response.json()["ticker"] == "BTC-USD"


@pytest.mark.asyncio
async def test_eth_current_price(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/crypto/current/ETH-USD")

    assert response.status_code == 200
    data = response.json()
    assert data["price"] > 0
    assert data["currency"] == "USD"
