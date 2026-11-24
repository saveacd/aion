"""Trading intents, orders and fills.

Nothing here may fabricate execution results: the risk engine and execution
engine are separate modules, and when they are absent the API says so (501)
instead of pretending a trade was approved or placed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal as D
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import func, select

from aion.api.deps import (
    CurrentUser,
    DbSession,
    SettingsDep,
    assert_owner,
    record_audit,
    record_system_event,
    scope_to_user,
)
from aion.core.errors import AppError, ErrorCode
from aion.exchange.errors import BinanceAPIError, ExchangeNetworkError, ExchangeTimeout
from aion.models.base import utcnow
from aion.models.trading import Fill, Order, OrderEvent, RiskDecision, TradeIntent
from aion.schemas.common import AionModel
from aion.schemas.trading import (
    CancelOrderRequest,
    FillOut,
    IntentResult,
    OrderDetail,
    OrderEventOut,
    OrderOut,
    RiskDecisionOut,
    TradeIntentIn,
    TradeIntentOut,
)

router = APIRouter(prefix="/trading", tags=["trading"])

IntentResult.model_rebuild()

INTENT_TTL = dt.timedelta(minutes=5)

RiskEvaluator = Callable[[Any, TradeIntent], Awaitable[Any]]


def _load_risk_evaluator() -> RiskEvaluator | None:
    """Return the risk engine entry point if that module has been implemented."""
    try:
        from aion.risk.service import evaluate_intent
    except ImportError:
        return None
    return evaluate_intent


def _upstream_unavailable(message: str) -> AppError:
    return AppError(
        ErrorCode.UPSTREAM_UNAVAILABLE,
        message,
        http_status=501,
    )


class TradeIntentList(AionModel):
    items: list[TradeIntentOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class OrderList(AionModel):
    items: list[OrderOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class FillList(AionModel):
    items: list[FillOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


@router.post("/intents", response_model=IntentResult, status_code=201)
async def create_intent(
    payload: TradeIntentIn,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> IntentResult:
    intent_key = payload.client_intent_key or f"{payload.symbol}-{uuid.uuid4().hex}"

    existing_stmt = scope_to_user(
        select(TradeIntent).where(TradeIntent.intent_key == intent_key),
        TradeIntent.user_id,
        user,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return IntentResult(
            intent=TradeIntentOut.model_validate(existing),
            risk=None,
            orders=[],
            accepted=existing.status == "APPROVED",
        )

    mode = settings.resolve_trading_mode().value
    intent = TradeIntent(
        intent_key=intent_key,
        symbol=payload.symbol,
        side=payload.side,
        position_side=payload.position_side,
        intent_type="ENTRY",
        order_type=payload.order_type,
        quantity=payload.quantity,
        notional_usd=payload.notional_usd,
        requested_leverage=payload.requested_leverage,
        entry_price=payload.entry_price,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
        time_in_force=payload.time_in_force,
        confidence=payload.confidence,
        reason=payload.reason,
        evidence_refs=[],
        status="PENDING",
        expires_at=utcnow() + INTENT_TTL,
        mode=mode,
        user_id=user.id,
    )
    db.add(intent)
    await db.flush()

    evaluator = _load_risk_evaluator()
    if evaluator is None:
        intent.status = "BLOCKED"
        intent.rejection_code = "RISK_ENGINE_UNAVAILABLE"
        intent.rejection_detail = {
            "message": (
                "Risk evaluation is not wired in this deployment; the intent was "
                "recorded but not approved."
            )
        }
        await record_audit(
            db,
            actor=user.username,
            action="trading.intent.blocked",
            target=str(intent.id),
            after={"intent_key": intent_key, "reason": "risk_engine_unavailable"},
            request=request,
            ok=False,
        )
        record_system_event(
            db,
            category="trading",
            level="WARNING",
            message="Trade intent blocked: risk evaluation is unavailable.",
            data={"intent_id": str(intent.id), "symbol": intent.symbol},
        )
        await db.commit()
        raise _upstream_unavailable(
            "Risk evaluation is unavailable in this deployment. The trade intent was "
            "stored as BLOCKED and will not be executed."
        )

    decision = await evaluator(db, intent)
    if not isinstance(decision, RiskDecision):
        await db.rollback()
        raise _upstream_unavailable("Risk engine returned an invalid response.")

    intent.status = "APPROVED" if decision.approved else "REJECTED"
    if not decision.approved:
        codes = [str(code) for code in (decision.reason_codes or [])]
        intent.rejection_code = codes[0] if codes else "RISK_REJECTED"
        intent.rejection_detail = {
            "reason_codes": codes,
            "checked_limits": decision.checked_limits or {},
        }
    await record_audit(
        db,
        actor=user.username,
        action="trading.intent.evaluated",
        target=str(intent.id),
        after={"approved": decision.approved, "status": intent.status},
        request=request,
    )
    await db.commit()

    return IntentResult(
        intent=TradeIntentOut.model_validate(intent),
        risk=RiskDecisionOut.model_validate(decision),
        # Approval workflow by design: an intent clears the risk engine but
        # execution happens through the approved order flow / autopilot, not
        # inline (audit BUG-30 — documented so clients don't expect a fill).
        orders=[],
        accepted=bool(decision.approved),
    )


@router.get("/intents", response_model=TradeIntentList)
async def list_intents(
    db: DbSession,
    user: CurrentUser,
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TradeIntentList:
    stmt = select(TradeIntent)
    count_stmt = select(func.count()).select_from(TradeIntent)
    if symbol:
        stmt = stmt.where(TradeIntent.symbol == symbol.upper())
        count_stmt = count_stmt.where(TradeIntent.symbol == symbol.upper())
    if status_filter:
        stmt = stmt.where(TradeIntent.status == status_filter)
        count_stmt = count_stmt.where(TradeIntent.status == status_filter)
    stmt = scope_to_user(stmt, TradeIntent.user_id, user)
    count_stmt = scope_to_user(count_stmt, TradeIntent.user_id, user)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(TradeIntent.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return TradeIntentList(
        items=[TradeIntentOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/orders", response_model=OrderList)
async def list_orders(
    db: DbSession,
    user: CurrentUser,
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    mode: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderList:
    stmt = select(Order)
    count_stmt = select(func.count()).select_from(Order)
    if symbol:
        stmt = stmt.where(Order.symbol == symbol.upper())
        count_stmt = count_stmt.where(Order.symbol == symbol.upper())
    if status_filter:
        stmt = stmt.where(Order.status == status_filter)
        count_stmt = count_stmt.where(Order.status == status_filter)
    if mode:
        stmt = stmt.where(Order.mode == mode.upper())
        count_stmt = count_stmt.where(Order.mode == mode.upper())
    stmt = scope_to_user(stmt, Order.user_id, user)
    count_stmt = scope_to_user(count_stmt, Order.user_id, user)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(Order.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return OrderList(
        items=[OrderOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/orders/{order_id}", response_model=OrderDetail)
async def get_order(
    order_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> OrderDetail:
    order = await db.get(Order, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "Order not found.")
    assert_owner(user, order.user_id, "Order not found.")

    events = (
        await db.execute(
            select(OrderEvent)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.created_at.asc())
        )
    ).scalars().all()
    fills = (
        await db.execute(
            select(Fill).where(Fill.order_id == order_id).order_by(Fill.traded_at.asc())
        )
    ).scalars().all()

    detail = OrderDetail.model_validate(order)
    detail.events = [OrderEventOut.model_validate(row) for row in events]
    detail.fills = [FillOut.model_validate(row) for row in fills]
    return detail


@router.get("/fills", response_model=FillList)
async def list_fills(
    db: DbSession,
    user: CurrentUser,
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    order_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FillList:
    stmt = select(Fill)
    count_stmt = select(func.count()).select_from(Fill)
    if symbol:
        stmt = stmt.where(Fill.symbol == symbol.upper())
        count_stmt = count_stmt.where(Fill.symbol == symbol.upper())
    if order_id:
        stmt = stmt.where(Fill.order_id == order_id)
        count_stmt = count_stmt.where(Fill.order_id == order_id)
    if not user.is_admin:
        own_orders = select(Order.id).where(Order.user_id == user.id)
        stmt = stmt.where(Fill.order_id.in_(own_orders))
        count_stmt = count_stmt.where(Fill.order_id.in_(own_orders))

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(Fill.traded_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return FillList(
        items=[FillOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/orders/{order_id}/cancel", status_code=202)
async def cancel_order(
    order_id: uuid.UUID,
    payload: CancelOrderRequest,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> dict[str, Any]:
    order = await db.get(Order, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "Order not found.")
    assert_owner(user, order.user_id, "Order not found.")

    if order.mode == "PAPER":
        raise AppError(ErrorCode.CONFLICT, "模拟盘订单会立即成交，没有可撤销的挂单。")
    if order.status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
        raise AppError(ErrorCode.CONFLICT, f"订单已是终态（{order.status}），无法撤单。")

    from aion.services.live_trading import resolve_live_client

    client = await resolve_live_client(db, settings, user.id, order.mode)
    if client is None:
        raise _upstream_unavailable("未找到可用交易所 API Key，撤单未提交。")
    try:
        await client.cancel_order(
            symbol=order.symbol,
            order_id=order.exchange_order_id,
            client_order_id=None if order.exchange_order_id else order.client_order_id,
        )
        # Cancel-vs-fill race: the cancel may arrive after the order already
        # filled. Always take the exchange's FINAL status as truth instead of
        # blindly marking CANCELED locally (audit BUG-16).
        final_status = "CANCELED"
        final_raw: dict[str, Any] = {}
        try:
            ack = await client.query_order(
                symbol=order.symbol,
                order_id=order.exchange_order_id,
                client_order_id=None if order.exchange_order_id else order.client_order_id,
            )
            final_raw = ack.raw
            from aion.execution.state import from_exchange_status

            mapped = from_exchange_status(ack.status).value
            if mapped != "UNKNOWN":
                final_status = mapped
            if D(ack.executed_qty) > 0:
                order.filled_quantity = ack.executed_qty
            if ack.avg_price:
                order.avg_fill_price = ack.avg_price
        except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
            pass  # keep CANCELED as the assumed status when verification fails
    except Exception as exc:  # noqa: BLE001 - surface the exchange reason
        await record_audit(
            db,
            actor=user.username,
            action="trading.order.cancel_failed",
            target=str(order_id),
            after={"reason": payload.reason, "error": type(exc).__name__},
            request=request,
            ok=False,
        )
        await db.commit()
        raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, f"交易所撤单失败：{exc}") from exc
    finally:
        await client.aclose()

    previous = order.status
    order.status = final_status
    order.terminal_at = utcnow()
    if not order.last_exchange_response:
        order.last_exchange_response = {"raw": final_raw} if final_raw else None
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type=final_status,
            from_status=previous,
            to_status=final_status,
            payload={"reason": payload.reason, "source": "api", "raw": final_raw},
        )
    )
    await record_audit(
        db,
        actor=user.username,
        action="trading.order.canceled",
        target=str(order_id),
        after={"reason": payload.reason},
        request=request,
    )
    await db.commit()
    return {"canceled": True, "order_id": str(order_id)}
