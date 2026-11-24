"""Account, balance, position and leverage endpoints (client mixin)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aion.exchange.orders import to_decimal

D = Decimal
D0 = D("0")


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    balance: Decimal
    available_balance: Decimal
    cross_wallet_balance: Decimal | None = None
    cross_unrealized_pnl: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    position_side: str
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    liquidation_price: Decimal
    leverage: int
    margin_mode: str
    isolated_margin: Decimal | None = None
    notional: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_open(self) -> bool:
        return self.quantity != D0

    @property
    def abs_quantity(self) -> Decimal:
        """Unsigned size: exchange shorts carry negative ``positionAmt``."""
        return abs(self.quantity)


@dataclass(frozen=True, slots=True)
class AccountInfo:
    total_wallet_balance: Decimal
    total_unrealized_pnl: Decimal
    total_margin_balance: Decimal
    available_balance: Decimal
    total_initial_margin: Decimal
    total_maint_margin: Decimal
    can_trade: bool
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def parse_balance(payload: dict[str, Any]) -> Balance:
    return Balance(
        asset=str(payload.get("asset", "")),
        balance=to_decimal(payload.get("balance")),
        available_balance=to_decimal(payload.get("availableBalance")),
        cross_wallet_balance=(
            to_decimal(payload["crossWalletBalance"]) if payload.get("crossWalletBalance") else None
        ),
        cross_unrealized_pnl=(
            to_decimal(payload["crossUnPnl"]) if payload.get("crossUnPnl") else None
        ),
    )


def parse_position(payload: dict[str, Any]) -> Position:
    return Position(
        symbol=str(payload.get("symbol", "")),
        position_side=str(payload.get("positionSide", "BOTH")),
        quantity=to_decimal(payload.get("positionAmt")),
        entry_price=to_decimal(payload.get("entryPrice")),
        mark_price=to_decimal(payload.get("markPrice")),
        unrealized_pnl=to_decimal(payload.get("unRealizedProfit", payload.get("unrealizedProfit"))),
        liquidation_price=to_decimal(payload.get("liquidationPrice")),
        leverage=int(to_decimal(payload.get("leverage"), D("1"))),
        margin_mode=str(payload.get("marginType") or payload.get("marginMode") or "CROSSED").upper(),
        isolated_margin=(
            to_decimal(payload["isolatedMargin"]) if payload.get("isolatedMargin") else None
        ),
        notional=to_decimal(payload["notional"]) if payload.get("notional") else None,
        raw=dict(payload),
    )


class AccountAPI:
    """Mixin: account / balance / position endpoints."""

    async def balances(self) -> list[Balance]:
        payload = await self._signed_request("GET", "/fapi/v3/balance", {})  # type: ignore[attr-defined]
        return [parse_balance(item) for item in payload]

    async def positions(self, symbol: str | None = None) -> list[Position]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        payload = await self._signed_request("GET", "/fapi/v3/positionRisk", params)  # type: ignore[attr-defined]
        return [parse_position(item) for item in payload]

    async def account_info(self) -> AccountInfo:
        payload = await self._signed_request("GET", "/fapi/v3/account", {})  # type: ignore[attr-defined]
        return AccountInfo(
            total_wallet_balance=to_decimal(payload.get("totalWalletBalance")),
            total_unrealized_pnl=to_decimal(payload.get("totalUnrealizedProfit")),
            total_margin_balance=to_decimal(payload.get("totalMarginBalance")),
            available_balance=to_decimal(payload.get("availableBalance")),
            total_initial_margin=to_decimal(payload.get("totalInitialMargin")),
            total_maint_margin=to_decimal(payload.get("totalMaintMargin")),
            can_trade=bool(payload.get("canTrade", False)),
            raw=dict(payload),
        )

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        if leverage < 1:
            raise ValueError("leverage must be >= 1")
        return await self._signed_request(  # type: ignore[attr-defined]
            "POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)}
        )

    async def set_margin_mode(self, symbol: str, margin_mode: str) -> dict[str, Any]:
        mode = margin_mode.upper()
        if mode not in ("ISOLATED", "CROSSED"):
            raise ValueError("margin_mode must be ISOLATED or CROSSED")
        return await self._signed_request(  # type: ignore[attr-defined]
            "POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": mode}
        )
