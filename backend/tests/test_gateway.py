"""Model gateway tests using a local stub provider (no network)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from aion.gateway.base import ChatMessage, InvalidModelOutput, ProviderError
from aion.gateway.budget import BudgetLedger
from aion.gateway.circuit import OPEN, CircuitBreaker, CircuitOpen
from aion.gateway.gateway import ModelGateway, Route, UnknownRoute
from tests.fakes import FakeProvider, FakeRedis

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def build_gateway(
    provider: FakeProvider, *, breaker_threshold: int = 5
) -> tuple[ModelGateway, BudgetLedger, CircuitBreaker]:
    ledger = BudgetLedger(
        FakeRedis(), daily_budget_usd=1.0, run_budget_usd=0.5
    )
    breaker = CircuitBreaker(FakeRedis(), failure_threshold=breaker_threshold)
    gateway = ModelGateway(
        {"fake": provider},
        ledger,
        breaker,
        routes={"market_regime": Route("fake", "fake-model")},
        default_max_tokens=64,
    )
    return gateway, ledger, breaker


def call(gateway: ModelGateway) -> object:
    return asyncio.run(
        gateway.call(
            "market_regime", [ChatMessage("user", "hello")], SCHEMA, run_id="run-1"
        )
    )


def test_valid_json_passes_schema_and_records_cost() -> None:
    provider = FakeProvider(['{"answer": "ok"}'])
    gateway, ledger, _ = build_gateway(provider)

    response = call(gateway)

    assert response.text == '{"answer": "ok"}'  # type: ignore[attr-defined]
    assert len(provider.calls) == 1
    assert asyncio.run(ledger.daily_spent()) == Decimal("0.0001")
    assert asyncio.run(ledger.run_spent("run-1")) == Decimal("0.0001")


def test_bad_json_is_retried_once_then_invalid_output() -> None:
    provider = FakeProvider(["not json", "still not json"])
    gateway, _, _ = build_gateway(provider)

    with pytest.raises(InvalidModelOutput):
        call(gateway)

    assert len(provider.calls) == 2


def test_bad_json_can_recover_on_retry() -> None:
    provider = FakeProvider(["not json", '{"answer": "ok"}'])
    gateway, _, _ = build_gateway(provider)

    response = call(gateway)

    assert response.text == '{"answer": "ok"}'  # type: ignore[attr-defined]
    assert len(provider.calls) == 2


def test_extra_fields_violate_schema() -> None:
    provider = FakeProvider(['{"answer": "ok", "extra": 1}'] * 2)
    gateway, _, _ = build_gateway(provider)

    with pytest.raises(InvalidModelOutput):
        call(gateway)

    assert len(provider.calls) == 2


def test_provider_error_opens_circuit_and_blocks_next_call() -> None:
    provider = FakeProvider([ProviderError("fake", "HTTP 500", retryable=True)])
    gateway, _, breaker = build_gateway(provider, breaker_threshold=1)

    with pytest.raises(ProviderError):
        call(gateway)

    assert asyncio.run(breaker.state("fake")) == OPEN
    with pytest.raises(CircuitOpen):
        call(gateway)
    assert len(provider.calls) == 1


def test_unknown_route_is_rejected() -> None:
    provider = FakeProvider(['{"answer": "ok"}'])
    gateway, _, _ = build_gateway(provider)

    with pytest.raises(UnknownRoute):
        asyncio.run(
            gateway.call("missing_role", [ChatMessage("user", "hi")], SCHEMA)
        )
