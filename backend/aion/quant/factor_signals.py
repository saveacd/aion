"""Deterministic live scoring for validated factor-library records.

This module never places orders. It converts active, previously validated
factor definitions into transparent current-bar signals for the LLM agents.
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import Any

from aion.models.factors import FactorRecord
from aion.quant.factor_values import DSL_FORMULA, factor_series, normalize_expression_value
from aion.quant.factors import FactorFormula


def _squash(value: float, scale: float) -> float:
    return math.tanh(value / scale)


def normalize_factor_value(formula: str, value: float) -> float:
    """Map heterogeneous formula units to a bounded [-1, 1] signal."""
    scales = {
        FactorFormula.MOMENTUM: 0.05,
        FactorFormula.MEAN_REVERSION_Z: 2.0,
        FactorFormula.VOLATILITY_RATIO: 0.5,
        FactorFormula.VOLUME_Z: 2.0,
        FactorFormula.RSI_DEVIATION: 20.0,
        FactorFormula.EMA_SLOPE: 0.02,
        FactorFormula.RANGE_POSITION: 0.25,
        FactorFormula.VOL_ADJUSTED_MOMENTUM: 3.0,
        FactorFormula.PRICE_VOLUME_CONFIRMATION: 0.03,
        FactorFormula.TREND_EFFICIENCY: 0.5,
        FactorFormula.BREAKOUT_PRESSURE: 0.5,
    }
    if formula == DSL_FORMULA:
        return normalize_expression_value(value)
    parsed = FactorFormula(formula)
    if parsed is FactorFormula.VOLATILITY_RATIO:
        value -= 1.0
    elif parsed is FactorFormula.RANGE_POSITION:
        value -= 0.5
    return _squash(value, scales[parsed])


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    size = min(len(left), len(right))
    if size < 12:
        return None
    a, b = list(left[-size:]), list(right[-size:])
    ma, mb = sum(a) / size, sum(b) / size
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    den = math.sqrt(va * vb)
    return cov / den if den > 1e-12 else None


def build_factor_signals(
    records: Sequence[FactorRecord], candles: Sequence[Any]
) -> dict[str, Any]:
    """Build regime-aware signals, penalizing redundant factors and exposing conflicts."""
    from aion.quant.factor_ensemble import detect_market_regime, regime_multiplier
    regime = detect_market_regime(candles)
    rows: list[dict[str, Any]] = []
    for record in records:
        try:
            if record.formula == DSL_FORMULA and (
                not record.discovery_state or dt.datetime.fromisoformat(record.discovery_state["active_until"]) < dt.datetime.now(dt.UTC)
            ):
                continue
            series = factor_series(record, candles)
            valid_history = [float(v) for v in series[-80:] if v is not None and math.isfinite(float(v))]
            raw = series[-1] if series else None
            if raw is None or not math.isfinite(float(raw)):
                continue
            normalized = normalize_factor_value(record.formula, float(raw))
            direction = str(record.direction).upper()
            if direction == "NEGATIVE":
                normalized = -normalized
                valid_history = [-normalize_factor_value(record.formula, v) for v in valid_history]
            elif direction == "POSITIVE":
                valid_history = [normalize_factor_value(record.formula, v) for v in valid_history]
            else:
                continue
            ic = abs(float(record.ic_mean or 0))
            stability = abs(float(record.ic_ir or 0))
            base_weight = min(1.0, max(0.0, ic / 0.10)) * min(1.0, max(0.0, stability / 1.0))
            multiplier = regime_multiplier(record.domain, record.formula, regime["label"])
            weight = min(1.0, base_weight * multiplier)
            if weight <= 0:
                continue
            rows.append({
                "factor_name": record.factor_name,
                "domain": record.domain,
                "formula": record.formula,
                "direction": direction,
                "raw_value": round(float(raw), 8),
                "normalized_signal": round(normalized, 6),
                "weight": round(weight, 6),
                "base_weight": round(base_weight, 6),
                "regime_multiplier": round(multiplier, 4),
                "ic_mean": float(record.ic_mean) if record.ic_mean is not None else None,
                "ic_ir": float(record.ic_ir) if record.ic_ir is not None else None,
                "lookback_bars": int(record.lookback_bars),
                "_history": valid_history,
            })
        except (ValueError, TypeError, OverflowError):
            continue

    correlations: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for i, row in enumerate(rows):
        redundancy: list[float] = []
        for other in rows[:i]:
            corr = _pearson(row["_history"], other["_history"])
            if corr is not None:
                correlations.append({
                    "factor_a": other["factor_name"], "factor_b": row["factor_name"],
                    "correlation": round(corr, 5),
                })
                redundancy.append(abs(corr))
            if row["normalized_signal"] * other["normalized_signal"] < 0:
                conflicts.append({
                    "factor_a": other["factor_name"], "factor_b": row["factor_name"],
                    "direction": "OPPOSING_CURRENT_SIGNALS",
                })
        mean_redundancy = sum(redundancy) / len(redundancy) if redundancy else 0.0
        penalty = max(0.2, 1.0 - 0.65 * mean_redundancy)
        row["correlation_penalty"] = round(penalty, 5)
        row["weight"] = round(row["weight"] * penalty, 6)

    for row in rows:
        row.pop("_history", None)

    weighted_total = sum(row["normalized_signal"] * row["weight"] for row in rows)
    weight_total = sum(row["weight"] for row in rows)
    composite = weighted_total / weight_total if weight_total > 0 else None
    if composite is None:
        bias = "NO_VALIDATED_FACTORS"
    elif composite >= 0.15:
        bias = "BULLISH"
    elif composite <= -0.15:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"
    return {
        "available": bool(rows),
        "market_regime": regime,
        "factor_count": len(rows),
        "composite_signal": round(composite, 6) if composite is not None else None,
        "bias": bias,
        "factors": rows,
        "correlations": correlations,
        "signal_conflicts": conflicts,
        "policy": "Research-validated signals are evidence only; they cannot bypass agent review, risk checks, or execution safeguards.",
    }
