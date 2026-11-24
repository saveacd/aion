"""Regime-aware, bounded factor ensemble weights.

This is a transparent baseline, not a learned trading policy. It only adjusts
research weights for context; it cannot create orders or alter risk limits.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def detect_market_regime(candles: Sequence[Any]) -> dict[str, Any]:
    closes = [float(getattr(c, "close", c.get("close", c.get("c")) if isinstance(c, dict) else 0)) for c in candles]
    if len(closes) < 30 or any(v <= 0 for v in closes[-30:]):
        return {"label": "UNKNOWN", "trend_strength": None, "volatility": None, "confidence": 0.0}
    window = closes[-30:]
    net = window[-1] / window[0] - 1.0
    changes = [(b / a) - 1.0 for a, b in zip(window, window[1:]) if a]
    mean = sum(changes) / len(changes)
    vol = math.sqrt(sum((x - mean) ** 2 for x in changes) / max(1, len(changes) - 1))
    short = changes[-10:]
    short_mean = sum(short) / len(short)
    short_vol = math.sqrt(sum((x - short_mean) ** 2 for x in short) / max(1, len(short) - 1))
    vol_expansion = short_vol / vol if vol > 1e-12 else 1.0
    path = sum(abs(x) for x in changes)
    efficiency = abs(net) / path if path > 1e-12 else 0.0
    if vol_expansion >= 1.5 and short_vol > 1e-8:
        label = "HIGH_VOLATILITY"
    elif efficiency >= 0.45 and abs(net) >= 0.015:
        label = "TRENDING"
    else:
        label = "RANGING"
    return {"label": label, "trend_strength": round(efficiency, 4), "volatility": round(vol, 6), "volatility_expansion": round(vol_expansion, 4), "confidence": round(min(1.0, max(efficiency, min(1.0, vol_expansion - 1.0))), 3)}


def regime_multiplier(domain: str, formula: str, regime: str) -> float:
    domain = str(domain).lower()
    formula = str(formula).upper()
    multipliers = {
        "TRENDING": {"momentum": 1.25, "price_structure": 1.2, "mean_reversion": 0.7, "volatility": 0.9},
        "RANGING": {"mean_reversion": 1.25, "momentum": 0.75, "price_structure": 0.9, "volatility": 1.0},
        "HIGH_VOLATILITY": {"volatility": 1.2, "volume_liquidity": 1.15, "momentum": 0.8, "mean_reversion": 0.8},
    }
    base = multipliers.get(regime, {}).get(domain, 1.0)
    if regime == "TRENDING" and formula in {"TREND_EFFICIENCY", "BREAKOUT_PRESSURE", "VOL_ADJUSTED_MOMENTUM"}:
        base *= 1.1
    if regime == "HIGH_VOLATILITY" and formula == "VOL_ADJUSTED_MOMENTUM":
        base *= 1.1
    return min(1.5, max(0.5, base))
