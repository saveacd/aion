from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from aion.schemas.common import AionModel, ORMModel, Price, Qty

Side = Literal["BUY", "SELL"]
PositionSide = Literal["LONG", "SHORT"]
OrderType = Literal["MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT"]
TimeInForce = Literal["GTC", "IOC", "FOK", "GTX"]


class TradeIntentIn(AionModel):
    """Manual intent from the app. Still has to clear the risk engine."""

    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Z0-9]{3,32}$")
    side: Side
    position_side: PositionSide
    order_type: OrderType = "MARKET"
    quantity: Qty | None = None
    notional_usd: Qty | None = None
    requested_leverage: int = Field(default=1, ge=1, le=20)
    entry_price: Price | None = None
    stop_loss: Price | None = None
    take_profit: Price | None = None
    time_in_force: TimeInForce = "GTC"
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    reason: str | None = Field(default=None, max_length=2000)
    client_intent_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _sizing_present(self) -> "TradeIntentIn":
        if self.quantity is None and self.notional_usd is None:
            raise ValueError("provide either quantity or notional_usd")
        if self.order_type == "LIMIT" and self.entry_price is None:
            raise ValueError("LIMIT orders require entry_price")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.notional_usd is not None and self.notional_usd <= 0:
            raise ValueError("notional_usd must be positive")
        # Entries open the position in the direction of the side: BUY opens
        # LONG, SELL opens SHORT. Any other combination would silently reduce
        # or flip an existing hedge-mode position (audit BUG-14).
        if (self.side, self.position_side) not in (("BUY", "LONG"), ("SELL", "SHORT")):
            raise ValueError(
                f"invalid side/position_side combination: {self.side}/{self.position_side}"
            )
        return self

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class TradeIntentOut(ORMModel):
    id: uuid.UUID
    intent_key: str
    run_id: uuid.UUID | None
    symbol: str
    side: str
    position_side: str
    intent_type: str
    order_type: str
    quantity: Qty | None
    notional_usd: Qty | None
    requested_leverage: int
    entry_price: Price | None
    stop_loss: Price | None
    take_profit: Price | None
    status: str
    mode: str
    reason: str | None
    rejection_code: str | None
    rejection_detail: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime
    expires_at: dt.datetime


class RiskDecisionOut(ORMModel):
    id: uuid.UUID
    intent_id: uuid.UUID | None
    symbol: str
    approved: bool
    reason_codes: list[str] = Field(default_factory=list)
    checked_limits: dict[str, Any] = Field(default_factory=dict)
    risk_snapshot: dict[str, Any] = Field(default_factory=dict)
    mode: str
    decided_at: dt.datetime
    latency_ms: int | None


class IntentResult(AionModel):
    intent: TradeIntentOut
    risk: RiskDecisionOut | None = None
    orders: list["OrderOut"] = Field(default_factory=list)
    accepted: bool


class OrderOut(ORMModel):
    id: uuid.UUID
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: str
    position_side: str
    order_type: str
    purpose: str
    reduce_only: bool
    quantity: Qty
    price: Price | None
    stop_price: Price | None
    filled_quantity: Qty
    avg_fill_price: Price | None
    status: str
    mode: str
    submit_attempts: int
    error_code: str | None
    error_message: str | None
    created_at: dt.datetime
    submitted_at: dt.datetime | None
    terminal_at: dt.datetime | None


class OrderEventOut(ORMModel):
    id: uuid.UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime


class FillOut(ORMModel):
    id: uuid.UUID
    exchange_trade_id: str
    symbol: str
    side: str
    price: Price
    quantity: Qty
    quote_quantity: Qty
    commission: Qty
    commission_asset: str | None
    realized_pnl: Price | None
    is_maker: bool
    traded_at: dt.datetime


class OrderDetail(OrderOut):
    events: list[OrderEventOut] = Field(default_factory=list)
    fills: list[FillOut] = Field(default_factory=list)


class PositionOut(AionModel):
    symbol: str
    position_side: str
    quantity: Qty
    entry_price: Price
    mark_price: Price | None = None
    unrealized_pnl: Price
    unrealized_pnl_pct: Price | None = None
    liquidation_price: Price | None = None
    leverage: int
    margin_mode: str
    notional_usd: Price | None = None
    has_stop_loss: bool = False
    has_take_profit: bool = False
    stop_loss_price: Price | None = None
    take_profit_price: Price | None = None
    protection_verified: bool = False


class AccountOut(AionModel):
    mode: str
    total_wallet_balance: Price
    total_unrealized_pnl: Price
    total_margin_balance: Price
    available_balance: Price
    total_initial_margin: Price
    total_maint_margin: Price
    margin_ratio: Price | None = None
    drawdown_pct: Price | None = None
    daily_realized_pnl: Price
    open_positions: int = 0
    captured_at: dt.datetime | None = None
    stale: bool = False


class PortfolioOut(AionModel):
    account: AccountOut
    positions: list[PositionOut] = Field(default_factory=list)


class ClosePositionRequest(AionModel):
    symbol: str = Field(min_length=3, max_length=32)
    position_side: PositionSide
    percentage: int = Field(default=100, ge=1, le=100)
    reason: str | None = Field(default=None, max_length=500)


class CancelOrderRequest(AionModel):
    reason: str | None = Field(default=None, max_length=200)


class SetProtectionRequest(AionModel):
    symbol: str = Field(min_length=3, max_length=32)
    position_side: PositionSide
    stop_loss: Price | None = None
    take_profit: Price | None = None

    @model_validator(mode="after")
    def _one_present(self) -> "SetProtectionRequest":
        if self.stop_loss is None and self.take_profit is None:
            raise ValueError("provide stop_loss and/or take_profit")
        return self


OrderDetail.model_rebuild()
