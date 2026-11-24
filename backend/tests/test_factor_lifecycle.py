from types import SimpleNamespace

from aion.quant import factor_lifecycle


def _record(**overrides):
    data = {
        "factor_name": "MOM_TEST", "status": "ACTIVE", "formula": "MOMENTUM",
        "lookback_bars": 5, "horizon_bars": 3, "direction": "POSITIVE",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_degraded_holdout_moves_active_factor_to_watch(monkeypatch):
    monkeypatch.setattr(factor_lifecycle, "evaluate_factor", lambda *a, **k: SimpleNamespace(samples=100, ic_holdout=-0.08))
    result = factor_lifecycle.assess_factor_lifecycle(_record(), [object()] * 100)
    assert result.new_status == "WATCH"
    assert result.reason == "holdout_direction_reversed"


def test_revalidated_watch_factor_returns_to_active(monkeypatch):
    monkeypatch.setattr(factor_lifecycle, "evaluate_factor", lambda *a, **k: SimpleNamespace(samples=100, ic_holdout=0.06))
    result = factor_lifecycle.assess_factor_lifecycle(_record(status="WATCH"), [object()] * 100)
    assert result.new_status == "ACTIVE"
    assert result.reason == "holdout_revalidated"


def test_insufficient_data_never_changes_factor_status(monkeypatch):
    monkeypatch.setattr(factor_lifecycle, "evaluate_factor", lambda *a, **k: SimpleNamespace(samples=12, ic_holdout=None))
    result = factor_lifecycle.assess_factor_lifecycle(_record(), [object()] * 12)
    assert result.new_status == "ACTIVE"
    assert result.reason == "insufficient_holdout"


def test_terminal_status_is_not_reactivated():
    result = factor_lifecycle.assess_factor_lifecycle(_record(status="ARCHIVED"), [])
    assert result.new_status == "ARCHIVED"
    assert result.reason == "terminal_status"


def test_testing_factor_passes_to_watch_before_activation(monkeypatch):
    monkeypatch.setattr(factor_lifecycle, "evaluate_factor", lambda *a, **k: SimpleNamespace(samples=100, ic_holdout=0.06))
    result = factor_lifecycle.assess_factor_lifecycle(_record(status="TESTING"), [object()] * 100)
    assert result.new_status == "WATCH"
    assert result.reason == "validation_passed_to_watch"


def test_factor_retires_only_after_three_consecutive_validation_failures():
    from aion.quant.factor_lifecycle import should_retire_after_failures

    assert should_retire_after_failures([False, False], False) is True
    assert should_retire_after_failures([False, True], False) is False
    assert should_retire_after_failures([False, False], True) is False
