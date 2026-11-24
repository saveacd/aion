"""Event-driven candle backtester.

No-lookahead contract
---------------------
A decision made from the close of bar ``i`` can only be filled at the **open
of bar ``i + 1``**. The strategy only receives ``candles[: i + 1]``, so future
bars are not reachable even by accident. A regression test in
``tests/test_backtest.py`` pins this behaviour.

Intrabar contract
-----------------
After a position exists, protective exits are evaluated against each bar's
high/low. If both the stop and the target are touched within one bar the stop
is assumed to fill first (the conservative assumption).

Costs: fees and slippage are charged in basis points, on both entries and
exits, always against the trader.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from collections.abc import Mapping

from aion.quant import metrics
from aion.quant.candles import Candle

D = Decimal
BPS_DIVISOR = D("10000")


class Signal(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(frozen=True, slots=True)
class PositionState:
    """Read-only position snapshot handed to strategies."""

    direction: Signal
    quantity: Decimal
    entry_price: Decimal
    entry_index: int
    entry_time: dt.datetime
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None


class StrategyLike(Protocol):
    """Minimal strategy interface.

    ``min_candles`` is optional; if present and ``len(candles)`` is smaller,
    the backtest returns an empty result with an ``error`` message instead of
    calling the strategy with insufficient history.
    """

    def on_candle(
        self,
        index: int,
        window: Sequence[Candle],
        position: PositionState | None,
    ) -> Signal | None: ...


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: Decimal = D("10000")
    fee_bps: Decimal = D("4")
    slippage_bps: Decimal = D("1")
    allow_short: bool = True
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    # Funding is applied only at supplied settlement bar indices. Positive
    # rates mean longs pay and shorts receive, matching USDⓈ-M convention.
    funding_rate_by_index: Mapping[int, Decimal] = field(default_factory=dict)
    max_volume_participation: Decimal = D("1")
    execution_delay_bars: int = 1
    max_notional_fraction: Decimal = D("1")

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be > 0")
        if self.fee_bps < 0:
            raise ValueError("fee_bps must be >= 0")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be >= 0")
        if not (D("0") < self.max_volume_participation <= D("1")):
            raise ValueError("max_volume_participation must be in (0, 1]")
        if not (D("0") < self.max_notional_fraction <= D("1")):
            raise ValueError("max_notional_fraction must be in (0, 1]")
        if self.execution_delay_bars < 1 or self.execution_delay_bars > 100:
            raise ValueError("execution_delay_bars must be in [1, 100]")
        if any(index < 0 for index in self.funding_rate_by_index):
            raise ValueError("funding settlement indices must be >= 0")
        for name in ("stop_loss_pct", "take_profit_pct"):
            value = getattr(self, name)
            if value is not None and not (D("0") < value < D("100")):
                raise ValueError(f"{name} must be in (0, 100), got {value}")


@dataclass(frozen=True, slots=True)
class Trade:
    direction: Signal
    entry_index: int
    exit_index: int
    entry_time: dt.datetime
    exit_time: dt.datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal
    pnl: Decimal
    exit_reason: str
    funding_paid: Decimal = D("0")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time: dt.datetime
    equity: Decimal


@dataclass(slots=True)
class BacktestResult:
    equity_curve: list[EquityPoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)
    error: str | None = None


@dataclass(slots=True)
class _OpenPosition:
    direction: Signal
    quantity: Decimal
    entry_price: Decimal
    entry_index: int
    entry_time: dt.datetime
    entry_fee: Decimal
    funding_paid: Decimal
    stop_price: Decimal | None
    take_profit_price: Decimal | None

    def state(self) -> PositionState:
        return PositionState(
            direction=self.direction,
            quantity=self.quantity,
            entry_price=self.entry_price,
            entry_index=self.entry_index,
            entry_time=self.entry_time,
            stop_price=self.stop_price,
            take_profit_price=self.take_profit_price,
        )


class _Engine:
    def __init__(self, candles: Sequence[Candle], strategy: StrategyLike, config: BacktestConfig) -> None:
        self.candles = candles
        self.strategy = strategy
        self.config = config
        self.cash: Decimal = config.initial_equity
        self.position: _OpenPosition | None = None
        self.trades: list[Trade] = []
        self.curve: list[EquityPoint] = []

    # --- fill helpers -----------------------------------------------------
    def _fill(self, price: Decimal, *, buying: bool) -> Decimal:
        bps = self.config.slippage_bps / BPS_DIVISOR
        return price * (1 + bps) if buying else price * (1 - bps)

    def _fee(self, notional: Decimal) -> Decimal:
        return notional * self.config.fee_bps / BPS_DIVISOR

    # --- position lifecycle ----------------------------------------------
    def _open(self, direction: Signal, price: Decimal, index: int, time: dt.datetime, volume: Decimal) -> None:
        buying = direction is Signal.LONG
        fill = self._fill(price, buying=buying)
        if fill <= 0:
            return
        notional = self.cash
        if notional <= 0:
            return
        # Cap fills to observable candle volume. This is a conservative
        # participation constraint; it does not pretend to model an order book.
        affordable_quantity = notional * self.config.max_notional_fraction / fill
        liquidity_quantity = max(D("0"), volume * self.config.max_volume_participation)
        quantity = min(affordable_quantity, liquidity_quantity)
        if quantity <= 0:
            return
        fee = self._fee(fill * quantity)
        self.cash -= fee

        stop_price: Decimal | None = None
        take_profit_price: Decimal | None = None
        if self.config.stop_loss_pct is not None:
            pct = self.config.stop_loss_pct / D("100")
            stop_price = fill * (1 - pct) if buying else fill * (1 + pct)
        if self.config.take_profit_pct is not None:
            pct = self.config.take_profit_pct / D("100")
            take_profit_price = fill * (1 + pct) if buying else fill * (1 - pct)

        self.position = _OpenPosition(
            direction=direction,
            quantity=quantity,
            entry_price=fill,
            entry_index=index,
            entry_time=time,
            entry_fee=fee,
            funding_paid=D("0"),
            stop_price=stop_price,
            take_profit_price=take_profit_price,
        )

    def _close(self, price: Decimal, index: int, time: dt.datetime, reason: str) -> None:
        position = self.position
        if position is None:
            return
        selling = position.direction is Signal.LONG
        fill = self._fill(price, buying=not selling)
        exit_fee = self._fee(fill * position.quantity)
        direction = D("1") if position.direction is Signal.LONG else D("-1")
        gross = (fill - position.entry_price) * position.quantity * direction
        self.cash += gross - exit_fee
        self.trades.append(
            Trade(
                direction=position.direction,
                entry_index=position.entry_index,
                exit_index=index,
                entry_time=position.entry_time,
                exit_time=time,
                entry_price=position.entry_price,
                exit_price=fill,
                quantity=position.quantity,
                gross_pnl=gross,
                fees=position.entry_fee + exit_fee,
                pnl=gross - exit_fee - position.entry_fee - position.funding_paid,
                exit_reason=reason,
                funding_paid=position.funding_paid,
            )
        )
        self.position = None

    def _check_protection(self, candle: Candle, index: int) -> None:
        position = self.position
        if position is None:
            return
        if position.direction is Signal.LONG:
            if position.stop_price is not None and candle.low <= position.stop_price:
                self._close(min(position.stop_price, candle.open), index, candle.open_time, "STOP_LOSS")
                return
            if position.take_profit_price is not None and candle.high >= position.take_profit_price:
                self._close(max(position.take_profit_price, candle.open), index, candle.open_time, "TAKE_PROFIT")
                return
        else:
            if position.stop_price is not None and candle.high >= position.stop_price:
                self._close(max(position.stop_price, candle.open), index, candle.open_time, "STOP_LOSS")
                return
            if position.take_profit_price is not None and candle.low <= position.take_profit_price:
                self._close(min(position.take_profit_price, candle.open), index, candle.open_time, "TAKE_PROFIT")

    def _mark(self, candle: Candle) -> Decimal:
        if self.position is None:
            return self.cash
        direction = D("1") if self.position.direction is Signal.LONG else D("-1")
        unrealised = (candle.close - self.position.entry_price) * self.position.quantity * direction
        return self.cash + unrealised

    def _apply(self, signal: Signal, index: int, time: dt.datetime, price: Decimal, volume: Decimal) -> None:
        if signal is Signal.FLAT:
            self._close(price, index, time, "SIGNAL")
            return
        if signal is Signal.SHORT and not self.config.allow_short:
            if self.position is not None and self.position.direction is Signal.LONG:
                self._close(price, index, time, "SIGNAL")
            return
        if self.position is not None and self.position.direction is signal:
            return
        if self.position is not None:
            self._close(price, index, time, "REVERSE")
        self._open(signal, price, index, time, volume)

    def run(self) -> BacktestResult:
        pending: dict[int, Signal] = {}
        for index, candle in enumerate(self.candles):
            due_signal = pending.pop(index, None)
            if due_signal is not None:
                self._apply(due_signal, index, candle.open_time, candle.open, candle.volume)
            self._check_protection(candle, index)
            funding_rate = self.config.funding_rate_by_index.get(index, D("0"))
            if self.position is not None and funding_rate:
                direction = D("1") if self.position.direction is Signal.LONG else D("-1")
                funding_cost = candle.close * self.position.quantity * funding_rate * direction
                self.cash -= funding_cost
                self.position.funding_paid += funding_cost
            self.curve.append(EquityPoint(time=candle.close_time, equity=self._mark(candle)))

            signal = self.strategy.on_candle(index, self.candles[: index + 1], self._position_state())
            if signal is not None:
                due_index = index + self.config.execution_delay_bars
                if due_index < len(self.candles):
                    pending[due_index] = Signal(signal)

        if self.position is not None and self.candles:
            last = self.candles[-1]
            self._close(last.close, last_index := len(self.candles) - 1, last.close_time, "END_OF_DATA")
            self.curve[last_index] = EquityPoint(time=last.close_time, equity=self.cash)

        return BacktestResult(
            equity_curve=self.curve,
            trades=self.trades,
            metrics=_compute_metrics(self.curve, self.trades, self.candles),
        )

    def _position_state(self) -> PositionState | None:
        return None if self.position is None else self.position.state()


def _periods_per_year(candles: Sequence[Candle]) -> int:
    if len(candles) < 2:
        return 365
    seconds = [
        (b.open_time - a.open_time).total_seconds()
        for a, b in zip(candles, candles[1:], strict=False)
        if b.open_time > a.open_time
    ]
    if not seconds:
        return 365
    interval = statistics.median(seconds)
    if interval <= 0:
        return 365
    return max(1, int(365 * 24 * 3600 / interval))


def _compute_metrics(
    curve: Sequence[EquityPoint],
    trades: Sequence[Trade],
    candles: Sequence[Candle],
) -> dict[str, object]:
    equity = [point.equity for point in curve]
    # Pathological equity (<= 0) would make ratio metrics undefined; degrade to
    # ``None`` rather than raising out of a backtest run.
    equity_valid = all(value > 0 for value in equity)
    returns = metrics.returns_from_equity(equity) if equity_valid else []
    ppy = _periods_per_year(candles)
    if equity_valid:
        dd_pct, dd_peak, dd_trough = metrics.max_drawdown(equity)
        calmar = metrics.calmar(equity, periods_per_year=ppy)
    else:
        dd_pct, dd_peak, dd_trough = None, None, None
        calmar = None
    return {
        "total_return": metrics.total_return(equity),
        "annualized_volatility": metrics.annualized_volatility(returns, ppy),
        "sharpe": metrics.sharpe(returns, periods_per_year=ppy),
        "sortino": metrics.sortino(returns, periods_per_year=ppy),
        "max_drawdown_pct": dd_pct,
        "max_drawdown_peak_index": dd_peak,
        "max_drawdown_trough_index": dd_trough,
        "profit_factor": metrics.profit_factor(trades),
        "win_rate": metrics.win_rate(trades),
        "expectancy": metrics.expectancy(trades),
        "calmar": calmar,
        "trade_count": len(trades),
        "periods_per_year": ppy,
    }


def run_backtest(
    candles: Sequence[Candle],
    strategy: StrategyLike,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run a single-position, close-to-open backtest."""
    cfg = config or BacktestConfig()
    if not candles:
        return BacktestResult(error="no candles supplied")

    required = int(getattr(strategy, "min_candles", 1) or 1)
    if len(candles) < required:
        return BacktestResult(
            error=f"insufficient candles: strategy needs at least {required}, got {len(candles)}"
        )

    return _Engine(candles, strategy, cfg).run()
