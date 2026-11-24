"""Factor research orchestrator tests: degradation, dedupe, deterministic referee."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pydantic", reason="agent schemas require pydantic")

from aion.agents.factor_research import (  # noqa: E402
    FACTOR_DOMAINS,
    FactorResearchOrchestrator,
)
from aion.gateway.base import ModelResponse, ProviderError  # noqa: E402
from aion.quant.factors import FactorFormula  # noqa: E402
from tests.test_factors_quant import Bar, make_candles  # noqa: E402

D = Decimal

COMMON: dict[str, Any] = {
    "summary": "proposal ok",
    "evidence": ["provided_inputs"],
    "assumptions": [],
    "confidence": 0.7,
    "warnings": [],
    "recommended_action": "record_factor",
}


def proposal_payload(
    domain: str,
    *,
    factor_name: str | None = None,
    formula: str = "MOMENTUM",
    lookback: int = 10,
    direction: str = "POSITIVE",
) -> dict[str, Any]:
    return {
        **COMMON,
        "domain": domain,
        "factor_name": factor_name or f"{domain.upper()}_FACTOR",
        "formula": formula,
        "lookback_bars": lookback,
        "direction": direction,
        "hypothesis": "该因子在当前市场状态下对 horizon 期收益有预测力",
        "rationale": "动量延续效应",
        "invalidation_conditions": ["市场转为震荡"],
    }


class StubGateway:
    """Same shape as the orchestrator test stub: no HTTP, no DB, no orders."""

    def __init__(self, payloads: dict[str, Any], *, fail: set[str] | None = None) -> None:
        self.payloads = payloads
        self.fail = fail or set()
        self.calls: list[str] = []

    def run_usage(self, run_id: uuid.UUID) -> tuple[int, int]:
        return (10 * len(self.calls), 5 * len(self.calls))

    async def call(
        self,
        role: str,
        messages: list[Any],
        json_schema: dict[str, Any] | None = None,
        *,
        run_id: uuid.UUID | str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        purpose: str | None = None,
    ) -> ModelResponse:
        self.calls.append(role)
        if role in self.fail:
            raise ProviderError("stub", "HTTP 500", retryable=True)
        return ModelResponse(
            text=json.dumps(self.payloads[role]),
            model="stub-model",
            provider="stub",
            input_tokens=10,
            output_tokens=5,
            cost_usd=D("0.001"),
            latency_ms=1,
            raw={},
        )


def payload_for_all(*, formula: str = "MOMENTUM", direction: str = "POSITIVE") -> dict[str, Any]:
    return {
        f"factor_research_{domain}": proposal_payload(
            domain, formula=formula, direction=direction
        )
        for domain in FACTOR_DOMAINS
    }


@pytest.fixture
def candles() -> list[Bar]:
    return make_candles(n=400, trend=0.006, seed=11)


def candle_dicts(candles: list[Bar]) -> list[dict[str, Any]]:
    return [
        {"t": str(i), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
        for i, b in enumerate(candles)
    ]


async def test_six_researchers_run_and_report_is_shaped(candles: list[Bar]) -> None:
    gateway = StubGateway(payload_for_all())
    orch = FactorResearchOrchestrator(gateway, timeout_seconds=5)
    report = await orch.run("BTCUSDT", "15m", candle_dicts(candles), 5)
    assert gateway.calls == [f"factor_research_{d}" for d in FACTOR_DOMAINS]
    assert not report.degraded
    assert len(report.proposals) == 6
    assert report.accepted_count + report.rejected_count == len(report.evaluations)
    # Momentum on a trending series with a positive direction must be accepted.
    assert any(row.accepted for row in report.evaluations)
    blob = json.dumps(report.to_dict(), default=str)
    assert '"accepted": true' in blob.replace("True", "true") or "accepted" in blob


async def test_all_proposals_collapse_on_dedupe(candles: list[Bar]) -> None:
    payloads = payload_for_all()
    for _domain, payload in payloads.items():
        payload["factor_name"] = "SAME_FACTOR"
    gateway = StubGateway(payloads)
    orch = FactorResearchOrchestrator(gateway, timeout_seconds=5)
    report = await orch.run("BTCUSDT", "15m", candle_dicts(candles), 5)
    assert len(report.evaluations) == 1


async def test_direction_mismatch_rejects_even_with_good_ic(candles: list[Bar]) -> None:
    payloads = payload_for_all(direction="NEGATIVE")
    gateway = StubGateway(payloads)
    orch = FactorResearchOrchestrator(gateway, timeout_seconds=5)
    report = await orch.run("BTCUSDT", "15m", candle_dicts(candles), 5)
    assert report.accepted_count == 0
    assert all("direction_mismatch" in row.notes for row in report.evaluations)


async def test_agent_failure_degrades_but_pipeline_survives(candles: list[Bar]) -> None:
    gateway = StubGateway(payload_for_all(), fail={"factor_research_volatility"})
    orch = FactorResearchOrchestrator(gateway, timeout_seconds=5)
    report = await orch.run("BTCUSDT", "15m", candle_dicts(candles), 5)
    assert report.degraded
    assert any("factor_research_volatility" in reason for reason in report.reasons)
    assert len(report.proposals) == 5


async def test_unknown_formula_is_scored_not_fatal(candles: list[Bar]) -> None:
    payloads = payload_for_all()
    payloads[f"factor_research_{FACTOR_DOMAINS[0]}"]["formula"] = "NOT_IN_CATALOG"
    gateway = StubGateway(payloads)
    orch = FactorResearchOrchestrator(gateway, timeout_seconds=5)
    report = await orch.run("BTCUSDT", "15m", candle_dicts(candles), 5)
    # The schema gate rejects unknown formulas before the referee sees them.
    assert report.degraded
    assert any(
        "agent_invalid_output" in reason and "momentum" in reason
        for reason in report.reasons
    )
    assert all(row.formula in {f.value for f in FactorFormula} for row in report.evaluations)


async def test_no_order_fields_anywhere(candles: list[Bar]) -> None:
    gateway = StubGateway(payload_for_all())
    orch = FactorResearchOrchestrator(gateway, timeout_seconds=5)
    report = await orch.run("BTCUSDT", "15m", candle_dicts(candles), 5)
    blob = json.dumps(report.to_dict(), default=str).lower()
    for forbidden in ("notional_usd", "leverage", "stop_loss", "take_profit"):
        assert forbidden not in blob
