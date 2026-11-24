"""Factor research pipeline: six LLM researchers, one deterministic referee.

Flow: the six researchers propose one factor each in parallel (each bound to
its own market domain) -> every proposal is scored by ``aion.quant.factors``
(Spearman IC on three contiguous time segments, hit rate, coverage) -> only
factors that pass the deterministic thresholds are accepted. Any agent
failure, timeout or schema violation degrades that researcher to "no
proposal"; it can never mark a factor accepted by itself.

The orchestrator returns a report; it never places orders and never writes
risk limits.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from aion.agents.base import AgentContext, BaseAgent, NullRunRecorder, RunRecorder
from aion.agents.factor_schemas import FactorProposalOutput
from aion.agents.orchestrator import _failure_reason  # shared degradation labels
from aion.agents.prompts import FACTOR_RESEARCHER_PROMPTS
from aion.gateway.gateway import ModelGateway
from aion.quant.factors import FORMULA_CATALOG, MIN_ABS_IC, FactorFormula, evaluate_factor

logger = logging.getLogger(__name__)

#: Domain -> agent name. Six researchers, one per domain.
FACTOR_DOMAINS: tuple[str, ...] = (
    "momentum",
    "mean_reversion",
    "volatility",
    "volume_liquidity",
    "price_structure",
    "regime",
)


def _researcher_class(domain: str) -> type[BaseAgent]:
    """Bind one domain to a subclass of BaseAgent (name/role are ClassVars)."""
    attrs = {
        "domain": domain,
        "name": f"factor_research_{domain}",
        "role": f"factor_research_{domain}",
        "output_model": FactorProposalOutput,
        "system_prompt": FACTOR_RESEARCHER_PROMPTS[domain],
        "max_tokens": 1200,
    }
    return type(f"FactorResearchAgent_{domain}", (BaseAgent,), attrs)


@dataclass(slots=True)
class FactorEvaluationRow:
    factor_name: str
    domain: str
    agent: str
    formula: str
    lookback_bars: int
    direction: str
    hypothesis: str
    ic_mean: float | None
    ic_ir: float | None
    hit_rate: float | None
    coverage: float
    accepted: bool
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FactorResearchReport:
    run_id: uuid.UUID
    symbol: str
    interval: str
    horizon_bars: int
    degraded: bool
    reasons: list[str]
    proposals: list[dict[str, Any]]
    evaluations: list[FactorEvaluationRow]
    accepted_count: int
    rejected_count: int
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "symbol": self.symbol,
            "interval": self.interval,
            "horizon_bars": self.horizon_bars,
            "degraded": self.degraded,
            "reasons": self.reasons,
            "proposals": self.proposals,
            "evaluations": [
                {
                    "factor_name": row.factor_name,
                    "domain": row.domain,
                    "agent": row.agent,
                    "formula": row.formula,
                    "lookback_bars": row.lookback_bars,
                    "direction": row.direction,
                    "hypothesis": row.hypothesis,
                    "ic_mean": row.ic_mean,
                    "ic_ir": row.ic_ir,
                    "hit_rate": row.hit_rate,
                    "coverage": row.coverage,
                    "accepted": row.accepted,
                    "notes": row.notes,
                }
                for row in self.evaluations
            ],
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "duration_ms": self.duration_ms,
        }


class FactorResearchOrchestrator:
    """Runs the six researchers and scores their proposals deterministically."""

    def __init__(
        self,
        gateway: ModelGateway,
        recorder: RunRecorder | None = None,
        *,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.gateway = gateway
        self.recorder = recorder or NullRunRecorder()
        self.timeout_seconds = timeout_seconds
        self.agents: dict[str, BaseAgent] = {
            domain: _researcher_class(domain)(gateway, self.recorder)
            for domain in FACTOR_DOMAINS
        }

    async def run(
        self,
        symbol: str,
        interval: str,
        candles_payload: list[dict[str, Any]],
        horizon_bars: int,
        *,
        user_id: uuid.UUID | None = None,
    ) -> FactorResearchReport:
        started = time.perf_counter()
        run_id = uuid.uuid4()
        await self.recorder.ensure_run(
            run_id=run_id,
            symbol=symbol,
            interval=interval,
            trigger="factor_research",
            user_id=user_id,
        )

        reasons: list[str] = []
        proposals: list[FactorProposalOutput] = []
        sem = asyncio.Semaphore(3)
        results = await asyncio.gather(
            *(
                self._run_one(domain, run_id, symbol, interval, candles_payload, horizon_bars, sem)
                for domain in FACTOR_DOMAINS
            ),
            return_exceptions=True,
        )
        for domain, result in zip(FACTOR_DOMAINS, results, strict=True):
            if isinstance(result, BaseException):
                reasons.append(_failure_reason(f"factor_research_{domain}", result))
            elif result is not None:
                proposals.append(result)

        # Deterministic referee: identical proposals collapse to one row.
        rows: list[FactorEvaluationRow] = []
        seen_names: set[str] = set()
        candles: list[dict[str, Any]] = candles_payload
        # Multiple-hypothesis correction (follow-up audit #11): more candidate
        # proposals in one round = higher chance of a lucky pass, so tighten
        # the strength gate by sqrt(log2(n)) of the unique candidate count.
        n_candidates = max(len({p.factor_name.upper() for p in proposals}), 1)
        correction = math.sqrt(max(1.0, math.log2(n_candidates)))
        effective_min_abs_ic = MIN_ABS_IC * correction
        for proposal in proposals:
            key = proposal.factor_name.upper()
            if key in seen_names:
                continue
            seen_names.add(key)
            stats = evaluate_factor(
                candles,
                FactorFormula(proposal.formula.value),
                proposal.lookback_bars,
                horizon_bars,
                direction=proposal.direction,
                min_abs_ic=effective_min_abs_ic,
            )
            direction_ok = (
                (stats.ic_mean is not None and stats.ic_mean > 0)
                == (proposal.direction == "POSITIVE")
            )
            accepted = stats.accepted and direction_ok
            notes = list(stats.notes)
            if stats.accepted and not direction_ok:
                notes.append("direction_mismatch")
            rows.append(
                FactorEvaluationRow(
                    factor_name=key,
                    domain=proposal.domain.value,
                    agent=proposal.agent_name,
                    formula=proposal.formula.value,
                    lookback_bars=proposal.lookback_bars,
                    direction=proposal.direction,
                    hypothesis=proposal.hypothesis,
                    ic_mean=stats.ic_mean,
                    ic_ir=stats.ic_ir,
                    hit_rate=stats.hit_rate,
                    coverage=stats.coverage,
                    accepted=accepted,
                    notes=notes,
                )
            )

        # Rank by stability STRENGTH (|ICIR|): a stable -0.8 factor is exactly
        # as strong as a stable +0.8 one; direction stays a separate field
        # (audit BUG-19 — signed ICIR buried negative factors at the bottom).
        rows.sort(key=lambda r: (not r.accepted, -abs(r.ic_ir or 0.0)))
        accepted_count = sum(1 for row in rows if row.accepted)
        verdict = "ACCEPTED" if accepted_count else "NO_FACTOR"
        duration_ms = int((time.perf_counter() - started) * 1000)
        summary = (
            f"factor research: {accepted_count}/{len(rows)} accepted "
            f"({len(FORMULA_CATALOG)} formulas available)"
        )
        await self.recorder.finish_run(
            run_id=run_id,
            verdict=verdict,
            confidence=None,
            summary=summary,
            duration_ms=duration_ms,
            error="; ".join(reasons) if reasons else None,
            status="DEGRADED" if reasons else "COMPLETED",
        )
        return FactorResearchReport(
            run_id=run_id,
            symbol=symbol,
            interval=interval,
            horizon_bars=horizon_bars,
            degraded=bool(reasons),
            reasons=reasons,
            proposals=[
                {
                    "agent": p.agent_name,
                    "domain": p.domain.value,
                    "factor_name": p.factor_name.upper(),
                    "hypothesis": p.hypothesis,
                    "lookback_bars": p.lookback_bars,
                    "direction": p.direction,
                }
                for p in proposals
            ],
            evaluations=rows,
            accepted_count=accepted_count,
            rejected_count=len(rows) - accepted_count,
            duration_ms=duration_ms,
        )

    async def _run_one(
        self,
        domain: str,
        run_id: uuid.UUID,
        symbol: str,
        interval: str,
        candles_payload: list[dict[str, Any]],
        horizon_bars: int,
        sem: asyncio.Semaphore,
    ) -> FactorProposalOutput | None:
        agent = self.agents[domain]
        ctx = AgentContext(
            symbol=symbol,
            interval=interval,
            run_id=run_id,
            inputs={
                "task": "factor_mining",
                "horizon_bars": horizon_bars,
                "formula_catalog": {f.value: desc for f, desc in FORMULA_CATALOG.items()},
                "candles": candles_payload,
            },
        )
        async with sem, asyncio.timeout(self.timeout_seconds):
            output = await agent.run(ctx)
            return output if isinstance(output, FactorProposalOutput) else None


__all__ = [
    "FACTOR_DOMAINS",
    "FactorEvaluationRow",
    "FactorResearchOrchestrator",
    "FactorResearchReport",
]
