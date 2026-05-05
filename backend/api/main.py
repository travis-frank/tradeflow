from core.telemetry import init_datadog
init_datadog()

import structlog
from api.routes.auth import router as auth_router
from api.routes.prices import router as prices_router
from api.routes.watchlist import router as watchlist_router
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.middleware.rate_limit import RateLimitMiddleware
from contextlib import asynccontextmanager
from core.config import get_settings

log = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("tradeflow api starting", env=settings.app_env)
    yield
    log.info("tradeflow api shutting down")


app: FastAPI = FastAPI(
    title="tradeflow.ai",
    description="Production financial data platform",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimitMiddleware)

crypto_router: APIRouter = APIRouter()
fundamentals_router: APIRouter = APIRouter()
news_router: APIRouter = APIRouter()
portfolio_router: APIRouter = APIRouter()
agent_router: APIRouter = APIRouter()

app.include_router(auth_router, prefix="/api/auth")
app.include_router(prices_router, prefix="/api/prices")
app.include_router(crypto_router, prefix="/api/crypto")
app.include_router(fundamentals_router, prefix="/api/fundamentals")
app.include_router(news_router, prefix="/api/news")
app.include_router(watchlist_router, prefix="/api/watchlist")
app.include_router(portfolio_router, prefix="/api/portfolio")
app.include_router(agent_router, prefix="/api/agent")


@app.get("/health")
async def health_check() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env}
