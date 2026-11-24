"""Performance metrics for equity curves and trade lists.

Return types are explicit because the unit matters:

* ``returns_from_equity`` → ``list[Decimal]`` (simple period returns)
* ``total_return`` / ``max_drawdown`` → ``Decimal`` (drawdown is a positive
  magnitude in percent, e.g. ``20`` means ``-20%``)
* ``annualized_volatility`` / ``sharpe`` / ``sortino`` / ``calmar`` /
  ``profit_factor`` / ``win_rate`` → ``float``
* ``expectancy`` → ``Decimal`` (quote currency per trade)

Insufficient samples yield ``None`` rather than raising: a metric that cannot
be computed is a fact the caller must handle, not an error to hide.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

D = Decimal
type Number = Decimal | float | int


class _HasPnl(Protocol):
    pnl: Decimal


type TradeLike = _HasPnl | Number


def _floats(values: Sequence[Number]) -> list[float]:
    return [float(v) for v in values]


def _pnl(trade: TradeLike) -> Decimal:
    if isinstance(trade, Decimal):
        return trade
    if isinstance(trade, int | float):
        return D(str(trade))
    return D(trade.pnl)


def _sample_std(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = math.fsum(values) / n
    variance = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(max(variance, 0.0))


def returns_from_equity(equity: Sequence[Number]) -> list[Decimal]:
    """Simple returns between consecutive equity points."""
    out: list[Decimal] = []
    for prev, cur in zip(equity, equity[1:], strict=False):
        prev_d = D(str(prev))
        cur_d = D(str(cur))
        if prev_d <= 0:
            raise ValueError("equity values must be > 0 to compute returns")
        out.append(cur_d / prev_d - 1)
    return out


def total_return(equity: Sequence[Number]) -> Decimal | None:
    """Total return as a fraction (``0.21`` == ``+21%``)."""
    if len(equity) < 2:
        return None
    first = D(str(equity[0]))
    last = D(str(equity[-1]))
    if first <= 0:
        return None
    return last / first - 1


def annualized_volatility(returns: Sequence[Number], periods_per_year: int) -> float | None:
    """Annualised sample standard deviation of returns."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")
    xs = _floats(returns)
    sd = _sample_std(xs)
    if sd is None:
        return None
    return sd * math.sqrt(periods_per_year)


def sharpe(returns: Sequence[Number], rf: float = 0.0, periods_per_year: int = 365) -> float | None:
    """Annualised Sharpe ratio. ``rf`` is an annualised risk-free rate."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")
    xs = _floats(returns)
    if len(xs) < 2:
        return None
    rf_period = rf / periods_per_year
    excess = [x - rf_period for x in xs]
    sd = _sample_std(excess)
    if sd is None or sd == 0.0:
        return None
    return math.fsum(excess) / len(excess) / sd * math.sqrt(periods_per_year)


def sortino(returns: Sequence[Number], rf: float = 0.0, periods_per_year: int = 365) -> float | None:
    """Annualised Sortino ratio using downside deviation around the target."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")
    xs = _floats(returns)
    if len(xs) < 2:
        return None
    rf_period = rf / periods_per_year
    excess = [x - rf_period for x in xs]
    downside = [min(x, 0.0) for x in excess]
    downside_dev = math.sqrt(math.fsum(x * x for x in downside) / len(downside))
    if downside_dev == 0.0:
        return None
    return math.fsum(excess) / len(excess) / downside_dev * math.sqrt(periods_per_year)


def max_drawdown(equity: Sequence[Number]) -> tuple[Decimal | None, int | None, int | None]:
    """Maximum peak-to-trough drawdown.

    Returns ``(max_dd_pct, peak_index, trough_index)`` where ``max_dd_pct`` is
    a positive magnitude in percent. ``(None, None, None)`` when there are
    fewer than two points.
    """
    values = [D(str(v)) for v in equity]
    if len(values) < 2:
        return None, None, None
    if any(v <= 0 for v in values):
        raise ValueError("equity values must be > 0 to compute drawdown")

    peak_value = values[0]
    peak_index = 0
    worst = D("0")
    worst_peak = 0
    worst_trough = 0
    for i, value in enumerate(values):
        if value > peak_value:
            peak_value = value
            peak_index = i
        drawdown = (peak_value - value) / peak_value
        if drawdown > worst:
            worst = drawdown
            worst_peak = peak_index
            worst_trough = i
    return worst * 100, worst_peak, worst_trough


def profit_factor(trades: Sequence[TradeLike]) -> float | None:
    """Gross profit divided by gross loss. ``None`` if there is no loss data."""
    if not trades:
        return None
    pnls = [_pnl(t) for t in trades]
    gross_profit = sum((p for p in pnls if p > 0), D("0"))
    gross_loss = -sum((p for p in pnls if p < 0), D("0"))
    if gross_loss == 0:
        return None
    return float(gross_profit) / float(gross_loss)


def win_rate(trades: Sequence[TradeLike]) -> float | None:
    if not trades:
        return None
    wins = sum(1 for t in trades if _pnl(t) > 0)
    return wins / len(trades)


def expectancy(trades: Sequence[TradeLike]) -> Decimal | None:
    """Average net PnL per trade in quote currency."""
    if not trades:
        return None
    pnls = [_pnl(t) for t in trades]
    return sum(pnls, D("0")) / len(pnls)


def calmar(equity: Sequence[Number], periods_per_year: int = 365) -> float | None:
    """Annualised return divided by maximum drawdown (as a fraction)."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be > 0")
    if len(equity) < 2:
        return None
    tr = total_return(equity)
    if tr is None:
        return None
    dd_pct, _, _ = max_drawdown(equity)
    if dd_pct is None or dd_pct == 0:
        return None
    periods = len(equity) - 1
    annualised = float(1.0 + float(tr)) ** (periods_per_year / periods) - 1.0
    return annualised / float(dd_pct / 100)
