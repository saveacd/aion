"""Risk policy, decisions and events.

Each user owns their limits. The AI has no write path here, and the server
enforces absolute caps no request can exceed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.api.deps import CurrentUser, DbSession, record_audit, scope_to_user
from aion.core.errors import AppError, ErrorCode
from aion.models.account import RiskEvent
from aion.models.trading import RiskDecision, RiskPolicy
from aion.schemas.common import AionModel, Money, ORMModel
from aion.schemas.trading import RiskDecisionOut

router = APIRouter(prefix="/risk", tags=["risk"])


class RiskPolicyIn(AionModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    max_leverage: int | None = Field(default=None, ge=1)
    max_position_notional_usd: Money | None = Field(default=None, gt=0)
    max_total_exposure_usd: Money | None = Field(default=None, gt=0)
    max_single_trade_loss_usd: Money | None = Field(default=None, gt=0)
    daily_loss_limit_usd: Money | None = Field(default=None, gt=0)
    max_drawdown_pct: Money | None = Field(default=None, ge=0, le=100)
    max_consecutive_losses: int | None = Field(default=None, ge=0, le=100)
    max_open_positions: int | None = Field(default=None, ge=1, le=100)
    max_directional_concentration_pct: Money | None = Field(default=None, ge=0, le=100)
    min_margin_ratio: Money | None = Field(default=None, ge=0, le=1)
    required_stop_loss: bool | None = None
    max_spread_bps: Money | None = Field(default=None, ge=0, le=1000)
    min_confidence: Money | None = Field(default=None, ge=0, le=1)


class RiskPolicyOut(ORMModel):
    id: int
    name: str
    is_active: bool
    max_leverage: int
    max_position_notional_usd: Money
    max_total_exposure_usd: Money
    max_single_trade_loss_usd: Money
    daily_loss_limit_usd: Money
    max_drawdown_pct: Money
    max_consecutive_losses: int
    max_open_positions: int
    max_directional_concentration_pct: Money
    min_margin_ratio: Money
    required_stop_loss: bool
    max_spread_bps: Money
    min_confidence: Money
    created_at: dt.datetime
    updated_at: dt.datetime


class PolicyResponse(AionModel):
    has_data: bool
    policy: RiskPolicyOut | None = None
    absolute_limits: dict[str, Any] = Field(default_factory=dict)


class RiskDecisionList(AionModel):
    items: list[RiskDecisionOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class RiskEventOut(ORMModel):
    id: uuid.UUID
    severity: str
    code: str
    message: str
    symbol: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    acknowledged_at: dt.datetime | None = None
    created_at: dt.datetime


class RiskEventList(AionModel):
    items: list[RiskEventOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


def _absolute_limits() -> dict[str, Any]:
    return {
        "max_leverage": RiskPolicy.ABSOLUTE_MAX_LEVERAGE,
        "max_position_notional_usd": format(RiskPolicy.ABSOLUTE_MAX_NOTIONAL, "f"),
        "daily_loss_limit_usd": format(RiskPolicy.ABSOLUTE_DAILY_LOSS, "f"),
    }


POLICY_FIELDS = (
    "max_leverage",
    "max_position_notional_usd",
    "max_total_exposure_usd",
    "max_single_trade_loss_usd",
    "daily_loss_limit_usd",
    "max_drawdown_pct",
    "max_consecutive_losses",
    "max_open_positions",
    "max_directional_concentration_pct",
    "min_margin_ratio",
    "required_stop_loss",
    "max_spread_bps",
    "min_confidence",
)


async def _user_policy(db: AsyncSession, user_id: uuid.UUID) -> RiskPolicy | None:
    stmt = (
        select(RiskPolicy)
        .where(RiskPolicy.is_active.is_(True), RiskPolicy.user_id == user_id)
        .order_by(RiskPolicy.updated_at.desc(), RiskPolicy.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _effective_policy(db: AsyncSession, user_id: uuid.UUID) -> RiskPolicy | None:
    own = await _user_policy(db, user_id)
    if own is not None:
        return own
    stmt = (
        select(RiskPolicy)
        .where(RiskPolicy.is_active.is_(True), RiskPolicy.user_id.is_(None))
        .order_by(RiskPolicy.updated_at.desc(), RiskPolicy.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("/policy", response_model=PolicyResponse)
async def get_policy(db: DbSession, user: CurrentUser) -> PolicyResponse:
    policy = await _effective_policy(db, user.id)
    return PolicyResponse(
        has_data=policy is not None,
        policy=RiskPolicyOut.model_validate(policy) if policy is not None else None,
        absolute_limits=_absolute_limits(),
    )


@router.put("/policy", response_model=PolicyResponse)
async def update_policy(
    payload: RiskPolicyIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> PolicyResponse:
    policy = await _user_policy(db, user.id)
    if policy is None:
        policy = RiskPolicy(name=payload.name or "我的风控", is_active=True, user_id=user.id)
        base = await _effective_policy(db, user.id)
        if base is not None:
            for field in POLICY_FIELDS:
                setattr(policy, field, getattr(base, field))
        db.add(policy)

    changes = payload.model_dump(exclude_unset=True, exclude_none=True)

    violations: dict[str, Any] = {}
    leverage = changes.get("max_leverage", policy.max_leverage)
    if leverage is not None and leverage > RiskPolicy.ABSOLUTE_MAX_LEVERAGE:
        violations["max_leverage"] = {
            "requested": leverage,
            "absolute_max": RiskPolicy.ABSOLUTE_MAX_LEVERAGE,
        }
    notional = changes.get("max_position_notional_usd", policy.max_position_notional_usd)
    if notional is not None and notional > RiskPolicy.ABSOLUTE_MAX_NOTIONAL:
        violations["max_position_notional_usd"] = {
            "requested": format(notional, "f"),
            "absolute_max": format(RiskPolicy.ABSOLUTE_MAX_NOTIONAL, "f"),
        }
    daily_loss = changes.get("daily_loss_limit_usd", policy.daily_loss_limit_usd)
    if daily_loss is not None and daily_loss > RiskPolicy.ABSOLUTE_DAILY_LOSS:
        violations["daily_loss_limit_usd"] = {
            "requested": format(daily_loss, "f"),
            "absolute_max": format(RiskPolicy.ABSOLUTE_DAILY_LOSS, "f"),
        }

    if violations:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Requested limits exceed the server-side absolute safety caps.",
            details={"violations": violations, "absolute_limits": _absolute_limits()},
        )

    before = {
        "max_leverage": policy.max_leverage,
        "max_position_notional_usd": str(policy.max_position_notional_usd),
        "max_total_exposure_usd": str(policy.max_total_exposure_usd),
        "daily_loss_limit_usd": str(policy.daily_loss_limit_usd),
    }
    for key, value in changes.items():
        setattr(policy, key, value)

    await record_audit(
        db,
        actor=user.username,
        action="risk.policy_updated",
        target=str(policy.id) if policy.id else "risk_policy",
        before=before,
        after={key: str(value) for key, value in changes.items()},
        request=request,
    )
    await db.commit()
    await db.refresh(policy)
    return PolicyResponse(
        has_data=True,
        policy=RiskPolicyOut.model_validate(policy),
        absolute_limits=_absolute_limits(),
    )


@router.get("/decisions", response_model=RiskDecisionList)
async def list_decisions(
    db: DbSession,
    user: CurrentUser,
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    approved: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RiskDecisionList:
    stmt = select(RiskDecision)
    count_stmt = select(func.count()).select_from(RiskDecision)
    if symbol:
        stmt = stmt.where(RiskDecision.symbol == symbol.upper())
        count_stmt = count_stmt.where(RiskDecision.symbol == symbol.upper())
    if approved is not None:
        stmt = stmt.where(RiskDecision.approved.is_(approved))
        count_stmt = count_stmt.where(RiskDecision.approved.is_(approved))
    stmt = scope_to_user(stmt, RiskDecision.user_id, user)
    count_stmt = scope_to_user(count_stmt, RiskDecision.user_id, user)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(RiskDecision.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return RiskDecisionList(
        items=[RiskDecisionOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/events", response_model=RiskEventList)
async def list_events(
    db: DbSession,
    user: CurrentUser,
    severity: Annotated[str | None, Query(max_length=16)] = None,
    code: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RiskEventList:
    stmt = select(RiskEvent)
    count_stmt = select(func.count()).select_from(RiskEvent)
    if severity:
        stmt = stmt.where(RiskEvent.severity == severity.upper())
        count_stmt = count_stmt.where(RiskEvent.severity == severity.upper())
    if code:
        stmt = stmt.where(RiskEvent.code == code)
        count_stmt = count_stmt.where(RiskEvent.code == code)
    stmt = scope_to_user(stmt, RiskEvent.user_id, user)
    count_stmt = scope_to_user(count_stmt, RiskEvent.user_id, user)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(RiskEvent.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return RiskEventList(
        items=[RiskEventOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
