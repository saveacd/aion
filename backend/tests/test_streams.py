from __future__ import annotations

from decimal import Decimal

from aion.exchange.streams import parse_event


def test_trade_update_exposes_trade_id_fee_and_realized_pnl() -> None:
    event = parse_event({
        "e": "ORDER_TRADE_UPDATE",
        "E": 1700000000000,
        "o": {
            "i": 123,
            "c": "aion-test",
            "s": "BTCUSDT",
            "S": "BUY",
            "o": "MARKET",
            "x": "TRADE",
            "X": "PARTIALLY_FILLED",
            "q": "0.010",
            "z": "0.004",
            "ap": "50000",
            "l": "0.004",
            "L": "50000",
            "t": 456,
            "n": "0.02",
            "N": "USDT",
            "rp": "1.25",
            "m": False,
            "T": 1700000000000,
            "R": False,
            "cp": False,
            "ps": "LONG",
        },
    })
    assert event.order is not None
    assert event.order.trade_id == "456"
    assert event.order.last_filled_qty == Decimal("0.004")
    assert event.order.commission == Decimal("0.02")
    assert event.order.commission_asset == "USDT"
    assert event.order.realized_pnl == Decimal("1.25")
    assert event.order.execution_type == "TRADE"
