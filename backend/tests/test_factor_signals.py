from types import SimpleNamespace

from aion.quant.factor_signals import build_factor_signals, normalize_factor_value


def _candle(close: float):
    return SimpleNamespace(close=close, high=close + 1, low=close - 1, volume=100.0)


def _record(**overrides):
    values = {
        "factor_name": "TEST_MOMENTUM", "domain": "momentum", "formula": "MOMENTUM",
        "direction": "POSITIVE", "lookback_bars": 2, "ic_mean": 0.05,
        "ic_ir": 0.6, "status": "ACTIVE",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_active_factor_generates_bounded_weighted_signal():
    result = build_factor_signals([_record()], [_candle(100), _candle(102), _candle(110)])
    assert result["available"] is True
    assert result["factor_count"] == 1
    assert result["bias"] == "BULLISH"
    assert 0 < result["composite_signal"] <= 1
    assert result["factors"][0]["weight"] == 0.3


def test_negative_factor_direction_inverts_signal():
    result = build_factor_signals([_record(direction="NEGATIVE")], [_candle(100), _candle(102), _candle(110)])
    assert result["bias"] == "BEARISH"
    assert result["composite_signal"] < 0


def test_unknown_formula_is_ignored_and_no_factor_is_not_a_signal():
    result = build_factor_signals([_record(formula="NOT_A_FORMULA")], [_candle(100)])
    assert result["available"] is False
    assert result["bias"] == "NO_VALIDATED_FACTORS"
    assert result["composite_signal"] is None


def test_normalized_signal_is_bounded():
    assert -1 <= normalize_factor_value("MOMENTUM", 1000) <= 1
    assert -1 <= normalize_factor_value("MOMENTUM", -1000) <= 1


def test_pearson_correlation_detects_redundant_series():
    from aion.quant.factor_signals import _pearson
    values = [float(i) for i in range(30)]
    assert _pearson(values, values) == 1.0


def test_pearson_correlation_requires_minimum_history():
    from aion.quant.factor_signals import _pearson
    assert _pearson([1.0] * 5, [2.0] * 5) is None


def test_correlated_factors_are_downweighted_and_reported():
    candles = [_candle(100 + i * (1 if i % 2 == 0 else 1.1)) for i in range(40)]
    first = _record(factor_name="MOM_A")
    second = _record(factor_name="MOM_B")
    result = build_factor_signals([first, second], candles)
    assert result["factor_count"] == 2
    assert result["correlations"]
    assert result["factors"][1]["weight"] < result["factors"][0]["weight"]
    assert result["factors"][1]["correlation_penalty"] < 1.0


def test_opposing_factor_signals_are_reported():
    candles = [_candle(100 + i * 0.2) for i in range(40)]
    result = build_factor_signals([
        _record(factor_name="MOM_LONG"),
        _record(factor_name="MOM_SHORT", direction="NEGATIVE"),
    ], candles)
    assert result["signal_conflicts"]
