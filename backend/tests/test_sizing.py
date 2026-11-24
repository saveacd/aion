from __future__ import annotations

from decimal import Decimal

import pytest

from aion.quant.sizing import (
    fee_estimate,
    margin_required,
    qty_from_notional,
    qty_from_risk,
    round_step,
    round_tick,
    validate_order,
)

D = Decimal


def test_round_step_floors_to_step() -> None:
    assert round_step(D("1.2345"), D("0.001")) == D("1.234")
    assert round_step(D("2.999"), D("1")) == D("2")
    assert round_step(D("5"), D("0.5")) == D("5.0")


def test_round_tick_floors_to_tick() -> None:
    assert round_tick(D("101.234"), D("0.5")) == D("101.0")
    assert round_tick(D("100.9"), D("0.1")) == D("100.9")


def test_rounding_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        round_step(D("-1"), D("0.1"))
    with pytest.raises(ValueError):
        round_step(D("1"), D("0"))
    with pytest.raises(ValueError):
        round_tick(D("0"), D("0.1"))
    with pytest.raises(ValueError):
        round_tick(D("1"), D("-0.1"))


def test_qty_from_notional() -> None:
    assert qty_from_notional(D("100"), D("33.33"), D("0.001")) == D("3.000")


def test_qty_from_risk() -> None:
    # risk 10 USDT over a 5 USDT stop distance -> 2 contracts
    assert qty_from_risk(D("1000"), D("1"), D("100"), D("95"), D("0.001")) == D("2.000")


def test_qty_from_risk_rejects_zero_distance() -> None:
    with pytest.raises(ValueError):
        qty_from_risk(D("1000"), D("1"), D("100"), D("100"), D("0.001"))
    with pytest.raises(ValueError):
        qty_from_risk(D("1000"), D("0"), D("100"), D("95"), D("0.001"))


def test_margin_and_fee() -> None:
    assert margin_required(D("1000"), 5) == D("200")
    assert fee_estimate(D("1000"), D("4")) == D("0.4")
    with pytest.raises(ValueError):
        margin_required(D("1000"), 0)
    with pytest.raises(ValueError):
        fee_estimate(D("1000"), D("-1"))


def test_validate_order_accepts_compliant_order() -> None:
    validate_order(
        qty=D("0.5"),
        price=D("100"),
        step=D("0.1"),
        tick=D("0.5"),
        min_qty=D("0.1"),
        min_notional=D("10"),
    )


def test_validate_order_reports_step_violation() -> None:
    with pytest.raises(ValueError, match="step"):
        validate_order(D("0.55"), D("100"), D("0.1"), D("0.5"), D("0.1"), D("10"))


def test_validate_order_reports_tick_violation() -> None:
    with pytest.raises(ValueError, match="tick"):
        validate_order(D("0.5"), D("100.2"), D("0.1"), D("0.5"), D("0.1"), D("10"))


def test_validate_order_reports_min_qty_violation() -> None:
    with pytest.raises(ValueError, match="min_qty"):
        validate_order(D("0.05"), D("100"), D("0.01"), D("0.5"), D("0.1"), D("10"))


def test_validate_order_reports_min_notional_violation() -> None:
    with pytest.raises(ValueError, match="min_notional"):
        validate_order(D("0.1"), D("50"), D("0.1"), D("0.5"), D("0.1"), D("10"))


def test_validate_order_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        validate_order(D("0"), D("100"), D("0.1"), D("0.5"), D("0.1"), D("10"))
    with pytest.raises(ValueError):
        validate_order(D("0.5"), D("-100"), D("0.1"), D("0.5"), D("0.1"), D("10"))
