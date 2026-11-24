"""Schema contract tests for the six agent outputs."""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic", reason="pydantic is required for agent schemas")

from pydantic import ValidationError  # noqa: E402

from aion.agents.schemas import (  # noqa: E402
    AdversarialReviewOutput,
    ChiefDecisionOutput,
    DerivativesOutput,
    MarketRegimeOutput,
    StrategyResearchOutput,
    TechnicalAnalysisOutput,
)

BASE = {
    "agent_name": "test",
    "run_id": "run-1",
    "symbol": "BTCUSDT",
    "interval": "15m",
    "summary": "summary",
    "confidence": 0.7,
    "recommended_action": "watch",
}


def payload(**extra: object) -> dict[str, object]:
    return {**BASE, **extra}


def trade_intent(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
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
    }
    base.update(overrides)
    return base


def test_market_regime_accepts_valid_enum() -> None:
    output = MarketRegimeOutput.model_validate(payload(regime="TRENDING_UP"))
    assert output.regime.value == "TRENDING_UP"


def test_market_regime_rejects_unknown_enum() -> None:
    with pytest.raises(ValidationError):
        MarketRegimeOutput.model_validate(payload(regime="MOON"))


def test_technical_analysis_requires_citations() -> None:
    with pytest.raises(ValidationError):
        TechnicalAnalysisOutput.model_validate(
            payload(indicator_readings={"rsi": 55}, cited_inputs=[])
        )

    output = TechnicalAnalysisOutput.model_validate(
        payload(indicator_readings={"rsi": 55}, cited_inputs=["indicators"])
    )
    assert output.cited_inputs == ["indicators"]


def test_derivatives_missing_data_must_be_flagged() -> None:
    with pytest.raises(ValidationError):
        DerivativesOutput.model_validate(
            payload(funding_rate=None, open_interest=100, basis_pct=0.01)
        )

    with pytest.raises(ValidationError):
        DerivativesOutput.model_validate(
            payload(funding_rate=None, open_interest=100, basis_pct=0.01, data_missing=True)
        )

    output = DerivativesOutput.model_validate(
        payload(
            funding_rate=None,
            open_interest=100,
            basis_pct=0.01,
            data_missing=True,
            missing_fields=["funding_rate"],
        )
    )
    assert output.data_missing is True


def test_strategy_output_has_no_order_fields() -> None:
    forbidden = {
        "side",
        "position_side",
        "quantity",
        "notional_usd",
        "leverage",
        "order_type",
        "stop_loss",
        "take_profit",
        "time_in_force",
    }
    assert forbidden.isdisjoint(StrategyResearchOutput.model_fields)


def test_strategy_output_validates_parameter_ranges() -> None:
    valid = payload(
        strategy_hypothesis="trend following",
        parameter_ranges={"ema": {"low": 10, "high": 50}},
        invalidation_conditions=["close below support"],
        validation_plan=["backtest in sample", "walk forward"],
    )
    output = StrategyResearchOutput.model_validate(valid)
    assert output.parameter_ranges["ema"].high == 50

    invalid = {**valid, "parameter_ranges": {"ema": {"low": 50, "high": 10}}}
    with pytest.raises(ValidationError):
        StrategyResearchOutput.model_validate(invalid)


def test_no_trade_requires_reason() -> None:
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(payload(decision="NO_TRADE"))

    output = ChiefDecisionOutput.model_validate(
        payload(decision="NO_TRADE", reason="signal conflict")
    )
    assert output.decision.value == "NO_TRADE"
    assert output.reason == "signal conflict"


def test_trade_requires_intent() -> None:
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(payload(decision="TRADE", reason="ok"))


def test_trade_intent_leverage_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(
            payload(decision="TRADE", reason="ok", trade_intent=trade_intent(leverage=0))
        )

    output = ChiefDecisionOutput.model_validate(
        payload(decision="TRADE", reason="ok", trade_intent=trade_intent(leverage=1))
    )
    assert output.trade_intent is not None
    assert output.trade_intent.leverage == 1


def test_trade_intent_leverage_absolute_cap() -> None:
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(
            payload(decision="TRADE", reason="ok", trade_intent=trade_intent(leverage=21))
        )


def test_veto_requires_concrete_objection() -> None:
    with pytest.raises(ValidationError):
        AdversarialReviewOutput.model_validate(payload(verdict="VETO"))

    output = AdversarialReviewOutput.model_validate(
        payload(verdict="VETO", logic_gaps=["look-ahead bias"])
    )
    assert output.verdict.value == "VETO"


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        MarketRegimeOutput.model_validate(payload(regime="RANGING", unverified=1))
