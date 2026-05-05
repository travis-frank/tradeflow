from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient


def _email() -> str:
    return f"test-{uuid4().hex}@example.com"


async def test_register_success(async_client: AsyncClient) -> None:
    payload = {"email": _email(), "password": "password123"}

    response = await async_client.post("/api/auth/register", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == payload["email"]
    assert isinstance(data["id"], int)
    assert data["is_active"] is True
    assert data["is_verified"] is False


async def test_register_duplicate_email(async_client: AsyncClient) -> None:
    payload = {"email": _email(), "password": "password123"}

    first = await async_client.post("/api/auth/register", json=payload)
    second = await async_client.post("/api/auth/register", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


async def test_login_success(async_client: AsyncClient) -> None:
    payload = {"email": _email(), "password": "password123"}

    register_response = await async_client.post("/api/auth/register", json=payload)
    login_response = await async_client.post("/api/auth/login", json=payload)

    assert register_response.status_code == 201
    assert login_response.status_code == 200
    login_data = login_response.json()
    assert isinstance(login_data["access_token"], str)
    assert login_data["access_token"]
    assert login_data["token_type"] == "bearer"


async def test_login_wrong_password(async_client: AsyncClient) -> None:
    payload = {"email": _email(), "password": "password123"}

    register_response = await async_client.post("/api/auth/register", json=payload)
    bad_login_response = await async_client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": "wrong-password"},
    )

    assert register_response.status_code == 201
    assert bad_login_response.status_code == 401


async def test_get_me(async_client: AsyncClient) -> None:
    payload = {"email": _email(), "password": "password123"}

    register_response = await async_client.post("/api/auth/register", json=payload)
    login_response = await async_client.post("/api/auth/login", json=payload)
    token = login_response.json()["access_token"]

    me_response = await async_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert register_response.status_code == 201
    assert login_response.status_code == 200
    assert me_response.status_code == 200
    me_data = me_response.json()
    assert me_data["email"] == payload["email"]
    assert isinstance(me_data["id"], int)
