"""Symbol trading rules derived from ``exchangeInfo``.

All rounding is *down*: never send a price or quantity the exchange would
reject as imprecise, and never round a quantity up into more risk than the
intent asked for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

D = Decimal


class SymbolRuleError(ValueError):
    """The order violates the symbol's tick/step/minNotional filters."""


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return D(default)
    try:
        return D(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SymbolRuleError(f"invalid decimal value: {value!r}") from exc


def _precision(step: Decimal) -> int:
    exponent = step.as_tuple().exponent
    return max(0, -int(exponent)) if isinstance(exponent, int) else 0


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise SymbolRuleError("tick/step size must be positive")
    steps = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return (steps * step).quantize(step, rounding=ROUND_DOWN)


@dataclass(frozen=True, slots=True)
class SymbolRules:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int
    market_step_size: Decimal | None = None
    market_min_qty: Decimal | None = None

    def quantize_price(self, price: Decimal | str | int | float) -> Decimal:
        return _round_down(_decimal(price), self.tick_size)

    def quantize_qty(self, qty: Decimal | str | int | float) -> Decimal:
        return _round_down(_decimal(qty), self.step_size)

    def quantize_qty_for(
        self, qty: Decimal | str | int | float, order_type: str | None = None
    ) -> Decimal:
        """Quantize with the MARKET_LOT_SIZE filter for market orders.

        Exchanges publish a separate, usually tighter filter for market
        orders; falling back to LOT_SIZE can produce a rejected order
        (audit BUG-15).
        """
        if order_type is not None and "MARKET" in order_type.upper() and self.market_step_size:
            return _round_down(_decimal(qty), self.market_step_size)
        return self.quantize_qty(qty)

    def check_qty(self, qty: Decimal) -> None:
        if qty <= 0:
            raise SymbolRuleError(f"{self.symbol}: quantity must be positive, got {qty}")
        if qty < self.min_qty:
            raise SymbolRuleError(f"{self.symbol}: quantity {qty} below minimum {self.min_qty}")

    def check_qty_for(self, qty: Decimal, order_type: str | None = None) -> None:
        """Apply MARKET_LOT_SIZE instead of LOT_SIZE for market orders."""
        if qty <= 0:
            raise SymbolRuleError(f"{self.symbol}: quantity must be positive, got {qty}")
        is_market = order_type is not None and "MARKET" in order_type.upper()
        if is_market and self.market_step_size is not None:
            if self.market_min_qty is not None and qty < self.market_min_qty:
                raise SymbolRuleError(
                    f"{self.symbol}: market quantity {qty} below market minimum {self.market_min_qty}"
                )
            return
        self.check_qty(qty)

    def check_min_notional(self, price: Decimal, qty: Decimal) -> None:
        if self.min_notional <= 0:
            return
        notional = price * qty
        if notional < self.min_notional:
            raise SymbolRuleError(
                f"{self.symbol}: notional {notional} below minimum {self.min_notional}"
            )

    def check_order(
        self, price: Decimal | None, qty: Decimal, order_type: str | None = None
    ) -> None:
        # Preserve the order-type-specific lot filter through the final check.
        self.check_qty_for(qty, order_type)
        if price is not None and price > 0:
            self.check_min_notional(price, qty)


def build_symbol_rules(entry: Mapping[str, Any]) -> SymbolRules:
    """Build rules from one item of ``/fapi/v1/exchangeInfo``."""
    symbol = str(entry.get("symbol", ""))
    filters = {
        str(f.get("filterType")): f
        for f in entry.get("filters", [])
        if isinstance(f, Mapping) and f.get("filterType")
    }
    price_filter = filters.get("PRICE_FILTER", {})
    lot_filter = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
    # Market orders must use MARKET_LOT_SIZE when the symbol publishes one;
    # LOT_SIZE alone is the limit-order filter (audit BUG-15).
    market_lot_filter = filters.get("MARKET_LOT_SIZE") or lot_filter
    notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}

    tick_size = _decimal(price_filter.get("tickSize"), "0.01")
    step_size = _decimal(lot_filter.get("stepSize"), "0.001")
    min_qty = _decimal(lot_filter.get("minQty"), "0")
    min_notional = _decimal(
        notional_filter.get("notional", notional_filter.get("minNotional")), "0"
    )

    if tick_size <= 0 or step_size <= 0:
        raise SymbolRuleError(f"{symbol}: exchangeInfo returned non-positive filters")

    price_precision = entry.get("pricePrecision")
    quantity_precision = entry.get("quantityPrecision")
    return SymbolRules(
        symbol=symbol,
        tick_size=tick_size,
        step_size=step_size,
        min_qty=min_qty,
        min_notional=min_notional,
        price_precision=int(price_precision) if price_precision is not None else _precision(tick_size),
        quantity_precision=(
            int(quantity_precision) if quantity_precision is not None else _precision(step_size)
        ),
        market_step_size=_decimal(market_lot_filter.get("stepSize"), "0") or None,
        market_min_qty=_decimal(market_lot_filter.get("minQty"), "0") or None,
    )


def build_symbol_map(exchange_info: Mapping[str, Any]) -> dict[str, SymbolRules]:
    rules: dict[str, SymbolRules] = {}
    for entry in exchange_info.get("symbols", []):
        if not isinstance(entry, Mapping):
            continue
        try:
            parsed = build_symbol_rules(entry)
        except SymbolRuleError:
            continue
        rules[parsed.symbol] = parsed
    return rules
