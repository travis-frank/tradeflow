from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from time import perf_counter
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import cache_get, cache_set, historical_key, indicators_key, price_key
from core.config import get_settings
from core.data_provider import get_provider
from core.indicators import calculate_bollinger_bands, calculate_macd, calculate_rsi
from core.metrics import api_call, cache_hit, cache_miss, record_latency
from db.session import get_db


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

log = structlog.get_logger()
router: APIRouter = APIRouter()


class CurrentPriceResponse(BaseModel):
    ticker: str
    price: float
    currency: str = "USD"
    cached: bool


class OHLCVBar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class HistoricalPricesResponse(BaseModel):
    ticker: str
    start: str
    end: str
    bars: list[OHLCVBar]
    cached: bool


class MACDPoint(BaseModel):
    macd: float | None
    signal: float | None
    histogram: float | None


class BollingerPoint(BaseModel):
    upper: float | None
    middle: float | None
    lower: float | None


class IndicatorsResponse(BaseModel):
    ticker: str
    start: str
    end: str
    cached: bool
    bars: list[str]
    rsi: list[float | None]
    macd: list[MACDPoint]
    bollinger: list[BollingerPoint]


async def _persist_ohlcv_bars(
    session: AsyncSession,
    ticker: str,
    bars: list[dict],
) -> None:
    if not bars:
        log.info("stock bars persisted", ticker=ticker, attempted=0, persisted=0)
        return

    statement: sa.TextClause = sa.text(
        """
        INSERT INTO stock_prices (time, ticker, open, high, low, close, volume, interval)
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
                    "interval": "1d",
                },
            )
            if result.rowcount and result.rowcount > 0:
                inserted_count += int(result.rowcount)
    except Exception:
        await session.rollback()
        log.exception("failed to persist stock bars", ticker=ticker, attempted=len(bars))
        return

    log.info(
        "stock bars persisted",
        ticker=ticker,
        attempted=len(bars),
        persisted=inserted_count,
    )


async def _fetch_from_db(
    session: AsyncSession,
    ticker: str,
    start: str,
    end: str,
) -> list[dict] | None:
    statement: sa.TextClause = sa.text(
        """
        SELECT time, open, high, low, close, volume
        FROM stock_prices
        WHERE ticker = :ticker
          AND time >= :start
          AND time < :end_exclusive
          AND open IS NOT NULL
          AND high IS NOT NULL
          AND low IS NOT NULL
          AND close IS NOT NULL
          AND volume IS NOT NULL
        ORDER BY time ASC
        """
    )

    try:
        result = await session.execute(
            statement,
            {
                "ticker": ticker,
                "start": _parse_range_start(start),
                "end_exclusive": _parse_range_end_exclusive(end),
            },
        )
    except Exception:
        await session.rollback()
        log.exception(
            "failed to read stock prices from database",
            ticker=ticker,
            start=start,
            end=end,
        )
        return None

    rows = result.mappings().all()
    if not rows:
        return None

    bars: list[dict] = []
    for row in rows:
        time_value: Any = row["time"]
        bars.append(
            {
                "time": time_value.isoformat() if isinstance(time_value, datetime) else str(time_value),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )

    return bars


@router.get("/current/{ticker}", response_model=CurrentPriceResponse)
async def get_current_price(
    ticker: str,
    session: AsyncSession = Depends(get_db),
) -> CurrentPriceResponse:
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = price_key(normalized_ticker)

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("prices")
        if isinstance(cached_data, dict):
            try:
                return CurrentPriceResponse(**cached_data, cached=True)
            except ValidationError:
                log.warning("invalid cached current price payload", ticker=normalized_ticker)
        else:
            log.warning("unexpected cached current price payload type", ticker=normalized_ticker)

    cache_miss("prices")
    provider = get_provider()

    try:
        price: float = await provider.get_current_price(normalized_ticker)
    except Exception:
        log.exception("failed to fetch current price", ticker=normalized_ticker)
        raise HTTPException(status_code=502, detail="Failed to fetch price data")

    api_call("yfinance", normalized_ticker)

    await _persist_ohlcv_bars(
        session=session,
        ticker=normalized_ticker,
        bars=[
            {
                "time": datetime.utcnow().isoformat(),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0,
            }
        ],
    )

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "price": price,
        "currency": "USD",
    }
    await cache_set(key, payload, settings.cache_ttl_current_price)
    return CurrentPriceResponse(**payload, cached=False)


@router.get("/historical/{ticker}", response_model=HistoricalPricesResponse)
async def get_historical_prices(
    ticker: str,
    start: str,
    end: str,
    interval: str = "1d",
    session: AsyncSession = Depends(get_db),
) -> HistoricalPricesResponse:
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = historical_key(normalized_ticker, start, end)

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("prices_historical")
        if isinstance(cached_data, dict):
            try:
                return HistoricalPricesResponse(**cached_data, cached=True)
            except ValidationError:
                log.warning(
                    "invalid cached historical payload",
                    ticker=normalized_ticker,
                    start=start,
                    end=end,
                )
        else:
            log.warning(
                "unexpected cached historical payload type",
                ticker=normalized_ticker,
                start=start,
                end=end,
            )

    cache_miss("prices_historical")

    db_bars: list[dict] | None = await _fetch_from_db(
        session=session,
        ticker=normalized_ticker,
        start=start,
        end=end,
    )

    if db_bars is not None:
        log.info(
            "historical prices served from database",
            ticker=normalized_ticker,
            start=start,
            end=end,
            bars=len(db_bars),
        )
        payload: dict[str, Any] = {
            "ticker": normalized_ticker,
            "start": start,
            "end": end,
            "bars": db_bars,
        }
        await cache_set(key, payload, settings.cache_ttl_historical)
        return HistoricalPricesResponse(**payload, cached=False)

    provider = get_provider()

    try:
        bars: list[dict] = await provider.get_historical(normalized_ticker, start, end)
    except Exception:
        log.exception(
            "failed to fetch historical prices",
            ticker=normalized_ticker,
            start=start,
            end=end,
            interval=interval,
        )
        raise HTTPException(status_code=502, detail="Failed to fetch price data")

    api_call("yfinance", normalized_ticker)

    await _persist_ohlcv_bars(
        session=session,
        ticker=normalized_ticker,
        bars=bars,
    )

    payload = {
        "ticker": normalized_ticker,
        "start": start,
        "end": end,
        "bars": bars,
    }
    await cache_set(key, payload, settings.cache_ttl_historical)
    return HistoricalPricesResponse(**payload, cached=False)


@router.get("/{ticker}/indicators", response_model=IndicatorsResponse)
async def get_price_indicators(
    ticker: str,
    start: str,
    end: str,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    session: AsyncSession = Depends(get_db),
) -> IndicatorsResponse:
    started_at: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = indicators_key(normalized_ticker, start, end)

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("prices_indicators")
        if isinstance(cached_data, dict):
            try:
                response = IndicatorsResponse(**cached_data, cached=True)
                record_latency("prices_indicators", (perf_counter() - started_at) * 1000.0)
                return response
            except ValidationError:
                log.warning(
                    "invalid cached indicators payload",
                    ticker=normalized_ticker,
                    start=start,
                    end=end,
                )
        else:
            log.warning(
                "unexpected cached indicators payload type",
                ticker=normalized_ticker,
                start=start,
                end=end,
            )

    cache_miss("prices_indicators")

    bars: list[dict]
    db_bars: list[dict] | None = await _fetch_from_db(
        session=session,
        ticker=normalized_ticker,
        start=start,
        end=end,
    )
    if db_bars is not None:
        bars = db_bars
        log.info(
            "indicator source database",
            ticker=normalized_ticker,
            start=start,
            end=end,
            bars=len(bars),
        )
    else:
        provider = get_provider()
        try:
            bars = await provider.get_historical(normalized_ticker, start, end)
        except Exception:
            log.exception(
                "failed to fetch historical prices for indicators",
                ticker=normalized_ticker,
                start=start,
                end=end,
            )
            raise HTTPException(status_code=502, detail="Failed to fetch price data")

        api_call("yfinance", normalized_ticker)
        await _persist_ohlcv_bars(
            session=session,
            ticker=normalized_ticker,
            bars=bars,
        )

    normalized_bars: list[dict[str, str | float]] = []
    for bar in bars:
        time_value: Any = bar.get("time")
        close_value: Any = bar.get("close")
        if time_value is None or close_value is None:
            continue
        try:
            normalized_bars.append({"time": str(time_value), "close": float(close_value)})
        except (TypeError, ValueError):
            continue

    close_prices: list[float] = [float(bar["close"]) for bar in normalized_bars]
    bar_times: list[str] = [str(bar["time"]) for bar in normalized_bars]

    rsi_values: list[float | None] = calculate_rsi(close_prices, period=rsi_period)
    macd_values: list[dict[str, float | None]] = calculate_macd(
        close_prices,
        fast=macd_fast,
        slow=macd_slow,
        signal=macd_signal,
    )
    bollinger_values: list[dict[str, float | None]] = calculate_bollinger_bands(
        close_prices,
        period=bb_period,
        num_std=bb_std,
    )

    macd_points: list[MACDPoint] = [MACDPoint(**point) for point in macd_values]
    bollinger_points: list[BollingerPoint] = [BollingerPoint(**point) for point in bollinger_values]

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "start": start,
        "end": end,
        "bars": bar_times,
        "rsi": rsi_values,
        "macd": [point.model_dump() for point in macd_points],
        "bollinger": [point.model_dump() for point in bollinger_points],
    }
    await cache_set(key, payload, settings.cache_ttl_historical)

    response = IndicatorsResponse(
        ticker=normalized_ticker,
        start=start,
        end=end,
        cached=False,
        bars=bar_times,
        rsi=rsi_values,
        macd=macd_points,
        bollinger=bollinger_points,
    )
    record_latency("prices_indicators", (perf_counter() - started_at) * 1000.0)
    return response
