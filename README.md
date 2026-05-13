# tradeflow

Production-style market intelligence platform — authenticated user workflows,
real-time stock and crypto prices, technical indicators, fundamentals, news,
and a LangGraph-powered AI research agent with traceability.

> Core platform functional; production deployment in progress.

## Features
- **Auth** — JWT register/login with protected routes and persisted session state
- **Watchlist** — per-user watchlist with live price and news cards
- **Market data** — current + historical OHLCV for stocks and crypto
- **Technical indicators** — RSI, MACD, Bollinger Bands with interactive chart ranges
- **Fundamentals** — income statement, balance sheet, cash flow per ticker
- **News** — ticker news fetch with DB persistence
- **Research agent** — LangGraph planner/tools/synthesizer with asset-scoped tool access,
  deterministic fallback mode, and a UI traceability panel (phases, tools, endpoints)

## Stack
- **Backend**: Python 3.11, FastAPI, TimescaleDB, Redis, Celery, LangGraph
- **Frontend**: React 18, TypeScript, Vite, Tailwind
- **Infra**: Docker, GitHub Actions, Datadog