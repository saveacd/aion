from decimal import Decimal
from types import SimpleNamespace

from aion.services.strategy_governance import validate_strategy_approval


def policy():
    return SimpleNamespace(max_leverage=3, max_position_notional_usd=Decimal("1000"), max_drawdown_pct=Decimal("15"))


def good_evidence():
    return {
        "max_leverage": 2, "max_position_notional_usd": 500, "max_drawdown_pct": 10,
        "stop_loss_pct": 5, "paper_max_drawdown_pct": 8, "paper_observation_days": 21,
        "paper_trade_count": 40, "oos_passed": True, "paper_pnl_positive": True,
        "funding_costs_included": True, "slippage_costs_included": True,
    }


def test_strategy_approval_requires_and_accepts_complete_evidence():
    result = validate_strategy_approval(good_evidence(), policy())
    assert result["passed"] is True
    assert result["failures"] == []


def test_strategy_approval_rejects_risk_policy_breaches_and_missing_paper_data():
    evidence = good_evidence()
    evidence.update(max_leverage=10, max_position_notional_usd=5000, paper_observation_days=2,
                    paper_trade_count=3, funding_costs_included=False)
    result = validate_strategy_approval(evidence, policy())
    assert result["passed"] is False
    assert "LEVERAGE_EXCEEDS_USER_POLICY" in result["failures"]
    assert "POSITION_NOTIONAL_EXCEEDS_USER_POLICY" in result["failures"]
    assert "PAPER_OBSERVATION_TOO_SHORT" in result["failures"]
    assert "PAPER_SAMPLE_TOO_SMALL" in result["failures"]
    assert "REQUIRED_EVIDENCE_NOT_CONFIRMED_FUNDING_COSTS_INCLUDED" in result["failures"]


def test_missing_evidence_fails_closed():
    result = validate_strategy_approval({}, policy())
    assert result["passed"] is False
    assert len(result["failures"]) >= 10



def test_approval_requires_an_independent_administrator_reviewer():
    from uuid import uuid4
    from aion.services.strategy_governance import is_independent_reviewer

    owner, reviewer, second_reviewer = uuid4(), uuid4(), uuid4()
    assert is_independent_reviewer(owner, reviewer, True) is True
    assert is_independent_reviewer(owner, owner, True) is False
    assert is_independent_reviewer(owner, second_reviewer, False) is False
