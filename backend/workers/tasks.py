from __future__ import annotations

from collections.abc import Iterable

import httpx
import psycopg2
import structlog

from core.config import get_settings
from workers.celery_app import celery_app

log = structlog.get_logger()

_API_BASE_URL: str = "http://api:8000"
_HTTP_TIMEOUT_SECONDS: float = 15.0


def _to_sync_postgres_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def _fetch_watchlist_tickers() -> list[str]:
    settings = get_settings()
    sync_database_url: str = _to_sync_postgres_url(settings.database_url)

    connection = psycopg2.connect(sync_database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT ticker FROM watchlist_items WHERE ticker IS NOT NULL")
            rows: Iterable[tuple[str | None]] = cursor.fetchall()
    finally:
        connection.close()

    tickers: list[str] = []
    for row in rows:
        ticker: str | None = row[0]
        if ticker:
            tickers.append(ticker.upper())
    return tickers


@celery_app.task
def warm_watchlist_cache() -> None:
    try:
        tickers: list[str] = _fetch_watchlist_tickers()
    except Exception:
        log.exception("failed to load watchlist tickers for cache warming")
        return

    warmed_count: int = 0
    failed_count: int = 0

    with httpx.Client(base_url=_API_BASE_URL, timeout=_HTTP_TIMEOUT_SECONDS) as client:
        for ticker in tickers:
            try:
                response = client.get(f"/api/prices/current/{ticker}")
                response.raise_for_status()
                warmed_count += 1
                log.info("ticker cache warmed", ticker=ticker)
            except Exception:
                failed_count += 1
                log.exception("failed to warm ticker cache", ticker=ticker)

    log.info(
        "watchlist cache warming complete",
        total_tickers=len(tickers),
        warmed=warmed_count,
        failed=failed_count,
    )


@celery_app.task
def refresh_fundamentals() -> None:
    try:
        tickers: list[str] = _fetch_watchlist_tickers()
    except Exception:
        log.exception("failed to load watchlist tickers for fundamentals refresh")
        return

    refreshed_count: int = 0
    failed_count: int = 0
    endpoints: tuple[str, str, str] = (
        "income-statement",
        "balance-sheet",
        "cash-flow",
    )

    with httpx.Client(base_url=_API_BASE_URL, timeout=_HTTP_TIMEOUT_SECONDS) as client:
        for ticker in tickers:
            ticker_failed: bool = False
            for endpoint in endpoints:
                try:
                    response = client.get(f"/api/fundamentals/{ticker}/{endpoint}")
                    response.raise_for_status()
                except Exception:
                    ticker_failed = True
                    log.exception(
                        "failed fundamentals refresh request",
                        ticker=ticker,
                        endpoint=endpoint,
                    )
            if ticker_failed:
                failed_count += 1
            else:
                refreshed_count += 1
                log.info("fundamentals refreshed", ticker=ticker)

    log.info(
        "fundamentals refresh complete",
        total_tickers=len(tickers),
        refreshed=refreshed_count,
        failed=failed_count,
    )
