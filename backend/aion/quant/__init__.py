"""AION quant engine — deterministic calculations only.

No database, no network, no LLM. Everything here is a pure function or a
dataclass, so results are reproducible and unit-testable.
"""

from __future__ import annotations

from aion.quant.backtest import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionState,
    Signal,
    StrategyLike,
    Trade,
    run_backtest,
)
from aion.quant.candles import (
    Candle,
    Gap,
    IssueCode,
    ValidationIssue,
    aggregate,
    detect_gaps,
    validate_candles,
)
from aion.quant.indicators import (
    atr,
    bollinger,
    crosses_above,
    crosses_below,
    ema,
    macd,
    rsi,
    sma,
    stdev,
    true_range,
)
from aion.quant.metrics import (
    annualized_volatility,
    calmar,
    expectancy,
    max_drawdown,
    profit_factor,
    returns_from_equity,
    sharpe,
    sortino,
    total_return,
    win_rate,
)
from aion.quant.sizing import (
    fee_estimate,
    margin_required,
    qty_from_notional,
    qty_from_risk,
    round_step,
    round_tick,
    validate_order,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Candle",
    "EquityPoint",
    "Gap",
    "IssueCode",
    "PositionState",
    "Signal",
    "StrategyLike",
    "Trade",
    "ValidationIssue",
    "aggregate",
    "annualized_volatility",
    "atr",
    "bollinger",
    "calmar",
    "crosses_above",
    "crosses_below",
    "detect_gaps",
    "ema",
    "expectancy",
    "fee_estimate",
    "macd",
    "margin_required",
    "max_drawdown",
    "profit_factor",
    "qty_from_notional",
    "qty_from_risk",
    "returns_from_equity",
    "round_step",
    "round_tick",
    "rsi",
    "run_backtest",
    "sharpe",
    "sma",
    "sortino",
    "stdev",
    "total_return",
    "true_range",
    "validate_candles",
    "validate_order",
    "win_rate",
]
