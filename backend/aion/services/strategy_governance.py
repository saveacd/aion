"""Hard approval gates for strategy version promotion."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def is_independent_reviewer(owner_id: Any, reviewer_id: Any, reviewer_is_admin: bool) -> bool:
    """Only an administrator distinct from the strategy owner may approve."""
    return bool(reviewer_is_admin and owner_id is not None and reviewer_id is not None and owner_id != reviewer_id)


def validate_strategy_approval(assumptions: dict[str, Any], policy: Any) -> dict[str, Any]:
    """Validate actual PAPER/OOS evidence against the owner's active risk policy."""
    failures: list[str] = []

    def number(key: str) -> Decimal | None:
        try:
            return Decimal(str(assumptions[key]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            failures.append(f"MISSING_OR_INVALID_{key.upper()}")
            return None

    leverage = number("max_leverage")
    notional = number("max_position_notional_usd")
    max_dd = number("max_drawdown_pct")
    stop_loss = number("stop_loss_pct")
    paper_dd = number("paper_max_drawdown_pct")
    observation_days = number("paper_observation_days")
    paper_trades = number("paper_trade_count")

    if leverage is not None and leverage > Decimal(policy.max_leverage):
        failures.append("LEVERAGE_EXCEEDS_USER_POLICY")
    if notional is not None and notional > Decimal(policy.max_position_notional_usd):
        failures.append("POSITION_NOTIONAL_EXCEEDS_USER_POLICY")
    if max_dd is not None and max_dd > Decimal(policy.max_drawdown_pct):
        failures.append("STRATEGY_DRAWDOWN_EXCEEDS_USER_POLICY")
    if stop_loss is not None and (stop_loss <= 0 or stop_loss > Decimal(policy.max_drawdown_pct)):
        failures.append("STOP_LOSS_OUTSIDE_USER_POLICY")
    if paper_dd is not None and paper_dd > Decimal(policy.max_drawdown_pct):
        failures.append("PAPER_DRAWDOWN_EXCEEDS_USER_POLICY")
    if observation_days is not None and observation_days < 14:
        failures.append("PAPER_OBSERVATION_TOO_SHORT")
    if paper_trades is not None and paper_trades < 30:
        failures.append("PAPER_SAMPLE_TOO_SMALL")

    for key in ("oos_passed", "paper_pnl_positive", "funding_costs_included", "slippage_costs_included"):
        if assumptions.get(key) is not True:
            failures.append(f"REQUIRED_EVIDENCE_NOT_CONFIRMED_{key.upper()}")

    return {
        "passed": not failures,
        "failures": failures,
        "checked_against_user_policy": {
            "max_leverage": str(policy.max_leverage),
            "max_position_notional_usd": str(policy.max_position_notional_usd),
            "max_drawdown_pct": str(policy.max_drawdown_pct),
        },
        "evidence": {
            key: assumptions.get(key) for key in (
                "oos_passed", "paper_pnl_positive", "funding_costs_included",
                "slippage_costs_included", "paper_observation_days", "paper_trade_count",
                "paper_max_drawdown_pct",
            )
        },
    }
