"""Absolute ceilings for user-configurable risk limits.

These values are compiled into the server. No API request, AI output or
database row can raise them — that is the entire point of the risk boundary.
"""

from __future__ import annotations

from decimal import Decimal

from aion.risk.engine import RiskLimits

D = Decimal

#: Upper bound per limit. Any client attempt above these is rejected with 422.
ABSOLUTE: dict[str, Decimal] = {
    "max_leverage": D("20"),
    "max_position_notional_usd": D("100000"),
    "max_total_exposure_usd": D("300000"),
    "max_single_trade_loss_usd": D("5000"),
    "daily_loss_limit_usd": D("10000"),
    "max_drawdown_pct": D("50"),
    "max_consecutive_losses": D("10"),
    "max_open_positions": D("10"),
    "max_directional_concentration_pct": D("100"),
    "min_margin_ratio": D("0.9"),
    "max_spread_bps": D("200"),
    "min_confidence": D("1"),
}

#: Lower bound per limit; 0 means "no explicit floor" except where noted.
MINIMUM: dict[str, Decimal] = {
    "max_leverage": D("1"),
    "max_consecutive_losses": D("1"),
    "max_open_positions": D("1"),
}

INT_FIELDS = frozenset(
    {"max_leverage", "max_consecutive_losses", "max_open_positions", "required_stop_loss"}
)


def validate_limits(limits: RiskLimits) -> RiskLimits:
    """Assert an already-constructed limits object is within absolute bounds.

    Used when loading limits from the database: a tampered row must not make
    the engine more permissive than the compiled ceiling.
    """
    for name, bound in ABSOLUTE.items():
        value = getattr(limits, name)
        if not isinstance(value, Decimal):
            value = D(str(value))
        if value > bound:
            raise ValueError(f"{name} exceeds absolute maximum {bound}")
        floor = MINIMUM.get(name)
        if floor is not None and value < floor:
            raise ValueError(f"{name} is below absolute minimum {floor}")
    return limits


def default_limits() -> RiskLimits:
    """Conservative defaults matching RiskPolicy column defaults."""
    return RiskLimits()
