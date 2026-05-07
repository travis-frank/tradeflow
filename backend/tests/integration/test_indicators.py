from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


INDICATORS_PATH: str = "/api/prices/AAPL/indicators"
INDICATORS_PARAMS: dict[str, str] = {"start": "2024-01-01", "end": "2024-06-01"}


@pytest.mark.asyncio
async def test_indicators_returns_data(async_client: AsyncClient) -> None:
    response = await async_client.get(INDICATORS_PATH, params=INDICATORS_PARAMS)

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    assert data["ticker"] == "AAPL"
    assert data["cached"] is False
    assert len(data["bars"]) > 0
    assert len(data["rsi"]) == len(data["bars"])
    assert len(data["macd"]) == len(data["bars"])
    assert len(data["bollinger"]) == len(data["bars"])


@pytest.mark.asyncio
async def test_indicators_cached(async_client: AsyncClient) -> None:
    first = await async_client.get(INDICATORS_PATH, params=INDICATORS_PARAMS)
    second = await async_client.get(INDICATORS_PATH, params=INDICATORS_PARAMS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True


@pytest.mark.asyncio
async def test_rsi_values(async_client: AsyncClient) -> None:
    response = await async_client.get(INDICATORS_PATH, params=INDICATORS_PARAMS)

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    rsi_values: list[float | None] = data["rsi"]

    assert len(rsi_values) >= 15
    assert all(value is None for value in rsi_values[:14])
    assert all(
        (value is None) or (0.0 <= value <= 100.0)
        for value in rsi_values
    )


@pytest.mark.asyncio
async def test_macd_structure(async_client: AsyncClient) -> None:
    response = await async_client.get(INDICATORS_PATH, params=INDICATORS_PARAMS)

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    macd_values: list[dict[str, float | None]] = data["macd"]

    assert len(macd_values) >= 26
    assert all(
        {"macd", "signal", "histogram"}.issubset(point.keys())
        for point in macd_values
    )
    assert all(point["macd"] is None for point in macd_values[:25])


@pytest.mark.asyncio
async def test_bollinger_structure(async_client: AsyncClient) -> None:
    response = await async_client.get(INDICATORS_PATH, params=INDICATORS_PARAMS)

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    bollinger_values: list[dict[str, float | None]] = data["bollinger"]

    assert all(
        {"upper", "middle", "lower"}.issubset(point.keys())
        for point in bollinger_values
    )

    for point in bollinger_values:
        upper: float | None = point["upper"]
        middle: float | None = point["middle"]
        lower: float | None = point["lower"]
        if upper is None or middle is None or lower is None:
            continue
        assert upper > middle > lower


@pytest.mark.asyncio
async def test_custom_rsi_period(async_client: AsyncClient) -> None:
    response = await async_client.get(
        INDICATORS_PATH,
        params={"start": "2024-01-01", "end": "2024-06-01", "rsi_period": "9"},
    )

    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    rsi_values: list[float | None] = data["rsi"]

    assert len(rsi_values) >= 10
    assert all(value is None for value in rsi_values[:9])
    assert rsi_values[9] is not None
