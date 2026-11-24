"""站内通知：写入数据库并通过事件总线推送。

通知失败绝不能影响交易主流程，因此这里的所有写入都是"尽力而为"。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.models import Notification
from aion.services.events import publish_event


def add_notification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    title: str,
    body: str | None = None,
    kind: str = "system",
    level: str = "INFO",
    data: dict[str, Any] | None = None,
) -> Notification:
    """Add a notification row to the caller's transaction (no commit)."""
    row = Notification(
        user_id=user_id,
        kind=kind[:32],
        level=level[:16],
        title=title[:160],
        body=body,
        data=data or {},
    )
    db.add(row)
    return row


async def notify(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID | None,
    title: str,
    body: str | None = None,
    kind: str = "system",
    level: str = "INFO",
    data: dict[str, Any] | None = None,
) -> None:
    """Persist and broadcast one notification. Never raises."""
    try:
        async with session_factory() as db:
            row = add_notification(
                db,
                user_id=user_id,
                title=title,
                body=body,
                kind=kind,
                level=level,
                data=data,
            )
            await db.commit()
            payload = {
                "id": str(row.id),
                "title": row.title,
                "body": row.body,
                "kind": row.kind,
                "level": row.level,
                "data": row.data,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        await publish_event("notification", payload)
    except Exception:  # noqa: BLE001 - notifications are best-effort
        import logging

        logging.getLogger(__name__).exception("failed to write notification")


__all__ = ["add_notification", "notify"]
