"""Initial schema with TimescaleDB hypertables.

Revision ID: 20260430_0001
Revises: None
Create Date: 2026-04-30 13:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260430_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_prices",
        sa.Column("time", sa.DateTime(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("interval", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("time", "ticker"),
    )
    op.create_index(
        "ix_stock_prices_ticker_time_desc",
        "stock_prices",
        ["ticker", sa.text("time DESC")],
        unique=False,
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("SELECT create_hypertable('stock_prices', 'time', if_not_exists => TRUE)")

    op.create_table(
        "crypto_prices",
        sa.Column("time", sa.DateTime(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("interval", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("time", "ticker"),
    )
    op.create_index(
        "ix_crypto_prices_ticker_time_desc",
        "crypto_prices",
        ["ticker", sa.text("time DESC")],
        unique=False,
    )
    op.execute("SELECT create_hypertable('crypto_prices', 'time', if_not_exists => TRUE)")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column(
            "asset_type",
            sa.Enum("stock", "crypto", name="asset_type_enum", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "ticker",
            "asset_type",
            name="uq_watchlist_items_user_ticker_asset_type",
        ),
    )
    op.create_index("ix_watchlist_items_user_id", "watchlist_items", ["user_id"], unique=False)

    op.create_table(
        "income_statements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column(
            "period_type",
            sa.Enum("annual", "quarterly", name="period_type_enum", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("period_date", sa.DateTime(), nullable=False),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("gross_profit", sa.Float(), nullable=True),
        sa.Column("operating_income", sa.Float(), nullable=True),
        sa.Column("net_income", sa.Float(), nullable=True),
        sa.Column("eps_basic", sa.Float(), nullable=True),
        sa.Column("eps_diluted", sa.Float(), nullable=True),
        sa.Column("ebitda", sa.Float(), nullable=True),
        sa.Column("raw_data", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "period_type",
            "period_date",
            name="uq_income_statements_ticker_period_type_period_date",
        ),
    )
    op.create_index(
        "ix_income_statements_ticker_period_date_desc",
        "income_statements",
        ["ticker", sa.text("period_date DESC")],
        unique=False,
    )

    op.create_table(
        "balance_sheets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column(
            "period_type",
            sa.Enum("annual", "quarterly", name="period_type_enum", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("period_date", sa.DateTime(), nullable=False),
        sa.Column("total_assets", sa.Float(), nullable=True),
        sa.Column("total_liabilities", sa.Float(), nullable=True),
        sa.Column("total_equity", sa.Float(), nullable=True),
        sa.Column("cash_and_equivalents", sa.Float(), nullable=True),
        sa.Column("total_debt", sa.Float(), nullable=True),
        sa.Column("working_capital", sa.Float(), nullable=True),
        sa.Column("raw_data", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "period_type",
            "period_date",
            name="uq_balance_sheets_ticker_period_type_period_date",
        ),
    )
    op.create_index(
        "ix_balance_sheets_ticker_period_date_desc",
        "balance_sheets",
        ["ticker", sa.text("period_date DESC")],
        unique=False,
    )

    op.create_table(
        "cash_flow_statements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column(
            "period_type",
            sa.Enum("annual", "quarterly", name="period_type_enum", native_enum=False, length=10),
            nullable=False,
        ),
        sa.Column("period_date", sa.DateTime(), nullable=False),
        sa.Column("operating_cash_flow", sa.Float(), nullable=True),
        sa.Column("investing_cash_flow", sa.Float(), nullable=True),
        sa.Column("financing_cash_flow", sa.Float(), nullable=True),
        sa.Column("free_cash_flow", sa.Float(), nullable=True),
        sa.Column("capital_expenditures", sa.Float(), nullable=True),
        sa.Column("raw_data", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "period_type",
            "period_date",
            name="uq_cash_flow_statements_ticker_period_type_period_date",
        ),
    )
    op.create_index(
        "ix_cash_flow_statements_ticker_period_date_desc",
        "cash_flow_statements",
        ["ticker", sa.text("period_date DESC")],
        unique=False,
    )

    op.create_table(
        "news_articles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "sentiment",
            sa.Enum(
                "positive",
                "negative",
                "neutral",
                name="sentiment_type_enum",
                native_enum=False,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index(
        "ix_news_articles_ticker_published_at_desc",
        "news_articles",
        ["ticker", sa.text("published_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_news_articles_published_at_desc",
        "news_articles",
        [sa.text("published_at DESC")],
        unique=False,
    )

    op.create_table(
        "backtest_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("strategy", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.DateTime(), nullable=False),
        sa.Column("end_date", sa.DateTime(), nullable=False),
        sa.Column("initial_capital", sa.Float(), nullable=False),
        sa.Column("final_capital", sa.Float(), nullable=False),
        sa.Column("total_return", sa.Float(), nullable=False),
        sa.Column("annualized_return", sa.Float(), nullable=False),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("total_trades", sa.Integer(), nullable=False),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("parameters", sa.Text(), nullable=True),
        sa.Column("equity_curve", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_backtest_results_user_id_created_at_desc",
        "backtest_results",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_backtest_results_ticker_strategy",
        "backtest_results",
        ["ticker", "strategy"],
        unique=False,
    )
    op.create_index("ix_backtest_results_user_id", "backtest_results", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_backtest_results_user_id", table_name="backtest_results")
    op.drop_index("ix_backtest_results_ticker_strategy", table_name="backtest_results")
    op.drop_index("ix_backtest_results_user_id_created_at_desc", table_name="backtest_results")
    op.drop_table("backtest_results")

    op.drop_index("ix_news_articles_published_at_desc", table_name="news_articles")
    op.drop_index("ix_news_articles_ticker_published_at_desc", table_name="news_articles")
    op.drop_table("news_articles")

    op.drop_index("ix_cash_flow_statements_ticker_period_date_desc", table_name="cash_flow_statements")
    op.drop_table("cash_flow_statements")

    op.drop_index("ix_balance_sheets_ticker_period_date_desc", table_name="balance_sheets")
    op.drop_table("balance_sheets")

    op.drop_index("ix_income_statements_ticker_period_date_desc", table_name="income_statements")
    op.drop_table("income_statements")

    op.drop_index("ix_watchlist_items_user_id", table_name="watchlist_items")
    op.drop_table("watchlist_items")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_crypto_prices_ticker_time_desc", table_name="crypto_prices")
    op.drop_table("crypto_prices")

    op.drop_index("ix_stock_prices_ticker_time_desc", table_name="stock_prices")
    op.drop_table("stock_prices")
