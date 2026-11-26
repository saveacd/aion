"""Pydantic v2 output contracts for the six agents.

Every agent returns the shared :class:`AgentOutput` envelope plus its own
specialised fields. ``extra="forbid"`` makes the model's JSON contract strict:
inventing columns is a validation failure, not silently ignored.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Side = Literal["BUY", "SELL"]
PositionSide = Literal["LONG", "SHORT"]
OrderType = Literal["MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
TimeInForce = Literal["GTC", "IOC", "FOK", "GTX"]


class Regime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    UNCERTAIN = "UNCERTAIN"


class Decision(StrEnum):
    TRADE = "TRADE"
    NO_TRADE = "NO_TRADE"


class ReviewVerdict(StrEnum):
    PASS = "PASS"  # noqa: S105 - verdict label, not a credential
    CONCERN = "CONCERN"
    VETO = "VETO"


class AgentOutput(BaseModel):
    """Common envelope required from every agent (MASTER_SPEC §5.2)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_name: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1)
    timestamp: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    symbol: str = Field(min_length=3, max_length=32)
    interval: str = Field(min_length=1, max_length=8)
    input_refs: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    recommended_action: str = Field(min_length=1)


class MarketRegimeOutput(AgentOutput):
    regime: Regime


class TechnicalAnalysisOutput(AgentOutput):
    timeframe_structure: dict[str, str] = Field(default_factory=dict)
    indicator_readings: dict[str, Decimal] = Field(default_factory=dict)
    key_levels: list[Decimal] = Field(default_factory=list)
    #: Every cited value must map to a key that was actually provided as input.
    cited_inputs: list[str] = Field(min_length=1)


class DerivativesOutput(AgentOutput):
    funding_rate: Decimal | None = None
    funding_trend: str | None = None
    open_interest: Decimal | None = None
    open_interest_change_pct: Decimal | None = None
    basis_pct: Decimal | None = None
    data_missing: bool = False
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _missing_data_is_explicit(self) -> DerivativesOutput:
        absent = [
            name
            for name in ("funding_rate", "open_interest", "basis_pct")
            if getattr(self, name) is None
        ]
        if absent and not self.data_missing:
            raise ValueError(
                "data_missing must be true when required derivatives data is absent: "
                + ", ".join(absent)
            )
        if self.data_missing and not self.missing_fields:
            raise ValueError("missing_fields must list the absent data when data_missing is true")
        coverage = set(absent) - set(self.missing_fields)
        if coverage:
            raise ValueError("missing_fields must include: " + ", ".join(sorted(coverage)))
        return self


class ParameterRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: Decimal
    high: Decimal

    @model_validator(mode="after")
    def _ordered(self) -> ParameterRange:
        if self.high < self.low:
            raise ValueError("parameter range high must be >= low")
        return self


class StrategyResearchOutput(AgentOutput):
    """Research only: intentionally has no order-execution fields."""

    strategy_hypothesis: str = Field(min_length=1)
    parameter_ranges: dict[str, ParameterRange] = Field(default_factory=dict)
    invalidation_conditions: list[str] = Field(min_length=1)
    validation_plan: list[str] = Field(min_length=1)


class TradeIntentCandidate(BaseModel):
    """A *proposal* only. It is never persisted or sent to an exchange here."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Z0-9]{3,32}$")
    side: Side
    position_side: PositionSide
    order_type: OrderType = "MARKET"
    notional_usd: Decimal = Field(gt=0)
    leverage: int = Field(ge=1, le=20)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    time_in_force: TimeInForce = "GTC"
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class EntryPlanCandidate(BaseModel):
    """A resting entry idea: wait for price to reach a zone, then re-analyze."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Z0-9]{3,32}$")
    side: Side
    entry_low: Decimal = Field(gt=0)
    entry_high: Decimal = Field(gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    leverage: int = Field(ge=1, le=20)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    expires_in_minutes: int = Field(default=240, ge=5, le=1440)


class PositionAction(StrEnum):
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


class ChiefDecisionOutput(AgentOutput):
    decision: Decision
    position_action: PositionAction | None = None
    reason: str | None = None
    trade_intent: TradeIntentCandidate | None = None
    watch_plan: EntryPlanCandidate | None = None

    @model_validator(mode="after")
    def _decision_is_consistent(self) -> ChiefDecisionOutput:
        if self.decision is Decision.NO_TRADE and not (self.reason and self.reason.strip()):
            raise ValueError("NO_TRADE requires a reason")
        if self.decision is Decision.TRADE and self.trade_intent is None:
            raise ValueError("TRADE requires a trade_intent")
        if self.decision is Decision.TRADE and self.watch_plan is not None:
            raise ValueError("TRADE must not include a watch_plan")
        if self.watch_plan is not None:
            if self.watch_plan.entry_low > self.watch_plan.entry_high:
                raise ValueError("watch_plan entry_low must be <= entry_high")
            if self.watch_plan.symbol != self.symbol:
                raise ValueError("watch_plan symbol must match the analyzed symbol")
        return self


class AdversarialReviewOutput(AgentOutput):
    verdict: ReviewVerdict
    counter_evidence: list[str] = Field(default_factory=list)
    data_quality_issues: list[str] = Field(default_factory=list)
    overfitting_risks: list[str] = Field(default_factory=list)
    logic_gaps: list[str] = Field(default_factory=list)
    requires_rework: bool = False

    @model_validator(mode="after")
    def _veto_is_justified(self) -> AdversarialReviewOutput:
        if self.verdict is ReviewVerdict.VETO and not (
            self.counter_evidence or self.data_quality_issues or self.logic_gaps
        ):
            raise ValueError("VETO requires at least one concrete objection")
        return self


__all__ = [
    "AdversarialReviewOutput",
    "AgentOutput",
    "ChiefDecisionOutput",
    "Decision",
    "DerivativesOutput",
    "EntryPlanCandidate",
    "MarketRegimeOutput",
    "ParameterRange",
    "Regime",
    "ReviewVerdict",
    "StrategyResearchOutput",
    "TechnicalAnalysisOutput",
    "TradeIntentCandidate",
]
