"""站内通知中心。"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import or_, select, update

from aion.api.deps import CurrentUser, DbSession
from aion.core.errors import AppError, ErrorCode
from aion.models import Notification
from aion.models.base import utcnow
from aion.schemas.common import AionModel

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(AionModel):
    id: uuid.UUID
    kind: str
    level: str
    title: str
    body: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    read_at: dt.datetime | None = None
    created_at: dt.datetime


class NotificationList(AionModel):
    items: list[NotificationOut] = Field(default_factory=list)
    total: int = 0
    unread: int = 0
    limit: int
    offset: int


@router.get("", response_model=NotificationList)
async def list_notifications(
    user: CurrentUser,
    db: DbSession,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationList:
    scope = or_(Notification.user_id == user.id, Notification.user_id.is_(None))
    stmt = select(Notification).where(scope)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (
        await db.execute(stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    all_rows = (
        await db.execute(select(Notification.read_at).where(scope))
    ).scalars().all()
    return NotificationList(
        items=[NotificationOut.model_validate(row, from_attributes=True) for row in rows],
        total=len(all_rows),
        unread=sum(1 for read_at in all_rows if read_at is None),
        limit=limit,
        offset=offset,
    )


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> NotificationOut:
    row = await db.get(Notification, notification_id)
    if row is None or row.user_id not in (None, user.id):
        raise AppError(ErrorCode.NOT_FOUND, "通知不存在。")
    if row.read_at is None:
        row.read_at = utcnow()
        await db.commit()
    return NotificationOut.model_validate(row, from_attributes=True)


@router.post("/read-all")
async def mark_all_read(user: CurrentUser, db: DbSession) -> dict[str, int]:
    result = await db.execute(
        update(Notification)
        .where(
            or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    await db.commit()
    return {"updated": int(result.rowcount or 0)}


__all__ = ["router"]
