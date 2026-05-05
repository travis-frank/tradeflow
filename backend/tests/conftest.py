from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@dataclass(frozen=True)
class PostgresURLs:
    sync_url: str
    async_url: str


@dataclass(frozen=True)
class RedisInfo:
    url: str


def _to_asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresURLs, None, None]:
    with PostgresContainer("timescale/timescaledb:latest-pg16") as postgres:
        sync_url: str = postgres.get_connection_url()
        async_url: str = _to_asyncpg_url(sync_url)

        os.environ["DATABASE_URL"] = async_url
        os.environ["SECRET_KEY"] = "test-secret-key"
        os.environ["APP_ENV"] = "development"

        from core.config import get_settings

        get_settings.cache_clear()

        alembic_cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(alembic_cfg, "head")

        yield PostgresURLs(sync_url=sync_url, async_url=async_url)


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisInfo, None, None]:
    with RedisContainer("redis:7-alpine") as redis:
        host: str = redis.get_container_host_ip()
        port: str = redis.get_exposed_port(6379)
        yield RedisInfo(url=f"redis://{host}:{port}/0")


@pytest_asyncio.fixture(scope="function")
async def async_client(
    postgres_container: PostgresURLs,
    redis_container: RedisInfo,
) -> AsyncGenerator[AsyncClient, None]:
    os.environ["DATABASE_URL"] = postgres_container.async_url
    os.environ["REDIS_URL"] = redis_container.url
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["APP_ENV"] = "development"

    from cache import redis_client as redis_cache_module
    from core.config import get_settings

    get_settings.cache_clear()

    from api.main import app
    from db.session import get_db

    engine = create_async_engine(postgres_container.async_url, echo=False)
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    redis_client = Redis.from_url(
        redis_container.url,
        encoding="utf-8",
        decode_responses=True,
    )
    await redis_client.flushdb()
    redis_cache_module._redis = redis_client

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        session: AsyncSession = session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def override_get_redis() -> Redis:
        return redis_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[redis_cache_module.get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    redis_cache_module._redis = None
    await redis_client.aclose()
    await engine.dispose()
