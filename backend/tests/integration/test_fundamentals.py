from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_income_statement(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/fundamentals/AAPL/income-statement")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert data["cached"] is False
    assert len(data["periods"]) > 0

    first_period = data["periods"][0]
    assert "period_date" in first_period
    assert first_period["period_type"] == "annual"


@pytest.mark.asyncio
async def test_income_statement_cached(async_client: AsyncClient) -> None:
    first = await async_client.get("/api/fundamentals/AAPL/income-statement")
    second = await async_client.get("/api/fundamentals/AAPL/income-statement")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True


@pytest.mark.asyncio
async def test_balance_sheet(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/fundamentals/AAPL/balance-sheet")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert len(data["periods"]) > 0


@pytest.mark.asyncio
async def test_cash_flow(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/fundamentals/AAPL/cash-flow")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert len(data["periods"]) > 0


@pytest.mark.asyncio
async def test_ticker_uppercased(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/fundamentals/aapl/income-statement")

    assert response.status_code == 200
    assert response.json()["ticker"] == "AAPL"
