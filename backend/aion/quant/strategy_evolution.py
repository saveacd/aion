"""Generate conservative, non-executing strategy research suggestions."""
from __future__ import annotations
from typing import Any


def build_strategy_evolution(factor_signals: dict[str, Any], regime: dict[str, Any], learning: dict[str, Any] | None = None) -> dict[str, Any]:
    signal = factor_signals.get("composite_signal")
    bias = factor_signals.get("bias", "NO_VALIDATED_FACTORS")
    label = regime.get("label", "UNKNOWN")
    suggestions: list[dict[str, str]] = []
    if signal is None or factor_signals.get("factor_count", 0) < 2:
        suggestions.append({"type": "DATA_GAP", "recommendation": "因子证据不足；维持观察，不提出策略参数变更。"})
    elif label == "HIGH_VOLATILITY":
        suggestions.append({"type": "RISK_REVIEW", "recommendation": "高波动状态：研究降低仓位或提高入场确认门槛；不得自动提高杠杆。"})
    elif label == "RANGING":
        suggestions.append({"type": "STRATEGY_RESEARCH", "recommendation": "震荡状态：优先回测均值回归与区间策略，避免追逐单边突破。"})
    elif label == "TRENDING":
        suggestions.append({"type": "STRATEGY_RESEARCH", "recommendation": "趋势状态：优先回测趋势跟随与突破确认策略，保留止损约束。"})
    if bias in {"BULLISH", "BEARISH"} and signal is not None and abs(float(signal)) >= 0.5:
        suggestions.append({"type": "SIGNAL_CONSENSUS", "recommendation": f"因子方向为 {bias} 且强度较高；交由六 AI 独立审查，不构成直接下单指令。"})

    # Mine historical PAPER outcomes by recorded regime. These are research
    # candidates only; small samples are explicitly marked inconclusive.
    regime_stats: dict[str, dict[str, float]] = {}
    for outcome in (learning or {}).get("recent_outcomes", []):
        outcome_regime = outcome.get("regime")
        r_value = outcome.get("r_multiple")
        if not outcome_regime or r_value is None:
            continue
        try:
            bucket = regime_stats.setdefault(str(outcome_regime), {"count": 0.0, "r_sum": 0.0})
            bucket["count"] += 1
            bucket["r_sum"] += float(r_value)
        except (TypeError, ValueError):
            continue
    for outcome_regime, stats in regime_stats.items():
        count = int(stats["count"])
        if count >= 5 and stats["r_sum"] / count < 0:
            suggestions.append({"type": "PERFORMANCE_REVIEW", "recommendation": f"历史 {outcome_regime} 环境下 {count} 笔近期结果的平均 R 为负；建议建立参数候选并进行独立样本外回测，不自动修改现有策略。"})

    # Evaluate a small, bounded strategy family every research cycle. The
    # current regime ranks candidates but does not suppress alternatives; this
    # creates a genuine parallel comparison instead of relabelling one backtest.
    candidates = [
        {"name": "trend_confirmation_candidate", "strategy_kind": "trend_confirmation",
         "entry_confirmation": "two_consecutive_completed_bars_agree",
         "applicable_market": "TRENDING",
         "evaluation_required": ["walk_forward", "fees_slippage", "funding", "volume_participation", "drawdown"]},
        {"name": "range_reversion_candidate", "strategy_kind": "range_reversion",
         "entry_confirmation": "contrarian_factor_consensus_threshold",
         "applicable_market": "RANGING",
         "evaluation_required": ["walk_forward", "fees_slippage", "funding", "volume_participation", "drawdown"]},
        {"name": "high_volatility_defensive_candidate", "strategy_kind": "high_volatility_defensive",
         "entry_confirmation": "high_consensus_threshold_reduced_notional",
         "applicable_market": "HIGH_VOLATILITY",
         "evaluation_required": ["walk_forward", "fees_slippage", "funding", "volume_participation", "drawdown"]},
    ]
    for candidate in candidates:
        candidate["regime_match"] = candidate["applicable_market"] == label
    return {"mode": "SHADOW_RESEARCH_ONLY", "regime": label, "suggestions": suggestions, "candidate_strategies": candidates, "historical_regime_stats": regime_stats, "learning_available": bool(learning and learning.get("total_outcomes")), "auto_parameter_changes": False, "auto_orders": False}
