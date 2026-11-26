"""Reconciliation tests: local state vs a fake exchange (offline)."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from aion.exchange.errors import ExchangeTimeout
from aion.exchange.orders import OrderAck
from aion.execution.reconcile import reconcile
from aion.models.account import PositionSnapshot, ReconciliationRun
from aion.models.trading import Order

D = Decimal
NOW = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self, orders: list[Order] | None = None, positions: list[Any] | None = None) -> None:
        self.orders = orders or []
        self.positions = positions or []
        self.added: list[Any] = []
        self.commits = 0
        self.statements: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        try:
            entity = statement.column_descriptions[0]["entity"]
        except (AttributeError, IndexError, KeyError):
            entity = None
        if entity is Order:
            return FakeResult(self.orders)
        if entity is PositionSnapshot:
            return FakeResult(self.positions)
        return FakeResult([])


class FakeClient:
    def __init__(
        self,
        orders: list[OrderAck] | None = None,
        positions: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._orders = orders or []
        self._positions = positions or []
        self.error = error

    async def open_orders(self, symbol: str | None = None) -> list[OrderAck]:
        if self.error is not None:
            raise self.error
        return list(self._orders)

    async def positions(self, symbol: str | None = None) -> list[Any]:
        if self.error is not None:
            raise self.error
        return list(self._positions)

    async def query_order(self, **kwargs: Any) -> Any:  # pragma: no cover - protocol completeness
        raise NotImplementedError

    async def new_order(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def cancel_order(self, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


def make_order(
    coid: str,
    status: str = "SUBMITTED",
    exchange_id: str | None = None,
    order_type: str = "MARKET",
    reduce_only: bool = False,
) -> Order:
    return Order(
        id=uuid.uuid4(),
        client_order_id=coid,
        exchange_order_id=exchange_id,
        symbol="BTCUSDT",
        side="SELL" if reduce_only else "BUY",
        position_side="LONG",
        order_type=order_type,
        purpose="EXIT" if reduce_only else "ENTRY",
        reduce_only=reduce_only,
        quantity=D("0.01"),
        status=status,
        mode="TESTNET",
    )


def make_ack(coid: str, order_type: str = "MARKET", reduce_only: bool = False) -> OrderAck:
    return OrderAck(
        symbol="BTCUSDT",
        status="NEW",
        side="SELL" if reduce_only else "BUY",
        order_type=order_type,
        order_id="1",
        client_order_id=coid,
        orig_qty=D("0.01"),
        reduce_only=reduce_only,
        position_side="LONG",
    )


def make_position(quantity: str = "0.5", side: str = "LONG") -> Any:
    return type(
        "FakePosition",
        (),
        {
            "symbol": "BTCUSDT",
            "position_side": side,
            "quantity": D(quantity),
        },
    )()


def make_snapshot(quantity: str = "0.5", side: str = "LONG") -> PositionSnapshot:
    return PositionSnapshot(
        id=uuid.uuid4(),
        mode="TESTNET",
        captured_at=NOW,
        symbol="BTCUSDT",
        position_side=side,
        quantity=D(quantity),
        entry_price=D("20000"),
        unrealized_pnl=D("0"),
        leverage=5,
        margin_mode="CROSSED",
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_local_order_missing_at_exchange_is_blocking() -> None:
    session = FakeSession(orders=[make_order("aion-a-1"), make_order("aion-b-1")])
    client = FakeClient(orders=[make_ack("aion-a-1")])

    run_result = run(reconcile(client, session, mode="TESTNET"))

    kinds = [difference["kind"] for difference in run_result.differences]
    assert "order_missing_at_exchange" in kinds
    assert run_result.blocking is True
    assert run_result.status == "COMPLETED"
    assert session.commits == 1
    assert isinstance(session.added[0], ReconciliationRun)


def test_no_differences_is_not_blocking() -> None:
    session = FakeSession()
    result = run(reconcile(FakeClient(), session))

    assert result.blocking is False
    assert result.differences == []
    assert result.notes == "no differences"


def test_orphan_protection_order_is_blocking() -> None:
    session = FakeSession()
    client = FakeClient(orders=[make_ack("aion-x-1", order_type="STOP_MARKET", reduce_only=True)])

    result = run(reconcile(client, session))

    kinds = [difference["kind"] for difference in result.differences]
    assert "orphan_protection_order" in kinds
    assert result.blocking is True


def test_unknown_position_is_blocking() -> None:
    session = FakeSession()
    client = FakeClient(positions=[make_position("0.5")])

    result = run(reconcile(client, session))

    kinds = [difference["kind"] for difference in result.differences]
    assert "unknown_exchange_position" in kinds
    assert "missing_protection" in kinds
    assert result.blocking is True


def test_matched_position_with_stop_is_clean() -> None:
    session = FakeSession(
        orders=[make_order("aion-stop-1", order_type="STOP_MARKET", reduce_only=True)],
        positions=[make_snapshot("0.5")],
    )
    client = FakeClient(
        orders=[make_ack("aion-stop-1", order_type="STOP_MARKET", reduce_only=True)],
        positions=[make_position("0.5")],
    )

    result = run(reconcile(client, session))

    assert result.differences == []
    assert result.blocking is False


def test_position_quantity_mismatch_is_recorded_non_blocking() -> None:
    session = FakeSession(
        orders=[make_order("aion-stop-1", order_type="STOP_MARKET", reduce_only=True)],
        positions=[make_snapshot("0.5")],
    )
    client = FakeClient(
        orders=[make_ack("aion-stop-1", order_type="STOP_MARKET", reduce_only=True)],
        positions=[make_position("0.4")],
    )

    result = run(reconcile(client, session))

    kinds = [difference["kind"] for difference in result.differences]
    assert kinds == ["position_quantity_mismatch"]
    assert result.blocking is False


def test_exchange_unavailable_blocks_and_marks_failed() -> None:
    session = FakeSession()
    client = FakeClient(error=ExchangeTimeout("exchange down"))

    result = run(reconcile(client, session))

    assert result.status == "FAILED"
    assert result.blocking is True
    assert result.differences[0]["kind"] == "exchange_unavailable"


def test_reconcile_local_queries_are_scoped_by_user_and_mode() -> None:
    user_id = uuid.uuid4()
    session = FakeSession()
    run(reconcile(FakeClient(), session, mode="TESTNET", user_id=user_id))
    sql = "\n".join(str(statement) for statement in session.statements)
    assert "orders.mode" in sql
    assert "live_positions.mode" in sql
    assert "orders.user_id" in sql
    assert "live_positions.user_id" in sql


def test_live_reconciliation_does_not_mistake_exchange_snapshots_for_local_ledger() -> None:
    session = FakeSession(positions=[make_snapshot("0.5")])
    client = FakeClient(positions=[make_position("0.5")])

    result = run(reconcile(client, session, mode="TESTNET", user_id=uuid.uuid4()))

    assert result.blocking is True
    assert any(problem["kind"] == "unknown_exchange_position" for problem in result.differences)


def test_user_reconcile_does_not_supersede_global_incidents() -> None:
    user_id = uuid.uuid4()
    session = FakeSession()
    run(reconcile(FakeClient(), session, mode="TESTNET", user_id=user_id))
    update_sql = str(session.statements[0])
    assert "reconciliation_runs.user_id =" in update_sql
    assert "reconciliation_runs.user_id IS NULL" not in update_sql
