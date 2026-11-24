from types import SimpleNamespace

from aion.quant.factor_ensemble import detect_market_regime, regime_multiplier
from aion.quant.factors import FactorFormula, compute_series
from aion.quant.strategy_evolution import build_strategy_evolution


def _bars(closes, volumes=None):
    volumes = volumes or [100.0] * len(closes)
    return [SimpleNamespace(close=c, high=c * 1.002, low=c * 0.998, volume=v) for c, v in zip(closes, volumes, strict=True)]


def test_compositional_factor_formulas_are_deterministic_and_no_lookahead():
    bars = _bars([100 + i * 0.5 for i in range(80)], [100 + (i % 7) * 10 for i in range(80)])
    for formula in (FactorFormula.VOL_ADJUSTED_MOMENTUM, FactorFormula.PRICE_VOLUME_CONFIRMATION,
                    FactorFormula.TREND_EFFICIENCY, FactorFormula.BREAKOUT_PRESSURE):
        values = compute_series(formula, bars, 10)
        assert len(values) == len(bars)
        assert values[-1] is not None
        truncated = compute_series(formula, bars[:-1], 10)
        assert values[-2] == truncated[-1]


def test_regime_aware_weights_prefer_trend_domain_in_trend_regime():
    assert regime_multiplier("momentum", "MOMENTUM", "TRENDING") > regime_multiplier("mean_reversion", "MEAN_REVERSION_Z", "TRENDING")


def test_strategy_evolution_is_shadow_only_and_never_authorizes_orders():
    result = build_strategy_evolution({"composite_signal": 0.7, "bias": "BULLISH", "factor_count": 3}, {"label": "HIGH_VOLATILITY"})
    assert result["mode"] == "SHADOW_RESEARCH_ONLY"
    assert result["auto_parameter_changes"] is False
    assert result["auto_orders"] is False
    assert result["suggestions"]


def test_regime_detection_handles_insufficient_history():
    assert detect_market_regime(_bars([100, 101]))["label"] == "UNKNOWN"
