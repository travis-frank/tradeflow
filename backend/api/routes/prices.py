from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from cache.redis_client import cache_get, cache_set, historical_key, price_key
from core.config import get_settings
from core.data_provider import get_provider
from core.metrics import api_call, cache_hit, cache_miss

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


@router.get("/current/{ticker}", response_model=CurrentPriceResponse)
async def get_current_price(ticker: str) -> CurrentPriceResponse:
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
) -> HistoricalPricesResponse:
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    _interval: str = interval
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
    provider = get_provider()

    try:
        bars: list[dict] = await provider.get_historical(normalized_ticker, start, end)
    except Exception:
        log.exception(
            "failed to fetch historical prices",
            ticker=normalized_ticker,
            start=start,
            end=end,
            interval=_interval,
        )
        raise HTTPException(status_code=502, detail="Failed to fetch price data")

    api_call("yfinance", normalized_ticker)

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "start": start,
        "end": end,
        "bars": bars,
    }
    await cache_set(key, payload, settings.cache_ttl_historical)
    return HistoricalPricesResponse(**payload, cached=False)
