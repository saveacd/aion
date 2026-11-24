"""Learning endpoints: performance report and lesson management."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import select

from aion.api.deps import CurrentUser, DbSession, assert_owner
from aion.core.errors import AppError, ErrorCode
from aion.models import LearningLesson
from aion.schemas.common import AionModel
from aion.services.learning import performance_report

router = APIRouter(prefix="/learning", tags=["learning"])


class LessonOut(AionModel):
    id: uuid.UUID
    lesson: str
    category: str
    tags: list[str]
    score: Decimal
    source: str
    outcomes_count: int
    active: bool
    created_at: dt.datetime


class LessonList(AionModel):
    items: list[LessonOut] = Field(default_factory=list)


class PerformanceReport(AionModel):
    total_trades: int
    wins: int
    win_rate: float | None
    avg_r_multiple: str | None
    total_pnl_usd: str
    today_pnl_usd: str
    by_symbol: dict[str, Any]
    by_exit_reason: dict[str, int]
    equity_curve: list[dict[str, str]]
    recent_outcomes: list[dict[str, Any]]


@router.get("/lessons", response_model=LessonList)
async def list_lessons(
    user: CurrentUser,
    db: DbSession,
    active_only: bool = Query(default=False),
):
    stmt = (
        select(LearningLesson)
        .where(LearningLesson.user_id == user.id)
        .order_by(LearningLesson.created_at.desc())
        .limit(100)
    )
    if active_only:
        stmt = stmt.where(LearningLesson.active.is_(True))
    lessons = (await db.execute(stmt)).scalars().all()
    return LessonList(items=[LessonOut.model_validate(lesson) for lesson in lessons])


@router.post("/lessons/{lesson_id}/toggle", response_model=LessonOut)
async def toggle_lesson(lesson_id: uuid.UUID, user: CurrentUser, db: DbSession):
    lesson = await db.get(LearningLesson, lesson_id)
    if lesson is None:
        raise AppError(ErrorCode.NOT_FOUND, "经验不存在。")
    assert_owner(user, lesson.user_id, "经验不存在。")
    lesson.active = not lesson.active
    await db.commit()
    return LessonOut.model_validate(lesson)


@router.get("/performance", response_model=PerformanceReport)
async def get_performance(user: CurrentUser, db: DbSession):
    report = await performance_report(db, user.id)
    return PerformanceReport(**report)


__all__ = ["router"]
