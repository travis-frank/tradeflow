from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


def _email() -> str:
    return f"watchlist-{uuid4().hex}@example.com"


async def _get_bearer_token(async_client: AsyncClient) -> str:
    payload: dict[str, str] = {
        "email": _email(),
        "password": "password123",
    }

    register_response = await async_client.post("/api/auth/register", json=payload)
    login_response = await async_client.post("/api/auth/login", json=payload)

    assert register_response.status_code == 201
    assert login_response.status_code == 200

    access_token: str = login_response.json()["access_token"]
    return access_token


@pytest.mark.asyncio
async def test_add_watchlist_item(async_client: AsyncClient) -> None:
    token: str = await _get_bearer_token(async_client)

    response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "aapl", "asset_type": "stock"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["ticker"] == "AAPL"
    assert data["asset_type"] == "stock"


@pytest.mark.asyncio
async def test_list_watchlist_items(async_client: AsyncClient) -> None:
    token: str = await _get_bearer_token(async_client)

    first_response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "asset_type": "stock"},
    )
    second_response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "TSLA", "asset_type": "stock"},
    )
    list_response = await async_client.get(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert list_response.status_code == 200

    items = list_response.json()
    assert len(items) == 2
    tickers: set[str] = {item["ticker"] for item in items}
    assert "AAPL" in tickers
    assert "TSLA" in tickers


@pytest.mark.asyncio
async def test_duplicate_watchlist_item(async_client: AsyncClient) -> None:
    token: str = await _get_bearer_token(async_client)

    first_response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "asset_type": "stock"},
    )
    second_response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "asset_type": "stock"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


@pytest.mark.asyncio
async def test_delete_watchlist_item(async_client: AsyncClient) -> None:
    token: str = await _get_bearer_token(async_client)

    add_response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
        json={"ticker": "AAPL", "asset_type": "stock"},
    )

    assert add_response.status_code == 201
    item_id: int = add_response.json()["id"]

    delete_response = await async_client.delete(
        f"/api/watchlist/{item_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    list_response = await async_client.get(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert delete_response.status_code == 204
    assert list_response.status_code == 200
    assert list_response.json() == []


@pytest.mark.asyncio
async def test_delete_wrong_user(async_client: AsyncClient) -> None:
    user1_token: str = await _get_bearer_token(async_client)
    user2_token: str = await _get_bearer_token(async_client)

    add_response = await async_client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {user1_token}"},
        json={"ticker": "AAPL", "asset_type": "stock"},
    )

    assert add_response.status_code == 201
    item_id: int = add_response.json()["id"]

    delete_response = await async_client.delete(
        f"/api/watchlist/{item_id}",
        headers={"Authorization": f"Bearer {user2_token}"},
    )

    assert delete_response.status_code == 404


@pytest.mark.asyncio
async def test_watchlist_requires_auth(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/watchlist")

    assert response.status_code == 401
