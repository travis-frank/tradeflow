from __future__ import annotations

import asyncio
from datetime import datetime, time
import json
from time import perf_counter
from typing import Any

import pandas as pd
import structlog
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy as sa

from cache.redis_client import cache_get, cache_set, fundamentals_key
from core.config import get_settings
from core.metrics import api_call, cache_hit, cache_miss, record_latency
from db.session import get_db

log = structlog.get_logger()
router: APIRouter = APIRouter()

INCOME_ITEM_MAP: dict[str, str] = {
    "Total Revenue": "revenue",
    "Gross Profit": "gross_profit",
    "Operating Income": "operating_income",
    "Net Income": "net_income",
    "Basic EPS": "eps_basic",
    "Diluted EPS": "eps_diluted",
}

BALANCE_ITEM_MAP: dict[str, str] = {
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": "total_liabilities",
    "Stockholders Equity": "total_equity",
    "Cash And Cash Equivalents": "cash_and_equivalents",
    "Total Debt": "total_debt",
}

CASHFLOW_ITEM_MAP: dict[str, str] = {
    "Operating Cash Flow": "operating_cash_flow",
    "Investing Cash Flow": "investing_cash_flow",
    "Financing Cash Flow": "financing_cash_flow",
    "Free Cash Flow": "free_cash_flow",
    "Capital Expenditure": "capital_expenditures",
}


class PeriodData(BaseModel):
    period_date: str
    period_type: str


class IncomeStatementResponse(BaseModel):
    ticker: str
    cached: bool
    periods: list[dict[str, Any]]


class BalanceSheetResponse(BaseModel):
    ticker: str
    cached: bool
    periods: list[dict[str, Any]]


class CashFlowResponse(BaseModel):
    ticker: str
    cached: bool
    periods: list[dict[str, Any]]


def _safe_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _period_date_to_iso(column_value: Any) -> str:
    if hasattr(column_value, "date"):
        return column_value.date().isoformat()
    parsed = datetime.fromisoformat(str(column_value))
    return parsed.date().isoformat()


def _to_db_datetime(date_iso: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(date_iso).date(), time.min)


def _json_safe_dict(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if pd.isna(value):
            cleaned[key] = None
        elif isinstance(value, (int, float, str, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


def _build_periods(df: pd.DataFrame, item_map: dict[str, str]) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []

    for column in df.columns:
        period_date: str = _period_date_to_iso(column)
        source_values: dict[str, Any] = df[column].to_dict()

        period_payload: dict[str, Any] = {
            "period_date": period_date,
            "period_type": "annual",
        }

        for source_key, response_key in item_map.items():
            converted_value: float | None = _safe_float(source_values.get(source_key))
            if converted_value is not None:
                period_payload[response_key] = converted_value

        periods.append(period_payload)

    return periods


async def _fetch_statement_df(ticker: str, statement_attr: str) -> pd.DataFrame:
    loop = asyncio.get_running_loop()

    def _load() -> pd.DataFrame:
        ticker_obj = yf.Ticker(ticker)
        return getattr(ticker_obj, statement_attr)

    result = await loop.run_in_executor(None, _load)
    if not isinstance(result, pd.DataFrame):
        return pd.DataFrame()
    return result


async def _persist_income_statements(
    session: AsyncSession,
    ticker: str,
    periods: list[dict[str, Any]],
    source_df: pd.DataFrame,
) -> None:
    if not periods:
        return

    statement = sa.text(
        """
        INSERT INTO income_statements (
            ticker, period_type, period_date, revenue, gross_profit,
            operating_income, net_income, eps_basic, eps_diluted, raw_data
        )
        VALUES (
            :ticker, :period_type, :period_date, :revenue, :gross_profit,
            :operating_income, :net_income, :eps_basic, :eps_diluted, :raw_data
        )
        ON CONFLICT (ticker, period_type, period_date) DO UPDATE SET
            revenue = EXCLUDED.revenue,
            gross_profit = EXCLUDED.gross_profit,
            operating_income = EXCLUDED.operating_income,
            net_income = EXCLUDED.net_income,
            eps_basic = EXCLUDED.eps_basic,
            eps_diluted = EXCLUDED.eps_diluted,
            raw_data = EXCLUDED.raw_data,
            updated_at = NOW()
        """
    )

    try:
        for column in source_df.columns:
            period_date: str = _period_date_to_iso(column)
            source_values = source_df[column].to_dict()
            await session.execute(
                statement,
                {
                    "ticker": ticker,
                    "period_type": "annual",
                    "period_date": _to_db_datetime(period_date),
                    "revenue": _safe_float(source_values.get("Total Revenue")),
                    "gross_profit": _safe_float(source_values.get("Gross Profit")),
                    "operating_income": _safe_float(source_values.get("Operating Income")),
                    "net_income": _safe_float(source_values.get("Net Income")),
                    "eps_basic": _safe_float(source_values.get("Basic EPS")),
                    "eps_diluted": _safe_float(source_values.get("Diluted EPS")),
                    "raw_data": json.dumps(_json_safe_dict(source_values)),
                },
            )
    except Exception:
        await session.rollback()
        log.exception("failed to persist income statements", ticker=ticker)


async def _persist_balance_sheets(
    session: AsyncSession,
    ticker: str,
    periods: list[dict[str, Any]],
    source_df: pd.DataFrame,
) -> None:
    if not periods:
        return

    statement = sa.text(
        """
        INSERT INTO balance_sheets (
            ticker, period_type, period_date, total_assets, total_liabilities,
            total_equity, cash_and_equivalents, total_debt, working_capital, raw_data
        )
        VALUES (
            :ticker, :period_type, :period_date, :total_assets, :total_liabilities,
            :total_equity, :cash_and_equivalents, :total_debt, :working_capital, :raw_data
        )
        ON CONFLICT (ticker, period_type, period_date) DO UPDATE SET
            total_assets = EXCLUDED.total_assets,
            total_liabilities = EXCLUDED.total_liabilities,
            total_equity = EXCLUDED.total_equity,
            cash_and_equivalents = EXCLUDED.cash_and_equivalents,
            total_debt = EXCLUDED.total_debt,
            working_capital = EXCLUDED.working_capital,
            raw_data = EXCLUDED.raw_data,
            updated_at = NOW()
        """
    )

    try:
        for column in source_df.columns:
            period_date: str = _period_date_to_iso(column)
            source_values = source_df[column].to_dict()
            await session.execute(
                statement,
                {
                    "ticker": ticker,
                    "period_type": "annual",
                    "period_date": _to_db_datetime(period_date),
                    "total_assets": _safe_float(source_values.get("Total Assets")),
                    "total_liabilities": _safe_float(
                        source_values.get("Total Liabilities Net Minority Interest")
                    ),
                    "total_equity": _safe_float(source_values.get("Stockholders Equity")),
                    "cash_and_equivalents": _safe_float(
                        source_values.get("Cash And Cash Equivalents")
                    ),
                    "total_debt": _safe_float(source_values.get("Total Debt")),
                    "working_capital": _safe_float(source_values.get("Working Capital")),
                    "raw_data": json.dumps(_json_safe_dict(source_values)),
                },
            )
    except Exception:
        await session.rollback()
        log.exception("failed to persist balance sheets", ticker=ticker)


async def _persist_cash_flows(
    session: AsyncSession,
    ticker: str,
    periods: list[dict[str, Any]],
    source_df: pd.DataFrame,
) -> None:
    if not periods:
        return

    statement = sa.text(
        """
        INSERT INTO cash_flow_statements (
            ticker, period_type, period_date, operating_cash_flow, investing_cash_flow,
            financing_cash_flow, free_cash_flow, capital_expenditures, raw_data
        )
        VALUES (
            :ticker, :period_type, :period_date, :operating_cash_flow, :investing_cash_flow,
            :financing_cash_flow, :free_cash_flow, :capital_expenditures, :raw_data
        )
        ON CONFLICT (ticker, period_type, period_date) DO UPDATE SET
            operating_cash_flow = EXCLUDED.operating_cash_flow,
            investing_cash_flow = EXCLUDED.investing_cash_flow,
            financing_cash_flow = EXCLUDED.financing_cash_flow,
            free_cash_flow = EXCLUDED.free_cash_flow,
            capital_expenditures = EXCLUDED.capital_expenditures,
            raw_data = EXCLUDED.raw_data,
            updated_at = NOW()
        """
    )

    try:
        for column in source_df.columns:
            period_date: str = _period_date_to_iso(column)
            source_values = source_df[column].to_dict()
            await session.execute(
                statement,
                {
                    "ticker": ticker,
                    "period_type": "annual",
                    "period_date": _to_db_datetime(period_date),
                    "operating_cash_flow": _safe_float(source_values.get("Operating Cash Flow")),
                    "investing_cash_flow": _safe_float(source_values.get("Investing Cash Flow")),
                    "financing_cash_flow": _safe_float(source_values.get("Financing Cash Flow")),
                    "free_cash_flow": _safe_float(source_values.get("Free Cash Flow")),
                    "capital_expenditures": _safe_float(source_values.get("Capital Expenditure")),
                    "raw_data": json.dumps(_json_safe_dict(source_values)),
                },
            )
    except Exception:
        await session.rollback()
        log.exception("failed to persist cash flow statements", ticker=ticker)


@router.get("/{ticker}/income-statement", response_model=IncomeStatementResponse)
async def get_income_statement(
    ticker: str,
    session: AsyncSession = Depends(get_db),
) -> IncomeStatementResponse:
    started: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = fundamentals_key(normalized_ticker, "income_statement")

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("fundamentals_income_statement")
        record_latency("fundamentals_income_statement", (perf_counter() - started) * 1000)
        return IncomeStatementResponse(**cached_data, cached=True)

    cache_miss("fundamentals_income_statement")

    try:
        financials_df: pd.DataFrame = await _fetch_statement_df(normalized_ticker, "financials")
    except Exception:
        log.exception("failed to fetch income statement", ticker=normalized_ticker)
        raise HTTPException(status_code=502, detail="Failed to fetch fundamentals data")

    periods: list[dict[str, Any]] = _build_periods(financials_df, INCOME_ITEM_MAP)
    api_call("yfinance", normalized_ticker)

    await _persist_income_statements(session, normalized_ticker, periods, financials_df)

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "periods": periods,
    }
    await cache_set(key, payload, settings.cache_ttl_fundamentals)
    record_latency("fundamentals_income_statement", (perf_counter() - started) * 1000)
    return IncomeStatementResponse(**payload, cached=False)


@router.get("/{ticker}/balance-sheet", response_model=BalanceSheetResponse)
async def get_balance_sheet(
    ticker: str,
    session: AsyncSession = Depends(get_db),
) -> BalanceSheetResponse:
    started: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = fundamentals_key(normalized_ticker, "balance_sheet")

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("fundamentals_balance_sheet")
        record_latency("fundamentals_balance_sheet", (perf_counter() - started) * 1000)
        return BalanceSheetResponse(**cached_data, cached=True)

    cache_miss("fundamentals_balance_sheet")

    try:
        balance_df: pd.DataFrame = await _fetch_statement_df(normalized_ticker, "balance_sheet")
    except Exception:
        log.exception("failed to fetch balance sheet", ticker=normalized_ticker)
        raise HTTPException(status_code=502, detail="Failed to fetch fundamentals data")

    periods: list[dict[str, Any]] = _build_periods(balance_df, BALANCE_ITEM_MAP)
    api_call("yfinance", normalized_ticker)

    await _persist_balance_sheets(session, normalized_ticker, periods, balance_df)

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "periods": periods,
    }
    await cache_set(key, payload, settings.cache_ttl_fundamentals)
    record_latency("fundamentals_balance_sheet", (perf_counter() - started) * 1000)
    return BalanceSheetResponse(**payload, cached=False)


@router.get("/{ticker}/cash-flow", response_model=CashFlowResponse)
async def get_cash_flow(
    ticker: str,
    session: AsyncSession = Depends(get_db),
) -> CashFlowResponse:
    started: float = perf_counter()
    settings = get_settings()
    normalized_ticker: str = ticker.upper()
    key: str = fundamentals_key(normalized_ticker, "cash_flow")

    cached_data: Any | None = await cache_get(key)
    if cached_data is not None:
        cache_hit("fundamentals_cash_flow")
        record_latency("fundamentals_cash_flow", (perf_counter() - started) * 1000)
        return CashFlowResponse(**cached_data, cached=True)

    cache_miss("fundamentals_cash_flow")

    try:
        cashflow_df: pd.DataFrame = await _fetch_statement_df(normalized_ticker, "cashflow")
    except Exception:
        log.exception("failed to fetch cash flow statement", ticker=normalized_ticker)
        raise HTTPException(status_code=502, detail="Failed to fetch fundamentals data")

    periods: list[dict[str, Any]] = _build_periods(cashflow_df, CASHFLOW_ITEM_MAP)
    api_call("yfinance", normalized_ticker)

    await _persist_cash_flows(session, normalized_ticker, periods, cashflow_df)

    payload: dict[str, Any] = {
        "ticker": normalized_ticker,
        "periods": periods,
    }
    await cache_set(key, payload, settings.cache_ttl_fundamentals)
    record_latency("fundamentals_cash_flow", (perf_counter() - started) * 1000)
    return CashFlowResponse(**payload, cached=False)
