"""Deterministic factor evaluation engine (the researcher's referee).

Design rules (mirrors ``quant/backtest.py``):

* Pure functions — no database, no network, no LLM. Everything a factor
  researcher proposes is scored here before it can enter the factor library.
* No-lookahead contract: the factor value at bar ``i`` only uses bars
  ``<= i``; its label is the forward return over ``[i, i + horizon]``.
* A proposal whose formula is not in the catalogue is rejected, never
  guessed. LLMs propose *which* deterministic formula to mine and how to
  read it; this module owns *how it is computed and scored*.

Scoring
-------
* ``ic_full``  — Spearman rank IC between factor values and forward returns.
* ``ic_mean`` / ``ic_ir`` — mean and studentized stability of the IC computed
  on three contiguous time segments (an IC that only worked in one regime is
  not accepted).
* ``hit_rate`` — share of samples whose ``sign(factor) * direction`` matches
  the sign of the forward return.
* ``coverage`` — usable samples / theoretically available samples.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Minimum bar count a proposal must cover before it is scored at all.
MIN_SAMPLES = 30
#: |mean segment IC| must reach this to count as signal (rank-IC scale).
MIN_ABS_IC = 0.02
#: IC stability (mean / std across segments) must reach this.
MIN_IC_IR = 0.30


class FactorFormula(StrEnum):
    """Deterministic formulas the researcher agents may propose."""

    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION_Z = "MEAN_REVERSION_Z"
    VOLATILITY_RATIO = "VOLATILITY_RATIO"
    VOLUME_Z = "VOLUME_Z"
    RSI_DEVIATION = "RSI_DEVIATION"
    EMA_SLOPE = "EMA_SLOPE"
    RANGE_POSITION = "RANGE_POSITION"
    VOL_ADJUSTED_MOMENTUM = "VOL_ADJUSTED_MOMENTUM"
    PRICE_VOLUME_CONFIRMATION = "PRICE_VOLUME_CONFIRMATION"
    TREND_EFFICIENCY = "TREND_EFFICIENCY"
    BREAKOUT_PRESSURE = "BREAKOUT_PRESSURE"


FORMULA_CATALOG: dict[FactorFormula, str] = {
    FactorFormula.MOMENTUM: "过去 lookback 根收盘价收益率：close[i] / close[i-lookback] - 1",
    FactorFormula.MEAN_REVERSION_Z: "收盘价相对 lookback 均线的 z 分数：(close - sma) / stdev",
    FactorFormula.VOLATILITY_RATIO: "短期/长期波动比：stdev(ret, lookback) / stdev(ret, lookback*2)",
    FactorFormula.VOLUME_Z: "成交量 z 分数：(volume - sma(volume)) / stdev(volume)",
    FactorFormula.RSI_DEVIATION: "RSI(lookback) - 50 的偏离",
    FactorFormula.EMA_SLOPE: "EMA(lookback) 斜率：ema[i] / ema[i-3] - 1",
    FactorFormula.RANGE_POSITION: "收盘价在 lookback 区间内的位置：(close - min(low)) / (max(high) - min(low))",
    FactorFormula.VOL_ADJUSTED_MOMENTUM: "波动率调整动量：lookback 收益率 / 同期收益波动率",
    FactorFormula.PRICE_VOLUME_CONFIRMATION: "价格动量乘以成交量 z 分数的有界确认信号",
    FactorFormula.TREND_EFFICIENCY: "趋势效率：净价格变化绝对值 / 区间内绝对变化总和，并保留方向",
    FactorFormula.BREAKOUT_PRESSURE: "收盘价相对前 lookback 区间高低点中轴的突破压力",
}


#: Compact payload keys (agent_service format) -> canonical field names.
_FIELD_ALIASES = {
    "close": ("close", "c"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "volume": ("volume", "v"),
}


def _get(bar: Any, field: str) -> float:
    """Read a candle field (object attribute, dict key or compact alias)."""
    for key in _FIELD_ALIASES.get(field, (field,)):
        if isinstance(bar, Mapping):
            if key in bar:
                return float(bar[key])
        else:
            value = getattr(bar, key, None)
            if value is not None:
                return float(value)
    raise KeyError(field)


def _values(candles: Sequence[Any], field: str) -> list[float]:
    return [_get(bar, field) for bar in candles]


def _sma(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = _sma(xs)
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def _ema_series(closes: Sequence[float], lookback: int) -> list[float | None]:
    k = 2.0 / (lookback + 1.0)
    out: list[float | None] = []
    ema: float | None = None
    for i, close in enumerate(closes):
        if i + 1 < lookback:
            out.append(None)
            continue
        if ema is None:
            ema = _sma(closes[:lookback])
            out.append(ema)
            continue
        ema = k * close + (1.0 - k) * ema
        out.append(ema)
    return out


def _rsi_series(closes: Sequence[float], lookback: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
        if len(gains) < lookback:
            continue
        window = slice(len(gains) - lookback, len(gains))
        avg_gain = _sma(gains[window])
        avg_loss = _sma(losses[window])
        if avg_loss == 0.0:
            out[i] = 100.0 if avg_gain > 0.0 else 50.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _zscore_series(xs: Sequence[float], lookback: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(xs)):
        if i + 1 < lookback:
            out.append(None)
            continue
        window = xs[i + 1 - lookback : i + 1]
        sd = _stdev(window)
        mean = _sma(window)
        out.append((xs[i] - mean) / sd if sd > 1e-12 else None)
    return out


def _returns(closes: Sequence[float]) -> list[float | None]:
    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out.append((closes[i] - prev) / prev if prev != 0.0 else None)
    return out


def compute_series(
    formula: FactorFormula,
    candles: Sequence[Any],
    lookback: int,
) -> list[float | None]:
    """Factor value at each bar; ``None`` where the lookback is not filled."""
    closes = _values(candles, "close")
    n = len(candles)
    if formula is FactorFormula.MOMENTUM:
        out: list[float | None] = []
        for i in range(n):
            if i < lookback or closes[i - lookback] == 0.0:
                out.append(None)
            else:
                out.append(closes[i] / closes[i - lookback] - 1.0)
        return out
    if formula is FactorFormula.MEAN_REVERSION_Z:
        return _zscore_series(closes, lookback)
    if formula is FactorFormula.VOLATILITY_RATIO:
        rets = [r if r is not None else 0.0 for r in _returns(closes)]
        out = []
        for i in range(n):
            long_lb = lookback * 2
            if i + 1 < long_lb:
                out.append(None)
                continue
            short = _stdev(rets[i + 1 - lookback : i + 1])
            long_v = _stdev(rets[i + 1 - long_lb : i + 1])
            out.append(short / long_v if long_v > 1e-12 else None)
        return out
    if formula is FactorFormula.VOLUME_Z:
        return _zscore_series(_values(candles, "volume"), lookback)
    if formula is FactorFormula.RSI_DEVIATION:
        return [
            None if v is None else v - 50.0 for v in _rsi_series(closes, lookback)
        ]
    if formula is FactorFormula.EMA_SLOPE:
        ema = _ema_series(closes, lookback)
        out = []
        for i in range(n):
            prev = ema[i - 3] if i >= 3 else None
            current = ema[i]
            if current is None or prev is None or prev == 0.0:
                out.append(None)
            else:
                out.append(current / prev - 1.0)
        return out
    if formula is FactorFormula.RANGE_POSITION:
        lows = _values(candles, "low")
        highs = _values(candles, "high")
        out = []
        for i in range(n):
            if i + 1 < lookback:
                out.append(None)
                continue
            lo = min(lows[i + 1 - lookback : i + 1])
            hi = max(highs[i + 1 - lookback : i + 1])
            span = hi - lo
            out.append((closes[i] - lo) / span if span > 1e-12 else None)
        return out
    if formula is FactorFormula.VOL_ADJUSTED_MOMENTUM:
        rets = _returns(closes)
        out = []
        for i in range(n):
            if i < lookback or closes[i - lookback] == 0.0:
                out.append(None)
                continue
            window = [r for r in rets[i - lookback + 1 : i + 1] if r is not None]
            sd = _stdev(window)
            momentum = closes[i] / closes[i - lookback] - 1.0
            out.append(momentum / sd if sd > 1e-12 else None)
        return out
    if formula is FactorFormula.PRICE_VOLUME_CONFIRMATION:
        momentum = compute_series(FactorFormula.MOMENTUM, candles, lookback)
        volume_z = _zscore_series(_values(candles, "volume"), lookback)
        return [
            None if m is None or vz is None else m * math.tanh(vz / 2.0)
            for m, vz in zip(momentum, volume_z, strict=True)
        ]
    if formula is FactorFormula.TREND_EFFICIENCY:
        out = []
        for i in range(n):
            if i < lookback:
                out.append(None)
                continue
            net = closes[i] - closes[i - lookback]
            path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - lookback + 1, i + 1))
            out.append(net / path if path > 1e-12 else None)
        return out
    if formula is FactorFormula.BREAKOUT_PRESSURE:
        highs = _values(candles, "high")
        lows = _values(candles, "low")
        out = []
        for i in range(n):
            if i < lookback:
                out.append(None)
                continue
            # Exclude the current candle from the reference range to avoid
            # making the breakout threshold move with the observation itself.
            hi = max(highs[i - lookback : i])
            lo = min(lows[i - lookback : i])
            span = hi - lo
            out.append((closes[i] - (hi + lo) / 2.0) / span if span > 1e-12 else None)
        return out
    raise ValueError(f"unsupported factor formula: {formula}")


def _rank(xs: Sequence[float]) -> list[float]:
    """Average ranks (ties share the mean rank)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mean_x, mean_y = _sma(rx), _sma(ry)
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)
    denom = math.sqrt(var_x * var_y)
    if denom <= 1e-12:
        return None
    return cov / denom


@dataclass(frozen=True, slots=True)
class FactorStats:
    """Deterministic score of one proposed factor."""

    formula: str
    lookback_bars: int
    horizon_bars: int
    ic_full: float | None
    ic_mean: float | None
    ic_ir: float | None
    segment_ics: list[float | None]
    hit_rate: float | None
    ic_holdout: float | None = None
    coverage: float = 0.0
    samples: int = 0
    accepted: bool = False
    notes: list[str] = field(default_factory=list)


def evaluate_factor(
    candles: Sequence[Any],
    formula: FactorFormula | str,
    lookback: int,
    horizon: int,
    *,
    direction: str = "POSITIVE",
    min_samples: int = MIN_SAMPLES,
    min_abs_ic: float = MIN_ABS_IC,
    min_ic_ir: float = MIN_IC_IR,
    min_coverage: float = 0.5,
    holdout_fraction: float = 0.3,
) -> FactorStats:
    """Score one proposed factor deterministically. Never raises on data."""
    if isinstance(formula, str):
        try:
            formula = FactorFormula(formula)
        except ValueError:
            return FactorStats(
                formula=str(formula), lookback_bars=lookback, horizon_bars=horizon,
                ic_full=None, ic_mean=None, ic_ir=None, segment_ics=[],
                hit_rate=None, coverage=0.0, samples=0, accepted=False,
                notes=["formula_not_in_catalog"],
            )
    if lookback < 2 or horizon < 1:
        return FactorStats(
            formula=formula.value, lookback_bars=lookback, horizon_bars=horizon,
            ic_full=None, ic_mean=None, ic_ir=None, segment_ics=[],
            hit_rate=None, coverage=0.0, samples=0, accepted=False,
            notes=["invalid_parameters"],
        )
    closes = _values(candles, "close")
    n = len(closes)
    available = max(n - lookback - horizon, 0)
    series = compute_series(formula, candles, lookback) if n else []

    pairs_x: list[float] = []
    pairs_y: list[float] = []
    for i in range(n - horizon):
        value = series[i]
        if value is None or i < lookback or closes[i] == 0.0:
            continue
        fwd = closes[i + horizon] / closes[i] - 1.0
        pairs_x.append(value)
        pairs_y.append(fwd)

    samples = len(pairs_x)
    coverage = samples / available if available > 0 else 0.0
    notes: list[str] = []
    if samples < min_samples:
        return FactorStats(
            formula=formula.value, lookback_bars=lookback, horizon_bars=horizon,
            ic_full=None, ic_mean=None, ic_ir=None, segment_ics=[],
            hit_rate=None, coverage=coverage, samples=samples, accepted=False,
            notes=["insufficient_samples"],
        )

    ic_full = _spearman(pairs_x, pairs_y)

    # Split into three contiguous segments: stability across regimes.
    third = samples // 3
    segments: list[list[tuple[float, float]]] = [
        list(zip(pairs_x[:third], pairs_y[:third], strict=False)),
        list(zip(pairs_x[third : 2 * third], pairs_y[third : 2 * third], strict=False)),
        list(zip(pairs_x[2 * third :], pairs_y[2 * third :], strict=False)),
    ]
    segment_ics: list[float | None] = []
    for seg in segments:
        xs = [p[0] for p in seg]
        ys = [p[1] for p in seg]
        segment_ics.append(_spearman(xs, ys) if len(seg) >= 5 else None)
    valid = [ic for ic in segment_ics if ic is not None]
    if len(valid) < 3:
        return FactorStats(
            formula=formula.value, lookback_bars=lookback, horizon_bars=horizon,
            ic_full=ic_full, ic_mean=None, ic_ir=None, segment_ics=segment_ics,
            hit_rate=None, coverage=coverage, samples=samples, accepted=False,
            notes=["segments_too_small"],
        )
    ic_mean = sum(valid) / len(valid)
    sd = _stdev(valid)
    if sd > 1e-6:
        ic_ir = ic_mean / sd
    elif abs(ic_mean) <= 1e-12:
        ic_ir = None
    else:
        # Identical segment ICs are MAXIMAL stability, not instability
        # (audit BUG-18): keep a huge signed ICIR instead of rejecting.
        ic_ir = math.copysign(1e6, ic_mean)

    # Direction-aware hit rate (audit BUG-17): a NEGATIVE factor trades the
    # inverted signal, so a hit is factor sign OPPOSITE the forward return.
    direction_positive = str(direction).upper() != "NEGATIVE"
    same_sign = sum(
        1
        for x, y in zip(pairs_x, pairs_y, strict=True)
        if (x > 0 and y > 0) or (x < 0 and y < 0)
    )
    hit_rate = same_sign / samples if direction_positive else 1.0 - same_sign / samples

    # Walk-forward holdout (audit BUG-20): the last `holdout_fraction` of the
    # sample is reserved and must confirm the in-sample signal. This is a
    # minimal out-of-sample guard against overfitting, not a full research
    # framework (rolling windows and cost modelling come later).
    holdout_n = max(int(samples * holdout_fraction), 1)
    ic_holdout = (
        _spearman(pairs_x[-holdout_n:], pairs_y[-holdout_n:])
        if holdout_n >= 5
        else None
    )

    accepted = True
    if abs(ic_mean) < min_abs_ic:
        accepted = False
        notes.append("ic_below_threshold")
    # Stability uses |ic_ir|: a NEGATIVE-direction factor has negative IC/ICIR
    # by construction; only the magnitude is "instability". Sign consistency
    # with the researcher's declared direction is checked by the caller.
    if ic_ir is None or abs(ic_ir) < min_ic_ir:
        accepted = False
        notes.append("ic_unstable")
    if coverage < min_coverage:
        accepted = False
        notes.append("coverage_too_low")
    if ic_holdout is not None and (
        (ic_holdout > 0) != (ic_mean > 0) or abs(ic_holdout) < min_abs_ic * 0.5
    ):
        accepted = False
        notes.append("holdout_ic_failed")

    return FactorStats(
        formula=formula.value, lookback_bars=lookback, horizon_bars=horizon,
        ic_full=ic_full, ic_mean=ic_mean, ic_ir=ic_ir, segment_ics=segment_ics,
        hit_rate=hit_rate, ic_holdout=ic_holdout, coverage=coverage, samples=samples,
        accepted=accepted, notes=notes,
    )


__all__ = [
    "FORMULA_CATALOG",
    "FactorFormula",
    "FactorStats",
    "MIN_ABS_IC",
    "MIN_IC_IR",
    "MIN_SAMPLES",
    "compute_series",
    "evaluate_factor",
]
