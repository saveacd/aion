import datetime as dt
from decimal import Decimal as D
from types import SimpleNamespace

from aion.quant.strategy_research import evaluate_factor_strategy


def _rows(count=240):
    base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    rows = []
    price = 100.0
    for i in range(count):
        # Smooth deterministic trend with small oscillation; OHLC remains valid.
        price *= 1.001 + (0.0002 if i % 3 else -0.0001)
        opened = base + dt.timedelta(hours=i)
        rows.append(SimpleNamespace(
            open_time=opened, close_time=opened + dt.timedelta(hours=1),
            open=D(str(price * 0.999)), high=D(str(price * 1.002)),
            low=D(str(price * 0.998)), close=D(str(price)), volume=D("1000"),
        ))
    return rows


def test_candidate_backtest_requires_two_factors_and_minimum_history():
    one = SimpleNamespace(formula="MOMENTUM", lookback_bars=8, direction="POSITIVE", ic_mean=D("0.05"), ic_ir=D("0.8"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC))
    assert evaluate_factor_strategy([one], _rows())["reason"] == "NEED_AT_LEAST_TWO_ACTIVE_FACTORS"
    two = [one, SimpleNamespace(**{**one.__dict__, "formula": "EMA_SLOPE"})]
    assert evaluate_factor_strategy(two, _rows(80))["reason"] == "INSUFFICIENT_HISTORY_FOR_HOLDOUT"


def test_candidate_backtest_runs_holdout_and_never_enables_execution():
    factors = [
        SimpleNamespace(formula="MOMENTUM", lookback_bars=8, direction="POSITIVE", ic_mean=D("0.05"), ic_ir=D("0.8"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC)),
        SimpleNamespace(formula="EMA_SLOPE", lookback_bars=12, direction="POSITIVE", ic_mean=D("0.04"), ic_ir=D("0.7"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC)),
    ]
    result = evaluate_factor_strategy(factors, _rows(), funding_history_complete=False)
    assert result["status"] == "COMPLETED"
    assert len(result["walk_forward"]) == 3
    assert result["execution_enabled"] is False
    assert result["human_approval_required"] is True
    assert "FUNDING_HISTORY_INCOMPLETE" in result["evaluation"]["failures"]
    assert result["evaluation"]["passed"] is False


def test_newly_mined_factors_cannot_validate_on_their_own_training_window():
    rows = _rows()
    late = rows[-1].open_time + dt.timedelta(days=1)
    factors = [
        SimpleNamespace(formula="MOMENTUM", lookback_bars=8, direction="POSITIVE", ic_mean=D("0.05"), ic_ir=D("0.8"), created_at=late),
        SimpleNamespace(formula="EMA_SLOPE", lookback_bars=12, direction="POSITIVE", ic_mean=D("0.04"), ic_ir=D("0.7"), created_at=late),
    ]
    result = evaluate_factor_strategy(factors, rows)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "FACTORS_NOT_PREEXISTING_AT_HOLDOUT_START"
    assert result["execution_enabled"] is False


def test_strategy_candidate_kinds_use_distinct_rules_and_risk_profiles():
    factors = [
        SimpleNamespace(formula="MOMENTUM", lookback_bars=8, direction="POSITIVE", ic_mean=D("0.05"), ic_ir=D("0.8"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC)),
        SimpleNamespace(formula="EMA_SLOPE", lookback_bars=12, direction="POSITIVE", ic_mean=D("0.04"), ic_ir=D("0.7"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC)),
    ]
    results = {
        kind: evaluate_factor_strategy(factors, _rows(), strategy_kind=kind, funding_history_complete=True)
        for kind in ("trend_confirmation", "range_reversion", "high_volatility_defensive")
    }
    assert all(result["status"] == "COMPLETED" for result in results.values())
    assumptions = [result["assumptions"] for result in results.values()]
    assert len({item["strategy_kind"] for item in assumptions}) == 3
    assert len({item["max_notional_fraction"] for item in assumptions}) == 3
    assert len({item["signal_threshold"] for item in assumptions}) == 3
    assert all(result["execution_enabled"] is False for result in results.values())


def test_unknown_strategy_kind_is_blocked():
    result = evaluate_factor_strategy([], [], strategy_kind="llm_generated_python")
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "UNSUPPORTED_STRATEGY_KIND"


def test_three_candidate_backtests_can_be_dispatched_as_independent_jobs():
    import asyncio
    from functools import partial

    factors = [
        SimpleNamespace(formula="MOMENTUM", lookback_bars=8, direction="POSITIVE", ic_mean=D("0.05"), ic_ir=D("0.8"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC)),
        SimpleNamespace(formula="EMA_SLOPE", lookback_bars=12, direction="POSITIVE", ic_mean=D("0.04"), ic_ir=D("0.7"), created_at=dt.datetime(2023, 1, 1, tzinfo=dt.UTC)),
    ]
    async def run_all():
        jobs = [asyncio.to_thread(partial(
            evaluate_factor_strategy, factors, _rows(), strategy_kind=kind,
            funding_history_complete=True,
        )) for kind in ("trend_confirmation", "range_reversion", "high_volatility_defensive")]
        return await asyncio.gather(*jobs)

    results = asyncio.run(run_all())
    assert [item["assumptions"]["strategy_kind"] for item in results] == [
        "trend_confirmation", "range_reversion", "high_volatility_defensive"
    ]
