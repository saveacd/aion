"""Orchestrator tests: degradation, veto and the no-execution guarantee."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("pydantic", reason="pydantic is required for agent schemas")

from aion.agents.base import AgentContext, NullRunRecorder  # noqa: E402
from aion.agents.orchestrator import Orchestrator  # noqa: E402
from aion.agents.schemas import TradeIntentCandidate  # noqa: E402
from aion.agents.specialists import (  # noqa: E402
    AdversarialReviewAgent,
    ChiefDecisionAgent,
    DerivativesAgent,
    MarketRegimeAgent,
    StrategyResearchAgent,
    TechnicalAnalysisAgent,
)
from aion.gateway.base import ModelResponse, ProviderError  # noqa: E402
from aion.gateway.budget import BudgetExceeded  # noqa: E402

D = Decimal

COMMON: dict[str, Any] = {
    "summary": "analysis ok",
    "evidence": ["provided_inputs"],
    "assumptions": [],
    "confidence": 0.8,
    "warnings": [],
    "recommended_action": "hold",
}

MARKET = {**COMMON, "regime": "TRENDING_UP"}
TECHNICAL = {
    **COMMON,
    "timeframe_structure": {"15m": "UP"},
    "indicator_readings": {"rsi": 55},
    "key_levels": [100],
    "cited_inputs": ["indicators"],
}
DERIVATIVES = {
    **COMMON,
    "funding_rate": 0.0001,
    "open_interest": 1000,
    "basis_pct": 0.01,
    "data_missing": False,
    "missing_fields": [],
}
STRATEGY = {
    **COMMON,
    "strategy_hypothesis": "trend following",
    "parameter_ranges": {"ema": {"low": 10, "high": 50}},
    "invalidation_conditions": ["close below support"],
    "validation_plan": ["backtest"],
}
CHIEF_TRADE = {
    **COMMON,
    "confidence": 0.85,
    "decision": "TRADE",
    "reason": "trend setup",
    "trade_intent": {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "position_side": "LONG",
        "order_type": "MARKET",
        "notional_usd": 100,
        "leverage": 3,
        "stop_loss": 95,
        "take_profit": 110,
        "time_in_force": "GTC",
        "confidence": 0.8,
        "reason": "trend",
        "evidence_refs": ["market_regime"],
    },
}
CHIEF_NO_TRADE = {**COMMON, "decision": "NO_TRADE", "reason": "signal conflict"}
ADVERSARIAL_PASS = {**COMMON, "verdict": "PASS"}
ADVERSARIAL_VETO = {**COMMON, "verdict": "VETO", "logic_gaps": ["look-ahead bias"]}


def all_payloads() -> dict[str, Any]:
    return {
        "market_regime": MARKET,
        "technical_analysis": TECHNICAL,
        "derivatives": DERIVATIVES,
        "strategy_research": STRATEGY,
        "chief_decision": CHIEF_TRADE,
        "adversarial_review": ADVERSARIAL_PASS,
    }


class StubGateway:
    """In-process gateway replacement. No HTTP, no database, no orders."""

    def __init__(
        self,
        payloads: dict[str, Any],
        *,
        fail: set[str] | None = None,
        budget_fail: set[str] | None = None,
        slow: set[str] | None = None,
    ) -> None:
        self.payloads = payloads
        self.fail = fail or set()
        self.budget_fail = budget_fail or set()
        self.slow = slow or set()
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
        if role in self.budget_fail:
            raise BudgetExceeded("daily", D("1"), D("2"))
        if role in self.fail:
            raise ProviderError("stub", "HTTP 500", retryable=True)
        if role in self.slow:
            await asyncio.sleep(30)
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


AGENTS = (
    MarketRegimeAgent,
    TechnicalAnalysisAgent,
    DerivativesAgent,
    StrategyResearchAgent,
    ChiefDecisionAgent,
    AdversarialReviewAgent,
)


def build(
    payloads: dict[str, Any],
    *,
    fail: set[str] | None = None,
    budget_fail: set[str] | None = None,
    slow: set[str] | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[Orchestrator, StubGateway]:
    gateway = StubGateway(payloads, fail=fail, budget_fail=budget_fail, slow=slow)
    agents = [cls(gateway) for cls in AGENTS]  # type: ignore[arg-type]
    settings = SimpleNamespace(agent_max_concurrency=3, agent_run_timeout_seconds=timeout_seconds)
    orchestrator = Orchestrator(
        gateway,  # type: ignore[arg-type]
        agents,
        recorder=NullRunRecorder(),
        settings=settings,
    )
    return orchestrator, gateway


def context() -> AgentContext:
    return AgentContext(
        symbol="BTCUSDT",
        interval="15m",
        run_id=uuid.uuid4(),
        inputs={"indicators": {"rsi": 55}, "candles": []},
    )


def test_full_success_returns_candidate_without_executing() -> None:
    orchestrator, gateway = build(all_payloads())
    before = {name for name in sys.modules if name.startswith("aion.execution")}

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "TRADE"
    assert result.degraded is False
    assert isinstance(result.trade_intent, TradeIntentCandidate)
    assert result.trade_intent.leverage == 3
    assert set(gateway.calls) == {
        "market_regime",
        "technical_analysis",
        "derivatives",
        "strategy_research",
        "chief_decision",
        "adversarial_review",
    }
    # The proposal is a plain schema object; no execution module was newly loaded
    # by this call. (Other tests may have imported the package already.)
    after = {name for name in sys.modules if name.startswith("aion.execution")}
    assert after == before


def test_agent_failure_degrades_to_no_trade() -> None:
    orchestrator, gateway = build(all_payloads(), fail={"derivatives"})

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "NO_TRADE"
    assert result.degraded is True
    assert result.trade_intent is None
    assert any(reason.startswith("agent_provider_error:derivatives") for reason in result.reasons)
    assert "strategy_research" not in gateway.calls


def test_adversarial_veto_forces_no_trade() -> None:
    payloads = all_payloads()
    payloads["adversarial_review"] = ADVERSARIAL_VETO
    orchestrator, _ = build(payloads)

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "NO_TRADE"
    assert result.degraded is False
    assert result.reasons == ["adversarial_veto"]
    assert result.adversarial_verdict == "VETO"
    assert result.trade_intent is None


def test_chief_no_trade_is_respected() -> None:
    payloads = all_payloads()
    payloads["chief_decision"] = CHIEF_NO_TRADE
    orchestrator, _ = build(payloads)

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "NO_TRADE"
    assert result.degraded is False
    assert result.reasons == ["signal conflict"]


def test_low_confidence_downgrades_to_no_trade() -> None:
    payloads = all_payloads()
    chief = json.loads(json.dumps(CHIEF_TRADE))
    chief["trade_intent"]["confidence"] = 0.4
    payloads["chief_decision"] = chief
    orchestrator, _ = build(payloads)

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "NO_TRADE"
    assert result.reasons == ["low_confidence"]
    assert result.trade_intent is None


def test_budget_exceeded_degrades_to_no_trade() -> None:
    orchestrator, _ = build(all_payloads(), budget_fail={"market_regime"})

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "NO_TRADE"
    assert result.degraded is True
    assert "budget_exceeded" in result.reasons


def test_agent_timeout_degrades_to_no_trade() -> None:
    orchestrator, _ = build(all_payloads(), slow={"technical_analysis"}, timeout_seconds=0.05)

    result = asyncio.run(orchestrator.analyze(context()))

    assert result.decision == "NO_TRADE"
    assert result.degraded is True
    assert "agent_timeout:technical_analysis" in result.reasons
