"""Strategy CRUD, versioning and lifecycle transitions."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.api.deps import (
    CurrentUser,
    DbSession,
    assert_owner,
    record_audit,
    scope_to_user,
)
from aion.core.errors import AppError, ErrorCode
from aion.models.base import utcnow
from aion.models.trading import STRATEGY_STATES, BacktestRun, Strategy, StrategyVersion, RiskPolicy
from aion.models.research import StrategyApproval
from aion.schemas.common import AionModel, Money, ORMModel

router = APIRouter(prefix="/strategies", tags=["strategies"])

StrategyState = Literal[
    "RESEARCH",
    "BACKTEST",
    "PAPER",
    "SHADOW",
    "APPROVED",
    "ACTIVE",
    "SUSPENDED",
    "RETIRED",
]

# Allowed lifecycle edges. Anything not listed is a 409 conflict.
STRATEGY_TRANSITIONS: dict[str, frozenset[str]] = {
    "RESEARCH": frozenset({"BACKTEST", "RETIRED"}),
    "BACKTEST": frozenset({"PAPER", "RETIRED"}),
    "PAPER": frozenset({"SHADOW", "RETIRED"}),
    "SHADOW": frozenset({"APPROVED", "RETIRED"}),
    "APPROVED": frozenset({"ACTIVE", "RETIRED"}),
    "ACTIVE": frozenset({"SUSPENDED", "RETIRED"}),
    "SUSPENDED": frozenset({"ACTIVE", "RETIRED"}),
    "RETIRED": frozenset(),
}

INTERVAL_PATTERN = r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|3d|1w)$"


class StrategyOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    state: str
    symbols: list[str] = Field(default_factory=list)
    interval: str
    active_version_id: uuid.UUID | None = None
    created_by: str
    notes: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class StrategyCreate(AionModel):
    name: str = Field(min_length=3, max_length=96)
    description: str | None = Field(default=None, max_length=4000)
    symbols: list[str] = Field(min_length=1, max_length=20)
    interval: str = Field(default="15m", pattern=INTERVAL_PATTERN)
    notes: str | None = Field(default=None, max_length=4000)


class StrategyUpdate(AionModel):
    description: str | None = Field(default=None, max_length=4000)
    symbols: list[str] | None = Field(default=None, min_length=1, max_length=20)
    interval: str | None = Field(default=None, pattern=INTERVAL_PATTERN)
    notes: str | None = Field(default=None, max_length=4000)


class StrategyList(AionModel):
    items: list[StrategyOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class VersionCreate(AionModel):
    params: dict[str, Any] = Field(default_factory=dict)
    risk_assumptions: dict[str, Any] = Field(default_factory=dict)
    invalidation: str | None = Field(default=None, max_length=4000)
    change_note: str | None = Field(default=None, max_length=4000)
    code_ref: str | None = Field(default=None, max_length=128)


class VersionOut(ORMModel):
    id: uuid.UUID
    strategy_id: uuid.UUID
    version: int
    params: dict[str, Any] = Field(default_factory=dict)
    risk_assumptions: dict[str, Any] = Field(default_factory=dict)
    invalidation: str | None = None
    change_note: str | None = None
    code_ref: str | None = None
    is_approved: bool
    approved_at: dt.datetime | None = None
    approved_by: str | None = None
    created_at: dt.datetime


class StateTransitionIn(AionModel):
    state: StrategyState
    version_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class RollbackIn(AionModel):
    version_id: uuid.UUID
    note: str | None = Field(default=None, max_length=2000)


class BacktestOut(ORMModel):
    id: uuid.UUID
    strategy_version_id: uuid.UUID
    symbol: str
    interval: str
    start_at: dt.datetime
    end_at: dt.datetime
    sample: str
    status: str
    fee_bps: Money
    slippage_bps: Money
    metrics: dict[str, Any] = Field(default_factory=dict)
    trades_count: int
    error: str | None = None
    finished_at: dt.datetime | None = None
    created_at: dt.datetime


class BacktestList(AionModel):
    items: list[BacktestOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


async def _get_strategy(
    db: AsyncSession, strategy_id: uuid.UUID, user: CurrentUser
) -> Strategy:
    strategy = await db.get(Strategy, strategy_id)
    if strategy is None:
        raise AppError(ErrorCode.NOT_FOUND, "Strategy not found.")
    assert_owner(user, strategy.user_id, "Strategy not found.")
    return strategy


@router.get("", response_model=StrategyList)
async def list_strategies(
    db: DbSession,
    user: CurrentUser,
    state: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StrategyList:
    stmt = select(Strategy)
    count_stmt = select(func.count()).select_from(Strategy)
    if state:
        stmt = stmt.where(Strategy.state == state)
        count_stmt = count_stmt.where(Strategy.state == state)
    stmt = scope_to_user(stmt, Strategy.user_id, user)
    count_stmt = scope_to_user(count_stmt, Strategy.user_id, user)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(Strategy.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return StrategyList(
        items=[StrategyOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=StrategyOut, status_code=201)
async def create_strategy(
    payload: StrategyCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> StrategyOut:
    duplicate_stmt = scope_to_user(
        select(Strategy).where(Strategy.name == payload.name), Strategy.user_id, user
    )
    duplicate = (await db.execute(duplicate_stmt)).scalar_one_or_none()
    if duplicate is not None:
        raise AppError(ErrorCode.CONFLICT, "A strategy with this name already exists.")

    strategy = Strategy(
        name=payload.name,
        description=payload.description,
        state="RESEARCH",
        symbols=[symbol.upper() for symbol in payload.symbols],
        interval=payload.interval,
        created_by=user.username,
        notes=payload.notes,
        user_id=user.id,
    )
    db.add(strategy)
    await db.flush()
    await record_audit(
        db,
        actor=user.username,
        action="strategy.created",
        target=str(strategy.id),
        after={"name": strategy.name, "state": strategy.state},
        request=request,
    )
    await db.commit()
    return StrategyOut.model_validate(strategy)


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(
    strategy_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> StrategyOut:
    return StrategyOut.model_validate(await _get_strategy(db, strategy_id, user))


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> StrategyOut:
    strategy = await _get_strategy(db, strategy_id, user)
    changes = payload.model_dump(exclude_unset=True)
    for required_key in ("symbols", "interval"):
        if changes.get(required_key) is None:
            changes.pop(required_key, None)

    before = {key: getattr(strategy, key) for key in changes}
    for key, value in changes.items():
        if key == "symbols" and value is not None:
            value = [symbol.upper() for symbol in value]
        setattr(strategy, key, value)

    await record_audit(
        db,
        actor=user.username,
        action="strategy.updated",
        target=str(strategy.id),
        before=before,
        after=changes,
        request=request,
    )
    await db.commit()
    return StrategyOut.model_validate(strategy)


@router.post("/{strategy_id}/versions", response_model=VersionOut, status_code=201)
async def create_version(
    strategy_id: uuid.UUID,
    payload: VersionCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> VersionOut:
    strategy = await _get_strategy(db, strategy_id, user)
    latest = (
        await db.execute(
            select(func.max(StrategyVersion.version)).where(
                StrategyVersion.strategy_id == strategy_id
            )
        )
    ).scalar_one_or_none()
    next_version = int(latest or 0) + 1

    version = StrategyVersion(
        strategy_id=strategy.id,
        version=next_version,
        params=payload.params,
        risk_assumptions=payload.risk_assumptions,
        invalidation=payload.invalidation,
        change_note=payload.change_note,
        code_ref=payload.code_ref,
    )
    db.add(version)
    await db.flush()
    await record_audit(
        db,
        actor=user.username,
        action="strategy.version_created",
        target=str(strategy.id),
        after={"version": next_version},
        request=request,
    )
    await db.commit()
    return VersionOut.model_validate(version)


@router.post("/{strategy_id}/state", response_model=StrategyOut)
async def transition_state(
    strategy_id: uuid.UUID,
    payload: StateTransitionIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> StrategyOut:
    strategy = await _get_strategy(db, strategy_id, user)
    if payload.state not in STRATEGY_STATES:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Unknown strategy state.")

    if payload.state == strategy.state:
        raise AppError(ErrorCode.CONFLICT, f"Strategy is already in state {payload.state}.")

    allowed = STRATEGY_TRANSITIONS.get(strategy.state, frozenset())
    if payload.state not in allowed:
        raise AppError(
            ErrorCode.CONFLICT,
            f"Illegal transition {strategy.state} -> {payload.state}.",
            details={"allowed": sorted(allowed)},
        )

    before = {"state": strategy.state, "active_version_id": str(strategy.active_version_id)}

    if payload.state == "APPROVED" and payload.version_id is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Manual approval requires an explicit strategy version.")
    if payload.version_id is not None:
        version = await db.get(StrategyVersion, payload.version_id)
        if version is None or version.strategy_id != strategy.id:
            raise AppError(ErrorCode.NOT_FOUND, "Strategy version not found for this strategy.")
        strategy.active_version_id = version.id
        if payload.state == "APPROVED":
            if not version.is_approved:
                from aion.services.strategy_governance import is_independent_reviewer
                if not is_independent_reviewer(strategy.user_id, user.id, user.is_admin):
                    raise AppError(ErrorCode.FORBIDDEN, "Strategy approval requires an independent administrator reviewer.")
                policy = await db.scalar(select(RiskPolicy).where(
                    RiskPolicy.user_id == strategy.user_id, RiskPolicy.is_active.is_(True),
                ).order_by(RiskPolicy.updated_at.desc()).limit(1))
                if policy is None:
                    raise AppError(ErrorCode.CONFLICT, "An active user risk policy is required before approval.")
                from aion.services.strategy_governance import validate_strategy_approval
                risk_check = validate_strategy_approval(version.risk_assumptions or {}, policy)
                if not risk_check["passed"]:
                    raise AppError(
                        ErrorCode.CONFLICT,
                        "Strategy approval gates have not passed; provide verified OOS and PAPER evidence.",
                        details={"failures": risk_check["failures"]},
                    )
                version.is_approved = True
                version.approved_at = utcnow()
                version.approved_by = user.username
            else:
                risk_check = {"passed": True, "gate": "previously_approved_immutable_version"}
            db.add(StrategyApproval(
                user_id=strategy.user_id, strategy_version_id=version.id, decision="APPROVED",
                reviewer=user.username, note=payload.note,
                risk_check={**risk_check, "prior_state": strategy.state},
            ))
        elif payload.state == "ACTIVE" and not version.is_approved:
            raise AppError(
                ErrorCode.CONFLICT,
                "A strategy version must be manually approved before activation.",
            )

    if payload.state == "ACTIVE":
        active_version = await db.get(StrategyVersion, strategy.active_version_id) if strategy.active_version_id else None
        if active_version is None or active_version.strategy_id != strategy.id or not active_version.is_approved:
            raise AppError(
                ErrorCode.CONFLICT,
                "Activating a strategy requires a manually approved active version.",
            )

    strategy.state = payload.state
    await record_audit(
        db,
        actor=user.username,
        action="strategy.state_changed",
        target=str(strategy.id),
        before=before,
        after={
            "state": strategy.state,
            "active_version_id": str(strategy.active_version_id),
            "note": payload.note,
        },
        request=request,
    )
    await db.commit()
    return StrategyOut.model_validate(strategy)


@router.get("/{strategy_id}/versions", response_model=list[VersionOut])
async def list_versions(
    strategy_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> list[VersionOut]:
    await _get_strategy(db, strategy_id, user)
    rows = (
        await db.execute(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
            .order_by(StrategyVersion.version.desc())
        )
    ).scalars().all()
    return [VersionOut.model_validate(row) for row in rows]


@router.get("/{strategy_id}/backtests", response_model=BacktestList)
async def list_backtests(
    strategy_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BacktestList:
    await _get_strategy(db, strategy_id, user)
    version_ids = select(StrategyVersion.id).where(StrategyVersion.strategy_id == strategy_id)

    count_stmt = select(func.count()).select_from(BacktestRun).where(
        BacktestRun.strategy_version_id.in_(version_ids)
    )
    total = int((await db.execute(count_stmt)).scalar_one())
    stmt = (
        select(BacktestRun)
        .where(BacktestRun.strategy_version_id.in_(version_ids))
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return BacktestList(
        items=[BacktestOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{strategy_id}/rollback", response_model=StrategyOut)
async def rollback_strategy(
    strategy_id: uuid.UUID,
    payload: RollbackIn,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> StrategyOut:
    """Restore a previously approved version without changing its immutable record."""
    strategy = await _get_strategy(db, strategy_id, user)
    if payload.version_id is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "version_id is required.")
    version = await db.get(StrategyVersion, payload.version_id)
    if version is None or version.strategy_id != strategy.id or not version.is_approved:
        raise AppError(ErrorCode.CONFLICT, "Rollback target must be an approved version of this strategy.")
    before = {"active_version_id": str(strategy.active_version_id)}
    strategy.active_version_id = version.id
    if strategy.state == "SUSPENDED":
        strategy.state = "ACTIVE"
    await record_audit(
        db, actor=user.username, action="strategy.version_rollback",
        target=str(strategy.id), before=before,
        after={"active_version_id": str(version.id), "note": payload.note},
        request=request,
    )
    await db.commit()
    return StrategyOut.model_validate(strategy)
