"""Simulated broker for PAPER mode.

Honesty rules (MASTER_SPEC §1.3): every fill is derived from the latest real
market snapshot plus a declared slippage/fee model — nothing is invented. All
resulting orders/fills/positions are persisted with ``mode="PAPER"`` and the
order payload carries ``{"simulated": true}`` so no one can mistake them for
exchange executions.

Equity accounting convention:
* entry fills: ``realized_pnl`` is NULL, ``commission`` holds the entry fee;
* exit fills: ``realized_pnl`` = gross pnl − exit commission;
* wallet equity = start_equity + Σ(realized_pnl) − Σ(commissions on entry fills).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.models import (
    AccountSnapshot,
    Fill,
    MarketSnapshot,
    Order,
    OrderEvent,
    PaperPosition,
    PositionSnapshot,
    TradeIntent,
    TradeOutcome,
)
from aion.models.agent import AgentMessage
from aion.models.base import utcnow
from aion.models.trading import RiskDecision
from aion.services.market_stream import latest_price as live_price

logger = logging.getLogger(__name__)

D = Decimal
DEFAULT_START_EQUITY = D("10000")
DEFAULT_FEE_BPS = D("4")
DEFAULT_SLIPPAGE_BPS = D("2")


def paper_client_order_id(intent_key: str) -> str:
    """Stable, collision-free id: hash instead of silent truncation.

    The old ``f"paper-{key}"[:64]`` truncated long keys, so the dedupe lookup
    and the inserted row could disagree and retries would double-open.
    """
    digest = hashlib.sha256(f"paper-{intent_key}".encode()).hexdigest()
    return f"paper-{digest[:58]}"


class PaperBroker:
    """Fills MARKET intents against the latest ingested market price."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        fee_bps: Decimal = DEFAULT_FEE_BPS,
        slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS,
        start_equity: Decimal = DEFAULT_START_EQUITY,
    ) -> None:
        self._session_factory = session_factory
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.start_equity = start_equity

    async def latest_price(self, db: AsyncSession, symbol: str) -> Decimal | None:
        # Prefer the live ticker feed: PAPER fills and mark-to-market should use
        # the current price, not a snapshot that may be a minute old.
        live = live_price(symbol)
        if live is not None and (utcnow() - live["ts"]).total_seconds() <= 30:
            return D(live["price"])
        snapshot = await db.scalar(
            select(MarketSnapshot)
            .where(MarketSnapshot.symbol == symbol)
            .order_by(MarketSnapshot.captured_at.desc())
            .limit(1)
        )
        if snapshot is None or snapshot.last_price is None:
            return None
        return D(snapshot.last_price)

    async def refresh_snapshots(self, user_id: uuid.UUID) -> None:
        """Re-mark a PAPER account against current prices (own transaction)."""
        async with self._session_factory() as db:
            await self._write_snapshots(db, user_id)
            await db.commit()

    async def ensure_account(self, user_id: uuid.UUID) -> None:
        """Write the initial paper account snapshot if the user has none."""
        async with self._session_factory() as db:
            existing = await db.scalar(
                select(AccountSnapshot.id)
                .where(AccountSnapshot.mode == "PAPER", AccountSnapshot.user_id == user_id)
                .limit(1)
            )
            if existing is not None:
                return
            now = utcnow()
            db.add(
                AccountSnapshot(
                    user_id=user_id,
                    mode="PAPER",
                    captured_at=now,
                    total_wallet_balance=self.start_equity,
                    total_unrealized_pnl=D("0"),
                    total_margin_balance=self.start_equity,
                    available_balance=self.start_equity,
                    total_initial_margin=D("0"),
                    total_maint_margin=D("0"),
                    margin_ratio=None,
                    equity_peak_usd=self.start_equity,
                    drawdown_pct=D("0"),
                    daily_realized_pnl=D("0"),
                    raw={"simulated": True, "start_equity_usd": str(self.start_equity)},
                )
            )
            await db.commit()

    def _apply_slippage(self, price: Decimal, side: str) -> Decimal:
        factor = self.slippage_bps / D("10000")
        # Slippage always works against the taker.
        return price * (1 + factor) if side == "BUY" else price * (1 - factor)

    def _fee(self, notional: Decimal) -> Decimal:
        return notional * self.fee_bps / D("10000")

    async def submit_entry(
        self, db: AsyncSession, intent: TradeIntent, decision: RiskDecision
    ) -> Order | None:
        """Fill an APPROVED entry intent. Returns None when no price is available.

        The caller owns the transaction (commit). Idempotent via the intent key:
        a second call for the same intent returns the existing order.
        """
        existing = await db.scalar(
            select(Order).where(Order.client_order_id == paper_client_order_id(intent.intent_key))
        )
        if existing is not None:
            return existing

        mark = await self.latest_price(db, intent.symbol)
        if mark is None or mark <= 0:
            return None

        fill_price = self._apply_slippage(mark, intent.side)
        if intent.quantity is not None and D(intent.quantity) > 0:
            quantity = D(intent.quantity)
        else:
            quantity = D(intent.notional_usd) / fill_price
        notional = quantity * fill_price
        commission = self._fee(notional)
        now = utcnow()

        order = Order(
            client_order_id=paper_client_order_id(intent.intent_key),
            exchange_order_id=f"paperex-{uuid.uuid4().hex[:24]}",
            intent_id=intent.id,
            risk_decision_id=decision.id,
            symbol=intent.symbol,
            side=intent.side,
            position_side=intent.position_side,
            order_type=intent.order_type or "MARKET",
            purpose="ENTRY",
            reduce_only=False,
            quantity=quantity,
            price=fill_price,
            filled_quantity=quantity,
            avg_fill_price=fill_price,
            last_fill_quantity=quantity,
            status="FILLED",
            time_in_force=intent.time_in_force,
            mode="PAPER",
            submit_attempts=1,
            last_exchange_response={"simulated": True, "reference_price": str(mark)},
            submitted_at=now,
            terminal_at=now,
            user_id=intent.user_id,
        )
        db.add(order)
        await db.flush()

        db.add(
            Fill(
                order_id=order.id,
                exchange_trade_id=f"pt-{uuid.uuid4().hex[:24]}",
                symbol=intent.symbol,
                side=intent.side,
                price=fill_price,
                quantity=quantity,
                quote_quantity=notional,
                commission=commission,
                commission_asset="USDT",
                realized_pnl=None,
                is_maker=False,
                traded_at=now,
            )
        )
        db.add(
            OrderEvent(
                order_id=order.id,
                event_type="FILLED",
                from_status="CREATED",
                to_status="FILLED",
                payload={"simulated": True, "fill_price": str(fill_price)},
            )
        )
        leverage = max(1, int(intent.requested_leverage or 1))
        db.add(
            PaperPosition(
                user_id=intent.user_id,
                intent_id=intent.id,
                entry_order_id=order.id,
                symbol=intent.symbol,
                position_side=intent.position_side,
                quantity=quantity,
                entry_price=fill_price,
                stop_loss=intent.stop_loss,
                take_profit=intent.take_profit,
                leverage=leverage,
                margin_usd=notional / leverage,
                status="OPEN",
                opened_at=now,
            )
        )
        await db.flush()
        await self._write_snapshots(db, intent.user_id)
        return order

    async def close_position(
        self,
        db: AsyncSession,
        position: PaperPosition,
        *,
        exit_price: Decimal,
        reason: str,
    ) -> TradeOutcome:
        """Close one OPEN paper position and record the trade outcome.

        Re-checks the status inside the same transaction flush so concurrent
        callers (manual close + learning sweep + autopilot) cannot double-close
        one position and book the loss twice.
        """
        await db.refresh(position)
        if position.status != "OPEN":
            raise ValueError("position is not OPEN (already closed?)")
        side = "SELL" if position.position_side == "LONG" else "BUY"
        fill_price = self._apply_slippage(exit_price, side)
        quantity = D(position.quantity)
        entry = D(position.entry_price)
        notional = quantity * fill_price
        commission = self._fee(notional)
        if position.position_side == "LONG":
            gross = (fill_price - entry) * quantity
        else:
            gross = (entry - fill_price) * quantity
        realized = gross - commission
        now = utcnow()

        order = Order(
            client_order_id=f"paper-exit-{position.id.hex[:24]}",
            exchange_order_id=f"paperex-{uuid.uuid4().hex[:24]}",
            intent_id=None,
            risk_decision_id=None,
            symbol=position.symbol,
            side=side,
            position_side=position.position_side,
            order_type="MARKET",
            purpose="EXIT",
            reduce_only=True,
            quantity=quantity,
            price=fill_price,
            filled_quantity=quantity,
            avg_fill_price=fill_price,
            last_fill_quantity=quantity,
            status="FILLED",
            time_in_force="GTC",
            mode="PAPER",
            submit_attempts=1,
            last_exchange_response={"simulated": True, "exit_reason": reason},
            submitted_at=now,
            terminal_at=now,
            user_id=position.user_id,
        )
        db.add(order)
        await db.flush()
        db.add(
            Fill(
                order_id=order.id,
                exchange_trade_id=f"pt-{uuid.uuid4().hex[:24]}",
                symbol=position.symbol,
                side=side,
                price=fill_price,
                quantity=quantity,
                quote_quantity=notional,
                commission=commission,
                commission_asset="USDT",
                realized_pnl=realized,
                is_maker=False,
                traded_at=now,
            )
        )
        db.add(
            OrderEvent(
                order_id=order.id,
                event_type="FILLED",
                from_status="CREATED",
                to_status="FILLED",
                payload={"simulated": True, "exit_reason": reason, "fill_price": str(fill_price)},
            )
        )

        position.status = "CLOSED"
        position.exit_reason = reason
        position.closed_at = now
        position.exit_order_id = order.id

        intent = (
            await db.get(TradeIntent, position.intent_id) if position.intent_id else None
        )
        r_multiple: Decimal | None = None
        if position.stop_loss:
            risk_per_unit = abs(entry - D(position.stop_loss))
            if risk_per_unit > 0:
                r_multiple = gross / (risk_per_unit * quantity)
        holding = D(str((now - position.opened_at).total_seconds() / 60))

        outcome = TradeOutcome(
            user_id=position.user_id,
            intent_id=position.intent_id,
            run_id=intent.run_id if intent else None,
            strategy_id=intent.strategy_id if intent else None,
            symbol=position.symbol,
            position_side=position.position_side,
            quantity=quantity,
            entry_price=entry,
            exit_price=fill_price,
            pnl_usd=realized,
            fee_usd=commission,
            r_multiple=r_multiple,
            holding_minutes=holding,
            regime=await self._regime_at_entry(db, intent),
            confidence=intent.confidence if intent else None,
            exit_reason=reason,
            opened_at=position.opened_at,
            closed_at=now,
        )
        db.add(outcome)
        await db.flush()
        await self._write_snapshots(db, position.user_id)
        return outcome

    async def _regime_at_entry(self, db: AsyncSession, intent: TradeIntent | None) -> str | None:
        if intent is None or intent.run_id is None:
            return None
        message = await db.scalar(
            select(AgentMessage)
            .where(
                AgentMessage.run_id == intent.run_id,
                AgentMessage.agent_name == "market_regime",
                AgentMessage.status == "OK",
            )
            .limit(1)
        )
        if message is None:
            return None
        regime = (message.structured or {}).get("regime")
        return str(regime)[:32] if regime else None

    async def _write_snapshots(self, db: AsyncSession, user_id: uuid.UUID | None) -> None:
        """Rebuild the PAPER account/position snapshots from the ledger."""
        if user_id is None:
            return
        now = utcnow()

        fills = (
            await db.execute(
                select(Fill)
                .join(Order, Fill.order_id == Order.id)
                .where(Order.user_id == user_id, Order.mode == "PAPER")
            )
        ).scalars().all()
        realized = sum((D(f.realized_pnl) for f in fills if f.realized_pnl is not None), D("0"))
        entry_fees = sum(
            (D(f.commission) for f in fills if f.realized_pnl is None), D("0")
        )
        today = now.date()
        daily_realized = sum(
            (
                D(f.realized_pnl)
                for f in fills
                if f.realized_pnl is not None and f.traded_at.date() == today
            ),
            D("0"),
        )

        open_positions = (
            await db.execute(
                select(PaperPosition).where(
                    PaperPosition.user_id == user_id, PaperPosition.status == "OPEN"
                )
            )
        ).scalars().all()

        equity = self.start_equity + realized - entry_fees
        margin_used = sum((D(p.margin_usd) for p in open_positions), D("0"))
        unrealized = D("0")
        marks: dict[str, Decimal] = {}
        for position in open_positions:
            mark = await self.latest_price(db, position.symbol)
            if mark is None:
                mark = D(position.entry_price)
            marks[position.symbol] = mark
            if position.position_side == "LONG":
                unrealized += (mark - D(position.entry_price)) * D(position.quantity)
            else:
                unrealized += (D(position.entry_price) - mark) * D(position.quantity)

        margin_balance = equity + unrealized
        previous_peak = await db.scalar(
            select(func.max(AccountSnapshot.equity_peak_usd)).where(
                AccountSnapshot.mode == "PAPER", AccountSnapshot.user_id == user_id
            )
        )
        peak = max(D(previous_peak) if previous_peak is not None else D("0"), margin_balance)
        drawdown = ((peak - margin_balance) / peak * 100) if peak > 0 else D("0")

        db.add(
            AccountSnapshot(
                user_id=user_id,
                mode="PAPER",
                captured_at=now,
                total_wallet_balance=equity,
                total_unrealized_pnl=unrealized,
                total_margin_balance=margin_balance,
                available_balance=equity - margin_used,
                total_initial_margin=margin_used,
                total_maint_margin=D("0"),
                margin_ratio=None,
                equity_peak_usd=peak,
                drawdown_pct=drawdown,
                daily_realized_pnl=daily_realized,
                raw={"simulated": True, "start_equity_usd": str(self.start_equity)},
            )
        )
        for position in open_positions:
            mark = marks[position.symbol]
            if position.position_side == "LONG":
                upnl = (mark - D(position.entry_price)) * D(position.quantity)
            else:
                upnl = (D(position.entry_price) - mark) * D(position.quantity)
            db.add(
                PositionSnapshot(
                    user_id=user_id,
                    mode="PAPER",
                    captured_at=now,
                    symbol=position.symbol,
                    position_side=position.position_side,
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    mark_price=mark,
                    unrealized_pnl=upnl,
                    leverage=position.leverage,
                    margin_mode="CROSSED",
                    has_stop_loss=position.stop_loss is not None,
                    has_take_profit=position.take_profit is not None,
                    stop_loss_price=position.stop_loss,
                    take_profit_price=position.take_profit,
                )
            )


__all__ = [
    "DEFAULT_FEE_BPS",
    "DEFAULT_SLIPPAGE_BPS",
    "DEFAULT_START_EQUITY",
    "PaperBroker",
]
