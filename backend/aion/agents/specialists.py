"""The six specialised agents. Each is a thin prompt/contract binding."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from aion.agents.base import AgentContext, BaseAgent, cited_inputs_are_known
from aion.agents.prompts import (
    ADVERSARIAL_REVIEW_PROMPT,
    CHIEF_DECISION_PROMPT,
    DERIVATIVES_PROMPT,
    MARKET_REGIME_PROMPT,
    STRATEGY_RESEARCH_PROMPT,
    TECHNICAL_ANALYSIS_PROMPT,
)
from aion.agents.schemas import (
    AdversarialReviewOutput,
    ChiefDecisionOutput,
    DerivativesOutput,
    MarketRegimeOutput,
    StrategyResearchOutput,
    TechnicalAnalysisOutput,
)


class MarketRegimeAgent(BaseAgent):
    name = "market_regime"
    role = "market_regime"
    output_model = MarketRegimeOutput
    system_prompt = MARKET_REGIME_PROMPT
    max_tokens = 800


class TechnicalAnalysisAgent(BaseAgent):
    name = "technical_analysis"
    role = "technical_analysis"
    output_model = TechnicalAnalysisOutput
    system_prompt = TECHNICAL_ANALYSIS_PROMPT
    max_tokens = 1700

    def post_validate(self, output: BaseModel, ctx: AgentContext) -> list[str]:
        parsed = cast(TechnicalAnalysisOutput, output)
        unknown = cited_inputs_are_known(parsed.cited_inputs, ctx)
        return [f"cited input not provided: {item}" for item in unknown[:5]]


class DerivativesAgent(BaseAgent):
    name = "derivatives"
    role = "derivatives"
    output_model = DerivativesOutput
    system_prompt = DERIVATIVES_PROMPT
    max_tokens = 800


class StrategyResearchAgent(BaseAgent):
    name = "strategy_research"
    role = "strategy_research"
    output_model = StrategyResearchOutput
    system_prompt = STRATEGY_RESEARCH_PROMPT
    max_tokens = 1600


class ChiefDecisionAgent(BaseAgent):
    name = "chief_decision"
    role = "chief_decision"
    output_model = ChiefDecisionOutput
    system_prompt = CHIEF_DECISION_PROMPT
    max_tokens = 1800


class AdversarialReviewAgent(BaseAgent):
    name = "adversarial_review"
    role = "adversarial_review"
    output_model = AdversarialReviewOutput
    system_prompt = ADVERSARIAL_REVIEW_PROMPT
    max_tokens = 1200


AGENT_CLASSES = (
    MarketRegimeAgent,
    TechnicalAnalysisAgent,
    DerivativesAgent,
    StrategyResearchAgent,
    ChiefDecisionAgent,
    AdversarialReviewAgent,
)

__all__ = [
    "AGENT_CLASSES",
    "AdversarialReviewAgent",
    "ChiefDecisionAgent",
    "DerivativesAgent",
    "MarketRegimeAgent",
    "StrategyResearchAgent",
    "TechnicalAnalysisAgent",
]
