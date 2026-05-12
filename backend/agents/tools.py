from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from core.config import get_settings

log = structlog.get_logger()

_HTTP_TIMEOUT_SECONDS: float = 20.0


def _default_range(days: int = 30) -> tuple[str, str]:
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _json_dumps(data: Any) -> str:
    return json.dumps(data, default=str)


def _should_use_in_process_transport() -> bool:
    settings = get_settings()
    return settings.app_env in {"development", "test"}


async def _request_json(
    path: str,
    query: Mapping[str, str] | None = None,
) -> str:
    settings = get_settings()
    transport: httpx.AsyncBaseTransport | None = None

    if _should_use_in_process_transport():
        from api.main import app

        transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=_HTTP_TIMEOUT_SECONDS,
            transport=transport,
        ) as client:
            response = await client.get(path, params=query)
            response.raise_for_status()
            return _json_dumps(response.json())
    except httpx.HTTPStatusError as error:
        log.warning(
            "research tool http error",
            path=path,
            status_code=error.response.status_code,
        )
        return _json_dumps(
            {
                "error": "tool request failed",
                "status_code": error.response.status_code,
                "detail": error.response.text,
            }
        )
    except Exception as error:
        log.exception("research tool failed", path=path)
        return _json_dumps({"error": "tool request failed", "detail": str(error)})


@tool
async def get_current_price(ticker: str) -> str:
    """Get the latest stock price for a ticker."""

    return await _request_json(f"/api/prices/current/{ticker.upper()}")


@tool
async def get_historical_prices(ticker: str, start: str, end: str) -> str:
    """Get stock historical OHLCV prices for a ticker and date range."""

    return await _request_json(
        f"/api/prices/historical/{ticker.upper()}",
        {"start": start, "end": end},
    )


@tool
async def get_indicators(ticker: str) -> str:
    """Get technical indicators for the last month of stock prices."""

    start, end = _default_range()
    return await _request_json(
        f"/api/prices/{ticker.upper()}/indicators",
        {"start": start, "end": end},
    )


@tool
async def get_news(ticker: str) -> str:
    """Get recent news for a ticker."""

    return await _request_json(f"/api/news/{ticker.upper()}")


@tool
async def get_fundamentals(ticker: str) -> str:
    """Get stock income statement fundamentals for a ticker."""

    return await _request_json(f"/api/fundamentals/{ticker.upper()}/income")


@tool
async def get_balance_sheet(ticker: str) -> str:
    """Get stock balance sheet fundamentals for a ticker."""

    return await _request_json(f"/api/fundamentals/{ticker.upper()}/balance-sheet")


@tool
async def get_cash_flow(ticker: str) -> str:
    """Get stock cash flow fundamentals for a ticker."""

    return await _request_json(f"/api/fundamentals/{ticker.upper()}/cash-flow")


@tool
async def get_crypto_price(ticker: str) -> str:
    """Get the latest crypto price for a ticker like BTC-USD."""

    return await _request_json(f"/api/crypto/current/{ticker.upper()}")


@tool
async def get_crypto_historical(ticker: str, start: str, end: str) -> str:
    """Get crypto historical OHLCV prices for a ticker and date range."""

    return await _request_json(
        f"/api/crypto/historical/{ticker.upper()}",
        {"start": start, "end": end},
    )


RESEARCH_TOOLS = [
    get_current_price,
    get_historical_prices,
    get_indicators,
    get_news,
    get_fundamentals,
    get_balance_sheet,
    get_cash_flow,
    get_crypto_price,
    get_crypto_historical,
]

STOCK_RESEARCH_TOOLS = [
    get_current_price,
    get_historical_prices,
    get_indicators,
    get_news,
    get_fundamentals,
    get_balance_sheet,
    get_cash_flow,
]

CRYPTO_RESEARCH_TOOLS = [
    get_crypto_price,
    get_crypto_historical,
    get_news,
]

RESEARCH_TOOL_MAP = {research_tool.name: research_tool for research_tool in RESEARCH_TOOLS}
STOCK_RESEARCH_TOOL_MAP = {
    research_tool.name: research_tool for research_tool in STOCK_RESEARCH_TOOLS
}
CRYPTO_RESEARCH_TOOL_MAP = {
    research_tool.name: research_tool for research_tool in CRYPTO_RESEARCH_TOOLS
}
