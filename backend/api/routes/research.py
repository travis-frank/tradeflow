from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from agents.research_agent import run_research
from api.routes.auth import get_current_user
from db.models.models import User

log = structlog.get_logger()
router: APIRouter = APIRouter()

AssetType = Literal["stock", "crypto"]
TracePhase = Literal["planner", "tool", "synthesizer", "fallback"]
TraceStatus = Literal["success", "error", "skipped"]


class ResearchRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    question: str = Field(min_length=3, max_length=500)
    asset_type: AssetType = "stock"

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("Ticker is required")
        return normalized

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("Question must be at least 3 characters")
        return normalized


class ResearchTraceEvent(BaseModel):
    step: int
    phase: TracePhase
    action: str
    tool: str | None = None
    endpoint: str | None = None
    status: TraceStatus = "success"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchResponse(BaseModel):
    ticker: str
    asset_type: AssetType
    question: str
    summary: str
    price_context: str
    news_summary: str
    technical_context: str
    fundamental_context: str
    risks: list[str]
    sources: list[dict[str, Any]]
    trace: list[ResearchTraceEvent]
    generated_at: str


@router.post("/research", response_model=ResearchResponse)
async def create_research_report(
    payload: ResearchRequest,
    current_user: User = Depends(get_current_user),
) -> ResearchResponse:
    if payload.asset_type == "crypto" and "-" not in payload.ticker:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Crypto tickers must contain a dash e.g. BTC-USD",
        )
    if payload.asset_type == "stock" and "-" in payload.ticker:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stock tickers should not contain a dash",
        )

    try:
        result = await run_research(
            ticker=payload.ticker,
            question=payload.question,
            asset_type=payload.asset_type,
        )
    except Exception:
        log.exception(
            "research agent failed",
            ticker=payload.ticker,
            user_id=current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate research report",
        )

    return ResearchResponse(
        ticker=str(result.get("ticker", payload.ticker)),
        asset_type=payload.asset_type,
        question=str(result.get("question", payload.question)),
        summary=str(result.get("summary", "")),
        price_context=str(result.get("price_context", "")),
        news_summary=str(result.get("news_summary", "")),
        technical_context=str(result.get("technical_context", "")),
        fundamental_context=str(result.get("fundamental_context", "")),
        risks=list(result.get("risks", [])),
        sources=list(result.get("sources", [])),
        trace=list(result.get("trace", [])),
        generated_at=str(result.get("generated_at", "")),
    )
