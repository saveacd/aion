"""Risk engine: the mandatory safety boundary in front of every real order.

Design rules (see MASTER_SPEC §9):

* Pure decision logic — no I/O, no ORM, no network. Callers assemble a
  :class:`RiskContext` from the database and exchange state.
* The AI has no write path into these limits.
* Reducing risk (``EXIT`` / ``REDUCE``) is treated differently from opening
  risk (``ENTRY``): closing must stay possible even during an emergency stop,
  but opening is blocked aggressively.
* A missing or broken input never becomes an approval. If the engine cannot
  verify a precondition, it rejects.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

D = Decimal


class ReasonCode(StrEnum):
    OK = "OK"
    INVALID_INTENT = "INVALID_INTENT"
    INTENT_EXPIRED = "INTENT_EXPIRED"
    MODE_NOT_ALLOWED = "MODE_NOT_ALLOWED"
    MAINNET_DISABLED = "MAINNET_DISABLED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    STOP_NEW_TRADES = "STOP_NEW_TRADES"
    AI_PAUSED = "AI_PAUSED"
    SYMBOL_NOT_ALLOWED = "SYMBOL_NOT_ALLOWED"
    LEVERAGE_EXCEEDED = "LEVERAGE_EXCEEDED"
    NOTIONAL_EXCEEDED = "NOTIONAL_EXCEEDED"
    SINGLE_TRADE_LOSS_EXCEEDED = "SINGLE_TRADE_LOSS_EXCEEDED"
    TOTAL_EXPOSURE_EXCEEDED = "TOTAL_EXPOSURE_EXCEEDED"
    DIRECTION_CONCENTRATION = "DIRECTION_CONCENTRATION"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    MARGIN_RATIO_LOW = "MARGIN_RATIO_LOW"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    DATA_QUALITY = "DATA_QUALITY"
    EXCHANGE_DISCONNECTED = "EXCHANGE_DISCONNECTED"
    RECONCILIATION_BLOCKING = "RECONCILIATION_BLOCKING"
    MISSING_STOP_LOSS = "MISSING_STOP_LOSS"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    RISK_ENGINE_UNAVAILABLE = "RISK_ENGINE_UNAVAILABLE"


#: Codes that mean "we could not verify something", never silent approvals.
HARD_BLOCK_CODES = frozenset(
    {
        ReasonCode.INVALID_INTENT,
        ReasonCode.INTENT_EXPIRED,
        ReasonCode.MODE_NOT_ALLOWED,
        ReasonCode.MAINNET_DISABLED,
        ReasonCode.EMERGENCY_STOP,
        ReasonCode.STOP_NEW_TRADES,
        ReasonCode.RECONCILIATION_BLOCKING,
        ReasonCode.RISK_ENGINE_UNAVAILABLE,
    }
)


class IntentKind(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REDUCE = "REDUCE"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """User-owned limits. Bounds are enforced before this object is built."""

    max_leverage: int = 5
    max_position_notional_usd: Decimal = D("1000")
    max_total_exposure_usd: Decimal = D("3000")
    max_single_trade_loss_usd: Decimal = D("25")
    daily_loss_limit_usd: Decimal = D("100")
    max_drawdown_pct: Decimal = D("15")
    max_consecutive_losses: int = 3
    max_open_positions: int = 3
    max_directional_concentration_pct: Decimal = D("70")
    min_margin_ratio: Decimal = D("0.15")
    required_stop_loss: bool = True
    max_spread_bps: Decimal = D("15")
    min_confidence: Decimal = D("0.6")


@dataclass(frozen=True, slots=True)
class IntentView:
    """The subset of a TradeIntent the risk engine needs to decide.

    ``notional_usd`` may be ``None`` when the intent only carries a quantity;
    the caller must resolve it against a price before calling the engine.
    """

    intent_id: str
    symbol: str
    side: str  # BUY | SELL
    position_side: str  # LONG | SHORT
    kind: IntentKind = IntentKind.ENTRY
    notional_usd: Decimal | None = None
    quantity: Decimal | None = None
    reference_price: Decimal | None = None
    requested_leverage: int = 1
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    confidence: Decimal | None = None
    expires_at: dt.datetime | None = None
    mode: str = "PAPER"

    def __post_init__(self) -> None:
        if self.kind is IntentKind.ENTRY and self.position_side not in ("LONG", "SHORT"):
            raise ValueError(f"invalid position_side: {self.position_side}")
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"invalid side: {self.side}")
        if self.requested_leverage < 1:
            raise ValueError("requested_leverage must be >= 1")

    @property
    def effective_notional(self) -> Decimal | None:
        if self.notional_usd is not None:
            return self.notional_usd
        if self.quantity is not None and self.reference_price is not None:
            return self.quantity * self.reference_price
        return None


@dataclass(frozen=True, slots=True)
class PositionView:
    symbol: str
    position_side: str
    notional_usd: Decimal


@dataclass(frozen=True, slots=True)
class ControlsView:
    stop_new_trades: bool = False
    pause_ai: bool = False
    emergency_stop: bool = False
    emergency_reason: str | None = None


@dataclass(slots=True)
class RiskContext:
    mode: str = "PAPER"
    mainnet_enabled: bool = False
    limits: RiskLimits = field(default_factory=RiskLimits)
    allowed_symbols: Sequence[str] = ()
    equity_usd: Decimal = D("0")
    available_balance_usd: Decimal = D("0")
    used_margin_usd: Decimal = D("0")
    margin_ratio: Decimal | None = None
    open_positions: Sequence[PositionView] = ()
    daily_realized_pnl_usd: Decimal = D("0")
    consecutive_losses: int = 0
    drawdown_pct: Decimal = D("0")
    market_age_seconds: float | None = None
    max_market_age_seconds: int = 90
    spread_bps: Decimal | None = None
    data_quality_ok: bool = True
    exchange_connected: bool = False
    reconciliation_blocking: bool = False
    controls: ControlsView = field(default_factory=ControlsView)
    now: dt.datetime | None = None

    @property
    def current_notional(self) -> Decimal:
        return sum((p.notional_usd for p in self.open_positions), D("0"))

    def notional_for_side(self, position_side: str) -> Decimal:
        return sum(
            (p.notional_usd for p in self.open_positions if p.position_side == position_side),
            D("0"),
        )


@dataclass(frozen=True, slots=True)
class RiskOutcome:
    approved: bool
    reason_codes: tuple[ReasonCode, ...]
    checked_limits: dict[str, object]
    risk_snapshot: dict[str, object]
    decided_at: dt.datetime

    @property
    def blocking_codes(self) -> tuple[ReasonCode, ...]:
        return tuple(c for c in self.reason_codes if c is not ReasonCode.OK)

    @property
    def is_hard_block(self) -> bool:
        return any(c in HARD_BLOCK_CODES for c in self.blocking_codes)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class RiskEngine:
    """Deterministic, side-effect free risk evaluation."""

    def evaluate(self, intent: IntentView, ctx: RiskContext) -> RiskOutcome:
        now = ctx.now or _utcnow()
        limits = ctx.limits
        codes: list[ReasonCode] = []
        checked: dict[str, object] = {}

        is_entry = intent.kind is IntentKind.ENTRY
        notional = intent.effective_notional

        # --- 1. intent validity -------------------------------------------
        if not intent.symbol:
            codes.append(ReasonCode.INVALID_INTENT)
        if notional is None or notional <= 0:
            codes.append(ReasonCode.INVALID_INTENT)
        if intent.expires_at is not None:
            expires = intent.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt.UTC)
            if expires <= now:
                codes.append(ReasonCode.INTENT_EXPIRED)

        # --- 2. mode / authorization --------------------------------------
        if intent.mode != ctx.mode:
            codes.append(ReasonCode.MODE_NOT_ALLOWED)
        if ctx.mode == "MAINNET" and not ctx.mainnet_enabled:
            codes.append(ReasonCode.MAINNET_DISABLED)

        # --- 3. controls (entries only) -----------------------------------
        if is_entry:
            if ctx.controls.emergency_stop:
                codes.append(ReasonCode.EMERGENCY_STOP)
            if ctx.controls.stop_new_trades:
                codes.append(ReasonCode.STOP_NEW_TRADES)
            if ctx.controls.pause_ai:
                codes.append(ReasonCode.AI_PAUSED)

        # --- 4. symbol allowlist ------------------------------------------
        if ctx.allowed_symbols and intent.symbol not in ctx.allowed_symbols:
            codes.append(ReasonCode.SYMBOL_NOT_ALLOWED)

        # --- 5. data / connectivity ---------------------------------------
        if is_entry:
            if ctx.market_age_seconds is None or ctx.market_age_seconds > ctx.max_market_age_seconds:
                codes.append(ReasonCode.STALE_MARKET_DATA)
            if not ctx.data_quality_ok:
                codes.append(ReasonCode.DATA_QUALITY)
            # Paper trading has no exchange session to verify, so connectivity
            # and reconciliation only gate the modes that hit a real venue.
            if ctx.mode != "PAPER":
                if not ctx.exchange_connected:
                    codes.append(ReasonCode.EXCHANGE_DISCONNECTED)
                if ctx.reconciliation_blocking:
                    codes.append(ReasonCode.RECONCILIATION_BLOCKING)

        if ctx.spread_bps is not None and ctx.spread_bps > limits.max_spread_bps and is_entry:
            codes.append(ReasonCode.SPREAD_TOO_WIDE)

        # --- 6. exposure limits (entries only) ----------------------------
        if is_entry and notional is not None:
            if intent.requested_leverage > limits.max_leverage:
                codes.append(ReasonCode.LEVERAGE_EXCEEDED)
            if notional > limits.max_position_notional_usd:
                codes.append(ReasonCode.NOTIONAL_EXCEEDED)

            projected_total = ctx.current_notional + notional
            if projected_total > limits.max_total_exposure_usd:
                codes.append(ReasonCode.TOTAL_EXPOSURE_EXCEEDED)

            if len(ctx.open_positions) >= limits.max_open_positions:
                codes.append(ReasonCode.MAX_OPEN_POSITIONS)

            same_side = ctx.notional_for_side(intent.position_side) + notional
            # Concentration is measured against equity, not against the book:
            # the first position on an empty book is always 100% of the book.
            if ctx.equity_usd > 0:
                share = (same_side / ctx.equity_usd) * D("100")
                if share > limits.max_directional_concentration_pct:
                    codes.append(ReasonCode.DIRECTION_CONCENTRATION)

            margin_needed = notional / D(intent.requested_leverage)
            if margin_needed > ctx.available_balance_usd:
                codes.append(ReasonCode.INSUFFICIENT_MARGIN)

            if limits.required_stop_loss and intent.stop_loss is None:
                codes.append(ReasonCode.MISSING_STOP_LOSS)

            loss_estimate = _estimate_loss(intent, notional)
            # Fail closed (follow-up audit #7): without a stop the only
            # honest upper bound on the loss is the whole notional, so the
            # cap applies EVEN IF required_stop_loss is disabled.
            if (loss_estimate is not None and loss_estimate > limits.max_single_trade_loss_usd) or (
                loss_estimate is None and notional > limits.max_single_trade_loss_usd
            ):
                codes.append(ReasonCode.SINGLE_TRADE_LOSS_EXCEEDED)

            if intent.confidence is not None and intent.confidence < limits.min_confidence:
                codes.append(ReasonCode.LOW_CONFIDENCE)

            # Aggregate guards apply to opening risk as well.
            if ctx.daily_realized_pnl_usd <= -limits.daily_loss_limit_usd:
                codes.append(ReasonCode.DAILY_LOSS_LIMIT)
            if ctx.drawdown_pct >= limits.max_drawdown_pct:
                codes.append(ReasonCode.MAX_DRAWDOWN)
            if ctx.consecutive_losses >= limits.max_consecutive_losses:
                codes.append(ReasonCode.CONSECUTIVE_LOSSES)
            # ``margin_ratio`` is the maintenance-margin FRACTION
            # (maintMargin / marginBalance, Binance semantics): HIGHER means
            # closer to liquidation. ``min_margin_ratio`` is the required
            # margin buffer, so the blocking condition is fraction >
            # 1 - buffer (audit BUG-03 — the old `<` comparison fired when
            # the account was SAFE and passed when it was near liquidation).
            if ctx.margin_ratio is not None and ctx.margin_ratio > (
                D("1") - limits.min_margin_ratio
            ):
                codes.append(ReasonCode.MARGIN_RATIO_LOW)

        checked["max_leverage"] = limits.max_leverage
        checked["requested_leverage"] = intent.requested_leverage
        checked["max_position_notional_usd"] = str(limits.max_position_notional_usd)
        checked["requested_notional_usd"] = str(notional) if notional is not None else None
        checked["projected_exposure_usd"] = str(ctx.current_notional + (notional or D("0")))
        checked["max_total_exposure_usd"] = str(limits.max_total_exposure_usd)
        checked["open_positions"] = len(ctx.open_positions)
        checked["max_open_positions"] = limits.max_open_positions
        checked["daily_realized_pnl_usd"] = str(ctx.daily_realized_pnl_usd)
        checked["daily_loss_limit_usd"] = str(limits.daily_loss_limit_usd)
        checked["drawdown_pct"] = str(ctx.drawdown_pct)
        checked["max_drawdown_pct"] = str(limits.max_drawdown_pct)
        checked["market_age_seconds"] = ctx.market_age_seconds
        checked["kind"] = intent.kind.value
        checked["mode"] = ctx.mode

        snapshot = {
            "equity_usd": str(ctx.equity_usd),
            "available_balance_usd": str(ctx.available_balance_usd),
            "used_margin_usd": str(ctx.used_margin_usd),
            "margin_ratio": str(ctx.margin_ratio) if ctx.margin_ratio is not None else None,
            "current_notional_usd": str(ctx.current_notional),
            "open_positions": [
                {"symbol": p.symbol, "position_side": p.position_side, "notional_usd": str(p.notional_usd)}
                for p in ctx.open_positions
            ],
            "controls": {
                "stop_new_trades": ctx.controls.stop_new_trades,
                "pause_ai": ctx.controls.pause_ai,
                "emergency_stop": ctx.controls.emergency_stop,
            },
            "exchange_connected": ctx.exchange_connected,
            "reconciliation_blocking": ctx.reconciliation_blocking,
        }

        unique = list(dict.fromkeys(codes))
        approved = not unique
        return RiskOutcome(
            approved=approved,
            reason_codes=tuple([ReasonCode.OK] if approved else unique),
            checked_limits=checked,
            risk_snapshot=snapshot,
            decided_at=now,
        )


def _estimate_loss(intent: IntentView, notional: Decimal) -> Decimal | None:
    """Estimated loss to the protective stop, in USD.

    ``None`` means "cannot be determined", which the caller must not treat as
    approval when stops are required.
    """
    if intent.stop_loss is None:
        return None
    entry = intent.reference_price
    if entry is None or entry <= 0:
        return None
    qty = intent.quantity if intent.quantity is not None else notional / entry
    distance = abs(entry - intent.stop_loss)
    if distance <= 0:
        return None
    return distance * qty


def clamp_limits(current: RiskLimits, updates: dict[str, object]) -> RiskLimits:
    """Apply a settings update, rejecting values outside absolute bounds.

    Raises ``ValueError`` with a human readable reason; the API layer maps it
    to HTTP 422. Absolute bounds cannot be raised by any client input.
    """
    from aion.risk.limits import ABSOLUTE

    values = {f: getattr(current, f) for f in current.__slots__}  # type: ignore[attr-defined]
    for key, raw in updates.items():
        if key not in values:
            raise ValueError(f"unknown limit: {key}")
        if key == "required_stop_loss":
            values[key] = bool(raw)
            if values[key] is False:
                # A user may disable stops, but the engine still records it.
                values[key] = False
            continue
        try:
            cast = D(str(raw))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{key} must be numeric") from exc
        if cast < 0:
            raise ValueError(f"{key} must be >= 0")
        bound = ABSOLUTE.get(key)
        if bound is not None and cast > bound:
            raise ValueError(f"{key} exceeds absolute maximum {bound}")
        values[key] = cast
    return RiskLimits(**values)
