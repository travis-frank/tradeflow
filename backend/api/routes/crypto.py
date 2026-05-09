from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from time import perf_counter
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import cache_get, cache_set, crypto_historical_key, crypto_price_key
from core.config import get_settings
from core.data_provider import get_provider
from core.metrics import api_call, cache_hit, cache_miss, record_latency
from db.models.models import CryptoPrice
from db.session import get_db

log = structlog.get_logger()
router: APIRouter = APIRouter()


def _parse_timestamp(value: str) -> datetime:
    parsed: datetime = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_range_start(value: str) -> datetime:
    if "T" not in value and len(value) == 10:
        return datetime.combine(datetime.fromisoformat(value).date(), time.min)
    return _parse_timestamp(value)


def _parse_range_end_exclusive(value: str) -> datetime:
    if "T" not in value and len(value) == 10:
        parsed_date = datetime.fromisoformat(value).date()
        return datetime.combine(parsed_date + timedelta(days=1), time.min)
    return _parse_timestamp(value) + timedelta(microseconds=1)


def _currency_from_ticker(ticker: str) -> str:
    parts: list[str] = ticker.split("-")
    if len(parts) > 1 and parts[-1]:
        return parts[-1]
    return "USD"


def _crypto_db_coverage_sufficient(
    bars: list[dict],
    start: str,
    end: str,
    interval: str,
) -> bool:
    start_dt: datetime = _parse_range_start(start)
    end_exclusive: datetime = _parse_range_end_exclusive(end)
    calendar_days: float = max(
        1.0,
        (end_exclusive - start_dt).total_seconds() / 86400.0,
    )

    if interval == "1d":
        required: int = max(1, int(calendar_days * 0.75))
        return len(bars) >= required

    intraday_expected: dict[str, int] = {
        "5m": 288,
        "15m": 96,
        "30m": 48,
        "1h": 24,
    }
    if interval in intraday_expected:
        required = max(1, int(calendar_days * intraday_expected[interval] * 0.2))
        return len(bars) >= required

    required = max(1, int(calendar_days * 0.5))
    return len(bars) >= required


class CryptoPriceResponse(BaseModel):
    ticker: str
    price: float
    currency: str
    cached: bool


class CryptoBarResponse(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class CryptoHistoricalResponse(BaseModel):
    ticker: str
    start: str
    end: str
    bars: list[CryptoBarResponse]
    cached: bool


async def _persist_ohlcv_bars(
    session: AsyncSession,
    ticker: str,
    bars: list[dict],
    interval: str = "1d",
) -> None:
    if not bars:
        log.info("crypto bars persisted", ticker=ticker, attempted=0, persisted=0)
        return

    statement: sa.TextClause = sa.text(
        """
        INSERT INTO crypto_prices (time, ticker, open, high, low, close, volume, interval)
        VALUES (:time, :ticker, :open, :high, :low, :close, :volume, :interval)
        ON CONFLICT (time, ticker) DO NOTHING
        """
    )

    inserted_count: int = 0

    try:
        for bar in bars:
            volume_raw: Any = bar.get("volume")
            volume_value: int | None = None
            if volume_raw is not None:
                volume_float: float = float(volume_raw)
                if math.isfinite(volume_float):
                    volume_value = int(volume_float)

            result = await session.execute(
                statement,
                {
                    "time": _parse_timestamp(str(bar.get("time"))),
                    "ticker": ticker,
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "volume": volume_value,
                    "interval": interval,
                },
            )
            if result.rowcount and result.rowcount > 0:
                inserted_count += int(result.rowcount)
    except Exception:
        await session.rollback()
        log.exception("failed to persist crypto bars", ticker=ticker, attempted=len(bars))
        return

    log.info(
        "crypto bars persisted",
        ticker=ticker,
        attempted=len(bars),
        persisted=inserted_count,
    )


async def _fetch_from_db(
    session: AsyncSession,
    ticker: str,
    start: str,
    end: str,
    interval: str = "1d",
) -> list[dict] | None:
    statement: Select[tuple[CryptoPrice]] = (
        select(CryptoPrice)
        .where(
            CryptoPrice.ticker == ticker,
            CryptoPrice.interval == interval,
            CryptoPrice.time >= _parse_range_start(start),
            CryptoPrice.time < _parse_range_end_exclusive(end),
            CryptoPrice.open.is_not(None),
            CryptoPrice.high.is_not(None),
            CryptoPrice.low.is_not(None),
            CryptoPrice.close.is_not(None),
            CryptoPrice.volume.is_not(None),
        )
        .order_by(CryptoPrice.time.asc())
    )

    try:
        rows: list[CryptoPrice] = list((await session.scalars(statement)).all())
    except Exception:
        await session.rollback()
        log.exception(
            "failed to read crypto prices from database",
            ticker=ticker,
            start=start,
            end=end,
        )
        return None

    if not rows:
        return None

    bars: list[dict] = []
    for row in rows:
        bars.append(
            {
                "time": row.time.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
        )

    return bars


@router.get("/current/{ticker}", response_model=CryptoPriceResponse)
async def get_crypto_current_price(
    ticker: str,
) -> CryptoPriceResponse:
    started: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = crypto_price_key(normalized_ticker)

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("crypto")
        if isinstance(cached_data, dict):
            try:
                response = CryptoPriceResponse(**cached_data, cached=True)
                record_latency("crypto_current", (perf_counter() - started) * 1000)
                return response
            except ValidationError:
                log.warning("invalid cached crypto current payload", ticker=normalized_ticker)
        else:
            log.warning("unexpected cached crypto current payload type", ticker=normalized_ticker)

    cache_miss("crypto")
    provider = get_provider()

    try:
        price: float = await provider.get_crypto_price(normalized_ticker)
    except Exception:
        log.exception("failed to fetch crypto current price", ticker=normalized_ticker)
        raise HTTPException(status_code=502, detail="Failed to fetch crypto data")

    api_call("yfinance", normalized_ticker)

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "price": price,
        "currency": _currency_from_ticker(normalized_ticker),
    }
    await cache_set(key, payload, settings.cache_ttl_current_price)
    record_latency("crypto_current", (perf_counter() - started) * 1000)
    return CryptoPriceResponse(**payload, cached=False)


@router.get("/historical/{ticker}", response_model=CryptoHistoricalResponse)
async def get_crypto_historical_prices(
    ticker: str,
    start: str,
    end: str,
    interval: str = "1d",
    session: AsyncSession = Depends(get_db),
) -> CryptoHistoricalResponse:
    started: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = crypto_historical_key(normalized_ticker, start, end, interval)

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("crypto_historical")
        if isinstance(cached_data, dict):
            try:
                response = CryptoHistoricalResponse(**cached_data, cached=True)
                record_latency("crypto_historical", (perf_counter() - started) * 1000)
                return response
            except ValidationError:
                log.warning(
                    "invalid cached crypto historical payload",
                    ticker=normalized_ticker,
                    start=start,
                    end=end,
                )
        else:
            log.warning(
                "unexpected cached crypto historical payload type",
                ticker=normalized_ticker,
                start=start,
                end=end,
            )

    cache_miss("crypto_historical")

    db_bars: list[dict] | None = await _fetch_from_db(
        session=session,
        ticker=normalized_ticker,
        start=start,
        end=end,
        interval=interval,
    )

    if db_bars is not None and _crypto_db_coverage_sufficient(db_bars, start, end, interval):
        log.info(
            "crypto historical prices served from database",
            ticker=normalized_ticker,
            start=start,
            end=end,
            interval=interval,
            bars=len(db_bars),
        )
        payload: dict[str, Any] = {
            "ticker": normalized_ticker,
            "start": start,
            "end": end,
            "bars": db_bars,
        }
        await cache_set(key, payload, settings.cache_ttl_historical)
        record_latency("crypto_historical", (perf_counter() - started) * 1000)
        return CryptoHistoricalResponse(**payload, cached=False)
    if db_bars is not None:
        log.info(
            "historical database coverage incomplete",
            ticker=normalized_ticker,
            start=start,
            end=end,
            interval=interval,
            db_bars=len(db_bars),
        )

    provider = get_provider()

    try:
        bars = await provider.get_crypto_historical(
            normalized_ticker,
            start,
            end,
            interval=interval,
        )
    except Exception:
        log.exception(
            "failed to fetch crypto historical prices",
            ticker=normalized_ticker,
            start=start,
            end=end,
            interval=interval,
        )
        raise HTTPException(status_code=502, detail="Failed to fetch crypto data")

    api_call("yfinance", normalized_ticker)

    await _persist_ohlcv_bars(
        session=session,
        ticker=normalized_ticker,
        bars=bars,
        interval=interval,
    )

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "start": start,
        "end": end,
        "bars": bars,
    }
    await cache_set(key, payload, settings.cache_ttl_historical)
    record_latency("crypto_historical", (perf_counter() - started) * 1000)
    return CryptoHistoricalResponse(**payload, cached=False)
