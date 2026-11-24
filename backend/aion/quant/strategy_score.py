"""Conservative strategy evaluation gates for research candidates.

This module only scores persisted, completed backtest evidence. It does not
approve, publish, or execute strategies. Small samples are shrunk toward a
neutral score and cannot pass the promotion gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategyGate:
    min_trades: int = 30
    min_oos_trades: int = 15
    max_drawdown_pct: float = 25.0
    min_profit_factor: float = 1.05
    min_sharpe: float = 0.25
    min_oos_return: float = 0.0
    max_consecutive_losses: int = 5


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def evaluate_strategy(
    in_sample: dict[str, Any],
    out_of_sample: dict[str, Any],
    *,
    gate: StrategyGate | None = None,
) -> dict[str, Any]:
    """Return a sample-aware score and explicit non-execution decision."""
    policy = gate or StrategyGate()
    is_n = int(in_sample.get("trade_count") or 0)
    oos_n = int(out_of_sample.get("trade_count") or 0)
    total_n = is_n + oos_n
    metrics = {
        "total_return": _finite_number(out_of_sample.get("total_return")),
        "annualized_return": _finite_number(out_of_sample.get("annualized_return")),
        "max_drawdown_pct": _finite_number(out_of_sample.get("max_drawdown_pct")),
        "sharpe": _finite_number(out_of_sample.get("sharpe")),
        "sortino": _finite_number(out_of_sample.get("sortino")),
        "win_rate": _finite_number(out_of_sample.get("win_rate")),
        "profit_factor": _finite_number(out_of_sample.get("profit_factor")),
        "risk_adjusted_return": _finite_number(out_of_sample.get("calmar")),
        "max_consecutive_losses": _finite_number(out_of_sample.get("max_consecutive_losses")),
    }
    # Bound every component to avoid one pathological metric dominating.
    def clamp(value: float | None, low: float, high: float) -> float:
        return max(low, min(high, value if value is not None else 0.0))

    components = {
        "return": clamp(metrics["total_return"], -1.0, 1.0),
        "sharpe": clamp(metrics["sharpe"], -3.0, 3.0) / 3.0,
        "sortino": clamp(metrics["sortino"], -3.0, 3.0) / 3.0,
        "profit_factor": (clamp(metrics["profit_factor"], 0.0, 3.0) - 1.0) / 2.0,
        "drawdown": -clamp(metrics["max_drawdown_pct"], 0.0, 100.0) / 100.0,
        "win_rate": clamp(metrics["win_rate"], 0.0, 1.0) * 2.0 - 1.0,
    }
    raw_score = (
        0.20 * components["return"] + 0.20 * components["sharpe"]
        + 0.15 * components["sortino"] + 0.20 * components["profit_factor"]
        + 0.15 * components["drawdown"] + 0.10 * components["win_rate"]
    )
    # Bayesian-style reliability shrinkage: fewer observations -> closer to 0.
    reliability = min(1.0, total_n / max(policy.min_trades + policy.min_oos_trades, 1))
    score = raw_score * reliability
    failures: list[str] = []
    if is_n < policy.min_trades:
        failures.append("INSUFFICIENT_IN_SAMPLE_TRADES")
    if oos_n < policy.min_oos_trades:
        failures.append("INSUFFICIENT_OUT_OF_SAMPLE_TRADES")
    if metrics["max_drawdown_pct"] is None or metrics["max_drawdown_pct"] > policy.max_drawdown_pct:
        failures.append("DRAWDOWN_GATE_FAILED")
    if metrics["profit_factor"] is None or metrics["profit_factor"] < policy.min_profit_factor:
        failures.append("PROFIT_FACTOR_GATE_FAILED")
    if metrics["sharpe"] is None or metrics["sharpe"] < policy.min_sharpe:
        failures.append("SHARPE_GATE_FAILED")
    if metrics["total_return"] is None or metrics["total_return"] <= policy.min_oos_return:
        failures.append("OUT_OF_SAMPLE_RETURN_GATE_FAILED")
    if metrics["max_consecutive_losses"] is None or metrics["max_consecutive_losses"] > policy.max_consecutive_losses:
        failures.append("CONSECUTIVE_LOSS_GATE_FAILED")
    passed = not failures
    return {
        "score": round(score, 6),
        "raw_score": round(raw_score, 6),
        "sample_reliability": round(reliability, 6),
        "in_sample_trade_count": is_n,
        "out_of_sample_trade_count": oos_n,
        "total_trade_count": total_n,
        "metrics": metrics,
        "components": {key: round(value, 6) for key, value in components.items()},
        "gate": "PAPER_ELIGIBLE" if passed else "RESEARCH_ONLY",
        "passed": passed,
        "failures": failures,
        "execution_enabled": False,
        "human_approval_required": True,
    }


def compare_strategies(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable score-ranked comparison; never infer execution permission."""
    rows = []
    for name, result in results.items():
        score = _finite_number(result.get("score")) or 0.0
        rows.append({"name": name, "score": score, "passed": bool(result.get("passed")),
                     "gate": result.get("gate", "RESEARCH_ONLY"),
                     "trade_count": int(result.get("total_trade_count") or 0)})
    return sorted(rows, key=lambda row: (row["passed"], row["score"], row["trade_count"]), reverse=True)
