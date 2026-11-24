from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from aion.quant.backtest import (
    BacktestConfig,
    Candle,
    PositionState,
    Signal,
    run_backtest,
)

BASE = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
D = Decimal


def bar(
    minute: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1",
) -> Candle:
    return Candle(
        open_time=BASE + dt.timedelta(minutes=minute),
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(volume),
    )


class SignalAt:
    """Emits one signal at a fixed index and records every window it sees."""

    min_candles = 1

    def __init__(self, at: int, signal: Signal) -> None:
        self.at = at
        self.signal = signal
        self.fired = False
        self.window_lengths: list[int] = []
        self.window_times: list[dt.datetime] = []

    def on_candle(
        self,
        index: int,
        window: Sequence[Candle],
        position: PositionState | None,
    ) -> Signal | None:
        self.window_lengths.append(len(window))
        self.window_times.append(window[-1].open_time)
        if index == self.at and not self.fired:
            self.fired = True
            return self.signal
        return None


class TwoSignals:
    min_candles = 1

    def __init__(self, first: tuple[int, Signal], second: tuple[int, Signal]) -> None:
        self.first = first
        self.second = second

    def on_candle(
        self,
        index: int,
        window: Sequence[Candle],
        position: PositionState | None,
    ) -> Signal | None:
        if index == self.first[0]:
            return self.first[1]
        if index == self.second[0]:
            return self.second[1]
        return None


def zero_cost_config(**overrides: object) -> BacktestConfig:
    values: dict[str, object] = {
        "initial_equity": D("1000"),
        "fee_bps": D("0"),
        "slippage_bps": D("0"),
        "allow_short": False,
    }
    values.update(overrides)
    return BacktestConfig(**values)  # type: ignore[arg-type]


def leakage_candles() -> list[Candle]:
    return [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="99", close="100"),
        bar(2, open_="100", high="106", low="99", close="105"),
        bar(3, open_="90", high="92", low="88", close="91"),
        bar(4, open_="91", high="92", low="90", close="91"),
    ]


def test_signal_fills_at_next_candle_open_not_signal_close() -> None:
    candles = leakage_candles()
    strategy = SignalAt(at=2, signal=Signal.LONG)
    result = run_backtest(candles, strategy, zero_cost_config())

    assert result.error is None
    assert len(result.trades) == 1
    trade = result.trades[0]
    # Decision was made on bar 2's close (105) but must fill at bar 3's open (90).
    assert trade.entry_index == 3
    assert trade.entry_price == D("90")
    assert trade.entry_price != candles[2].close
    assert trade.entry_time == candles[3].open_time


def test_strategy_never_sees_future_candles() -> None:
    candles = leakage_candles()
    strategy = SignalAt(at=2, signal=Signal.LONG)
    run_backtest(candles, strategy, zero_cost_config())

    assert strategy.window_lengths == [1, 2, 3, 4, 5]
    assert strategy.window_times == [c.open_time for c in candles]


def test_stop_loss_wins_when_stop_and_target_hit_in_same_bar() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="110", low="90", close="100"),
        bar(2, open_="100", high="101", low="99", close="100"),
    ]
    strategy = SignalAt(at=0, signal=Signal.LONG)
    config = zero_cost_config(stop_loss_pct=D("1"), take_profit_pct=D("1"))
    result = run_backtest(candles, strategy, config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.exit_index == 1
    assert trade.exit_price == D("99")
    assert trade.pnl < 0


def test_take_profit_fills_when_only_target_is_hit() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="102", low="99.5", close="101"),
        bar(2, open_="101", high="102", low="100", close="101"),
    ]
    strategy = SignalAt(at=0, signal=Signal.LONG)
    config = zero_cost_config(stop_loss_pct=D("5"), take_profit_pct=D("2"))
    result = run_backtest(candles, strategy, config)

    trade = result.trades[0]
    assert trade.exit_reason == "TAKE_PROFIT"
    assert trade.exit_price == D("102")


def test_flat_signal_exits_at_next_open() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="102", low="99", close="101"),
        bar(2, open_="101", high="103", low="100", close="102"),
        bar(3, open_="90", high="91", low="89", close="90"),
    ]
    strategy = TwoSignals(first=(0, Signal.LONG), second=(2, Signal.FLAT))
    result = run_backtest(candles, strategy, zero_cost_config())

    trade = result.trades[0]
    assert trade.exit_reason == "SIGNAL"
    assert trade.exit_index == 3
    assert trade.exit_price == D("90")


def test_fees_and_slippage_are_charged_against_the_trader() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="99", close="100"),
    ]
    strategy = SignalAt(at=0, signal=Signal.LONG)
    config = BacktestConfig(
        initial_equity=D("1000"),
        fee_bps=D("10"),
        slippage_bps=D("10"),
        allow_short=False,
    )
    result = run_backtest(candles, strategy, config)

    trade = result.trades[0]
    assert trade.entry_price == D("100.100")
    assert trade.fees > 0
    assert result.equity_curve[-1].equity < D("1000")


def test_open_position_is_closed_at_end_of_data() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="99", close="100"),
        bar(2, open_="100", high="105", low="99", close="104"),
    ]
    strategy = SignalAt(at=0, signal=Signal.LONG)
    result = run_backtest(candles, strategy, zero_cost_config())

    trade = result.trades[0]
    assert trade.exit_reason == "END_OF_DATA"
    assert trade.exit_index == 2
    assert trade.exit_price == D("104")


def test_short_is_blocked_when_not_allowed() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="101", low="89", close="90"),
        bar(2, open_="90", high="91", low="88", close="90"),
    ]
    strategy = SignalAt(at=0, signal=Signal.SHORT)
    result = run_backtest(candles, strategy, zero_cost_config(allow_short=False))

    assert result.trades == []
    assert result.equity_curve[-1].equity == D("1000")


def test_short_profits_when_price_falls() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100"),
        bar(1, open_="100", high="100", low="91", close="92"),
        bar(2, open_="92", high="93", low="89", close="90"),
    ]
    strategy = SignalAt(at=0, signal=Signal.SHORT)
    result = run_backtest(candles, strategy, zero_cost_config(allow_short=True))

    assert len(result.trades) == 1
    assert result.trades[0].direction is Signal.SHORT
    assert result.trades[0].pnl > 0


def test_insufficient_candles_returns_error_instead_of_crashing() -> None:
    class NeedsHistory(SignalAt):
        min_candles = 10

    candles = leakage_candles()
    result = run_backtest(candles, NeedsHistory(at=2, signal=Signal.LONG))

    assert result.error is not None
    assert "insufficient candles" in result.error
    assert result.trades == []
    assert result.equity_curve == []


def test_empty_candles_returns_error() -> None:
    result = run_backtest([], SignalAt(at=0, signal=Signal.LONG))
    assert result.error == "no candles supplied"


def test_backtest_result_contains_metrics() -> None:
    candles = leakage_candles()
    result = run_backtest(candles, SignalAt(at=2, signal=Signal.LONG), zero_cost_config())

    assert len(result.equity_curve) == len(candles)
    assert result.metrics["trade_count"] == 1
    assert "total_return" in result.metrics
    assert "max_drawdown_pct" in result.metrics
    assert "profit_factor" in result.metrics


def test_funding_is_charged_to_long_and_credited_to_short() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100", volume="100"),
        bar(1, open_="100", high="101", low="99", close="100", volume="100"),
        bar(2, open_="100", high="101", low="99", close="100", volume="100"),
    ]
    funding = {1: D("0.01")}
    long_result = run_backtest(
        candles, SignalAt(at=0, signal=Signal.LONG),
        zero_cost_config(funding_rate_by_index=funding),
    )
    short_result = run_backtest(
        candles, SignalAt(at=0, signal=Signal.SHORT),
        zero_cost_config(allow_short=True, funding_rate_by_index=funding),
    )
    assert long_result.trades[0].funding_paid > 0
    assert short_result.trades[0].funding_paid < 0
    assert long_result.trades[0].pnl < short_result.trades[0].pnl


def test_volume_participation_caps_filled_quantity() -> None:
    candles = [
        bar(0, open_="100", high="101", low="99", close="100", volume="100"),
        bar(1, open_="100", high="101", low="99", close="100", volume="2"),
    ]
    result = run_backtest(
        candles, SignalAt(at=0, signal=Signal.LONG),
        zero_cost_config(max_volume_participation=D("0.1")),
    )
    assert result.trades[0].quantity == D("0.2")


def test_execution_delay_is_explicit_and_prevents_same_bar_fill() -> None:
    candles = [
        bar(i, open_=str(100 + i), high=str(102 + i), low=str(99 + i), close=str(101 + i), volume="100")
        for i in range(5)
    ]
    result = run_backtest(
        candles, SignalAt(at=0, signal=Signal.LONG),
        zero_cost_config(execution_delay_bars=2),
    )
    assert result.trades[0].entry_index == 2
    assert result.trades[0].entry_price == D("102")


def test_invalid_execution_and_liquidity_assumptions_are_rejected() -> None:
    import pytest
    with pytest.raises(ValueError, match="max_volume_participation"):
        zero_cost_config(max_volume_participation=D("0"))
    with pytest.raises(ValueError, match="execution_delay_bars"):
        zero_cost_config(execution_delay_bars=0)
