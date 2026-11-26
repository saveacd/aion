"""Autopilot config & status: user-controlled fully-automatic AI trading.

Enabling ``auto_execute`` is a two-step operation: the client must send
``confirm_auto_execute=true`` together with ``auto_execute=true``. The account
can be PAPER (default), TESTNET or MAINNET. Real modes require a stored Binance
key, and MAINNET additionally requires the server-wide unlock plus user TOTP.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import func, select

from aion.api.deps import CurrentUser, DbSession, SettingsDep, record_audit
from aion.core.crypto import SecretBox
from aion.core.errors import AppError, ErrorCode
from aion.core.security import verify_totp
from aion.models import (
    AgentRun,
    AutopilotConfig,
    LivePosition,
    PaperPosition,
    Strategy,
    TradeOutcome,
)
from aion.models.base import utcnow
from aion.schemas.common import AionModel, Money
from aion.services.live_trading import close_live_position, resolve_live_client

router = APIRouter(prefix="/autopilot", tags=["autopilot"])

INTERVAL_PATTERN = r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$"
MAX_SYMBOLS = 10
MAX_CYCLE_SECONDS = 86400
MIN_CYCLE_SECONDS = 60
EXECUTION_MODES = ("PAPER", "TESTNET", "MAINNET")


class WatchPlanOut(AionModel):
    """AI 等待区：价格进入区间后会自动重新分析并考虑入场。"""

    symbol: str
    side: str
    entry_low: Money
    entry_high: Money
    stop_loss: Money | None = None
    take_profit: Money | None = None
    leverage: int
    confidence: Money
    reason: str
    created_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None


class AutopilotConfigOut(AionModel):
    enabled: bool
    auto_execute: bool
    execution_mode: str
    symbols: list[str]
    watch_plans: list[WatchPlanOut] = Field(default_factory=list)
    interval: str
    intraday: bool
    max_holding_minutes: int
    live_authorized_at: dt.datetime | None = None
    cycle_seconds: int
    max_trades_per_day: int
    min_confidence: Money
    strategy_id: uuid.UUID | None
    last_run_at: dt.datetime | None
    last_error: str | None
    trades_today: int
    trading_mode: str


class AutopilotUpdate(AionModel):
    enabled: bool | None = None
    auto_execute: bool | None = None
    confirm_auto_execute: bool = False
    execution_mode: Literal["PAPER", "TESTNET", "MAINNET"] | None = None
    # Fresh TOTP code required when arming MAINNET auto-trading (follow-up
    # audit #8): merely having TOTP configured is not second-factor proof.
    totp_code: str | None = Field(default=None, max_length=16)
    symbols: list[str] | None = Field(default=None, min_length=1, max_length=MAX_SYMBOLS)
    interval: str | None = Field(default=None, pattern=INTERVAL_PATTERN)
    intraday: bool | None = None
    max_holding_minutes: int | None = Field(default=None, ge=15, le=1440)
    cycle_seconds: int | None = Field(
        default=None, ge=MIN_CYCLE_SECONDS, le=MAX_CYCLE_SECONDS
    )
    max_trades_per_day: int | None = Field(default=None, ge=0, le=50)
    min_confidence: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    strategy_id: uuid.UUID | None = None


class PaperPositionOut(AionModel):
    id: uuid.UUID
    symbol: str
    position_side: str
    quantity: Money
    entry_price: Money
    stop_loss: Money | None
    take_profit: Money | None
    leverage: int
    opened_at: dt.datetime


class LivePositionOut(AionModel):
    id: uuid.UUID
    mode: str
    symbol: str
    position_side: str
    quantity: Money
    entry_price: Money
    stop_loss: Money | None
    take_profit: Money | None
    leverage: int
    status: str
    protection_status: str
    opened_at: dt.datetime


class AutoRunOut(AionModel):
    id: uuid.UUID
    symbol: str
    verdict: str | None
    confidence: Money | None
    summary: str | None
    started_at: dt.datetime


class AutopilotStatus(AionModel):
    config: AutopilotConfigOut
    open_positions: list[PaperPositionOut]
    live_positions: list[LivePositionOut]
    today_realized_pnl_usd: Money
    total_trades: int
    recent_runs: list[AutoRunOut]


def _out(db_cfg: AutopilotConfig) -> AutopilotConfigOut:
    mode = db_cfg.execution_mode or "PAPER"
    return AutopilotConfigOut(
        enabled=db_cfg.enabled,
        auto_execute=db_cfg.auto_execute,
        execution_mode=mode,
        symbols=[str(s) for s in (db_cfg.symbols or [])],
        watch_plans=[
            WatchPlanOut.model_validate(p) for p in (db_cfg.watch_plans or [])
        ],
        interval=db_cfg.interval,
        intraday=db_cfg.intraday,
        max_holding_minutes=db_cfg.max_holding_minutes,
        live_authorized_at=db_cfg.live_authorized_at,
        cycle_seconds=db_cfg.cycle_seconds,
        max_trades_per_day=db_cfg.max_trades_per_day,
        min_confidence=db_cfg.min_confidence,
        strategy_id=db_cfg.strategy_id,
        last_run_at=db_cfg.last_run_at,
        last_error=db_cfg.last_error,
        trades_today=db_cfg.trades_today,
        trading_mode=mode,
    )


async def _get_or_create(db, user_id: uuid.UUID) -> AutopilotConfig:
    cfg = await db.get(AutopilotConfig, user_id)
    if cfg is None:
        cfg = AutopilotConfig(user_id=user_id, symbols=[])
        db.add(cfg)
        await db.flush()
    return cfg


@router.get("", response_model=AutopilotConfigOut)
async def get_config(user: CurrentUser, db: DbSession, settings: SettingsDep):
    cfg = await _get_or_create(db, user.id)
    await db.commit()
    return _out(cfg)


@router.put("", response_model=AutopilotConfigOut)
async def update_config(
    payload: AutopilotUpdate, user: CurrentUser, db: DbSession, settings: SettingsDep,
    request: Request,
):
    cfg = await _get_or_create(db, user.id)
    before = {
        "enabled": cfg.enabled,
        "auto_execute": cfg.auto_execute,
        "execution_mode": cfg.execution_mode,
        "live_authorized": cfg.live_authorized_at is not None,
    }
    target_mode = payload.execution_mode or cfg.execution_mode or "PAPER"
    target_enabled = payload.enabled if payload.enabled is not None else cfg.enabled
    target_auto = target_enabled and (
        payload.auto_execute if payload.auto_execute is not None else cfg.auto_execute
    )
    mode_changed = target_mode != cfg.execution_mode
    needs_live_authorization = (
        target_mode != "PAPER" and target_auto
        and (mode_changed or not cfg.auto_execute or cfg.live_authorized_at is None)
    )
    if target_auto and (not cfg.auto_execute or needs_live_authorization) and not payload.confirm_auto_execute:
        raise AppError(ErrorCode.VALIDATION_ERROR, "开启或切换自动执行必须明确确认。")

    if target_mode != "PAPER" and (mode_changed or needs_live_authorization or payload.enabled is True):
        if target_mode == "MAINNET" and not settings.mainnet_enabled:
            raise AppError(ErrorCode.CONFLICT, "主网交易未在服务器解锁。")
        client = await resolve_live_client(db, settings, user.id, target_mode, allow_operator_fallback=False)
        if client is None:
            raise AppError(ErrorCode.CONFLICT, f"未找到可用的 Binance {target_mode} API Key。")
        await client.aclose()

    if needs_live_authorization and target_mode == "MAINNET" and settings.require_totp_for_mainnet:
        secret = (
            SecretBox(settings).try_decrypt(user.totp_secret_enc)
            if user.totp_enabled and user.totp_secret_enc else ""
        )
        if not secret or not payload.totp_code or not verify_totp(secret, payload.totp_code):
            raise AppError(ErrorCode.CONFLICT, "首次授权主网持续托管需要有效的两步验证码。")

    if payload.strategy_id is not None:
        strategy = await db.get(Strategy, payload.strategy_id)
        if strategy is None or (strategy.user_id not in (None, user.id)):
            raise AppError(ErrorCode.NOT_FOUND, "策略不存在或不属于当前用户。")
        if strategy.state in ("RETIRED", "SUSPENDED"):
            raise AppError(ErrorCode.CONFLICT, f"策略状态为 {strategy.state}，不可用于自动代理。")

    if payload.enabled is not None:
        cfg.enabled = payload.enabled
    if payload.execution_mode is not None:
        cfg.execution_mode = target_mode
    cfg.auto_execute = target_auto
    if not target_auto or target_mode == "PAPER":
        cfg.live_authorized_at = None
    elif needs_live_authorization:
        cfg.live_authorized_at = utcnow()
    if mode_changed:
        cfg.watch_plans = []
        cfg.last_run_at = None
    if payload.symbols is not None:
        cfg.symbols = [s.strip().upper() for s in payload.symbols if s.strip()][:MAX_SYMBOLS]
    if payload.interval is not None:
        cfg.interval = payload.interval
    if payload.intraday is not None:
        cfg.intraday = payload.intraday
    if payload.max_holding_minutes is not None:
        cfg.max_holding_minutes = payload.max_holding_minutes
    if payload.cycle_seconds is not None:
        cfg.cycle_seconds = payload.cycle_seconds
    if payload.max_trades_per_day is not None:
        cfg.max_trades_per_day = payload.max_trades_per_day
    if payload.min_confidence is not None:
        cfg.min_confidence = payload.min_confidence
    if payload.strategy_id is not None or "strategy_id" in payload.model_fields_set:
        cfg.strategy_id = payload.strategy_id

    await record_audit(
        db,
        actor=user.username,
        action="autopilot.update",
        target=str(user.id),
        before=before,
        after={
            "enabled": cfg.enabled,
            "auto_execute": cfg.auto_execute,
            "execution_mode": cfg.execution_mode,
            "live_authorized": cfg.live_authorized_at is not None,
        },
        request=request,
    )
    await db.commit()
    return _out(cfg)


@router.get("/status", response_model=AutopilotStatus)
async def get_status(user: CurrentUser, db: DbSession, settings: SettingsDep):
    cfg = await _get_or_create(db, user.id)
    positions = (
        await db.execute(
            select(PaperPosition).where(
                PaperPosition.user_id == user.id, PaperPosition.status == "OPEN"
            )
        )
    ).scalars().all()
    live_positions = (
        await db.execute(
            select(LivePosition).where(
                LivePosition.user_id == user.id, LivePosition.status == "OPEN"
            )
        )
    ).scalars().all()
    today = utcnow().date()
    today_pnl = await db.scalar(
        select(func.coalesce(func.sum(TradeOutcome.pnl_usd), 0)).where(
            TradeOutcome.user_id == user.id,
            TradeOutcome.closed_at >= dt.datetime.combine(today, dt.time.min, tzinfo=dt.UTC),
        )
    )
    total_trades = await db.scalar(
        select(func.count()).select_from(TradeOutcome).where(TradeOutcome.user_id == user.id)
    )
    runs = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.user_id == user.id, AgentRun.trigger == "autopilot")
            .order_by(AgentRun.started_at.desc())
            .limit(5)
        )
    ).scalars().all()
    return AutopilotStatus(
        config=_out(cfg),
        open_positions=[
            PaperPositionOut.model_validate(p, from_attributes=True) for p in positions
        ],
        live_positions=[
            LivePositionOut.model_validate(p, from_attributes=True) for p in live_positions
        ],
        today_realized_pnl_usd=today_pnl or Decimal("0"),
        total_trades=int(total_trades or 0),
        recent_runs=[
            AutoRunOut(
                id=r.id, symbol=r.symbol, verdict=r.verdict,
                confidence=r.confidence, summary=r.summary, started_at=r.started_at,
            )
            for r in runs
        ],
    )


@router.post("/positions/{position_id}/close")
async def close_position(
    position_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
    request: Request,
) -> dict[str, object]:
    """Close one live position immediately at market."""
    position = await db.get(LivePosition, position_id)
    if position is None or position.user_id != user.id:
        raise AppError(ErrorCode.NOT_FOUND, "持仓不存在。")
    if position.status != "OPEN":
        raise AppError(ErrorCode.CONFLICT, "该持仓已平仓。")
    mode = position.mode
    await db.commit()

    from aion.core.db import get_sessionmaker

    closed = await close_live_position(
        get_sessionmaker(), settings, position_id, reason="MANUAL", force=True
    )
    await record_audit(
        db,
        actor=user.username,
        action="autopilot.position.close",
        target=str(position_id),
        after={"symbol": position.symbol, "mode": mode, "closed": closed},
        request=request,
    )
    await db.commit()
    if not closed:
        raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "平仓未能确认，请查看交易所订单。")
    return {"closed": True, "position_id": str(position_id)}


__all__ = ["router"]

