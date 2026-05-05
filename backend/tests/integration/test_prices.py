from __future__ import annotations

from httpx import AsyncClient


async def test_current_price(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/prices/current/AAPL")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert isinstance(data["price"], float)
    assert data["currency"] == "USD"
    assert isinstance(data["cached"], bool)


async def test_current_price_cached(async_client: AsyncClient) -> None:
    first = await async_client.get("/api/prices/current/AAPL")
    second = await async_client.get("/api/prices/current/AAPL")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["cached"] is True


async def test_historical_prices(async_client: AsyncClient) -> None:
    response = await async_client.get(
        "/api/prices/historical/AAPL",
        params={"start": "2024-01-01", "end": "2024-01-10"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert data["start"] == "2024-01-01"
    assert data["end"] == "2024-01-10"
    assert isinstance(data["bars"], list)
