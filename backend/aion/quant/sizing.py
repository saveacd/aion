"""Order sizing, precision rounding and cost estimation.

All money and quantity values are :class:`~decimal.Decimal`. Nothing here
talks to an exchange: ``step`` (quantity step size), ``tick`` (price tick
size) and ``min_notional`` must come from the exchange's symbol filters
(MASTER_SPEC §7), never from hard-coded guesses.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, InvalidOperation

D = Decimal

BPS_DIVISOR = D("10000")


def _decimal(value: Decimal | str | int | float, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return D(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{name} must be a finite decimal") from exc


def _positive(value: Decimal | str | int | float, name: str) -> Decimal:
    number = _decimal(value, name)
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    if number <= 0:
        raise ValueError(f"{name} must be > 0, got {number}")
    return number


def _non_negative(value: Decimal | str | int | float, name: str) -> Decimal:
    number = _decimal(value, name)
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    if number < 0:
        raise ValueError(f"{name} must be >= 0, got {number}")
    return number


def round_step(qty: Decimal, step: Decimal) -> Decimal:
    """Round quantity **down** to a multiple of ``step``."""
    quantity = _non_negative(qty, "qty")
    increment = _positive(step, "step")
    return (quantity / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def round_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Round price **down** to a multiple of ``tick``."""
    value = _positive(price, "price")
    increment = _positive(tick, "tick")
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def qty_from_notional(notional: Decimal, price: Decimal, step: Decimal) -> Decimal:
    """Quantity for a target notional, rounded down to ``step``."""
    target = _positive(notional, "notional")
    px = _positive(price, "price")
    return round_step(target / px, step)


def qty_from_risk(
    equity: Decimal,
    risk_pct: Decimal,
    entry: Decimal,
    stop: Decimal,
    step: Decimal,
) -> Decimal:
    """Quantity such that hitting ``stop`` loses ``equity * risk_pct / 100``."""
    total = _positive(equity, "equity")
    pct = _positive(risk_pct, "risk_pct")
    if pct > D("100"):
        raise ValueError(f"risk_pct must be <= 100, got {pct}")
    entry_price = _positive(entry, "entry")
    stop_price = _positive(stop, "stop")
    distance = abs(entry_price - stop_price)
    if distance == 0:
        raise ValueError("entry and stop must differ")
    risk_amount = total * pct / D("100")
    return round_step(risk_amount / distance, step)


def margin_required(notional: Decimal, leverage: int) -> Decimal:
    """Initial margin for ``notional`` at ``leverage``."""
    amount = _positive(notional, "notional")
    if leverage < 1:
        raise ValueError(f"leverage must be >= 1, got {leverage}")
    return amount / D(leverage)


def fee_estimate(notional: Decimal, fee_bps: Decimal) -> Decimal:
    """Taker fee for one side of a trade, in quote currency."""
    amount = _positive(notional, "notional")
    bps = _non_negative(fee_bps, "fee_bps")
    return amount * bps / BPS_DIVISOR


def validate_order(
    qty: Decimal,
    price: Decimal,
    step: Decimal,
    tick: Decimal,
    min_qty: Decimal,
    min_notional: Decimal,
) -> None:
    """Raise ``ValueError`` naming the first violated exchange rule."""
    quantity = _positive(qty, "qty")
    px = _positive(price, "price")
    increment = _positive(step, "step")
    tick_size = _positive(tick, "tick")
    minimum_qty = _positive(min_qty, "min_qty")
    minimum_notional = _positive(min_notional, "min_notional")

    if quantity % increment != 0:
        raise ValueError(f"qty {quantity} is not a multiple of step {increment}")
    if px % tick_size != 0:
        raise ValueError(f"price {px} is not a multiple of tick {tick_size}")
    if quantity < minimum_qty:
        raise ValueError(f"qty {quantity} is below min_qty {minimum_qty}")
    notional = quantity * px
    if notional < minimum_notional:
        raise ValueError(
            f"notional {notional} is below min_notional {minimum_notional} (qty={quantity}, price={px})"
        )
