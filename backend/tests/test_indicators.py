from __future__ import annotations

import math

import pytest

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


def test_sma_known_series() -> None:
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_sma_constant_series() -> None:
    assert sma([10, 10, 10, 10], 2) == [None, 10.0, 10.0, 10.0]


def test_sma_invalid_period_and_short_input() -> None:
    with pytest.raises(ValueError):
        sma([1, 2, 3], 0)
    with pytest.raises(ValueError):
        sma([1, 2, 3], 4)


def test_ema_seeded_with_sma() -> None:
    # seed at index 2 = mean(1,2,3) = 2; alpha = 0.5
    result = ema([1, 2, 3, 4, 5], 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


def test_ema_period_one_matches_input() -> None:
    assert ema([3, 4, 5], 1) == [3.0, 4.0, 5.0]


def test_rsi_wilder_classic_series() -> None:
    closes = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
        45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
        46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
        43.42, 42.66, 43.13,
    ]
    result = rsi(closes, 14)
    assert result[:14] == [None] * 14
    assert result[14] == pytest.approx(70.46, abs=0.02)
    assert result[15] == pytest.approx(66.25, abs=0.02)
    assert result[19] == pytest.approx(57.92, abs=0.02)
    assert result[-1] == pytest.approx(37.79, abs=0.02)


def test_rsi_monotonic_series_extremes() -> None:
    rising = rsi([float(i) for i in range(1, 20)], 14)
    falling = rsi([float(i) for i in range(20, 1, -1)], 14)
    assert all(v == pytest.approx(100.0) for v in rising[14:])
    assert all(v == pytest.approx(0.0) for v in falling[14:])


def test_rsi_requires_period_plus_one_values() -> None:
    with pytest.raises(ValueError):
        rsi([1.0] * 14, 14)
    with pytest.raises(ValueError):
        rsi([1.0] * 20, -1)


def test_macd_constant_series_is_flat() -> None:
    macd_line, signal_line, histogram = macd([5.0] * 40, fast=3, slow=6, signal=4)
    assert len(macd_line) == len(signal_line) == len(histogram) == 40
    assert macd_line[5] == pytest.approx(0.0)
    assert signal_line[8] == pytest.approx(0.0)
    assert all(v is None for v in macd_line[:5])
    assert all(v is None for v in signal_line[:8])
    assert all(v == pytest.approx(0.0) for v in histogram[8:])


def test_macd_histogram_is_macd_minus_signal() -> None:
    closes = [float(i) for i in range(20, 60)]
    macd_line, signal_line, histogram = macd(closes, fast=3, slow=6, signal=4)
    for i in range(8, len(closes)):
        assert histogram[i] == pytest.approx(macd_line[i] - signal_line[i])


def test_atr_constant_range() -> None:
    result = atr([10.0] * 5, [8.0] * 5, [9.0] * 5, period=3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(2.0)
    assert result[4] == pytest.approx(2.0)


def test_atr_uses_wilder_smoothing() -> None:
    # TR = [2, 4]; first ATR(2) = (2 + 4) / 2 = 3
    result = atr([10.0, 13.0], [8.0, 11.0], [9.0, 12.0], period=2)
    assert result == [None, pytest.approx(3.0)]


def test_true_range_includes_previous_close_gaps() -> None:
    result = true_range([10.0, 12.0], [8.0, 9.0], [9.0, 11.0])
    assert result[0] == pytest.approx(2.0)
    assert result[1] == pytest.approx(3.0)


def test_bollinger_known_series() -> None:
    upper, middle, lower = bollinger([1, 2, 3, 4, 5], period=3, mult=2.0)
    assert middle == [None, None, 2.0, 3.0, 4.0]
    assert upper == [None, None, 4.0, 5.0, 6.0]
    assert lower == [None, None, 0.0, 1.0, 2.0]


def test_bollinger_constant_series_has_zero_width() -> None:
    upper, middle, lower = bollinger([7.0] * 5, period=3, mult=2.0)
    assert upper[2] == pytest.approx(7.0)
    assert middle[2] == pytest.approx(7.0)
    assert lower[2] == pytest.approx(7.0)


def test_stdev_is_sample_standard_deviation() -> None:
    result = stdev([1, 2, 3, 4, 5], 3)
    assert result[:2] == [None, None]
    assert result[2] == pytest.approx(1.0)
    assert result[4] == pytest.approx(1.0)
    assert stdev([2.0, 4.0], 2)[1] == pytest.approx(math.sqrt(2.0))


def test_stdev_rejects_period_one() -> None:
    with pytest.raises(ValueError):
        stdev([1.0, 2.0], 1)


def test_crosses_above_and_below() -> None:
    a = [None, 1.0, 3.0, 2.0, 4.0]
    b = [None, 2.0, 2.0, 2.0, 2.0]
    assert crosses_above(a, b) == [False, False, True, False, True]
    assert crosses_below(a, b) == [False, False, False, False, False]

    c = [3.0, 1.0, 1.0]
    d = [2.0, 2.0, 0.5]
    assert crosses_below(c, d) == [False, True, False]


def test_crosses_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        crosses_above([1.0, 2.0], [1.0])
