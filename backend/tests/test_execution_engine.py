"""Execution engine tests with an offline fake exchange.

The fake broker mimics the client interface, so no socket is ever opened.
A second, optional test uses ``httpx.MockTransport`` to prove the wire-level
behaviour: one POST, then exactly one GET, never a resend.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from aion.exchange.errors import BinanceAPIError, ExchangeTimeout
from aion.exchange.orders import OrderAck
from aion.exchange.symbols import SymbolRules
from aion.execution.engine import (
    ExecutionEngine,
    ProtectionMissing,
    RiskNotApproved,
    build_client_order_id,
)
from aion.models.trading import Order, OrderEvent, RiskDecision, TradeIntent

D = Decimal
NOW = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)

RULES = SymbolRules(
    symbol="BTCUSDT",
    tick_size=D("0.1"),
    step_size=D("0.001"),
    min_qty=D("0.001"),
    min_notional=D("5"),
    price_precision=2,
    quantity_precision=3,
)


# --------------------------------------------------------------------- fakes
class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> FakeResult:
        return self

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeSession:
    def __init__(self) -> None:
        self.orders: list[Order] = []
        self.added: list[Any] = []
        self.commits = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, Order):
            self.orders.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, statement: Any) -> FakeResult:
        wanted = {
            value
            for value in statement.compile().params.values()
            if isinstance(value, str)
        }
        return FakeResult([order for order in self.orders if order.client_order_id in wanted])


class FakeBroker:
    def __init__(self) -> None:
        self.new_order_calls = 0
        self.query_calls = 0
        self.cancel_calls = 0
        self.new_order_error: Exception | None = None
        self.query_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.new_order_ack: OrderAck | None = None
        self.query_ack: OrderAck | None = None
        self.cancel_ack: OrderAck | None = None
        self.open_orders_result: list[OrderAck] = []
        self.sent: dict[str, Any] = {}

    @staticmethod
    def _default_ack(status: str = "NEW") -> OrderAck:
        return OrderAck(
            symbol="BTCUSDT",
            status=status,
            side="BUY",
            order_type="MARKET",
            order_id="9001",
            orig_qty=D("0.01"),
            raw={"orderId": 9001, "status": status},
        )

    async def new_order(self, **params: Any) -> OrderAck:
        self.new_order_calls += 1
        self.sent = params
        if self.new_order_error is not None:
            raise self.new_order_error
        return self.new_order_ack or self._default_ack()

    async def query_order(
        self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> OrderAck:
        self.query_calls += 1
        if self.query_error is not None:
            raise self.query_error
        return self.query_ack or self._default_ack()

    async def cancel_order(
        self, *, symbol: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> OrderAck:
        self.cancel_calls += 1
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_ack or self._default_ack("CANCELED")

    async def open_orders(self, symbol: str | None = None) -> list[OrderAck]:
        return list(self.open_orders_result)


# ------------------------------------------------------------------ fixtures
def make_intent(**overrides: Any) -> TradeIntent:
    base: dict[str, Any] = {
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "intent_key": "k-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "position_side": "LONG",
        "intent_type": "ENTRY",
        "order_type": "MARKET",
        "quantity": D("0.0109"),
        "requested_leverage": 5,
        "time_in_force": "GTC",
        "mode": "TESTNET",
        "expires_at": NOW + dt.timedelta(minutes=5),
    }
    base.update(overrides)
    return TradeIntent(**base)


def make_decision(approved: bool = True) -> RiskDecision:
    decision = RiskDecision(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        approved=approved,
        reason_codes=[],
        checked_limits={},
        risk_snapshot={},
        mode="TESTNET",
        decided_at=NOW,
    )
    return decision


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------- tests
def test_submit_persists_events_and_quantizes() -> None:
    broker = FakeBroker()
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)
    intent = make_intent()

    order = run(engine.submit(intent, make_decision(), RULES))

    assert order.status == "ACKNOWLEDGED"
    assert order.exchange_order_id == "9001"
    assert order.quantity == D("0.010")
    assert order.client_order_id == build_client_order_id(intent.id, 1)
    assert broker.new_order_calls == 1
    events = [obj.event_type for obj in session.added if isinstance(obj, OrderEvent)]
    assert events == ["VALIDATED", "SUBMIT_REQUEST", "EXCHANGE_ACK"]
    assert session.commits == 2


def test_timeout_queries_order_instead_of_resending() -> None:
    broker = FakeBroker()
    broker.new_order_error = ExchangeTimeout("network timeout")
    broker.query_ack = OrderAck(
        symbol="BTCUSDT",
        status="FILLED",
        side="BUY",
        order_type="MARKET",
        order_id="9002",
        orig_qty=D("0.01"),
        executed_qty=D("0.01"),
        avg_price=D("20000"),
        raw={"orderId": 9002, "status": "FILLED"},
    )
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)

    order = run(engine.submit(make_intent(), make_decision(), RULES))

    assert broker.new_order_calls == 1
    assert broker.query_calls == 1
    assert order.status == "FILLED"
    assert order.filled_quantity == D("0.01")
    assert session.orders[0].exchange_order_id == "9002"


def test_timeout_without_confirmation_becomes_unknown() -> None:
    broker = FakeBroker()
    broker.new_order_error = ExchangeTimeout("network timeout")
    broker.query_error = ExchangeTimeout("query timeout")
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)

    order = run(engine.submit(make_intent(), make_decision(), RULES))

    assert order.status == "UNKNOWN"
    assert broker.new_order_calls == 1
    assert broker.query_calls == 1
    assert len(engine.recovery_queue) == 1
    assert engine.recovery_queue[0].client_order_id == order.client_order_id


def test_client_order_id_is_stable_and_idempotent() -> None:
    broker = FakeBroker()
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)
    intent = make_intent()
    decision = make_decision()

    first = run(engine.submit(intent, decision, RULES))
    second = run(engine.submit(intent, decision, RULES))

    assert first.id == second.id
    assert broker.new_order_calls == 1
    assert len(session.orders) == 1
    assert build_client_order_id(intent.id, 2) != build_client_order_id(intent.id, 1)


def test_non_retryable_rejection_marks_order_rejected() -> None:
    broker = FakeBroker()
    broker.new_order_error = BinanceAPIError(-2010, "insufficient margin", http_status=400)
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)

    order = run(engine.submit(make_intent(), make_decision(), RULES))

    assert order.status == "REJECTED"
    assert order.error_code == "-2010"
    assert broker.query_calls == 0


def test_risk_rejection_is_never_sent() -> None:
    broker = FakeBroker()
    engine = ExecutionEngine(broker, lambda: FakeSession())

    with pytest.raises(RiskNotApproved):
        run(engine.submit(make_intent(), make_decision(approved=False), RULES))
    assert broker.new_order_calls == 0


def test_precision_failure_rejects_before_sending() -> None:
    broker = FakeBroker()
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)

    order = run(engine.submit(make_intent(quantity=D("0.0004")), make_decision(), RULES))

    assert order.status == "REJECTED"
    assert order.error_code == "VALIDATION_FAILED"
    assert broker.new_order_calls == 0


def test_verify_protection_detects_missing_stop() -> None:
    broker = FakeBroker()
    engine = ExecutionEngine(broker, lambda: FakeSession())
    order = run(engine.submit(make_intent(), make_decision(), RULES))

    broker.open_orders_result = []
    missing = run(engine.verify_protection(order))
    assert isinstance(missing, ProtectionMissing)
    assert missing.reason == "no_stop_order"

    broker.open_orders_result = [
        OrderAck(
            symbol="BTCUSDT",
            status="NEW",
            side="SELL",
            order_type="STOP_MARKET",
            order_id="9100",
            orig_qty=D("0.01"),
            reduce_only=True,
            close_position=False,
            position_side="LONG",
        )
    ]
    assert run(engine.verify_protection(order)) is None

    broker.open_orders_result[0] = OrderAck(
        symbol="BTCUSDT",
        status="NEW",
        side="SELL",
        order_type="STOP_MARKET",
        order_id="9100",
        orig_qty=D("0.001"),
        reduce_only=True,
        position_side="LONG",
    )
    mismatch = run(engine.verify_protection(order))
    assert mismatch is not None
    assert mismatch.reason == "stop_quantity_mismatch"


def test_cancel_transitions_to_canceled() -> None:
    broker = FakeBroker()
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)
    order = run(engine.submit(make_intent(), make_decision(), RULES))

    canceled = run(engine.cancel(order))

    assert canceled.status == "CANCELED"
    assert broker.cancel_calls == 1


def test_cancel_timeout_marks_unknown() -> None:
    broker = FakeBroker()
    session = FakeSession()
    engine = ExecutionEngine(broker, lambda: session)
    order = run(engine.submit(make_intent(), make_decision(), RULES))

    broker.cancel_error = ExchangeTimeout("cancel timeout")
    result = run(engine.cancel(order))

    assert result.status == "UNKNOWN"
    assert len(engine.recovery_queue) == 1


def test_timeout_over_http_is_one_post_and_one_get() -> None:
    httpx = pytest.importorskip("httpx")
    calls: list[tuple[str, str]] = []

    def handler(request: Any) -> Any:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            raise httpx.TimeoutException("simulated timeout", request=request)
        return httpx.Response(
            200,
            json={
                "symbol": "BTCUSDT",
                "status": "FILLED",
                "side": "BUY",
                "type": "MARKET",
                "orderId": 4242,
                "clientOrderId": request.url.params.get("origClientOrderId"),
                "origQty": "0.01",
                "executedQty": "0.01",
                "avgPrice": "20000",
                "price": "0",
                "updateTime": 1700000000000,
            },
        )

    session = FakeSession()

    async def scenario() -> Order:
        from aion.exchange.client import BinanceFuturesClient

        client = BinanceFuturesClient(
            "test-key",
            "test-secret",
            transport=httpx.MockTransport(handler),
        )
        try:
            engine = ExecutionEngine(client, lambda: session)
            return await engine.submit(make_intent(), make_decision(), RULES)
        finally:
            await client.aclose()

    order = run(scenario())

    assert order.status == "FILLED"
    order_calls = [method for method, path in calls if path == "/fapi/v1/order"]
    assert order_calls == ["POST", "GET"]
    assert len(calls) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_restarted_worker_recovers_committed_submission_without_resending(trader_user):
    from sqlalchemy import select

    from aion.core.db import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as db:
        intent = make_intent(user_id=trader_user.id)
        db.add(intent)
        await db.commit()

    class InterruptedBroker(FakeBroker):
        async def new_order(self, **params: Any) -> OrderAck:
            # The local row must already be visible to a different connection.
            async with factory() as db:
                row = await db.scalar(select(Order).where(Order.client_order_id == params["client_order_id"]))
                assert row is not None and row.status == "SUBMITTED"
                assert row.user_id == trader_user.id
            self.new_order_calls += 1
            raise asyncio.CancelledError

    broker = InterruptedBroker()
    decision = make_decision()
    with pytest.raises(asyncio.CancelledError):
        await ExecutionEngine(broker, factory).submit(intent, decision, RULES)

    broker.query_ack = broker._default_ack("FILLED")
    resumed = ExecutionEngine(broker, factory)
    assert await resumed.recover_pending(user_id=uuid.uuid4(), mode="TESTNET") == 0
    assert await resumed.recover_pending(user_id=trader_user.id, mode="MAINNET") == 0
    assert await resumed.recover_pending(user_id=trader_user.id, mode="TESTNET") == 1
    result = await resumed.submit(intent, decision, RULES)
    assert result.status == "FILLED"
    assert broker.new_order_calls == 1
    assert broker.query_calls == 1
