"""Glue between the database and the pure risk engine.

The engine itself never touches the ORM. This module assembles a
:class:`~aion.risk.engine.RiskContext` from persisted state, runs the decision,
and writes the immutable ``risk_decisions`` record that makes the rejection
explainable months later.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.core.config import Settings, get_settings
from aion.models import (
    AccountSnapshot,
    ExchangeAccount,
    Fill,
    MarketSnapshot,
    MarketSymbol,
    Order,
    PositionSnapshot,
    ReconciliationRun,
    RiskDecision,
    RiskEvent,
    RiskPolicy,
    TradeIntent,
    TradingControlState,
)
from aion.models.base import utcnow
from aion.risk.engine import (
    ControlsView,
    IntentKind,
    IntentView,
    PositionView,
    RiskContext,
    RiskEngine,
    RiskLimits,
    RiskOutcome,
)

D = Decimal
_ENGINE = RiskEngine()


def _limits_from_policy(policy: RiskPolicy | None) -> RiskLimits:
    if policy is None:
        return RiskLimits()
    return RiskLimits(
        max_leverage=policy.max_leverage,
        max_position_notional_usd=D(policy.max_position_notional_usd),
        max_total_exposure_usd=D(policy.max_total_exposure_usd),
        max_single_trade_loss_usd=D(policy.max_single_trade_loss_usd),
        daily_loss_limit_usd=D(policy.daily_loss_limit_usd),
        max_drawdown_pct=D(policy.max_drawdown_pct),
        max_consecutive_losses=policy.max_consecutive_losses,
        max_open_positions=policy.max_open_positions,
        max_directional_concentration_pct=D(policy.max_directional_concentration_pct),
        min_margin_ratio=D(policy.min_margin_ratio),
        required_stop_loss=policy.required_stop_loss,
        max_spread_bps=D(policy.max_spread_bps),
        min_confidence=D(policy.min_confidence),
    )


async def _latest_snapshot(
    db: AsyncSession, mode: str, user_id: uuid.UUID | None = None
) -> AccountSnapshot | None:
    stmt = select(AccountSnapshot).where(AccountSnapshot.mode == mode)
    if user_id is not None:
        stmt = stmt.where(AccountSnapshot.user_id == user_id)
    return await db.scalar(stmt.order_by(AccountSnapshot.captured_at.desc()).limit(1))


async def _open_positions(
    db: AsyncSession, mode: str, user_id: uuid.UUID | None = None
) -> list[PositionSnapshot]:
    latest_stmt = select(func.max(PositionSnapshot.captured_at)).where(
        PositionSnapshot.mode == mode
    )
    if user_id is not None:
        latest_stmt = latest_stmt.where(PositionSnapshot.user_id == user_id)
    latest = await db.scalar(latest_stmt)
    if latest is None:
        return []
    stmt = select(PositionSnapshot).where(
        PositionSnapshot.mode == mode, PositionSnapshot.captured_at == latest
    )
    if user_id is not None:
        stmt = stmt.where(PositionSnapshot.user_id == user_id)
    rows = await db.execute(stmt)
    return [row for row in rows.scalars().all() if D(row.quantity) != 0]


async def _market_snapshot(db: AsyncSession, symbol: str) -> MarketSnapshot | None:
    return await db.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == symbol)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(1)
    )


async def _reconciliation_blocking(
    db: AsyncSession, mode: str, user_id: uuid.UUID | None = None
) -> bool:
    stmt = (
        select(func.count())
        .select_from(ReconciliationRun)
        .where(
            ReconciliationRun.mode == mode,
            ReconciliationRun.blocking.is_(True),
            ReconciliationRun.resolved.is_(False),
        )
    )
    if user_id is not None:
        # Scope to this user's runs plus global (operator) runs; another
        # user's reconciliation failure must not block this one (BUG-21).
        stmt = stmt.where(
            or_(ReconciliationRun.user_id == user_id, ReconciliationRun.user_id.is_(None))
        )
    count = await db.scalar(stmt)
    return bool(count)


async def _consecutive_losses(
    db: AsyncSession, limit: int = 10, user_id: uuid.UUID | None = None
) -> int:
    """Count consecutive losing *trades*, not partial fills.

    One position closing in several fills used to count as several losses and
    trip CONSECUTIVE_LOSSES early. Group fills by order (a position's exit is
    one order) and judge one loss per order.
    """
    stmt = (
        select(Fill.order_id, Fill.realized_pnl)
        .where(Fill.realized_pnl.is_not(None))
        .order_by(Fill.traded_at.desc())
        .limit(limit * 5)
    )
    if user_id is not None:
        stmt = stmt.where(Fill.order_id.in_(select(Order.id).where(Order.user_id == user_id)))
    rows = await db.execute(stmt)
    streak = 0
    seen_orders: set[uuid.UUID] = set()
    for order_id, pnl in rows.all():
        if order_id in seen_orders:
            continue  # another partial fill of the same exit
        seen_orders.add(order_id)
        if pnl is not None and D(pnl) < 0:
            streak += 1
        else:
            break
        if streak >= limit:
            break
    return streak


async def _allowed_symbols(db: AsyncSession, settings: Settings) -> tuple[str, ...]:
    rows = await db.execute(select(MarketSymbol.symbol).where(MarketSymbol.is_allowed.is_(True)))
    db_symbols = tuple(rows.scalars().all())
    if db_symbols:
        return db_symbols
    return tuple(settings.default_symbols)


async def _exchange_connected(
    db: AsyncSession, mode: str, user_id: uuid.UUID | None = None
) -> bool:
    if mode == "PAPER":
        return True
    stmt = select(ExchangeAccount).where(
        ExchangeAccount.mode == mode, ExchangeAccount.is_active.is_(True)
    )
    if user_id is not None:
        # Never judge this user's connectivity from another user's account
        # row (audit BUG-13).
        stmt = stmt.where(ExchangeAccount.user_id == user_id)
    account = await db.scalar(stmt)
    if account is None:
        return False
    if account.last_checked_at is None:
        return False
    age = utcnow() - account.last_checked_at
    return age.total_seconds() < 300


async def _live_spread_bps(
    db: AsyncSession,
    settings: Settings,
    symbol: str,
    mode: str,
    user_id: uuid.UUID | None,
) -> tuple[Decimal | None, bool]:
    """(top-of-book spread in bps, book_ok) for live modes.

    ``book_ok=False`` means the book could not be fetched — the caller must
    treat live entries as UNVERIFIED (follow-up audit #10: a dead feed must
    block new risk, not silently skip the spread gate). Paper returns
    ``(None, True)``.
    """
    if mode not in ("TESTNET", "MAINNET") or user_id is None:
        return None, True
    client = None
    try:
        from aion.services.live_trading import resolve_live_client

        client = await resolve_live_client(db, settings, user_id, mode)
        if client is None:
            return None, False
        book = await client.depth(symbol, limit=5)
        if not book.bids or not book.asks:
            return None, False
        bid, ask = D(book.bids[0].price), D(book.asks[0].price)
        if bid <= 0 or ask <= 0 or ask < bid:
            return None, False
        mid = (bid + ask) / 2
        if mid <= 0:
            return None, False
        return (ask - bid) / mid * D("10000"), True
    except Exception:  # noqa: BLE001 - dead book => unverified, never "pass"
        return None, False
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()


async def build_context(
    db: AsyncSession,
    *,
    symbol: str,
    mode: str,
    settings: Settings | None = None,
    user_id: uuid.UUID | None = None,
    spread_bps: Decimal | None = None,
) -> tuple[RiskContext, RiskLimits]:
    settings = settings or get_settings()

    policy = None
    if user_id is not None:
        policy = await db.scalar(
            select(RiskPolicy)
            .where(RiskPolicy.is_active.is_(True), RiskPolicy.user_id == user_id)
            .limit(1)
        )
    if policy is None:
        # Global fallback must only ever match operator-owned (NULL user_id)
        # policies, otherwise user A inherits user B's policy (BUG-12).
        policy = await db.scalar(
            select(RiskPolicy)
            .where(
                RiskPolicy.is_active.is_(True),
                RiskPolicy.user_id.is_(None),
            )
            .limit(1)
        )
    limits = _limits_from_policy(policy)
    control = await db.get(TradingControlState, 1)
    snapshot = await _latest_snapshot(db, mode, user_id)
    positions = await _open_positions(db, mode, user_id)
    market = await _market_snapshot(db, symbol)
    now = utcnow()

    # Real top-of-book spread for live modes (audits #5/#10): dead book => ok=False.
    _book_spread, book_ok = await _live_spread_bps(db, settings, symbol, mode, user_id)

    age_seconds: float | None = None
    if market is not None:
        captured = market.captured_at
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=dt.UTC)
        age_seconds = max((now - captured).total_seconds(), 0.0)

    ctx = RiskContext(
        mode=mode,
        mainnet_enabled=settings.mainnet_enabled,
        limits=limits,
        allowed_symbols=await _allowed_symbols(db, settings),
        equity_usd=D(snapshot.total_wallet_balance) if snapshot else D("0"),
        available_balance_usd=D(snapshot.available_balance) if snapshot else D("0"),
        used_margin_usd=D(snapshot.total_initial_margin) if snapshot else D("0"),
        margin_ratio=D(snapshot.margin_ratio) if snapshot and snapshot.margin_ratio else None,
        open_positions=[
            PositionView(
                symbol=p.symbol,
                position_side=p.position_side,
                # Short snapshots carry signed quantities; notional must be
                # unsigned so longs and shorts ADD UP instead of canceling
                # (audit BUG-01).
                notional_usd=abs(D(p.quantity)) * D(p.entry_price),
            )
            for p in positions
        ],
        daily_realized_pnl_usd=D(snapshot.daily_realized_pnl) if snapshot else D("0"),
        consecutive_losses=await _consecutive_losses(db, user_id=user_id),
        drawdown_pct=D(snapshot.drawdown_pct) if snapshot and snapshot.drawdown_pct else D("0"),
        market_age_seconds=age_seconds,
        max_market_age_seconds=settings.market_stale_seconds,
        # Live modes fetch the REAL top-of-book spread so SPREAD_TOO_WIDE is
        # an active gate (follow-up audit #5). Paper has no book: None.
        spread_bps=_book_spread if spread_bps is None else spread_bps,
        # Dead/unreachable order book on a live account = unverified market:
        # DATA_QUALITY blocks the entry (follow-up audit #10). The snapshot
        # price check is the DB-side data-quality gate (audit BUG-11).
        data_quality_ok=(
            market is not None and D(market.last_price) > 0 and book_ok
        ),
        exchange_connected=await _exchange_connected(db, mode, user_id),
        reconciliation_blocking=await _reconciliation_blocking(db, mode, user_id),
        controls=ControlsView(
            stop_new_trades=bool(control.stop_new_trades) if control else False,
            pause_ai=bool(control.pause_ai) if control else False,
            emergency_stop=bool(control.emergency_stop) if control else False,
            emergency_reason=control.emergency_reason if control else None,
        ),
        now=now,
    )
    return ctx, limits


def _intent_view(intent: TradeIntent, reference_price: Decimal | None) -> IntentView:
    kind = IntentKind.ENTRY
    if intent.intent_type in ("EXIT", "REDUCE"):
        kind = IntentKind(intent.intent_type)
    return IntentView(
        intent_id=str(intent.id),
        symbol=intent.symbol,
        side=intent.side,
        position_side=intent.position_side,
        kind=kind,
        notional_usd=D(intent.notional_usd) if intent.notional_usd is not None else None,
        quantity=D(intent.quantity) if intent.quantity is not None else None,
        reference_price=(
            D(intent.entry_price)
            if intent.entry_price is not None
            else reference_price
        ),
        requested_leverage=intent.requested_leverage,
        stop_loss=D(intent.stop_loss) if intent.stop_loss is not None else None,
        take_profit=D(intent.take_profit) if intent.take_profit is not None else None,
        confidence=(
            D(intent.confidence) if getattr(intent, "confidence", None) is not None else None
        ),
        expires_at=intent.expires_at,
        mode=intent.mode,
    )


async def evaluate_intent(db: AsyncSession, intent: TradeIntent) -> RiskDecision:
    """Evaluate one persisted intent and store the decision. Returns the row."""
    started = time.perf_counter()
    ctx, _limits = await build_context(
        db, symbol=intent.symbol, mode=intent.mode, user_id=intent.user_id
    )
    market = await _market_snapshot(db, intent.symbol)
    reference = D(market.last_price) if market is not None else None

    try:
        view = _intent_view(intent, reference)
    except ValueError as exc:
        outcome = RiskOutcome(
            approved=False,
            reason_codes=(),
            checked_limits={},
            risk_snapshot={},
            decided_at=utcnow(),
        )
        intent.rejection_code = "INVALID_INTENT"
        intent.rejection_detail = {"error": str(exc)}
        decision = RiskDecision(
            intent_id=intent.id,
            symbol=intent.symbol,
            approved=False,
            reason_codes=["INVALID_INTENT"],
            checked_limits={},
            risk_snapshot={"error": str(exc)},
            mode=intent.mode,
            decided_at=outcome.decided_at,
            latency_ms=int((time.perf_counter() - started) * 1000),
            user_id=intent.user_id,
        )
        db.add(decision)
        await db.flush()
        return decision

    outcome = _ENGINE.evaluate(view, ctx)
    latency_ms = int((time.perf_counter() - started) * 1000)

    decision = RiskDecision(
        intent_id=intent.id,
        symbol=intent.symbol,
        approved=outcome.approved,
        reason_codes=[str(code) for code in outcome.reason_codes],
        checked_limits=outcome.checked_limits,
        risk_snapshot=outcome.risk_snapshot,
        mode=intent.mode,
        decided_at=outcome.decided_at,
        latency_ms=latency_ms,
        user_id=intent.user_id,
    )
    db.add(decision)

    if not outcome.approved:
        db.add(
            RiskEvent(
                severity="WARN" if not outcome.is_hard_block else "CRITICAL",
                code="INTENT_REJECTED",
                message="Trade intent rejected by risk engine.",
                symbol=intent.symbol,
                context={
                    "intent_id": str(intent.id),
                    "reason_codes": [str(c) for c in outcome.blocking_codes],
                },
                user_id=intent.user_id,
            )
        )

    await db.flush()
    return decision


async def snapshot_status(db: AsyncSession, mode: str) -> dict[str, Any]:
    """Small summary used by /system/status and the dashboard."""
    snapshot = await _latest_snapshot(db, mode)
    blocking = await _reconciliation_blocking(db, mode)
    return {
        "has_account_snapshot": snapshot is not None,
        "equity_usd": str(snapshot.total_wallet_balance) if snapshot else None,
        "unrealized_pnl_usd": str(snapshot.total_unrealized_pnl) if snapshot else None,
        "reconciliation_blocking": blocking,
    }
