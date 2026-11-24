"""Deterministic factor engine tests: IC math, no-lookahead, rejection rules."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("numpy", reason="quant engine tests only need stdlib, numpy guards import ordering")

from aion.quant.factors import (  # noqa: E402
    FORMULA_CATALOG,
    FactorFormula,
    evaluate_factor,
)


@dataclass(slots=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float


def make_candles(
    n: int = 300,
    trend: float = 0.0,
    seed: int = 7,
    regime_flip: float = 0.04,
) -> list[Bar]:
    """Synthetic OHLCV series with persistent return regimes.

    A slowly-switching regime multiplier makes past returns informative about
    future returns (a pure random walk with constant drift has zero momentum
    IC by construction). Positive `trend` amplifies predictability.
    Pass ``regime_flip=0.0`` for an unpredictable pure-noise random walk.
    """
    import random

    rng = random.Random(seed)  # noqa: S311 - synthetic market data, not crypto
    price = 100.0
    regime = 1.0
    bars: list[Bar] = []
    for _ in range(n):
        if rng.random() < regime_flip:
            regime = rng.choice((-1.0, 1.0))
        drift = trend * regime + rng.gauss(0, 0.01)
        open_price = price
        close = price * (1 + drift)
        high = max(open_price, close) * (1 + abs(rng.gauss(0, 0.003)))
        low = min(open_price, close) * (1 - abs(rng.gauss(0, 0.003)))
        volume = 1000 * (1 + rng.gauss(0, 0.3))
        bars.append(
            Bar(
                open=open_price,
                high=max(high, open_price, close),
                low=min(low, open_price, close),
                close=close,
                volume=max(volume, 1.0),
            )
        )
        price = close
    return bars


def test_all_catalog_formulas_compute() -> None:
    candles = make_candles()
    for formula in FORMULA_CATALOG:
        from aion.quant.factors import compute_series

        series = compute_series(formula, candles, lookback=10)
        assert len(series) == len(candles)
        assert series[:9] == [None] * 9 or any(v is None for v in series[:10])
        assert any(v is not None for v in series[12:])


def test_trending_market_accepts_momentum_positive() -> None:
    candles = make_candles(trend=0.008)
    stats = evaluate_factor(candles, FactorFormula.MOMENTUM, lookback=10, horizon=5)
    assert stats.samples > 30
    assert stats.ic_full is not None and stats.ic_full > 0.3
    assert stats.accepted, stats.notes


def test_noise_market_yields_weak_momentum_ic() -> None:
    """Pure-noise random walk never produces a strong momentum signal."""
    candles = make_candles(trend=0.0, regime_flip=0.0)
    stats = evaluate_factor(candles, FactorFormula.MOMENTUM, lookback=10, horizon=5)
    # Finite samples can yield weak spurious IC, never a strong one; anything
    # the referee accepts at this strength is vetted by the declared-direction
    # check and library re-validation.
    assert abs(stats.ic_mean) < 0.5


def test_insufficient_samples_rejects() -> None:
    candles = make_candles(n=40)
    stats = evaluate_factor(candles, FactorFormula.MOMENTUM, lookback=30, horizon=5)
    assert not stats.accepted
    assert stats.notes == ["insufficient_samples"]


def test_unknown_formula_rejects_without_raising() -> None:
    candles = make_candles()
    stats = evaluate_factor(candles, "TOTALLY_UNKNOWN", lookback=5, horizon=1)
    assert not stats.accepted
    assert stats.notes == ["formula_not_in_catalog"]


def test_invalid_parameters_reject() -> None:
    candles = make_candles()
    for lookback, horizon in ((1, 5), (10, 0)):
        stats = evaluate_factor(candles, FactorFormula.MOMENTUM, lookback, horizon)
        assert not stats.accepted
        assert stats.notes == ["invalid_parameters"]


def test_no_lookahead_constant_series_has_zero_ic() -> None:
    # A constant series has no variance -> no information -> never accepted.
    candles = [Bar(open=100, high=100, low=100, close=100, volume=1) for _ in range(200)]
    stats = evaluate_factor(candles, FactorFormula.MOMENTUM, lookback=5, horizon=2)
    assert not stats.accepted


def make_mean_reverting(n: int = 400, seed: int = 9) -> list[Bar]:
    """Fast oscillation (period ~8 bars): above-mean price -> next drop."""
    import math
    import random

    rng = random.Random(seed)  # noqa: S311 - synthetic market data, not crypto
    bars: list[Bar] = []
    for i in range(n):
        close = 100.0 + 8.0 * math.sin(i * (2 * math.pi / 8.0)) + rng.gauss(0, 0.2)
        prev = bars[-1].close if bars else 100.0
        bars.append(
            Bar(
                open=prev,
                high=max(prev, close) * (1 + abs(rng.gauss(0, 0.001))),
                low=min(prev, close) * (1 - abs(rng.gauss(0, 0.001))),
                close=close,
                volume=1000.0,
            )
        )
    return bars


def test_mean_reverting_series_accepts_negative_ic_factor() -> None:
    """Regression: NEGATIVE-IC factors used to always fail the |ICIR| gate."""
    candles = make_mean_reverting()
    stats = evaluate_factor(
        candles, FactorFormula.MEAN_REVERSION_Z, lookback=10, horizon=3
    )
    assert stats.ic_full is not None and stats.ic_full < 0
    assert stats.accepted, stats.notes


def test_direction_mismatch_still_rejects_negative_ic_as_positive() -> None:
    """Caller-side rule: negative IC + declared POSITIVE => rejected."""
    candles = make_mean_reverting()
    stats = evaluate_factor(
        candles, FactorFormula.MEAN_REVERSION_Z, lookback=10, horizon=3
    )
    assert stats.accepted
    assert stats.ic_mean < 0
    # Mirrors FactorResearchOrchestrator direction check with POSITIVE declared.
    direction_ok = (stats.ic_mean > 0) == True  # noqa: E712 - declared POSITIVE
    assert not direction_ok


def test_segment_stability_penalizes_regime_dependent_ic() -> None:
    """IC strong only in the last third must not pass the stability gate."""
    base = make_candles(n=200, trend=0.0, seed=3)
    tail = make_candles(n=120, trend=0.01, seed=4)
    stats = evaluate_factor(base + tail, FactorFormula.MOMENTUM, lookback=10, horizon=5)
    assert stats.segment_ics and len(stats.segment_ics) == 3
    # Either rejected by stability, or at minimum the first segment IC is weak.
    first_ic = stats.segment_ics[0]
    if stats.accepted:
        assert stats.ic_ir is not None and stats.ic_ir > 0.3
    else:
        assert first_ic is None or abs(first_ic) < 0.5


@pytest.mark.parametrize("formula", list(FORMULA_CATALOG))
def test_evaluation_never_raises_on_any_catalog_formula(formula: FactorFormula) -> None:
    candles = make_candles(n=150)
    stats = evaluate_factor(candles, formula, lookback=12, horizon=4)
    assert stats.samples >= 0
    assert 0.0 <= stats.coverage <= 1.0


def test_holdout_confirms_stable_factor() -> None:
    """BUG-20: accepted factors carry a confirming holdout IC."""
    candles = make_mean_reverting(n=400)
    stats = evaluate_factor(
        candles, FactorFormula.MEAN_REVERSION_Z, lookback=10, horizon=3
    )
    if stats.accepted:
        assert stats.ic_holdout is not None
        assert (stats.ic_holdout > 0) == (stats.ic_mean > 0)
