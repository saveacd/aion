"""Symbol filter / quantization tests (Decimal, round down)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from aion.exchange.symbols import SymbolRuleError, build_symbol_map, build_symbol_rules

D = Decimal

EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "pricePrecision": 2,
            "quantityPrecision": 3,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        },
        {
            "symbol": "ETHUSDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.010", "minQty": "0.01"},
                {"filterType": "NOTIONAL", "minNotional": "5"},
            ],
        },
    ]
}


def test_rules_are_parsed_from_exchange_info() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    assert rules.symbol == "BTCUSDT"
    assert rules.tick_size == D("0.1")
    assert rules.step_size == D("0.001")
    assert rules.min_qty == D("0.001")
    assert rules.min_notional == D("5")
    assert rules.price_precision == 2
    assert rules.quantity_precision == 3


def test_symbol_map_covers_all_symbols() -> None:
    rules = build_symbol_map(EXCHANGE_INFO)
    assert set(rules) == {"BTCUSDT", "ETHUSDT"}
    assert rules["ETHUSDT"].min_notional == D("5")


def test_price_rounds_down_to_tick() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    assert rules.quantize_price("20000.37") == D("20000.30")
    assert rules.quantize_price(D("0.109")) == D("0.10")
    assert rules.quantize_price("20000") == D("20000.00")


def test_quantity_rounds_down_to_step() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    assert rules.quantize_qty("0.0109") == D("0.010")
    assert rules.quantize_qty("1.9999") == D("1.999")
    assert rules.quantize_qty("0.0009") == D("0.000")


def test_min_notional_violation_raises() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    with pytest.raises(SymbolRuleError, match="notional"):
        rules.check_min_notional(D("100"), D("0.04"))  # 4 USDT < 5


def test_min_quantity_violation_raises() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    with pytest.raises(SymbolRuleError, match="minimum"):
        rules.check_qty(D("0.0005"))


def test_check_order_rejects_zero_after_rounding() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    qty = rules.quantize_qty("0.0004")
    with pytest.raises(SymbolRuleError):
        rules.check_order(D("20000"), qty)


def test_check_order_accepts_valid_values() -> None:
    rules = build_symbol_rules(EXCHANGE_INFO["symbols"][0])
    rules.check_order(D("20000"), D("0.01"))


def test_invalid_filters_raise() -> None:
    with pytest.raises(SymbolRuleError):
        build_symbol_rules(
            {
                "symbol": "BADUSDT",
                "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0"}],
            }
        )


def test_market_orders_use_market_lot_size_filter() -> None:
    """BUG-15: MARKET_LOT_SIZE must win for market orders when present."""
    entry = {
        "symbol": "TESTUSDT",
        "pricePrecision": 2,
        "quantityPrecision": 3,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {
                "filterType": "MARKET_LOT_SIZE",
                "stepSize": "0.010",
                "minQty": "0.100",
            },
            {"filterType": "NOTIONAL", "notional": "5"},
        ],
    }
    rules = build_symbol_rules(entry)
    assert rules.market_step_size == D("0.010")
    # Market order quantizes to the market step, limit to the lot step.
    assert rules.quantize_qty_for(D("0.123"), "MARKET") == D("0.120")
    assert rules.quantize_qty_for(D("0.123"), "LIMIT") == D("0.123")


def test_market_min_qty_enforced_for_market_orders() -> None:
    """#4: MARKET_LOT_SIZE.minQty must be checked for market orders."""
    import pytest as _pytest

    from aion.exchange.symbols import SymbolRuleError

    entry = {
        "symbol": "TESTUSDT",
        "pricePrecision": 2,
        "quantityPrecision": 3,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.010", "minQty": "0.100"},
            {"filterType": "NOTIONAL", "notional": "5"},
        ],
    }
    rules = build_symbol_rules(entry)
    with _pytest.raises(SymbolRuleError):
        rules.check_qty_for(D("0.050"), "MARKET")
    rules.check_qty_for(D("0.050"), "LIMIT")


def test_final_order_validation_keeps_market_lot_filter() -> None:
    entry = {
        "symbol": "TESTUSDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.010", "minQty": "0.100"},
        ],
    }
    rules = build_symbol_rules(entry)
    with pytest.raises(SymbolRuleError, match="market minimum"):
        rules.check_order(D("100"), D("0.050"), "MARKET")
    rules.check_order(D("100"), D("0.050"), "LIMIT")
