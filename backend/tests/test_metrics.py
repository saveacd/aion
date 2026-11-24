from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

import pytest

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

D = Decimal


@dataclass
class FakeTrade:
    pnl: Decimal


def test_returns_from_equity() -> None:
    assert returns_from_equity([D("100"), D("110"), D("99")]) == [D("0.1"), D("-0.1")]
    assert returns_from_equity([D("100")]) == []


def test_returns_from_equity_rejects_zero_base() -> None:
    with pytest.raises(ValueError):
        returns_from_equity([D("0"), D("10")])


def test_total_return() -> None:
    assert total_return([D("100"), D("121")]) == D("0.21")
    assert total_return([D("100")]) is None


def test_annualized_volatility() -> None:
    assert annualized_volatility([0.01, 0.02], 365) == pytest.approx(math.sqrt(0.00005) * math.sqrt(365))
    assert annualized_volatility([0.01], 365) is None
    assert annualized_volatility([0.01, 0.01, 0.01], 365) == pytest.approx(0.0)


def test_sharpe() -> None:
    value = sharpe([0.01, 0.02, 0.015], periods_per_year=365)
    assert value is not None
    assert value > 0
    assert sharpe([0.01], periods_per_year=365) is None
    assert sharpe([0.01, 0.01], periods_per_year=365) is None  # zero dispersion


def test_sortino() -> None:
    value = sortino([0.01, -0.02, 0.03], periods_per_year=365)
    assert value is not None
    assert value > 0
    assert sortino([0.01, 0.02], periods_per_year=365) is None  # no downside


def test_max_drawdown_reports_peak_and_trough() -> None:
    dd, peak, trough = max_drawdown([D("100"), D("120"), D("90"), D("110"), D("80")])
    assert peak == 1
    assert trough == 4
    assert dd is not None
    assert round(dd, 2) == D("33.33")


def test_max_drawdown_insufficient_or_invalid() -> None:
    assert max_drawdown([D("100")]) == (None, None, None)
    with pytest.raises(ValueError):
        max_drawdown([D("100"), D("0")])


def test_trade_stats() -> None:
    trades = [FakeTrade(D("10")), FakeTrade(D("-5")), FakeTrade(D("5")), FakeTrade(D("-10"))]
    assert profit_factor(trades) == pytest.approx(1.0)
    assert win_rate(trades) == pytest.approx(0.5)
    assert expectancy(trades) == D("0")


def test_trade_stats_empty_inputs() -> None:
    assert profit_factor([]) is None
    assert win_rate([]) is None
    assert expectancy([]) is None
    assert profit_factor([FakeTrade(D("10"))]) is None  # no losing trades


def test_calmar() -> None:
    value = calmar([D("100"), D("120"), D("90"), D("110")], periods_per_year=365)
    assert value is not None
    assert value > 0
    assert calmar([D("100")], 365) is None
