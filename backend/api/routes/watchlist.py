from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes.auth import get_current_user
from db.models.models import AssetType, User, WatchlistItem
from db.session import get_db

log = structlog.get_logger()
router: APIRouter = APIRouter()


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    asset_type: str = Field(default="stock", pattern="^(stock|crypto)$")


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    asset_type: str
    notes: str | None
    created_at: datetime


def _to_response(item: WatchlistItem) -> WatchlistItemResponse:
    return WatchlistItemResponse(
        id=item.id,
        ticker=item.ticker,
        asset_type=item.asset_type.value,
        notes=item.notes,
        created_at=item.created_at,
    )


@router.get("", response_model=list[WatchlistItemResponse])
async def list_watchlist_items(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[WatchlistItemResponse]:
    statement: Select[tuple[WatchlistItem]] = (
        select(WatchlistItem)
        .where(WatchlistItem.user_id == current_user.id)
        .order_by(WatchlistItem.created_at.desc())
    )

    try:
        items: list[WatchlistItem] = list((await session.scalars(statement)).all())
    except Exception:
        log.exception("failed to list watchlist items", user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list watchlist items",
        )

    return [_to_response(item) for item in items]


@router.post("", response_model=WatchlistItemResponse, status_code=status.HTTP_201_CREATED)
async def add_watchlist_item(
    payload: WatchlistAddRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> WatchlistItemResponse:
    ticker: str = payload.ticker.upper().strip()
    asset_type: AssetType = AssetType(payload.asset_type)

    duplicate_check: Select[tuple[WatchlistItem]] = select(WatchlistItem).where(
        WatchlistItem.user_id == current_user.id,
        WatchlistItem.ticker == ticker,
        WatchlistItem.asset_type == asset_type,
    )

    try:
        existing_item: WatchlistItem | None = await session.scalar(duplicate_check)
    except Exception:
        log.exception(
            "failed to check duplicate watchlist item",
            user_id=current_user.id,
            ticker=ticker,
            asset_type=asset_type.value,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add watchlist item",
        )

    if existing_item is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ticker already in watchlist",
        )

    item = WatchlistItem(
        user_id=current_user.id,
        ticker=ticker,
        asset_type=asset_type,
    )
    session.add(item)

    try:
        await session.flush()
        await session.refresh(item)
    except Exception:
        log.exception(
            "failed to create watchlist item",
            user_id=current_user.id,
            ticker=ticker,
            asset_type=asset_type.value,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add watchlist item",
        )

    log.info(
        "watchlist item created",
        user_id=current_user.id,
        item_id=item.id,
        ticker=item.ticker,
        asset_type=item.asset_type.value,
    )
    return _to_response(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    statement: Select[tuple[WatchlistItem]] = select(WatchlistItem).where(
        WatchlistItem.id == item_id,
        WatchlistItem.user_id == current_user.id,
    )

    try:
        item: WatchlistItem | None = await session.scalar(statement)
    except Exception:
        log.exception(
            "failed to query watchlist item for deletion",
            user_id=current_user.id,
            item_id=item_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete watchlist item",
        )

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist item not found",
        )

    try:
        await session.delete(item)
    except Exception:
        log.exception(
            "failed to delete watchlist item",
            user_id=current_user.id,
            item_id=item_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete watchlist item",
        )

    log.info("watchlist item deleted", user_id=current_user.id, item_id=item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
