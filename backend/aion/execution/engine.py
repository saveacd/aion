"""Execution engine — the only module allowed to submit orders.

Guarantees enforced here:

* An idempotent ``client_order_id`` (``aion-{intent}-{attempt}``) and a
  database row are created *before* anything is sent.
* Every state change writes an :class:`~aion.models.trading.OrderEvent`.
* A timeout or network failure is never followed by a blind resend: the order
  is first queried at the exchange. If it still cannot be confirmed the order
  becomes ``UNKNOWN`` and is queued for recovery.
* Prices/quantities are Decimal and rounded down to the symbol filters.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aion.exchange.errors import BinanceAPIError, ExchangeNetworkError, ExchangeTimeout
from aion.exchange.symbols import SymbolRuleError, SymbolRules
from aion.execution.state import (
    TERMINAL_STATES,
    OrderState,
    can_transition,
    ensure_transition,
    from_exchange_status,
)
from aion.models.trading import Order, OrderEvent

D = Decimal
D0 = D("0")

CONDITIONAL_TYPES = frozenset({"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"})
PROTECTIVE_TYPES = frozenset({"STOP", "STOP_MARKET"})
_SENSITIVE_KEYS = frozenset({"signature", "apiKey", "api_key", "secret", "apiSecret"})


class ExecutionError(Exception):
    """Base class for execution failures."""


class RiskNotApproved(ExecutionError):
    """Refusing to send because the risk decision did not approve the intent."""


class ExecutionClient(Protocol):
    async def new_order(self, **params: Any) -> Any: ...

    async def query_order(
        self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> Any: ...

    async def cancel_order(
        self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> Any: ...

    async def open_orders(self, symbol: str | None = None) -> Sequence[Any]: ...


@dataclass(frozen=True, slots=True)
class ProtectionMissing:
    symbol: str
    position_side: str
    required_qty: Decimal
    protective_qty: Decimal
    reason: str
    missing_types: tuple[str, ...] = ("STOP_MARKET",)


@dataclass(frozen=True, slots=True)
class RecoveryItem:
    order_id: str
    client_order_id: str
    symbol: str
    reason: str
    created_at: dt.datetime


def build_client_order_id(intent_id: Any, attempt: int = 1) -> str:
    """Deterministic idempotency key, safe for Binance (<= 36 chars)."""
    compact = re.sub(r"[^A-Za-z0-9]", "", str(intent_id)) or "unknown"
    return f"aion-{compact[:24]}-{int(attempt)}"


def _as_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return D0
    try:
        return D(value) if isinstance(value, Decimal) else D(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionError(f"invalid decimal value: {value!r}") from exc


def _as_optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return _as_decimal(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): ("***" if k in _SENSITIVE_KEYS else _json_safe(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class ExecutionEngine:
    """Turns an approved intent into exactly one exchange order."""

    def __init__(
        self,
        client: ExecutionClient,
        session_factory: Callable[[], AbstractAsyncContextManager[Any]],
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self.recovery_queue: list[RecoveryItem] = []

    # ----------------------------------------------------------- submission
    async def submit(
        self,
        intent: Any,
        risk_decision: Any,
        rules: SymbolRules,
        *,
        attempt: int = 1,
    ) -> Order:
        if not bool(getattr(risk_decision, "approved", False)):
            raise RiskNotApproved("risk decision did not approve this intent")

        coid = build_client_order_id(_intent_id(intent), attempt)

        async with self._session_factory() as session:
            existing = await self._find_order(session, coid)
            if existing is not None:
                # Idempotent replay: never send a second order for the same key.
                return existing

            order = self._build_order(intent, risk_decision, coid, attempt)
            session.add(order)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                existing = await self._find_order(session, coid)
                if existing is None:
                    raise
                return existing

            if not await self._validate(session, order, intent, rules):
                await session.commit()
                return order

            await self._transition(session, order, OrderState.VALIDATED, "VALIDATED")
            await self._transition(
                session, order, OrderState.SUBMITTED, "SUBMIT_REQUEST", {"attempt": attempt}
            )
            order.submit_attempts = attempt
            order.submitted_at = _utcnow()
            # Persist the claim before the request can reach Binance. A second
            # worker or a restarted process must query this ID, never resend it.
            await session.commit()

            try:
                ack = await self._client.new_order(
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=order.quantity,
                    price=order.price,
                    stop_price=order.stop_price,
                    time_in_force=order.time_in_force if order.order_type == "LIMIT" else None,
                    reduce_only=order.reduce_only,
                    close_position=False,
                    working_type="CONTRACT_PRICE" if order.order_type in CONDITIONAL_TYPES else None,
                    client_order_id=order.client_order_id,
                    position_side=order.position_side,
                )
            except (ExchangeTimeout, ExchangeNetworkError) as exc:
                if isinstance(session, AsyncSession):
                    await session.refresh(order, with_for_update=True)
                if _state_of(order) in TERMINAL_STATES:
                    return order
                ack = await self._confirm_after_ambiguous(
                    session, order, reason=exc.__class__.__name__
                )
            except BinanceAPIError as exc:
                if isinstance(session, AsyncSession):
                    await session.refresh(order, with_for_update=True)
                if _state_of(order) not in {OrderState.SUBMITTED, OrderState.UNKNOWN}:
                    return order
                await self._handle_api_error(session, order, exc)
                await session.commit()
                return order

            if ack is None:
                await session.commit()
                return order

            if isinstance(session, AsyncSession):
                await session.refresh(order, with_for_update=True)
            await self._apply_ack(session, order, ack, event_type="EXCHANGE_ACK")
            await session.commit()
            return order

    async def _validate(
        self, session: Any, order: Order, intent: Any, rules: SymbolRules
    ) -> bool:
        try:
            quantity = _as_optional_decimal(getattr(intent, "quantity", None))
            reference_price = _as_optional_decimal(getattr(intent, "entry_price", None))
            if quantity is None:
                notional = _as_optional_decimal(getattr(intent, "notional_usd", None))
                if notional is None or reference_price is None:
                    raise SymbolRuleError("intent needs quantity, or notional_usd plus a price")
                quantity = notional / reference_price

            order.quantity = rules.quantize_qty_for(quantity, order.order_type)
            rules.check_qty_for(order.quantity, order.order_type)

            if order.order_type == "MARKET":
                order.price = None
                check_price = reference_price
            else:
                price = reference_price or _as_optional_decimal(getattr(intent, "price", None))
                if price is None:
                    raise SymbolRuleError(f"{order.order_type} orders require a price")
                order.price = rules.quantize_price(price)
                check_price = order.price

            if order.stop_price is not None:
                order.stop_price = rules.quantize_price(order.stop_price)

            rules.check_order(check_price, order.quantity, order.order_type)
            return True
        except (SymbolRuleError, ArithmeticError, ExecutionError) as exc:
            order.error_code = "VALIDATION_FAILED"
            order.error_message = str(exc)
            await self._transition(
                session, order, OrderState.REJECTED, "VALIDATION_FAILED", {"error": str(exc)}
            )
            return False

    @staticmethod
    def _build_order(intent: Any, risk_decision: Any, coid: str, attempt: int) -> Order:
        intent_kind = str(getattr(intent, "intent_type", "ENTRY") or "ENTRY").upper()
        reduce_only = intent_kind in ("EXIT", "REDUCE")
        order_type = str(getattr(intent, "order_type", "MARKET") or "MARKET").upper()
        stop_price = None
        if order_type in CONDITIONAL_TYPES:
            stop_price = _as_optional_decimal(getattr(intent, "stop_loss", None))
        return Order(
            id=uuid.uuid4(),
            client_order_id=coid,
            intent_id=getattr(intent, "id", None),
            user_id=getattr(intent, "user_id", None),
            risk_decision_id=getattr(risk_decision, "id", None),
            symbol=str(intent.symbol),
            side=str(intent.side).upper(),
            position_side=str(getattr(intent, "position_side", "LONG") or "LONG").upper(),
            order_type=order_type,
            purpose="EXIT" if reduce_only else "ENTRY",
            reduce_only=reduce_only,
            quantity=_as_optional_decimal(getattr(intent, "quantity", None)) or D0,
            price=_as_optional_decimal(getattr(intent, "entry_price", None)),
            stop_price=stop_price,
            trigger_direction="CONTRACT_PRICE" if order_type in CONDITIONAL_TYPES else None,
            status=OrderState.CREATED.value,
            time_in_force=str(getattr(intent, "time_in_force", "GTC") or "GTC").upper(),
            mode=str(getattr(intent, "mode", "PAPER") or "PAPER"),
            submit_attempts=attempt,
            last_exchange_response={},
        )

    # -------------------------------------------------------------- failures
    async def _handle_api_error(self, session: Any, order: Order, exc: BinanceAPIError) -> None:
        order.error_code = str(exc.code) if exc.code is not None else "EXCHANGE_ERROR"
        order.error_message = exc.msg
        order.last_exchange_response = _json_safe(exc.payload)
        if exc.retryable:
            await self._mark_unknown(session, order, f"RETRYABLE_ERROR:{exc.code}")
        else:
            await self._transition(
                session,
                order,
                OrderState.REJECTED,
                "EXCHANGE_REJECTED",
                {"code": order.error_code, "message": exc.msg},
            )

    async def _confirm_after_ambiguous(self, session: Any, order: Order, reason: str) -> Any:
        """Query before doing anything else. Never resend here."""
        try:
            return await self._client.query_order(
                symbol=order.symbol, client_order_id=order.client_order_id
            )
        except BinanceAPIError as exc:
            await self._mark_unknown(
                session, order, f"{reason}:QUERY_ERROR:{exc.code}", error_code=exc.code
            )
        except (ExchangeTimeout, ExchangeNetworkError):
            await self._mark_unknown(session, order, f"{reason}:QUERY_UNAVAILABLE")
        return None

    async def _mark_unknown(
        self,
        session: Any,
        order: Order,
        reason: str,
        *,
        error_code: int | None = None,
    ) -> None:
        if error_code is not None:
            order.error_code = str(error_code)
        await self._transition(session, order, OrderState.UNKNOWN, "UNKNOWN_OUTCOME", {"reason": reason})
        self.recovery_queue.append(
            RecoveryItem(
                order_id=str(order.id),
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                reason=reason,
                created_at=_utcnow(),
            )
        )

    @staticmethod
    def _absorb_ack(order: Order, ack: Any) -> None:
        order.exchange_order_id = getattr(ack, "order_id", None) or order.exchange_order_id
        filled = _as_decimal(getattr(ack, "executed_qty", None))
        order.filled_quantity = max(_as_decimal(order.filled_quantity), filled)
        avg_price = _as_decimal(getattr(ack, "avg_price", None))
        if avg_price > D0:
            order.avg_fill_price = avg_price
        raw = getattr(ack, "raw", None)
        if isinstance(raw, dict) and raw:
            order.last_exchange_response = _json_safe(raw)

    async def _apply_ack(
        self, session: Any, order: Order, ack: Any, *, event_type: str = "EXCHANGE_ACK"
    ) -> OrderState:
        self._absorb_ack(order, ack)
        target = from_exchange_status(getattr(ack, "status", None), fallback=OrderState.ACKNOWLEDGED)
        if not can_transition(_state_of(order), target):
            return _state_of(order)
        await self._transition(session, order, target, event_type)
        return target

    async def recover_pending(self, *, user_id: uuid.UUID, mode: str) -> int:
        """Resolve persisted submissions through exchange queries only."""
        recoverable = {
            OrderState.SUBMITTED, OrderState.UNKNOWN, OrderState.CANCEL_PENDING,
            OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED,
        }
        async with self._session_factory() as session:
            ids = (await session.scalars(select(Order.id).where(
                Order.user_id == user_id, Order.mode == mode,
                Order.status.in_(tuple(recoverable)),
            ).order_by(Order.updated_at.asc()).limit(100))).all()

        resolved = 0
        for order_id in ids:
            async with self._session_factory() as session:
                order = await session.get(Order, order_id, with_for_update=True)
                if order is None or _state_of(order) not in recoverable:
                    continue
                ack = await self._confirm_after_ambiguous(session, order, "RECOVERY")
                if ack is not None:
                    state = await self._apply_ack(session, order, ack, event_type="RECOVERY_ACK")
                    resolved += state not in (OrderState.UNKNOWN, OrderState.SUBMITTED, OrderState.CANCEL_PENDING)
                await session.commit()
        return resolved

    # -------------------------------------------------------------- cancel
    async def cancel(self, order: Order) -> Order:
        async with self._session_factory() as session:
            current = _state_of(order)
            if current in TERMINAL_STATES:
                return order

            await self._transition(session, order, OrderState.CANCEL_PENDING, "CANCEL_REQUEST")
            await session.flush()

            try:
                ack = await self._client.cancel_order(
                    symbol=order.symbol,
                    order_id=order.exchange_order_id,
                    client_order_id=order.client_order_id,
                )
            except BinanceAPIError as exc:
                if exc.code in (-2011, -2013):
                    # Race: the order was already gone. Resolve from the exchange.
                    return await self._resolve_cancel_race(session, order)
                await self._mark_unknown(session, order, f"CANCEL_FAILED:{exc.code}")
                await session.commit()
                return order
            except (ExchangeTimeout, ExchangeNetworkError):
                await self._mark_unknown(session, order, "CANCEL_TIMEOUT")
                await session.commit()
                return order

            mapped = from_exchange_status(getattr(ack, "status", None), fallback=OrderState.CANCELED)
            if mapped in (OrderState.FILLED, OrderState.PARTIALLY_FILLED):
                await self._apply_ack(session, order, ack, event_type="CANCEL_ACK")
                if _state_of(order) is OrderState.PARTIALLY_FILLED:
                    await self._transition(session, order, OrderState.CANCELED, "CANCEL_CONFIRMED")
            else:
                self._absorb_ack(order, ack)
                target = OrderState.EXPIRED if mapped is OrderState.EXPIRED else OrderState.CANCELED
                await self._transition(session, order, target, "CANCEL_CONFIRMED")
            await session.commit()
            return order

    async def _resolve_cancel_race(self, session: Any, order: Order) -> Order:
        try:
            ack = await self._client.query_order(
                symbol=order.symbol, client_order_id=order.client_order_id
            )
        except BinanceAPIError as exc:
            if exc.code in (-2011, -2013):
                # Exchange has no record and the cancel was refused: nothing is live.
                await self._transition(session, order, OrderState.CANCELED, "CANCEL_CONFIRMED")
                await session.commit()
                return order
            await self._mark_unknown(session, order, f"CANCEL_RACE:{exc.code}")
            await session.commit()
            return order
        except (ExchangeTimeout, ExchangeNetworkError):
            await self._mark_unknown(session, order, "CANCEL_RACE_QUERY_UNAVAILABLE")
            await session.commit()
            return order

        mapped = from_exchange_status(getattr(ack, "status", None), fallback=OrderState.UNKNOWN)
        if mapped is OrderState.ACKNOWLEDGED:
            # The order is still live at the exchange; the cancel did not take.
            self._absorb_ack(order, ack)
            await self._mark_unknown(session, order, "CANCEL_RACE_STILL_LIVE")
        else:
            await self._apply_ack(session, order, ack, event_type="CANCEL_RACE_ACK")
        await session.commit()
        return order

    # -------------------------------------------------------- protection
    async def verify_protection(
        self, order: Order, position_qty: Decimal | None = None
    ) -> ProtectionMissing | None:
        """Confirm a live stop exists and covers the filled/position quantity."""
        required = _as_decimal(position_qty) if position_qty is not None else D0
        if required <= D0:
            required = _as_decimal(order.filled_quantity) or _as_decimal(order.quantity)

        open_orders = await self._client.open_orders(symbol=order.symbol)
        protective_qty = D0
        found_stop = False
        for candidate in open_orders:
            order_type = str(getattr(candidate, "order_type", "")).upper()
            if order_type not in PROTECTIVE_TYPES:
                continue
            if not (getattr(candidate, "reduce_only", False) or getattr(candidate, "close_position", False)):
                continue
            candidate_side = getattr(candidate, "position_side", None)
            if candidate_side and candidate_side != order.position_side:
                continue
            found_stop = True
            if getattr(candidate, "close_position", False):
                protective_qty = max(protective_qty, required)
            else:
                protective_qty += max(
                    D0, _as_decimal(getattr(candidate, "orig_qty", None))
                )

        if found_stop and protective_qty >= required and required > D0:
            return None

        reason = "no_stop_order" if not found_stop else "stop_quantity_mismatch"
        return ProtectionMissing(
            symbol=order.symbol,
            position_side=order.position_side,
            required_qty=required,
            protective_qty=protective_qty,
            reason=reason,
            missing_types=("STOP_MARKET",),
        )

    # -------------------------------------------------------------- helpers
    async def _find_order(self, session: Any, client_order_id: str) -> Order | None:
        result = await session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        return result.scalars().first()

    async def _transition(
        self,
        session: Any,
        order: Order,
        target: OrderState,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        source = _state_of(order)
        if source is not target:
            ensure_transition(source, target)
            order.status = target.value
            if target in TERMINAL_STATES:
                order.terminal_at = _utcnow()
        session.add(
            OrderEvent(
                order_id=order.id,
                event_type=event_type,
                from_status=source.value,
                to_status=target.value,
                payload=_json_safe(payload or {}),
                exchange_event_time=None,
            )
        )


def _state_of(order: Order) -> OrderState:
    try:
        return OrderState(order.status)
    except ValueError:
        return OrderState.UNKNOWN


def _intent_id(intent: Any) -> Any:
    return getattr(intent, "id", None) or getattr(intent, "intent_id", None) or "intent"
