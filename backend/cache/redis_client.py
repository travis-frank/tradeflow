"""Redis cache layer for storing and retrieving data payloads."""

import json
from typing import Any

import structlog
from redis.asyncio import Redis

from core.config import get_settings

log = structlog.get_logger()
_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis

    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    return _redis


async def cache_get(key: str) -> Any | None:
    redis_client: Redis = await get_redis()
    cached_value: str | None = await redis_client.get(key)

    if cached_value is None:
        log.debug("cache miss", key=key)
        return None

    log.debug("cache hit", key=key)

    try:
        return json.loads(cached_value)
    except json.JSONDecodeError:
        log.warning("cache deserialization failed", key=key)
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    redis_client: Redis = await get_redis()
    serialized_value: str = json.dumps(value)
    await redis_client.setex(key, ttl, serialized_value)


async def cache_delete(key: str) -> None:
    redis_client: Redis = await get_redis()
    await redis_client.delete(key)

async def cache_exists(key: str) -> bool:
    redis_client: Redis = await get_redis()
    return bool(await redis_client.exists(key))

def price_key(ticker: str) -> str:
    return f"price:current:{ticker.upper()}"


def historical_key(ticker: str, start: str, end: str, interval: str = "1d") -> str:
    return f"price:historical:{ticker.upper()}:{start}:{end}:{interval}"


def fundamentals_key(ticker: str, statement: str) -> str:
    return f"fundamentals:{statement}:{ticker.upper()}"


def news_key(ticker: str) -> str:
    return f"news:{ticker.upper()}"


def crypto_price_key(ticker: str) -> str:
    return f"crypto:current:{ticker.upper()}"


def crypto_historical_key(ticker: str, start: str, end: str, interval: str = "1d") -> str:
    return f"crypto:historical:{ticker.upper()}:{start}:{end}:{interval}"


def indicators_key(ticker: str, start: str, end: str) -> str:
    return f"indicators:{ticker.upper()}:{start}:{end}"
