"""Reconciliation: exchange is the source of truth.

``reconcile`` compares local open orders/positions with the exchange and
records every difference in :class:`~aion.models.account.ReconciliationRun`.
``blocking=True`` means new risk must not be opened until the difference is
resolved (unknown position, orphan protection order, order missing at the
exchange, or the exchange being unavailable).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from aion.exchange.errors import ExchangeError
from aion.models.account import PositionSnapshot, ReconciliationRun
from aion.models.trading import Order

D = Decimal
D0 = D("0")

LIVE_ORDER_STATUSES = ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING", "UNKNOWN")
PROTECTIVE_TYPES = frozenset({"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"})
STOP_TYPES = frozenset({"STOP", "STOP_MARKET"})


@dataclass(frozen=True, slots=True)
class Difference:
    kind: str
    severity: str
    blocking: bool
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "blocking": self.blocking,
            "detail": self.detail,
        }


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return D0
    return Decimal(str(value))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _normalize_side(quantity: Decimal, position_side: str | None) -> str:
    side = (position_side or "BOTH").upper()
    if side == "BOTH":
        return "LONG" if quantity >= D0 else "SHORT"
    return side


def _position_key(symbol: str, quantity: Decimal, position_side: str | None) -> tuple[str, str]:
    return symbol, _normalize_side(quantity, position_side)


def _matches_position(candidate: Any, key: tuple[str, str]) -> bool:
    if str(getattr(candidate, "symbol", "")) != key[0]:
        return False
    side = str(getattr(candidate, "position_side", None) or "BOTH").upper()
    return side in ("BOTH", key[1])


async def _local_live_orders(
    session: Any, user_id: uuid.UUID | None = None, mode: str = "PAPER"
) -> list[Order]:
    # Reconciliation is scoped to one owner AND one execution environment.
    stmt = select(Order).where(Order.status.in_(LIVE_ORDER_STATUSES), Order.mode == mode)
    stmt = stmt.where(Order.user_id == user_id if user_id is not None else Order.user_id.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _latest_local_positions(
    session: Any, user_id: uuid.UUID | None = None, mode: str = "PAPER"
) -> list[PositionSnapshot]:
    stmt = select(PositionSnapshot).where(PositionSnapshot.mode == mode).order_by(
        PositionSnapshot.captured_at.desc()
    )
    stmt = stmt.where(
        PositionSnapshot.user_id == user_id
        if user_id is not None
        else PositionSnapshot.user_id.is_(None)
    )
    result = await session.execute(stmt)
    latest: dict[tuple[str, str], PositionSnapshot] = {}
    for snapshot in result.scalars().all():
        key = _position_key(snapshot.symbol, _dec(snapshot.quantity), snapshot.position_side)
        latest.setdefault(key, snapshot)
    return [row for row in latest.values() if _dec(row.quantity) != D0]


async def reconcile(
    client: Any,
    session: Any,
    *,
    mode: str = "PAPER",
    now: dt.datetime | None = None,
    user_id: uuid.UUID | None = None,
) -> ReconciliationRun:
    started = now or _now()
    # The newest run supersedes older unresolved ones: without this, the
    # first blocking run would wedge the risk gate forever (follow-up #1).
    # Fake test sessions may not support UPDATE statements — degrade to a
    # no-op instead of breaking the comparison itself.
    try:
        from sqlalchemy import update as sa_update

        supersede = sa_update(ReconciliationRun).where(
            ReconciliationRun.mode == mode,
            ReconciliationRun.resolved.is_(False),
        )
        # Never let a user's run resolve global or another user's incidents.
        supersede = supersede.where(
            ReconciliationRun.user_id == user_id
            if user_id is not None
            else ReconciliationRun.user_id.is_(None)
        )
        await session.execute(supersede.values(resolved=True))
    except AttributeError:
        pass
    run = ReconciliationRun(
        id=uuid.uuid4(),
        mode=mode,
        status="RUNNING",
        started_at=started,
        differences=[],
        blocking=False,
        resolved=False,
        user_id=user_id,
    )
    differences: list[Difference] = []

    try:
        exchange_orders = list(await client.open_orders())
        exchange_positions = [
            position for position in await client.positions() if _dec(position.quantity) != D0
        ]
    except ExchangeError as exc:
        run.status = "FAILED"
        run.blocking = True
        run.finished_at = now or _now()
        run.notes = f"exchange unavailable during reconciliation: {exc.__class__.__name__}"
        run.differences = [
            Difference(
                kind="exchange_unavailable",
                severity="CRITICAL",
                blocking=True,
                detail={"error": exc.__class__.__name__},
            ).as_dict()
        ]
        session.add(run)
        await session.commit()
        return run

    local_orders = await _local_live_orders(session, user_id, mode)
    local_positions = await _latest_local_positions(session, user_id, mode)

    exchange_by_client_id = {
        order.client_order_id: order for order in exchange_orders if order.client_order_id
    }
    exchange_by_order_id = {
        order.order_id: order for order in exchange_orders if getattr(order, "order_id", None)
    }
    matched_exchange_orders: set[Any] = set()

    for local in local_orders:
        match = exchange_by_client_id.get(local.client_order_id)
        if match is None and local.exchange_order_id:
            match = exchange_by_order_id.get(local.exchange_order_id)
        if match is None:
            differences.append(
                Difference(
                    kind="order_missing_at_exchange",
                    severity="CRITICAL",
                    blocking=True,
                    detail={
                        "client_order_id": local.client_order_id,
                        "exchange_order_id": local.exchange_order_id,
                        "symbol": local.symbol,
                        "local_status": local.status,
                    },
                )
            )
        else:
            matched_exchange_orders.add(id(match))

    for exchange_order in exchange_orders:
        if id(exchange_order) in matched_exchange_orders:
            continue
        is_protective = bool(
            getattr(exchange_order, "reduce_only", False)
            or getattr(exchange_order, "close_position", False)
            or str(getattr(exchange_order, "order_type", "")).upper() in PROTECTIVE_TYPES
        )
        differences.append(
            Difference(
                kind="orphan_protection_order" if is_protective else "unknown_exchange_order",
                severity="CRITICAL",
                blocking=True,
                detail={
                    "client_order_id": getattr(exchange_order, "client_order_id", None),
                    "exchange_order_id": getattr(exchange_order, "order_id", None),
                    "symbol": getattr(exchange_order, "symbol", None),
                    "order_type": getattr(exchange_order, "order_type", None),
                },
            )
        )

    local_by_key = {
        _position_key(row.symbol, _dec(row.quantity), row.position_side): row
        for row in local_positions
    }
    exchange_by_key = {
        _position_key(position.symbol, _dec(position.quantity), position.position_side): position
        for position in exchange_positions
    }

    for key, position in exchange_by_key.items():
        local = local_by_key.get(key)
        if local is None:
            differences.append(
                Difference(
                    kind="unknown_exchange_position",
                    severity="CRITICAL",
                    blocking=True,
                    detail={
                        "symbol": key[0],
                        "position_side": key[1],
                        "quantity": str(_dec(position.quantity)),
                    },
                )
            )
        else:
            local_qty = abs(_dec(local.quantity))
            exchange_qty = abs(_dec(position.quantity))
            if local_qty != exchange_qty:
                differences.append(
                    Difference(
                        kind="position_quantity_mismatch",
                        severity="WARN",
                        blocking=False,
                        detail={
                            "symbol": key[0],
                            "position_side": key[1],
                            "local_quantity": str(local_qty),
                            "exchange_quantity": str(exchange_qty),
                        },
                    )
                )

        has_stop = any(
            _matches_position(candidate, key)
            and str(getattr(candidate, "order_type", "")).upper() in STOP_TYPES
            and bool(
                getattr(candidate, "reduce_only", False)
                or getattr(candidate, "close_position", False)
            )
            for candidate in exchange_orders
        )
        if not has_stop:
            differences.append(
                Difference(
                    kind="missing_protection",
                    severity="CRITICAL",
                    blocking=True,
                    detail={
                        "symbol": key[0],
                        "position_side": key[1],
                        "quantity": str(abs(_dec(position.quantity))),
                    },
                )
            )

    for key, local in local_by_key.items():
        if key not in exchange_by_key:
            differences.append(
                Difference(
                    kind="position_missing_at_exchange",
                    severity="CRITICAL",
                    blocking=True,
                    detail={
                        "symbol": key[0],
                        "position_side": key[1],
                        "local_quantity": str(abs(_dec(local.quantity))),
                    },
                )
            )

    run.differences = [difference.as_dict() for difference in differences]
    run.blocking = any(difference.blocking for difference in differences)
    run.status = "COMPLETED"
    run.finished_at = now or _now()
    run.notes = (
        "no differences"
        if not differences
        else f"{len(differences)} difference(s); blocking={run.blocking}"
    )
    session.add(run)
    await session.commit()
    return run
