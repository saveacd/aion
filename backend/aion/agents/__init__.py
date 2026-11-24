"""Six AI agents plus the deterministic orchestrator."""

from __future__ import annotations

from aion.agents.base import (
    AgentContext,
    BaseAgent,
    DbRunRecorder,
    NullRunRecorder,
    RunRecorder,
)
from aion.agents.orchestrator import Orchestrator, OrchestratorResult
from aion.agents.schemas import (
    AdversarialReviewOutput,
    AgentOutput,
    ChiefDecisionOutput,
    Decision,
    DerivativesOutput,
    MarketRegimeOutput,
    ParameterRange,
    Regime,
    ReviewVerdict,
    StrategyResearchOutput,
    TechnicalAnalysisOutput,
    TradeIntentCandidate,
)
from aion.agents.specialists import (
    AGENT_CLASSES,
    AdversarialReviewAgent,
    ChiefDecisionAgent,
    DerivativesAgent,
    MarketRegimeAgent,
    StrategyResearchAgent,
    TechnicalAnalysisAgent,
)

__all__ = [
    "AGENT_CLASSES",
    "AdversarialReviewAgent",
    "AdversarialReviewOutput",
    "AgentContext",
    "AgentOutput",
    "BaseAgent",
    "ChiefDecisionAgent",
    "ChiefDecisionOutput",
    "DbRunRecorder",
    "Decision",
    "DerivativesAgent",
    "DerivativesOutput",
    "MarketRegimeAgent",
    "MarketRegimeOutput",
    "NullRunRecorder",
    "Orchestrator",
    "OrchestratorResult",
    "ParameterRange",
    "Regime",
    "ReviewVerdict",
    "RunRecorder",
    "StrategyResearchAgent",
    "StrategyResearchOutput",
    "TechnicalAnalysisAgent",
    "TechnicalAnalysisOutput",
    "TradeIntentCandidate",
]
