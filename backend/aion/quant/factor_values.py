"""Evaluate catalog factors and discovered expressions through the same consumers."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import Any

from aion.quant.factor_discovery import evaluate_expression, parse_expression
from aion.quant.factors import FactorFormula, compute_series

DSL_FORMULA = "FACTOR_DSL"


def factor_series(record: Any, candles: Sequence) -> list:
    if record.formula != DSL_FORMULA:
        return compute_series(FactorFormula(record.formula), candles, int(record.lookback_bars))
    state = record.discovery_state or {}
    if record.status != "ACTIVE" or not state.get("active_since"):
        return [None] * len(candles)

    active_since = dt.datetime.fromisoformat(state["active_since"])
    active_until = dt.datetime.fromisoformat(state["active_until"])
    values = evaluate_expression(parse_expression(record.expression), candles)
    return [
        value if active_since <= candle.close_time <= active_until else None
        for value, candle in zip(values, candles, strict=True)
    ]


def normalize_expression_value(value: float) -> float:
    return math.tanh(value)
