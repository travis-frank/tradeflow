from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def disable_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")

    from core.config import get_settings

    get_settings.cache_clear()


def _email() -> str:
    return f"research-{uuid4().hex}@example.com"


async def _get_bearer_token(async_client: AsyncClient) -> str:
    payload: dict[str, str] = {
        "email": _email(),
        "password": "password123",
    }

    register_response = await async_client.post("/api/auth/register", json=payload)
    login_response = await async_client.post("/api/auth/login", json=payload)

    assert register_response.status_code == 201
    assert login_response.status_code == 200

    return str(login_response.json()["access_token"])


@pytest.mark.asyncio
async def test_research_stock_success(async_client: AsyncClient) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "AAPL",
            "asset_type": "stock",
            "question": "What are the current technical signals for AAPL?",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert data["asset_type"] == "stock"
    assert data["generated_at"]
    assert data["price_context"]
    assert isinstance(data["sources"], list)


@pytest.mark.asyncio
async def test_research_crypto_success(async_client: AsyncClient) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "BTC-USD",
            "asset_type": "crypto",
            "question": "What is the current trend for Bitcoin?",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "BTC-USD"
    assert data["asset_type"] == "crypto"
    assert data["fundamental_context"] == "" or "not available" in data[
        "fundamental_context"
    ].lower()
    assert isinstance(data["sources"], list)


@pytest.mark.asyncio
async def test_research_crypto_without_dash_fails(async_client: AsyncClient) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "BTCUSD",
            "asset_type": "crypto",
            "question": "What is the current trend?",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_research_stock_with_dash_fails(async_client: AsyncClient) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "AAPL-USD",
            "asset_type": "stock",
            "question": "What is the current trend?",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_research_empty_question_fails(async_client: AsyncClient) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "asset_type": "stock", "question": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_research_sources_present(async_client: AsyncClient) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "MSFT",
            "asset_type": "stock",
            "question": "Summarize recent news for MSFT",
        },
    )

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert isinstance(sources, list)
    assert len(sources) > 0


@pytest.mark.asyncio
async def test_research_risks_present_in_deterministic_mode(
    async_client: AsyncClient,
) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "AAPL",
            "asset_type": "stock",
            "question": "What risks should I watch?",
        },
    )

    assert response.status_code == 200
    risks = response.json()["risks"]
    assert len(risks) >= 1
    assert any("deterministic mode" in risk or "No LLM" in risk for risk in risks)


@pytest.mark.asyncio
async def test_research_stock_general_question_gathers_richer_context(
    async_client: AsyncClient,
) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "AAPL",
            "asset_type": "stock",
            "question": "Give me an overview of AAPL",
        },
    )

    assert response.status_code == 200
    source_tools = {source["tool"] for source in response.json()["sources"]}
    assert {
        "get_current_price",
        "get_news",
        "get_historical_prices",
        "get_indicators",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cash_flow",
    }.issubset(source_tools)


@pytest.mark.asyncio
async def test_research_crypto_general_question_skips_stock_fundamentals(
    async_client: AsyncClient,
) -> None:
    token = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/agent/research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "ticker": "BTC-USD",
            "asset_type": "crypto",
            "question": "Give me an overview of BTC-USD",
        },
    )

    assert response.status_code == 200
    source_tools = {source["tool"] for source in response.json()["sources"]}
    assert {"get_crypto_price", "get_crypto_historical", "get_news"}.issubset(
        source_tools
    )
    assert "get_fundamentals" not in source_tools
    assert "get_balance_sheet" not in source_tools
    assert "get_cash_flow" not in source_tools
