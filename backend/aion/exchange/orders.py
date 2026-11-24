"""Order endpoints for USDⓈ-M Futures (as a client mixin)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

D = Decimal
D0 = D("0")


def to_decimal(value: Any, default: Decimal = D0) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return D(str(value))
    except (InvalidOperation, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class OrderAck:
    symbol: str
    status: str
    side: str
    order_type: str
    order_id: str | None = None
    client_order_id: str | None = None
    orig_qty: Decimal = D0
    executed_qty: Decimal = D0
    avg_price: Decimal = D0
    price: Decimal = D0
    stop_price: Decimal | None = None
    reduce_only: bool = False
    close_position: bool = False
    position_side: str | None = None
    update_time_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def remaining_qty(self) -> Decimal:
        return max(D0, self.orig_qty - self.executed_qty)


def parse_order(payload: dict[str, Any]) -> OrderAck:
    update_time = payload.get("updateTime") or payload.get("time")
    return OrderAck(
        symbol=str(payload.get("symbol", "")),
        status=str(payload.get("status", "")).upper(),
        side=str(payload.get("side", "")).upper(),
        order_type=str(payload.get("type", "")).upper(),
        order_id=str(payload["orderId"]) if payload.get("orderId") is not None else None,
        client_order_id=payload.get("clientOrderId"),
        orig_qty=to_decimal(payload.get("origQty")),
        executed_qty=to_decimal(payload.get("executedQty")),
        avg_price=to_decimal(payload.get("avgPrice")),
        price=to_decimal(payload.get("price")),
        stop_price=to_decimal(payload["stopPrice"]) if payload.get("stopPrice") else None,
        reduce_only=bool(payload.get("reduceOnly", False)),
        close_position=bool(payload.get("closePosition", False)),
        position_side=payload.get("positionSide"),
        update_time_ms=int(update_time) if update_time is not None else None,
        raw=dict(payload),
    )


@dataclass(frozen=True, slots=True)
class UserTrade:
    trade_id: str
    order_id: str | None
    symbol: str
    side: str
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    commission: Decimal
    commission_asset: str | None
    realized_pnl: Decimal
    is_maker: bool
    traded_at: dt.datetime
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def parse_trade(payload: dict[str, Any]) -> UserTrade:
    traded_at = dt.datetime.fromtimestamp(int(payload.get("time", 0)) / 1000, tz=dt.UTC)
    return UserTrade(
        trade_id=str(payload.get("id", "")),
        order_id=str(payload["orderId"]) if payload.get("orderId") is not None else None,
        symbol=str(payload.get("symbol", "")),
        side=str(payload.get("side", "")).upper(),
        price=to_decimal(payload.get("price")),
        qty=to_decimal(payload.get("qty")),
        quote_qty=to_decimal(payload.get("quoteQty")),
        commission=to_decimal(payload.get("commission")),
        commission_asset=payload.get("commissionAsset"),
        realized_pnl=to_decimal(payload.get("realizedPnl")),
        is_maker=bool(payload.get("maker", False)),
        traded_at=traded_at,
        raw=dict(payload),
    )


class OrdersAPI:
    """Mixin: order placement/query/cancel endpoints."""

    async def new_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal | str | None = None,
        price: Decimal | str | None = None,
        stop_price: Decimal | str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool = False,
        close_position: bool = False,
        working_type: str | None = None,
        client_order_id: str | None = None,
        position_side: str | None = None,
    ) -> OrderAck:
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "newOrderRespType": "RESULT",
        }
        if position_side:
            params["positionSide"] = position_side
        if close_position:
            params["closePosition"] = True
        elif quantity is not None:
            params["quantity"] = str(quantity)
        if price is not None:
            params["price"] = str(price)
        if stop_price is not None:
            params["stopPrice"] = str(stop_price)
        if time_in_force:
            params["timeInForce"] = time_in_force
        if reduce_only:
            params["reduceOnly"] = True
        if working_type:
            params["workingType"] = working_type
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        payload = await self._signed_request("POST", "/fapi/v1/order", params)  # type: ignore[attr-defined]
        return parse_order(payload)

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> OrderAck:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        payload = await self._signed_request("DELETE", "/fapi/v1/order", params)  # type: ignore[attr-defined]
        return parse_order(payload)

    async def query_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> OrderAck:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        payload = await self._signed_request("GET", "/fapi/v1/order", params)  # type: ignore[attr-defined]
        return parse_order(payload)

    async def open_orders(self, symbol: str | None = None) -> list[OrderAck]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        payload = await self._signed_request("GET", "/fapi/v1/openOrders", params)  # type: ignore[attr-defined]
        return [parse_order(item) for item in payload]

    async def all_orders(
        self,
        *,
        symbol: str,
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[OrderAck]:
        params: dict[str, Any] = {"symbol": symbol, "limit": int(limit)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        payload = await self._signed_request("GET", "/fapi/v1/allOrders", params)  # type: ignore[attr-defined]
        return [parse_order(item) for item in payload]

    async def user_trades(
        self,
        *,
        symbol: str,
        limit: int = 100,
        from_id: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[UserTrade]:
        params: dict[str, Any] = {"symbol": symbol, "limit": int(limit)}
        if from_id is not None:
            params["fromId"] = int(from_id)
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        payload = await self._signed_request("GET", "/fapi/v1/userTrades", params)  # type: ignore[attr-defined]
        return [parse_trade(item) for item in payload]
