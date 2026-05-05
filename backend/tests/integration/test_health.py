from __future__ import annotations

from httpx import AsyncClient


async def test_health_check(async_client: AsyncClient) -> None:
    response = await async_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "env": "development"}
