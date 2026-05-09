from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import structlog
import yfinance as yf

from core.config import get_settings

log = structlog.get_logger()


class DataProvider(ABC):
    @abstractmethod
    async def get_current_price(self, ticker: str) -> float:
        raise NotImplementedError

    @abstractmethod
    async def get_historical(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def get_fundamentals(self, ticker: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def get_news(self, ticker: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def get_crypto_price(self, ticker: str) -> float:
        raise NotImplementedError

    @abstractmethod
    async def get_crypto_historical(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> list[dict]:
        raise NotImplementedError


class YFinanceProvider(DataProvider):
    async def get_current_price(self, ticker: str) -> float:
        try:
            loop = asyncio.get_running_loop()
            price: Any = await loop.run_in_executor(
                None,
                lambda: yf.Ticker(ticker).fast_info.last_price,
            )
            return float(price)
        except Exception:
            log.exception("failed to fetch current price", ticker=ticker)
            raise

    async def get_historical(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> list[dict]:
        try:
            loop = asyncio.get_running_loop()
            history: Any = await loop.run_in_executor(
                None,
                lambda: yf.Ticker(ticker).history(start=start, end=end, interval=interval),
            )
            records: list[dict] = []
            for timestamp, row in history.iterrows():
                records.append({
                    "time": timestamp.isoformat(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                })
            return records
        except Exception:
            log.exception(
                "failed to fetch historical data",
                ticker=ticker,
                start=start,
                end=end,
                interval=interval,
            )
            raise

    async def get_fundamentals(self, ticker: str) -> dict:
        try:
            loop = asyncio.get_running_loop()
            info: Any = await loop.run_in_executor(
                None,
                lambda: yf.Ticker(ticker).info,
            )
            return {
                "revenue": info.get("totalRevenue"),
                "gross_profit": info.get("grossProfits"),
                "net_income": info.get("netIncomeToCommon"),
                "eps": info.get("trailingEps"),
            }
        except Exception:
            log.exception("failed to fetch fundamentals", ticker=ticker)
            raise

    async def get_news(self, ticker: str) -> list[dict]:
        try:
            loop = asyncio.get_running_loop()
            news_items: Any = await loop.run_in_executor(
                None,
                lambda: yf.Ticker(ticker).news,
            )
            articles: list[dict] = []
            for item in news_items:
                content = item.get("content", {})
                url = (
                    (content.get("canonicalUrl") or {}).get("url")
                    or (content.get("clickThroughUrl") or {}).get("url")
                )
                pub_date = content.get("pubDate")
                articles.append({
                    "title": content.get("title"),
                    "url": url,
                    "published_at": pub_date,
                    "source": (content.get("provider") or {}).get("displayName"),
                })
            return articles
        except Exception:
            log.exception("failed to fetch news", ticker=ticker)
            raise

    async def get_crypto_price(self, ticker: str) -> float:
        try:
            return await self.get_current_price(ticker)
        except Exception:
            log.exception("failed to fetch crypto price", ticker=ticker)
            raise

    async def get_crypto_historical(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> list[dict]:
        try:
            return await self.get_historical(
                ticker=ticker,
                start=start,
                end=end,
                interval=interval,
            )
        except Exception:
            log.exception(
                "failed to fetch crypto historical data",
                ticker=ticker,
                start=start,
                end=end,
                interval=interval,
            )
            raise


def get_provider() -> DataProvider:
    settings = get_settings()
    if settings.data_provider == "yfinance":
        provider = YFinanceProvider()
        log.info("data provider initialized", provider="yfinance")
        return provider
    raise ValueError(f"Unknown data provider: {settings.data_provider}")
