"""Binance USDⓈ-M Futures adapter.

Heavy modules (``client``, ``streams``) are imported lazily so that pure
helpers (signing, error mapping, symbol rules) stay importable without the
HTTP/WebSocket dependencies being installed.
"""

from __future__ import annotations

from typing import Any

from aion.exchange.errors import (
    BinanceAPIError,
    ExchangeError,
    ExchangeNetworkError,
    ExchangeTimeout,
    classify,
)
from aion.exchange.signing import auth_headers, build_signed_params, redact_params, sign_query
from aion.exchange.symbols import SymbolRuleError, SymbolRules, build_symbol_map

_LAZY_EXPORTS = {
    "AccountAPI": "aion.exchange.account",
    "AccountInfo": "aion.exchange.account",
    "Balance": "aion.exchange.account",
    "Position": "aion.exchange.account",
    "BinanceFuturesClient": "aion.exchange.client",
    "MarketAPI": "aion.exchange.market",
    "FundingRate": "aion.exchange.market",
    "Kline": "aion.exchange.market",
    "MarkPrice": "aion.exchange.market",
    "OpenInterest": "aion.exchange.market",
    "OrderBook": "aion.exchange.market",
    "TickerPrice": "aion.exchange.market",
    "OrdersAPI": "aion.exchange.orders",
    "OrderAck": "aion.exchange.orders",
    "UserTrade": "aion.exchange.orders",
    "UserDataStream": "aion.exchange.streams",
    "UserDataEvent": "aion.exchange.streams",
    "OrderUpdate": "aion.exchange.streams",
    "parse_event": "aion.exchange.streams",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "AccountAPI",
    "AccountInfo",
    "Balance",
    "BinanceAPIError",
    "BinanceFuturesClient",
    "ExchangeError",
    "ExchangeNetworkError",
    "ExchangeTimeout",
    "FundingRate",
    "Kline",
    "MarkPrice",
    "MarketAPI",
    "OpenInterest",
    "OrderAck",
    "OrderBook",
    "OrderUpdate",
    "OrdersAPI",
    "Position",
    "SymbolRuleError",
    "SymbolRules",
    "TickerPrice",
    "UserDataEvent",
    "UserDataStream",
    "UserTrade",
    "auth_headers",
    "build_signed_params",
    "build_symbol_map",
    "classify",
    "parse_event",
    "redact_params",
    "sign_query",
]
