"""Deterministic multi-agent orchestration.

Flow: market regime / technical / derivatives in parallel -> strategy research
-> chief decision -> adversarial review. Any failure, timeout or budget
problem degrades to ``NO_TRADE``; nothing here can place an order.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from aion.agents.base import AgentContext, BaseAgent, NullRunRecorder, RunRecorder
from aion.agents.schemas import (
    AdversarialReviewOutput,
    AgentOutput,
    ChiefDecisionOutput,
    Decision,
    EntryPlanCandidate,
    ReviewVerdict,
    TradeIntentCandidate,
)
from aion.gateway.base import InvalidModelOutput, ProviderError
from aion.gateway.budget import BudgetExceeded
from aion.gateway.circuit import CircuitOpen
from aion.gateway.gateway import ModelGateway

logger = logging.getLogger(__name__)

FIRST_STAGE: tuple[str, ...] = ("market_regime", "technical_analysis", "derivatives")


@dataclass(slots=True)
class OrchestratorResult:
    run_id: uuid.UUID
    symbol: str
    interval: str
    decision: str
    degraded: bool
    reasons: list[str]
    outputs: dict[str, AgentOutput] = field(default_factory=dict)
    trade_intent: TradeIntentCandidate | None = None
    watch_plan: EntryPlanCandidate | None = None
    confidence: float | None = None
    adversarial_verdict: str | None = None

    @property
    def is_trade(self) -> bool:
        return self.decision == Decision.TRADE.value


class Orchestrator:
    """Runs one analysis cycle. Produces a proposal; never executes it."""

    def __init__(
        self,
        gateway: ModelGateway,
        agents: Mapping[str, BaseAgent] | Iterable[BaseAgent],
        *,
        recorder: RunRecorder | None = None,
        settings: Any | None = None,
        min_trade_confidence: float = 0.6,
    ) -> None:
        if isinstance(agents, Mapping):
            self.agents: dict[str, BaseAgent] = dict(agents)
        else:
            self.agents = {agent.name: agent for agent in agents}
        self.gateway = gateway
        self.recorder: RunRecorder = recorder or NullRunRecorder()
        self.max_concurrency = max(1, int(getattr(settings, "agent_max_concurrency", 3)))
        self.timeout_seconds = float(getattr(settings, "agent_run_timeout_seconds", 180))
        self.min_trade_confidence = min(1.0, max(0.0, float(min_trade_confidence)))

    async def analyze(self, ctx: AgentContext) -> OrchestratorResult:
        started = time.perf_counter()
        outputs: dict[str, AgentOutput] = {}
        await self.recorder.ensure_run(
            run_id=ctx.run_id, symbol=ctx.symbol, interval=ctx.interval
        )
        sem = asyncio.Semaphore(self.max_concurrency)

        reasons = await self._run_stage(FIRST_STAGE, ctx, sem, outputs)
        if reasons:
            return await self._finalize(
                ctx, outputs, Decision.NO_TRADE.value, True, reasons, started
            )

        reasons = await self._run_stage(
            ("strategy_research",), ctx.with_upstream(_dump(outputs)), sem, outputs
        )
        if reasons:
            return await self._finalize(
                ctx, outputs, Decision.NO_TRADE.value, True, reasons, started
            )

        reasons = await self._run_stage(
            ("chief_decision",), ctx.with_upstream(_dump(outputs)), sem, outputs
        )
        if reasons:
            return await self._finalize(
                ctx, outputs, Decision.NO_TRADE.value, True, reasons, started
            )

        reasons = await self._run_stage(
            ("adversarial_review",), ctx.with_upstream(_dump(outputs)), sem, outputs
        )
        if reasons:
            return await self._finalize(
                ctx, outputs, Decision.NO_TRADE.value, True, reasons, started
            )

        chief = cast(ChiefDecisionOutput, outputs["chief_decision"])
        review = cast(AdversarialReviewOutput, outputs["adversarial_review"])
        verdict = review.verdict.value

        if review.verdict is ReviewVerdict.VETO:
            return await self._finalize(
                ctx,
                outputs,
                Decision.NO_TRADE.value,
                False,
                ["adversarial_veto"],
                started,
                confidence=chief.confidence,
                adversarial_verdict=verdict,
            )

        if chief.decision is Decision.NO_TRADE:
            reason = (chief.reason or "chief_no_trade").strip()
            return await self._finalize(
                ctx,
                outputs,
                Decision.NO_TRADE.value,
                False,
                [reason],
                started,
                confidence=chief.confidence,
                adversarial_verdict=verdict,
                watch_plan=chief.watch_plan,
            )

        candidate = chief.trade_intent
        if candidate is None:
            return await self._finalize(
                ctx,
                outputs,
                Decision.NO_TRADE.value,
                True,
                ["chief_missing_intent"],
                started,
                adversarial_verdict=verdict,
            )

        confidence = min(chief.confidence, candidate.confidence)
        if confidence < self.min_trade_confidence:
            return await self._finalize(
                ctx,
                outputs,
                Decision.NO_TRADE.value,
                False,
                ["low_confidence"],
                started,
                confidence=confidence,
                adversarial_verdict=verdict,
            )

        # Schema-validated candidate only; it is returned, never persisted/executed.
        proposal = TradeIntentCandidate.model_validate(candidate.model_dump())
        return await self._finalize(
            ctx,
            outputs,
            Decision.TRADE.value,
            False,
            [],
            started,
            trade_intent=proposal,
            confidence=confidence,
            adversarial_verdict=verdict,
        )

    async def _run_stage(
        self,
        names: Sequence[str],
        ctx: AgentContext,
        sem: asyncio.Semaphore,
        outputs: dict[str, AgentOutput],
    ) -> list[str]:
        results = await asyncio.gather(
            *(self._run_one(name, ctx, sem) for name in names), return_exceptions=True
        )
        reasons: list[str] = []
        for name, result in zip(names, results, strict=True):
            if isinstance(result, BaseException):
                reasons.append(_failure_reason(name, result))
            else:
                outputs[name] = result
        return reasons

    async def _run_one(
        self, name: str, ctx: AgentContext, sem: asyncio.Semaphore
    ) -> AgentOutput:
        agent = self.agents.get(name)
        if agent is None:
            raise LookupError(f"agent not configured: {name}")
        async with sem, asyncio.timeout(self.timeout_seconds):
            return await agent.run(ctx)

    async def _finalize(
        self,
        ctx: AgentContext,
        outputs: dict[str, AgentOutput],
        decision: str,
        degraded: bool,
        reasons: Sequence[str],
        started: float,
        *,
        trade_intent: TradeIntentCandidate | None = None,
        watch_plan: EntryPlanCandidate | None = None,
        confidence: float | None = None,
        adversarial_verdict: str | None = None,
    ) -> OrchestratorResult:
        unique = list(dict.fromkeys(reasons))
        duration_ms = int((time.perf_counter() - started) * 1000)
        summary = _summary(decision, unique, trade_intent)
        input_tokens, output_tokens = _usage(self.gateway, ctx.run_id)
        cost = await self._run_cost(ctx.run_id)
        provider, model = _route_info(self.gateway)

        await self.recorder.finish_run(
            run_id=ctx.run_id,
            verdict=decision,
            confidence=confidence,
            summary=summary,
            duration_ms=duration_ms,
            error="; ".join(unique) if degraded and unique else None,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            status="DEGRADED" if degraded else "COMPLETED",
        )
        return OrchestratorResult(
            run_id=ctx.run_id,
            symbol=ctx.symbol,
            interval=ctx.interval,
            decision=decision,
            degraded=degraded,
            reasons=unique,
            outputs=outputs,
            trade_intent=trade_intent,
            watch_plan=watch_plan,
            confidence=confidence,
            adversarial_verdict=adversarial_verdict,
        )

    async def _run_cost(self, run_id: uuid.UUID) -> Decimal:
        ledger = getattr(self.gateway, "ledger", None)
        if ledger is None:
            return Decimal("0")
        try:
            return await ledger.run_spent(str(run_id))
        except Exception as exc:  # noqa: BLE001 - cost reporting must not break analysis
            logger.warning("failed to read run budget (%s)", type(exc).__name__)
            return Decimal("0")


def _failure_reason(name: str, exc: BaseException) -> str:
    if isinstance(exc, BudgetExceeded):
        return "budget_exceeded"
    if isinstance(exc, TimeoutError):
        return f"agent_timeout:{name}"
    if isinstance(exc, InvalidModelOutput):
        return f"agent_invalid_output:{name}"
    if isinstance(exc, CircuitOpen):
        return f"agent_circuit_open:{name}"
    if isinstance(exc, ProviderError):
        return f"agent_provider_error:{name}"
    return f"agent_failed:{name}"


def _dump(outputs: Mapping[str, AgentOutput]) -> dict[str, Any]:
    return {name: output.model_dump(mode="json") for name, output in outputs.items()}


def _summary(
    decision: str, reasons: Sequence[str], candidate: TradeIntentCandidate | None
) -> str:
    if decision == Decision.TRADE.value and candidate is not None:
        return (
            f"TRADE {candidate.symbol} {candidate.side} {candidate.position_side} "
            f"notional={candidate.notional_usd} leverage={candidate.leverage}"
        )
    detail = "; ".join(reasons) if reasons else "no actionable setup"
    return f"NO_TRADE: {detail}"[:1000]


def _usage(gateway: ModelGateway, run_id: uuid.UUID) -> tuple[int, int]:
    reader = getattr(gateway, "run_usage", None)
    if reader is None:
        return (0, 0)
    value = reader(run_id)
    if isinstance(value, tuple) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    return (0, 0)


def _route_info(gateway: ModelGateway) -> tuple[str | None, str | None]:
    routes = getattr(gateway, "routes", None)
    if not isinstance(routes, Mapping) or not routes:
        return (None, None)
    first = next(iter(routes.values()))
    return (getattr(first, "provider", None), getattr(first, "model", None))


__all__ = ["FIRST_STAGE", "Orchestrator", "OrchestratorResult"]
