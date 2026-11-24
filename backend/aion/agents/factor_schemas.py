"""Output contracts for the six factor-researcher agents.

Researchers may only propose factors from the deterministic catalogue
(``aion.quant.factors``); anything else is a validation failure. They never
output order-execution fields — a factor proposal is research, not a trade.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from aion.agents.schemas import AgentOutput
from aion.quant.factors import FactorFormula


class FactorDomain(StrEnum):
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    VOLATILITY = "volatility"
    VOLUME_LIQUIDITY = "volume_liquidity"
    PRICE_STRUCTURE = "price_structure"
    REGIME = "regime"


FactorDirection = Literal["POSITIVE", "NEGATIVE"]


class FactorProposalOutput(AgentOutput):
    """One factor hypothesis proposed by one researcher."""

    domain: FactorDomain
    factor_name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
    formula: FactorFormula
    lookback_bars: int = Field(ge=2, le=200)
    direction: FactorDirection
    hypothesis: str = Field(min_length=8)
    #: Why the researcher believes this factor has predictive power.
    rationale: str = Field(min_length=4)
    #: Conditions under which the researcher expects the factor to fail.
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=4)


__all__ = ["FactorDirection", "FactorDomain", "FactorProposalOutput"]
