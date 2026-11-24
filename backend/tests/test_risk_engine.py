"""Deterministic tests for the risk boundary. No database, no network."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aion.risk import (
    ControlsView,
    IntentKind,
    IntentView,
    PositionView,
    ReasonCode,
    RiskContext,
    RiskEngine,
    RiskLimits,
    clamp_limits,
)

D = Decimal
NOW = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)


def make_intent(**overrides: object) -> IntentView:
    base: dict[str, object] = {
        "intent_id": "i-1",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "position_side": "LONG",
        "notional_usd": D("500"),
        "quantity": D("5"),
        "reference_price": D("100"),
        "requested_leverage": 5,
        "stop_loss": D("98"),
        "confidence": D("0.8"),
        "expires_at": NOW + dt.timedelta(minutes=5),
        "mode": "PAPER",
    }
    base.update(overrides)
    return IntentView(**base)  # type: ignore[arg-type]


def make_ctx(**overrides: object) -> RiskContext:
    base: dict[str, object] = {
        "mode": "PAPER",
        "mainnet_enabled": False,
        "limits": RiskLimits(),
        "allowed_symbols": ("BTCUSDT", "ETHUSDT"),
        "equity_usd": D("1000"),
        "available_balance_usd": D("1000"),
        "margin_ratio": D("0.5"),
        "market_age_seconds": 3.0,
        "spread_bps": D("2"),
        "exchange_connected": True,
        "now": NOW,
    }
    base.update(overrides)
    return RiskContext(**base)  # type: ignore[arg-type]


def test_happy_path_is_approved() -> None:
    outcome = RiskEngine().evaluate(make_intent(), make_ctx())
    assert outcome.approved, outcome.reason_codes
    assert outcome.reason_codes == (ReasonCode.OK,)
    assert outcome.checked_limits["requested_notional_usd"] == "500"


def test_leverage_above_limit_rejected() -> None:
    outcome = RiskEngine().evaluate(make_intent(requested_leverage=10), make_ctx())
    assert not outcome.approved
    assert ReasonCode.LEVERAGE_EXCEEDED in outcome.reason_codes


def test_notional_above_limit_rejected() -> None:
    outcome = RiskEngine().evaluate(make_intent(notional_usd=D("2000"), quantity=D("20")), make_ctx())
    assert ReasonCode.NOTIONAL_EXCEEDED in outcome.reason_codes


def test_stale_market_data_rejected() -> None:
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(market_age_seconds=300.0))
    assert ReasonCode.STALE_MARKET_DATA in outcome.reason_codes


def test_missing_market_data_rejected() -> None:
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(market_age_seconds=None))
    assert ReasonCode.STALE_MARKET_DATA in outcome.reason_codes


def test_disconnected_exchange_rejected() -> None:
    ctx = make_ctx(mode="TESTNET", exchange_connected=False)
    outcome = RiskEngine().evaluate(make_intent(mode="TESTNET"), ctx)
    assert ReasonCode.EXCHANGE_DISCONNECTED in outcome.reason_codes


def test_reconciliation_difference_blocks_new_risk() -> None:
    ctx = make_ctx(mode="TESTNET", reconciliation_blocking=True)
    outcome = RiskEngine().evaluate(make_intent(mode="TESTNET"), ctx)
    assert ReasonCode.RECONCILIATION_BLOCKING in outcome.reason_codes
    assert outcome.is_hard_block


def test_emergency_stop_blocks_entry_but_allows_exit() -> None:
    ctx = make_ctx(controls=ControlsView(emergency_stop=True, stop_new_trades=True))
    entry = RiskEngine().evaluate(make_intent(), ctx)
    assert ReasonCode.EMERGENCY_STOP in entry.reason_codes

    # Reducing risk must remain possible during an emergency.
    exit_intent = make_intent(kind=IntentKind.REDUCE, side="SELL")
    assert RiskEngine().evaluate(exit_intent, ctx).approved


def test_missing_stop_loss_rejected() -> None:
    outcome = RiskEngine().evaluate(make_intent(stop_loss=None), make_ctx())
    assert ReasonCode.MISSING_STOP_LOSS in outcome.reason_codes


def test_stop_loss_disabled_by_user_is_respected() -> None:
    # Missing stop is allowed when required_stop_loss=False, but the loss cap
    # now bounds the WHOLE notional (follow-up audit #7): keep it small.
    ctx = make_ctx(limits=RiskLimits(required_stop_loss=False))
    intent = make_intent(stop_loss=None, notional_usd=D("20"), quantity=D("0.2"))
    outcome = RiskEngine().evaluate(intent, ctx)
    assert outcome.approved, outcome.reason_codes


def test_single_trade_loss_budget() -> None:
    # entry 100, stop 90, qty 5 -> 50 USD loss, budget is 25.
    outcome = RiskEngine().evaluate(make_intent(stop_loss=D("90")), make_ctx())
    assert ReasonCode.SINGLE_TRADE_LOSS_EXCEEDED in outcome.reason_codes


def test_daily_loss_limit_reached() -> None:
    ctx = make_ctx(daily_realized_pnl_usd=D("-100"))
    outcome = RiskEngine().evaluate(make_intent(), ctx)
    assert ReasonCode.DAILY_LOSS_LIMIT in outcome.reason_codes


def test_drawdown_limit_reached() -> None:
    ctx = make_ctx(drawdown_pct=D("16"))
    outcome = RiskEngine().evaluate(make_intent(), ctx)
    assert ReasonCode.MAX_DRAWDOWN in outcome.reason_codes


def test_consecutive_losses() -> None:
    ctx = make_ctx(consecutive_losses=3)
    outcome = RiskEngine().evaluate(make_intent(), ctx)
    assert ReasonCode.CONSECUTIVE_LOSSES in outcome.reason_codes


def test_max_open_positions() -> None:
    positions = [
        PositionView("BTCUSDT", "LONG", D("100")),
        PositionView("ETHUSDT", "LONG", D("100")),
        PositionView("SOLUSDT", "SHORT", D("100")),
    ]
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(open_positions=positions))
    assert ReasonCode.MAX_OPEN_POSITIONS in outcome.reason_codes


def test_total_exposure() -> None:
    positions = [PositionView("ETHUSDT", "LONG", D("2700"))]
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(open_positions=positions))
    assert ReasonCode.TOTAL_EXPOSURE_EXCEEDED in outcome.reason_codes


def test_directional_concentration_against_equity() -> None:
    ctx = make_ctx(equity_usd=D("1000"), available_balance_usd=D("5000"))
    intent = make_intent(notional_usd=D("800"), quantity=D("8"), requested_leverage=1)
    outcome = RiskEngine().evaluate(intent, ctx)
    assert ReasonCode.DIRECTION_CONCENTRATION in outcome.reason_codes


def test_insufficient_margin() -> None:
    ctx = make_ctx(available_balance_usd=D("50"))
    outcome = RiskEngine().evaluate(make_intent(), ctx)
    assert ReasonCode.INSUFFICIENT_MARGIN in outcome.reason_codes


def test_confidence_below_floor() -> None:
    outcome = RiskEngine().evaluate(make_intent(confidence=D("0.3")), make_ctx())
    assert ReasonCode.LOW_CONFIDENCE in outcome.reason_codes


def test_symbol_not_in_allowlist() -> None:
    outcome = RiskEngine().evaluate(make_intent(symbol="DOGEUSDT"), make_ctx())
    assert ReasonCode.SYMBOL_NOT_ALLOWED in outcome.reason_codes


def test_expired_intent() -> None:
    outcome = RiskEngine().evaluate(
        make_intent(expires_at=NOW - dt.timedelta(seconds=1)), make_ctx()
    )
    assert ReasonCode.INTENT_EXPIRED in outcome.reason_codes


def test_mainnet_blocked_unless_enabled() -> None:
    ctx = make_ctx(mode="MAINNET", mainnet_enabled=False)
    outcome = RiskEngine().evaluate(make_intent(mode="MAINNET"), ctx)
    assert ReasonCode.MAINNET_DISABLED in outcome.reason_codes


def test_mode_mismatch_rejected() -> None:
    outcome = RiskEngine().evaluate(make_intent(mode="TESTNET"), make_ctx(mode="PAPER"))
    assert ReasonCode.MODE_NOT_ALLOWED in outcome.reason_codes


def test_clamp_limits_rejects_above_absolute_ceiling() -> None:
    with pytest.raises(ValueError, match="absolute maximum"):
        clamp_limits(RiskLimits(), {"max_leverage": 21})


def test_clamp_limits_accepts_valid_update() -> None:
    updated = clamp_limits(RiskLimits(), {"max_leverage": 3, "max_position_notional_usd": "250"})
    assert updated.max_leverage == 3
    assert updated.max_position_notional_usd == D("250")


def test_outcome_is_immutable_snapshot() -> None:
    outcome = RiskEngine().evaluate(make_intent(), make_ctx())
    assert outcome.risk_snapshot["controls"]["emergency_stop"] is False
    assert outcome.decided_at == NOW


def test_high_maintenance_fraction_blocks() -> None:
    """BUG-03: margin_ratio is a maint-margin fraction — high = near liquidation."""
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(margin_ratio=D("0.9")))
    assert not outcome.approved
    assert ReasonCode.MARGIN_RATIO_LOW in outcome.reason_codes


def test_low_maintenance_fraction_is_safe() -> None:
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(margin_ratio=D("0.1")))
    assert ReasonCode.MARGIN_RATIO_LOW not in outcome.reason_codes


def test_unestimable_loss_fails_closed() -> None:
    """BUG-09: a missing/invalid stop must not bypass the single-trade cap."""
    outcome = RiskEngine().evaluate(make_intent(stop_loss=None), make_ctx())
    assert not outcome.approved
    assert ReasonCode.SINGLE_TRADE_LOSS_EXCEEDED in outcome.reason_codes


def test_unestimable_loss_bounded_by_notional() -> None:
    """#7: without a stop, the honest loss bound is the whole notional."""
    outcome = RiskEngine().evaluate(make_intent(stop_loss=None), make_ctx())
    assert not outcome.approved
    assert ReasonCode.SINGLE_TRADE_LOSS_EXCEEDED in outcome.reason_codes


def test_no_stop_small_notional_still_within_loss_cap() -> None:
    """#7: disabled stops only pass the cap when notional <= cap itself."""
    limits = RiskLimits(required_stop_loss=False)
    intent = make_intent(stop_loss=None, notional_usd=D("20"), quantity=D("0.2"))
    outcome = RiskEngine().evaluate(intent, make_ctx(limits=limits))
    assert ReasonCode.SINGLE_TRADE_LOSS_EXCEEDED not in outcome.reason_codes


def test_extreme_spread_blocks_entry() -> None:
    """#10: SPREAD_TOO_WIDE is a live gate once real book data is wired."""
    outcome = RiskEngine().evaluate(make_intent(), make_ctx(spread_bps=D("500")))
    assert not outcome.approved
    assert ReasonCode.SPREAD_TOO_WIDE in outcome.reason_codes
