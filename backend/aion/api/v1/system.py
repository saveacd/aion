"""System health, status, event feed and emergency controls."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aion import __version__
from aion.api.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    SettingsDep,
    record_audit,
    record_system_event,
)
from aion.core.config import TradingMode
from aion.core.db import get_engine
from aion.core.redis import ping as redis_ping
from aion.models.account import SystemEvent, TradingControlState
from aion.models.auth import ExchangeAccount
from aion.models.base import utcnow
from aion.schemas.common import AionModel, HealthResponse, ORMModel
from aion.services.events import publish_event

router = APIRouter(prefix="/system", tags=["system"])

ControlActionName = Literal["STOP_NEW_TRADES", "PAUSE_AI", "EMERGENCY_STOP"]


class ExchangeStatus(AionModel):
    mode: str
    credentials_configured: bool
    has_api_key: bool
    has_api_secret: bool
    account_row_active: bool
    permissions_checked: bool


class ControlStateOut(ORMModel):
    stop_new_trades: bool
    pause_ai: bool
    emergency_stop: bool
    emergency_reason: str | None = None
    updated_by: str | None = None
    engaged_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


class SystemStatus(AionModel):
    app_name: str
    version: str
    environment: str
    debug: bool
    trading_mode: str
    mainnet_unlocked: bool
    database_ok: bool
    redis_ok: bool
    exchange: list[ExchangeStatus] = Field(default_factory=list)
    controls: ControlStateOut | None = None
    checked_at: dt.datetime


class SystemEventOut(ORMModel):
    id: uuid.UUID
    category: str
    level: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime


class SystemEventList(AionModel):
    items: list[SystemEventOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class ControlRequest(AionModel):
    action: ControlActionName
    enabled: bool = True
    reason: str | None = Field(default=None, max_length=500)


async def _database_ok() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health checks must never raise
        return False
    return True


async def _load_controls(db: AsyncSession) -> TradingControlState | None:
    return await db.get(TradingControlState, 1)


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep, user: CurrentUser) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.environment.value,
        trading_mode=settings.resolve_trading_mode().value,
        mainnet_unlocked=settings.mainnet_enabled,
        database=await _database_ok(),
        redis=await redis_ping(),
        checked_at=utcnow(),
    )


@router.get("/status", response_model=SystemStatus)
async def status(settings: SettingsDep, db: DbSession, user: CurrentUser) -> SystemStatus:
    stmt = select(ExchangeAccount).where(ExchangeAccount.is_active.is_(True))
    if not user.is_admin:
        stmt = stmt.where(ExchangeAccount.user_id == user.id)
    rows = (await db.execute(stmt)).scalars().all()
    by_mode = {row.mode: row for row in rows}

    exchange: list[ExchangeStatus] = []
    for mode in (TradingMode.TESTNET, TradingMode.MAINNET):
        api_key, api_secret = settings.binance_credentials(mode)
        row = by_mode.get(mode.value)
        exchange.append(
            ExchangeStatus(
                mode=mode.value,
                credentials_configured=bool(api_key and api_secret),
                has_api_key=bool(api_key) or bool(row and row.api_key_enc),
                has_api_secret=bool(api_secret) or bool(row and row.api_secret_enc),
                account_row_active=row is not None,
                permissions_checked=bool(row and row.permissions_checked),
            )
        )

    controls = await _load_controls(db)
    return SystemStatus(
        app_name=settings.app_name,
        version=__version__,
        environment=settings.environment.value,
        debug=settings.debug,
        trading_mode=settings.resolve_trading_mode().value,
        mainnet_unlocked=settings.mainnet_enabled,
        database_ok=await _database_ok(),
        redis_ok=await redis_ping(),
        exchange=exchange,
        controls=ControlStateOut.model_validate(controls) if controls is not None else None,
        checked_at=utcnow(),
    )


@router.get("/events", response_model=SystemEventList)
async def list_events(
    db: DbSession,
    user: CurrentUser,
    category: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SystemEventList:
    stmt = select(SystemEvent)
    count_stmt = select(func.count()).select_from(SystemEvent)
    if category:
        stmt = stmt.where(SystemEvent.category == category)
        count_stmt = count_stmt.where(SystemEvent.category == category)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(
            stmt.order_by(SystemEvent.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return SystemEventList(
        items=[SystemEventOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/controls", response_model=ControlStateOut)
async def set_control(
    payload: ControlRequest,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> ControlStateOut:
    state = await _load_controls(db)
    if state is None:
        state = TradingControlState(id=1)
        db.add(state)

    before = {
        "stop_new_trades": bool(state.stop_new_trades),
        "pause_ai": bool(state.pause_ai),
        "emergency_stop": bool(state.emergency_stop),
    }

    now = utcnow()
    if payload.action == "STOP_NEW_TRADES":
        state.stop_new_trades = payload.enabled
    elif payload.action == "PAUSE_AI":
        state.pause_ai = payload.enabled
    else:
        state.emergency_stop = payload.enabled
        state.engaged_at = now if payload.enabled else None
        state.emergency_reason = payload.reason if payload.enabled else None
        if payload.enabled:
            # An emergency halt always blocks new exposure as well.
            state.stop_new_trades = True

    state.updated_by = admin.username

    after = {
        "stop_new_trades": state.stop_new_trades,
        "pause_ai": state.pause_ai,
        "emergency_stop": state.emergency_stop,
        "emergency_reason": state.emergency_reason,
    }

    await record_audit(
        db,
        actor=admin.username,
        action=f"system.control.{payload.action.lower()}",
        target="trading_control_state",
        before=before,
        after=after,
        request=request,
    )
    record_system_event(
        db,
        category="controls",
        level="WARNING" if payload.action == "EMERGENCY_STOP" and payload.enabled else "INFO",
        message=(
            f"{payload.action} {'engaged' if payload.enabled else 'released'}"
            + (f": {payload.reason}" if payload.reason else "")
        ),
        data={"action": payload.action, "enabled": payload.enabled, "by": admin.username},
    )
    await db.commit()

    await publish_event(
        "system.control",
        {"action": payload.action, "enabled": payload.enabled, "reason": payload.reason},
    )
    return ControlStateOut.model_validate(state)
