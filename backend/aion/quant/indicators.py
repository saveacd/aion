"""Deterministic technical indicators.

Every function is a pure function over a numeric sequence. Results always have
the same length as the input; values that cannot be computed yet (not enough
history) are ``None`` instead of being silently dropped or zero-filled.

Indicators are computed in ``float`` internally; callers that need Decimal
precision for money must convert at the boundary. This is documented per
function via the ``list[float | None]`` return type.

See MASTER_SPEC §7 (Quant Engine).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

FloatSeries = list[float | None]
BoolSeries = list[bool]

Number = float | int


def _to_floats(values: Sequence[Number]) -> list[float]:
    return [float(v) for v in values]


def _require_period(period: int) -> None:
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")


def _require_length(values: Sequence[object], period: int) -> None:
    if len(values) < period:
        raise ValueError(f"not enough values: need at least {period}, got {len(values)}")


def _require_aligned(*series: Sequence[object]) -> None:
    if not series:
        return
    length = len(series[0])
    if any(len(s) != length for s in series):
        raise ValueError("input series must have identical length")


def sma(values: Sequence[Number], period: int) -> FloatSeries:
    """Simple moving average."""
    _require_period(period)
    xs = _to_floats(values)
    _require_length(xs, period)

    out: FloatSeries = [None] * len(xs)
    running = math.fsum(xs[:period])
    out[period - 1] = running / period
    for i in range(period, len(xs)):
        running += xs[i] - xs[i - period]
        out[i] = running / period
    return out


def ema(values: Sequence[Number], period: int) -> FloatSeries:
    """Exponential moving average seeded with the SMA of the first window."""
    _require_period(period)
    xs = _to_floats(values)
    _require_length(xs, period)

    out: FloatSeries = [None] * len(xs)
    seed = math.fsum(xs[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(xs)):
        prev = xs[i] * alpha + prev * (1.0 - alpha)
        out[i] = prev
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi(values: Sequence[Number], period: int = 14) -> FloatSeries:
    """Relative Strength Index using Wilder's smoothing."""
    _require_period(period)
    xs = _to_floats(values)
    _require_length(xs, period + 1)

    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(xs, xs[1:], strict=False):
        delta = cur - prev
        gains.append(delta if delta > 0 else 0.0)
        losses.append(-delta if delta < 0 else 0.0)

    avg_gain = math.fsum(gains[:period]) / period
    avg_loss = math.fsum(losses[:period]) / period

    out: FloatSeries = [None] * len(xs)
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(xs)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def macd(
    values: Sequence[Number],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[FloatSeries, FloatSeries, FloatSeries]:
    """Moving Average Convergence Divergence.

    Returns ``(macd_line, signal_line, histogram)``. Each series is the same
    length as ``values`` with ``None`` where insufficient history exists.
    """
    _require_period(fast)
    _require_period(slow)
    _require_period(signal)

    xs = _to_floats(values)
    fast_line = ema(xs, fast)
    slow_line = ema(xs, slow)

    macd_line: FloatSeries = [None] * len(xs)
    first_macd: int | None = None
    for i, (f, s) in enumerate(zip(fast_line, slow_line, strict=True)):
        if f is None or s is None:
            continue
        macd_line[i] = f - s
        if first_macd is None:
            first_macd = i

    if first_macd is None:
        raise ValueError("not enough values to compute MACD")

    valid = [v for v in macd_line[first_macd:] if v is not None]
    signal_valid = ema(valid, signal)

    signal_line: FloatSeries = [None] * len(xs)
    histogram: FloatSeries = [None] * len(xs)
    for offset, value in enumerate(signal_valid):
        if value is None:
            continue
        idx = first_macd + offset
        signal_line[idx] = value
        # ``macd_line[idx]`` is guaranteed non-None for the same offset.
        macd_value = macd_line[idx]
        if macd_value is not None:
            histogram[idx] = macd_value - value
    return macd_line, signal_line, histogram


def true_range(
    highs: Sequence[Number],
    lows: Sequence[Number],
    closes: Sequence[Number],
) -> FloatSeries:
    """True range. The first bar uses ``high - low`` (no previous close)."""
    _require_aligned(highs, lows, closes)
    if len(highs) == 0:
        raise ValueError("not enough values: need at least 1")

    hs = _to_floats(highs)
    ls = _to_floats(lows)
    cs = _to_floats(closes)

    out: FloatSeries = [hs[0] - ls[0]]
    for i in range(1, len(hs)):
        prev_close = cs[i - 1]
        out.append(max(hs[i] - ls[i], abs(hs[i] - prev_close), abs(ls[i] - prev_close)))
    return out


def atr(
    highs: Sequence[Number],
    lows: Sequence[Number],
    closes: Sequence[Number],
    period: int = 14,
) -> FloatSeries:
    """Average True Range using Wilder's smoothing."""
    _require_period(period)
    _require_aligned(highs, lows, closes)
    _require_length(highs, period)

    tr = true_range(highs, lows, closes)
    # ``tr`` is fully populated (index 0 included).
    values = [v for v in tr if v is not None]
    avg = math.fsum(values[:period]) / period

    out: FloatSeries = [None] * len(highs)
    out[period - 1] = avg
    for i in range(period, len(values)):
        avg = (avg * (period - 1) + values[i]) / period
        out[i] = avg
    return out


def stdev(values: Sequence[Number], period: int) -> FloatSeries:
    """Rolling sample standard deviation (``n - 1`` denominator)."""
    _require_period(period)
    if period < 2:
        raise ValueError("period must be >= 2 for sample standard deviation")
    xs = _to_floats(values)
    _require_length(xs, period)

    out: FloatSeries = [None] * len(xs)
    for i in range(period - 1, len(xs)):
        window = xs[i - period + 1 : i + 1]
        mean = math.fsum(window) / period
        variance = math.fsum((x - mean) ** 2 for x in window) / (period - 1)
        out[i] = math.sqrt(max(variance, 0.0))
    return out


def bollinger(
    values: Sequence[Number],
    period: int = 20,
    mult: float = 2.0,
) -> tuple[FloatSeries, FloatSeries, FloatSeries]:
    """Bollinger Bands. Returns ``(upper, middle, lower)``."""
    if mult < 0:
        raise ValueError("mult must be >= 0")
    middle = sma(values, period)
    deviation = stdev(values, period)

    upper: FloatSeries = [None] * len(middle)
    lower: FloatSeries = [None] * len(middle)
    for i, (mid, sd) in enumerate(zip(middle, deviation, strict=True)):
        if mid is None or sd is None:
            continue
        upper[i] = mid + mult * sd
        lower[i] = mid - mult * sd
    return upper, middle, lower


def _crosses(
    a: Sequence[Number | None],
    b: Sequence[Number | None],
    *,
    above: bool,
) -> BoolSeries:
    _require_aligned(a, b)
    out: BoolSeries = [False] * len(a)
    for i in range(1, len(a)):
        a_prev, b_prev = a[i - 1], b[i - 1]
        a_cur, b_cur = a[i], b[i]
        if a_prev is None or b_prev is None or a_cur is None or b_cur is None:
            continue
        if above:
            out[i] = a_prev <= b_prev and a_cur > b_cur
        else:
            out[i] = a_prev >= b_prev and a_cur < b_cur
    return out


def crosses_above(a: Sequence[Number | None], b: Sequence[Number | None]) -> BoolSeries:
    """True where ``a`` crosses from at-or-below ``b`` to above ``b``."""
    return _crosses(a, b, above=True)


def crosses_below(a: Sequence[Number | None], b: Sequence[Number | None]) -> BoolSeries:
    """True where ``a`` crosses from at-or-above ``b`` to below ``b``."""
    return _crosses(a, b, above=False)
