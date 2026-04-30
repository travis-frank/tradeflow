from __future__ import annotations

"""SQLAlchemy ORM models for financial and user-domain data."""

from datetime import datetime
from enum import Enum
from typing import ClassVar

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, desc
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class PeriodType(str, Enum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"


class SentimentType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class StockPrice(Base):
    __tablename__: ClassVar[str] = "stock_prices"
    __table_args__: ClassVar[tuple[object, ...]] = (
        Index("ix_stock_prices_ticker_time_desc", "ticker", desc("time")),
    )

    time: Mapped[datetime] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    open: Mapped[float | None]
    high: Mapped[float | None]
    low: Mapped[float | None]
    close: Mapped[float | None]
    volume: Mapped[int | None] = mapped_column(BigInteger)
    interval: Mapped[str] = mapped_column(String(10), default="1d")


class CryptoPrice(Base):
    __tablename__: ClassVar[str] = "crypto_prices"
    __table_args__: ClassVar[tuple[object, ...]] = (
        Index("ix_crypto_prices_ticker_time_desc", "ticker", desc("time")),
    )

    time: Mapped[datetime] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    open: Mapped[float | None]
    high: Mapped[float | None]
    low: Mapped[float | None]
    close: Mapped[float | None]
    volume: Mapped[int | None] = mapped_column(BigInteger)
    interval: Mapped[str] = mapped_column(String(10), default="1d")


class User(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "users"
    __table_args__: ClassVar[tuple[object, ...]] = (
        Index("ix_users_email", "email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_verified: Mapped[bool] = mapped_column(default=False)


class WatchlistItem(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "watchlist_items"
    __table_args__: ClassVar[tuple[object, ...]] = (
        UniqueConstraint(
            "user_id",
            "ticker",
            "asset_type",
            name="uq_watchlist_items_user_ticker_asset_type",
        ),
        Index("ix_watchlist_items_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ticker: Mapped[str] = mapped_column(String(20))
    asset_type: Mapped[AssetType] = mapped_column(
        SQLEnum(AssetType, name="asset_type_enum", native_enum=False, length=10),
        default=AssetType.STOCK,
    )
    notes: Mapped[str | None] = mapped_column(String(500))


class IncomeStatement(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "income_statements"
    __table_args__: ClassVar[tuple[object, ...]] = (
        UniqueConstraint(
            "ticker",
            "period_type",
            "period_date",
            name="uq_income_statements_ticker_period_type_period_date",
        ),
        Index("ix_income_statements_ticker_period_date_desc", "ticker", desc("period_date")),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20))
    period_type: Mapped[PeriodType] = mapped_column(
        SQLEnum(PeriodType, name="period_type_enum", native_enum=False, length=10),
    )
    period_date: Mapped[datetime]
    revenue: Mapped[float | None]
    gross_profit: Mapped[float | None]
    operating_income: Mapped[float | None]
    net_income: Mapped[float | None]
    eps_basic: Mapped[float | None]
    eps_diluted: Mapped[float | None]
    ebitda: Mapped[float | None]
    raw_data: Mapped[str | None] = mapped_column(Text)


class BalanceSheet(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "balance_sheets"
    __table_args__: ClassVar[tuple[object, ...]] = (
        UniqueConstraint(
            "ticker",
            "period_type",
            "period_date",
            name="uq_balance_sheets_ticker_period_type_period_date",
        ),
        Index("ix_balance_sheets_ticker_period_date_desc", "ticker", desc("period_date")),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20))
    period_type: Mapped[PeriodType] = mapped_column(
        SQLEnum(PeriodType, name="period_type_enum", native_enum=False, length=10),
    )
    period_date: Mapped[datetime]
    total_assets: Mapped[float | None]
    total_liabilities: Mapped[float | None]
    total_equity: Mapped[float | None]
    cash_and_equivalents: Mapped[float | None]
    total_debt: Mapped[float | None]
    working_capital: Mapped[float | None]
    raw_data: Mapped[str | None] = mapped_column(Text)


class CashFlowStatement(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "cash_flow_statements"
    __table_args__: ClassVar[tuple[object, ...]] = (
        UniqueConstraint(
            "ticker",
            "period_type",
            "period_date",
            name="uq_cash_flow_statements_ticker_period_type_period_date",
        ),
        Index(
            "ix_cash_flow_statements_ticker_period_date_desc",
            "ticker",
            desc("period_date"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20))
    period_type: Mapped[PeriodType] = mapped_column(
        SQLEnum(PeriodType, name="period_type_enum", native_enum=False, length=10),
    )
    period_date: Mapped[datetime]
    operating_cash_flow: Mapped[float | None]
    investing_cash_flow: Mapped[float | None]
    financing_cash_flow: Mapped[float | None]
    free_cash_flow: Mapped[float | None]
    capital_expenditures: Mapped[float | None]
    raw_data: Mapped[str | None] = mapped_column(Text)


class NewsArticle(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "news_articles"
    __table_args__: ClassVar[tuple[object, ...]] = (
        Index("ix_news_articles_ticker_published_at_desc", "ticker", desc("published_at")),
        Index("ix_news_articles_published_at_desc", desc("published_at")),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    source: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime | None]
    summary: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[SentimentType | None] = mapped_column(
        SQLEnum(SentimentType, name="sentiment_type_enum", native_enum=False, length=20),
    )
    sentiment_score: Mapped[float | None]


class BacktestResult(TimestampMixin, Base):
    __tablename__: ClassVar[str] = "backtest_results"
    __table_args__: ClassVar[tuple[object, ...]] = (
        Index("ix_backtest_results_user_id_created_at_desc", "user_id", desc("created_at")),
        Index("ix_backtest_results_ticker_strategy", "ticker", "strategy"),
        Index("ix_backtest_results_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ticker: Mapped[str] = mapped_column(String(20))
    strategy: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[datetime]
    end_date: Mapped[datetime]
    initial_capital: Mapped[float]
    final_capital: Mapped[float]
    total_return: Mapped[float]
    annualized_return: Mapped[float]
    sharpe_ratio: Mapped[float | None]
    max_drawdown: Mapped[float | None]
    total_trades: Mapped[int]
    win_rate: Mapped[float | None]
    parameters: Mapped[str | None] = mapped_column(Text)
    equity_curve: Mapped[str | None] = mapped_column(Text)
