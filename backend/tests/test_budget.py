"""Budget ledger tests with an in-memory Redis fake."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest

from aion.gateway.budget import BudgetExceeded, BudgetLedger, CallRecord
from tests.fakes import FakeRedis, FakeSessionFactory

D = Decimal


def make_ledger(
    *, daily: float = 1.0, per_run: float = 0.5, factory: FakeSessionFactory | None = None
) -> BudgetLedger:
    return BudgetLedger(
        FakeRedis(),
        daily_budget_usd=daily,
        run_budget_usd=per_run,
        session_factory=factory,
    )


def test_single_call_over_run_budget_is_rejected() -> None:
    ledger = make_ledger(daily=5.0, per_run=0.5)

    with pytest.raises(BudgetExceeded) as info:
        asyncio.run(ledger.check_and_reserve(D("0.75"), run_id="run-1"))

    assert info.value.scope == "run"
    assert asyncio.run(ledger.daily_spent()) == D("0")


def test_run_budget_accumulates() -> None:
    ledger = make_ledger(daily=5.0, per_run=0.5)
    reservation = asyncio.run(ledger.check_and_reserve(D("0.4"), run_id="run-1"))
    asyncio.run(ledger.settle(D("0.4"), reservation))

    with pytest.raises(BudgetExceeded) as info:
        asyncio.run(ledger.check_and_reserve(D("0.2"), run_id="run-1"))

    assert info.value.scope == "run"
    assert asyncio.run(ledger.run_spent("run-1")) == D("0.4")


def test_daily_budget_accumulates_across_runs() -> None:
    ledger = make_ledger(daily=0.5, per_run=5.0)
    first = asyncio.run(ledger.check_and_reserve(D("0.3"), run_id="run-1"))
    asyncio.run(ledger.settle(D("0.3"), first))
    second = asyncio.run(ledger.check_and_reserve(D("0.1"), run_id="run-2"))
    asyncio.run(ledger.settle(D("0.1"), second))

    with pytest.raises(BudgetExceeded) as info:
        asyncio.run(ledger.check_and_reserve(D("0.2"), run_id="run-3"))

    assert info.value.scope == "daily"
    assert asyncio.run(ledger.daily_spent()) == D("0.4")


def test_failed_call_refunds_reservation() -> None:
    ledger = make_ledger(daily=1.0, per_run=1.0)
    reservation = asyncio.run(ledger.check_and_reserve(D("0.4"), run_id="run-1"))

    asyncio.run(ledger.settle(D("0"), reservation))

    assert asyncio.run(ledger.daily_spent()) == D("0")
    assert asyncio.run(ledger.run_spent("run-1")) == D("0")


def test_daily_key_uses_documented_prefix() -> None:
    ledger = make_ledger()

    assert ledger.daily_key().startswith("aion:budget:")


def test_settle_persists_model_call_row() -> None:
    factory = FakeSessionFactory()
    ledger = make_ledger(factory=factory)
    run_id = uuid.uuid4()
    reservation = asyncio.run(ledger.check_and_reserve(D("0.1"), run_id=str(run_id)))

    asyncio.run(
        ledger.settle(
            D("0.05"),
            reservation,
            record=CallRecord(
                provider="fake",
                model="fake-model",
                input_tokens=3,
                output_tokens=2,
                run_id=run_id,
                request_meta={"role": "market_regime"},
            ),
        )
    )

    assert len(factory.objects) == 1
    call = factory.objects[0]
    assert call.provider == "fake"
    assert call.model == "fake-model"
    assert call.cost_usd == D("0.05")
    assert call.attempts == 1
