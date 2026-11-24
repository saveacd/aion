"""真实交易所执行与持仓托管（TESTNET / MAINNET）。

职责：
* 用用户自己保存在服务端的 Binance API Key 下单（密钥永不下发到客户端）；
* 成交后立刻在交易所挂上止损 STOP_MARKET 与止盈 TAKE_PROFIT_MARKET；
* 服务端每轮巡检持仓与保护单：缺失补挂、触发平仓、超时平仓；
* 把账户/持仓快照写入数据库，并把已平仓结果写入学习闭环。

安全边界：
* 只有用户在自动代理中显式选择 TESTNET/MAINNET 且开启自动执行时才会真实下单；
* 主网还需要服务端 ``AION_MAINNET_ENABLED`` 与用户 TOTP；
* 所有订单先落库再发送（ExecutionEngine 负责幂等与状态机）；
* 任何未确认的成交都不会被当作成功。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.core.config import Settings, TradingMode
from aion.core.crypto import SecretBox
from aion.exchange.client import MAINNET_BASE_URL, TESTNET_BASE_URL, BinanceFuturesClient
from aion.exchange.errors import (
    BinanceAPIError,
    ExchangeError,
    ExchangeNetworkError,
    ExchangeTimeout,
)
from aion.exchange.symbols import SymbolRules
from aion.execution.engine import ExecutionEngine
from aion.execution.reconcile import reconcile
from aion.execution.state import from_exchange_status
from aion.models import (
    AccountSnapshot,
    AgentMessage,
    AutopilotConfig,
    ExchangeAccount,
    Fill,
    LivePosition,
    Order,
    OrderEvent,
    PositionSnapshot,
    RiskDecision,
    TradeIntent,
    TradeOutcome,
)
from aion.models.base import utcnow
from aion.services.events import publish_event
from aion.services.notifications import notify

logger = logging.getLogger(__name__)

D = Decimal
D0 = D("0")
LIVE_MODES = ("TESTNET", "MAINNET")
MAX_HOLDING_HOURS = 48
_RECONCILE_LOCK = asyncio.Lock()
_RULES_CACHE: dict[tuple[str, str], tuple[SymbolRules, float]] = {}
_RULES_TTL_SECONDS = 3600.0


def _now_ts() -> float:
    return dt.datetime.now(dt.UTC).timestamp()


async def resolve_live_client(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID, mode: str
) -> BinanceFuturesClient | None:
    """Return a signed client for this user/mode, or None when unavailable."""
    if mode not in LIVE_MODES:
        return None
    row = (
        await db.execute(
            select(ExchangeAccount).where(
                ExchangeAccount.user_id == user_id,
                ExchangeAccount.exchange == "binance",
                ExchangeAccount.mode == mode,
                ExchangeAccount.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    api_key = api_secret = ""
    if row is not None and row.api_key_enc and row.api_secret_enc:
        box = SecretBox(settings)
        api_key = box.try_decrypt(row.api_key_enc) or ""
        api_secret = box.try_decrypt(row.api_secret_enc) or ""

    if not (api_key and api_secret):
        # Single-tenant deployments may configure the operator key in env. Only
        # use it when the deployment itself is in that exact mode.
        active = settings.resolve_trading_mode().value
        if active == mode:
            api_key, api_secret = settings.binance_credentials(TradingMode(mode))
            api_key, api_secret = api_key or "", api_secret or ""

    if not api_key or not api_secret:
        return None

    base_url = MAINNET_BASE_URL if mode == "MAINNET" else TESTNET_BASE_URL
    return BinanceFuturesClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        timeout=settings.binance_request_timeout_seconds,
        recv_window=settings.binance_recv_window_ms,
    )


async def _rules(client: BinanceFuturesClient, symbol: str) -> SymbolRules:
    # Cache per (environment, symbol): TESTNET and MAINNET filters differ.
    cache_key = (client._base_url, symbol)  # noqa: SLF001 - same-package use
    cached = _RULES_CACHE.get(cache_key)
    if cached is not None and _now_ts() - cached[1] < _RULES_TTL_SECONDS:
        return cached[0]
    info = await client.exchange_info()
    rules = info.symbols.get(symbol)
    if rules is None:
        raise ValueError(f"交易所未返回 {symbol} 的交易规则")
    _RULES_CACHE[cache_key] = (rules, _now_ts())
    return rules


def _exit_side(position_side: str) -> str:
    return "SELL" if position_side.upper() == "LONG" else "BUY"


async def _daily_realized_pnl(
    db: Any, user_id: uuid.UUID, mode: str
) -> Decimal:
    """Sum today's recorded realized PnL (UTC day) for this user and mode."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(Fill.realized_pnl), D0)).where(
            Fill.traded_at >= day_start,
            Fill.order_id.in_(
                select(Order.id).where(Order.user_id == user_id, Order.mode == mode)
            ),
        )
    )
    return D(total) if total is not None else D0


async def _with_position_side_fallback(call: Any, position_side: str, **kwargs: Any) -> Any:
    """Hedge mode wants LONG/SHORT; one-way mode wants BOTH. Try both."""
    last: Exception | None = None
    for side in (position_side, "BOTH"):
        try:
            return await call(position_side=side, **kwargs)
        except BinanceAPIError as exc:
            last = exc
            if exc.code not in (-4061, -4059, -1106):
                raise
    raise last  # type: ignore[misc]


async def _place_protective(
    client: BinanceFuturesClient,
    db: AsyncSession,
    position: LivePosition,
    *,
    order_type: str,
    trigger_price: Decimal,
    label: str,
) -> str | None:
    coid = f"aion-{label}-{position.id.hex[:20]}"[:36]
    try:
        ack = await _with_position_side_fallback(
            client.new_order,
            position.position_side,
            symbol=position.symbol,
            side=_exit_side(position.position_side),
            order_type=order_type,
            close_position=True,
            stop_price=trigger_price,
            working_type="CONTRACT_PRICE",
            client_order_id=coid,
        )
    except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout) as exc:
        logger.warning("protective %s failed for %s: %s", order_type, position.symbol, exc)
        return None

    db.add(
        Order(
            client_order_id=coid,
            exchange_order_id=ack.order_id,
            intent_id=position.intent_id,
            risk_decision_id=None,
            symbol=position.symbol,
            side=_exit_side(position.position_side),
            position_side=position.position_side,
            order_type=order_type,
            purpose="PROTECTIVE",
            reduce_only=True,
            quantity=D0,
            price=None,
            stop_price=trigger_price,
            trigger_direction="CONTRACT_PRICE",
            status="ACKNOWLEDGED",
            time_in_force="GTC",
            mode=position.mode,
            submit_attempts=1,
            last_exchange_response={"protective": True, "raw": ack.raw},
            submitted_at=utcnow(),
            user_id=position.user_id,
        )
    )
    return ack.order_id


async def ensure_protection(
    client: BinanceFuturesClient, db: AsyncSession, position: LivePosition
) -> None:
    """Make sure the exchange holds both a stop and a take-profit order."""
    if position.stop_loss is None and position.take_profit is None:
        position.protection_status = "NONE"
        return
    try:
        open_orders = await client.open_orders(symbol=position.symbol)
    except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout) as exc:
        position.protection_status = "UNKNOWN"
        position.last_error = f"保护单查询失败：{exc.__class__.__name__}"
        return

    # Compare against BOTH id kinds: sl_order_id/tp_order_id store the
    # exchange order id, while client_order_id is a different string
    # (audit BUG-06 — mixing them made existing protection look missing).
    active = {
        str(o.client_order_id or "")
        for o in open_orders
    } | {str(o.order_id) for o in open_orders if o.order_id}
    sl_id = position.sl_order_id if position.sl_order_id in active else None
    tp_id = position.tp_order_id if position.tp_order_id in active else None

    if position.stop_loss is not None and not any(
        (o.client_order_id or "").startswith(f"aion-sl-{position.id.hex[:20]}")
        and str(o.order_type).upper() in {"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"}
        for o in open_orders
    ):
        sl_id = await _place_protective(
            client,
            db,
            position,
            order_type="STOP_MARKET",
            trigger_price=D(position.stop_loss),
            label="sl",
        )
    if position.take_profit is not None and not any(
        (o.client_order_id or "").startswith(f"aion-tp-{position.id.hex[:20]}")
        and str(o.order_type).upper() in {"TAKE_PROFIT", "TAKE_PROFIT_MARKET"}
        for o in open_orders
    ):
        tp_id = await _place_protective(
            client,
            db,
            position,
            order_type="TAKE_PROFIT_MARKET",
            trigger_price=D(position.take_profit),
            label="tp",
        )

    position.sl_order_id = sl_id
    position.tp_order_id = tp_id
    expected = int(position.stop_loss is not None) + int(position.take_profit is not None)
    present = int(sl_id is not None) + int(tp_id is not None)
    if expected == 0:
        position.protection_status = "NONE"
    elif present >= expected:
        position.protection_status = "ACTIVE"
    elif present:
        position.protection_status = "PARTIAL"
    else:
        position.protection_status = "MISSING"


async def _recent_trades(
    client: BinanceFuturesClient, symbol: str, order_id: str | None
) -> tuple[Decimal | None, Decimal]:
    """(realized_pnl, commission) for one exchange order, when available."""
    try:
        trades = await client.user_trades(symbol=symbol, limit=50)
    except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
        return None, D0
    matched = [t for t in trades if order_id and t.order_id == str(order_id)]
    if not matched:
        return None, D0
    return sum((t.realized_pnl for t in matched), D0), sum((t.commission for t in matched), D0)


async def execute_live_entry(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    user_id: uuid.UUID,
    intent: TradeIntent,
    decision: RiskDecision,
    mode: str,
) -> Order | None:
    """Send an approved intent to the real exchange and register protection."""
    if mode not in LIVE_MODES:
        raise ValueError(f"unsupported live mode: {mode}")
    # MAINNET double-check (audit #8): the 24h armed marker is set only by a
    # successful fresh-TOTP verification at autopilot enable time. Without it,
    # no mainnet order leaves this process.
    if mode == "MAINNET" and settings.require_totp_for_mainnet:
        from aion.core.redis import get_redis

        try:
            armed = await get_redis().get(f"aion:mainnet:armed:{user_id}")
        except Exception:  # noqa: BLE001 - redis down = not armed (fail closed)
            armed = None
        if not armed:
            raise PermissionError(
                "主网自动交易未武装：需要在启用主网自动执行时通过 TOTP 二次验证（24 小时有效）。"
            )
    async with session_factory() as db:
        client = await resolve_live_client(db, settings, user_id, mode)
    if client is None:
        return None

    try:
        rules = await _rules(client, intent.symbol)
        mark = await client.mark_price(intent.symbol)
        if intent.entry_price is None:
            intent.entry_price = mark.mark_price
        try:
            await client.set_leverage(intent.symbol, max(1, int(intent.requested_leverage or 1)))
        except BinanceAPIError as exc:
            # -4046 = leverage already set; not an error for us.
            if exc.code != -4046:
                logger.warning("set_leverage failed for %s: %s", intent.symbol, exc)

        engine = ExecutionEngine(client, session_factory)
        order = await engine.submit(intent, decision, rules)

        # A market order may come back ACKNOWLEDGED; confirm once.
        if order.status not in ("FILLED", "REJECTED", "CANCELED") and (
            order.avg_fill_price is None or D(order.filled_quantity) <= 0
        ):
            try:
                ack = await client.query_order(
                    symbol=order.symbol, client_order_id=order.client_order_id
                )
                async with session_factory() as db:
                    fresh = await db.get(Order, order.id)
                    if fresh is not None:
                        # Map through the state machine instead of trusting the
                        # raw exchange status string (fix #6: EXPIRED etc. must
                        # land in a state the order state machine knows).
                        mapped = from_exchange_status(ack.status).value
                        fresh.status = (
                            mapped if mapped != "UNKNOWN" else fresh.status
                        )
                        fresh.exchange_order_id = ack.order_id or fresh.exchange_order_id
                        fresh.filled_quantity = ack.executed_qty
                        fresh.avg_fill_price = ack.avg_price
                        fresh.last_exchange_response = ack.raw
                        db.add(
                            OrderEvent(
                                order_id=fresh.id,
                                event_type="EXCHANGE_CONFIRM",
                                from_status=order.status,
                                to_status=fresh.status,
                                payload={"raw": ack.raw},
                            )
                        )
                        await db.commit()
                        order = fresh
            except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
                pass

        # A PARTIALLY_FILLED market order has REAL exposure on the exchange
        # already: book whatever filled instead of requiring FILLED
        # (audit BUG-04).
        if D(order.filled_quantity) <= 0:
            return order

        async with session_factory() as db:
            existing_id = await db.scalar(
                select(LivePosition.id).where(
                    LivePosition.intent_id == intent.id,
                    LivePosition.status == "OPEN",
                )
            )
            if existing_id is not None:
                # Follow-up fills of the same entry (e.g. the order finished
                # filling after our first sync): top up the managed size and
                # refresh protection instead of ignoring the fill (BUG-04).
                existing = await db.get(LivePosition, existing_id)
                if existing is not None and D(order.filled_quantity) > D(existing.quantity):
                    delta = D(order.filled_quantity) - D(existing.quantity)
                    existing.quantity = D(order.filled_quantity)
                    existing.entry_price = D(order.avg_fill_price or intent.entry_price or 0)
                    existing.margin_usd = (
                        D(existing.quantity) * existing.entry_price / existing.leverage
                    )
                    # Append the ledger row for the increment too, or fee
                    # stats and the learning loop silently miss it
                    # (follow-up audit #6).
                    _delta_fee = (
                        await _recent_trades(client, intent.symbol, order.exchange_order_id)
                    )[1]
                    db.add(
                        Fill(
                            order_id=order.id,
                            exchange_trade_id=f"live-{order.exchange_order_id or order.id}-topup"[:64],
                            symbol=intent.symbol,
                            side=intent.side,
                            price=existing.entry_price,
                            quantity=delta,
                            quote_quantity=delta * existing.entry_price,
                            commission=_delta_fee,
                            commission_asset="USDT",
                            realized_pnl=None,
                            is_maker=False,
                            traded_at=utcnow(),
                        )
                    )
                    await ensure_protection(client, db, existing)
                    await db.commit()
                return order

            qty = D(order.filled_quantity)
            entry_price = D(order.avg_fill_price or intent.entry_price or 0)
            leverage = max(1, int(intent.requested_leverage or 1))
            position = LivePosition(
                user_id=user_id,
                mode=mode,
                intent_id=intent.id,
                entry_order_id=order.id,
                symbol=intent.symbol,
                position_side=intent.position_side,
                quantity=qty,
                entry_price=entry_price,
                stop_loss=intent.stop_loss,
                take_profit=intent.take_profit,
                leverage=leverage,
                margin_usd=qty * entry_price / leverage,
                status="OPEN",
                opened_at=utcnow(),
            )
            db.add(position)
            await db.flush()

            _entry_realized, entry_fee = await _recent_trades(
                client, intent.symbol, order.exchange_order_id
            )
            db.add(
                Fill(
                    order_id=order.id,
                    exchange_trade_id=f"live-{order.exchange_order_id or order.id}"[:64],
                    symbol=intent.symbol,
                    side=intent.side,
                    price=entry_price,
                    quantity=qty,
                    quote_quantity=qty * entry_price,
                    commission=entry_fee,
                    commission_asset="USDT",
                    realized_pnl=None,
                    is_maker=False,
                    traded_at=utcnow(),
                )
            )
            await ensure_protection(client, db, position)
            await db.commit()

            await notify(
                session_factory,
                user_id=user_id,
                title=f"AI 已开仓 {intent.symbol} {intent.position_side}",
                body=(
                    f"成交 {qty} @ {entry_price}，{leverage}x；"
                    f"止损 {intent.stop_loss or '—'}，止盈 {intent.take_profit or '—'}"
                ),
                kind="trade",
                level="INFO",
                data={"position_id": str(position.id), "mode": mode},
            )
            await publish_event(
                "order",
                {
                    "event": "entry_filled",
                    "symbol": intent.symbol,
                    "mode": mode,
                    "position_id": str(position.id),
                    "quantity": str(qty),
                    "price": str(entry_price),
                },
                symbol=intent.symbol,
            )
        return order
    finally:
        await client.aclose()


async def _resolve_exit_reason(
    client: BinanceFuturesClient, position: LivePosition
) -> tuple[str, Decimal, str | None]:
    """Find which protective order closed a vanished position.

    Returns ``(reason, exit_price, exchange_order_id)``. The exit price and
    order id let the caller book the REAL fill (price, fees, realized PnL)
    instead of a placeholder at the entry price (audit BUG-07).
    """
    for order_id, reason in (
        (position.sl_order_id, "SL"),
        (position.tp_order_id, "TP"),
    ):
        if not order_id:
            continue
        try:
            ack = await client.query_order(symbol=position.symbol, order_id=order_id)
        except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
            continue
        if str(ack.status).upper() == "FILLED":
            exit_price = D(ack.avg_price) if D(ack.avg_price) > 0 else D(position.entry_price)
            return reason, exit_price, ack.order_id
    return "SYNC", D(position.entry_price), None


async def close_live_position(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    position_id: uuid.UUID,
    *,
    reason: str = "MANUAL",
    force: bool = False,
) -> bool:
    """Close one live position at market, cancel protection and record outcome."""
    async with session_factory() as db:
        position = await db.get(LivePosition, position_id)
        if position is None or position.status != "OPEN":
            return False
        user_id = position.user_id
        mode = position.mode
        client = await resolve_live_client(db, settings, user_id, mode)
    if client is None:
        return False

    try:
        # Cancel the exchange-side protection first so it cannot fire during exit.
        for order_id in (position.sl_order_id, position.tp_order_id):
            if not order_id:
                continue
            with contextlib.suppress(BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
                await client.cancel_order(symbol=position.symbol, order_id=order_id)
        exchange_positions = await client.positions(symbol=position.symbol)
        live = next(
            (
                p
                for p in exchange_positions
                if p.symbol == position.symbol
                and p.quantity != D0
                and p.position_side in (position.position_side, "BOTH")
            ),
            None,
        )
        realized: Decimal | None = None
        exit_fee = D0
        exit_price = D(position.entry_price)
        exit_order_pk: uuid.UUID | None = None
        exit_exchange_id: str | None = None

        if live is None:
            if not force:
                # Position vanished on the exchange: recover the REAL exit
                # price, order id, fees and realized PnL from the protective
                # order's fill (audit BUG-07) instead of booking at entry.
                reason, exit_price, exit_exchange_id = await _resolve_exit_reason(
                    client, position
                )
                realized, exit_fee = await _recent_trades(
                    client, position.symbol, exit_exchange_id
                )
        else:
            ack = await _with_position_side_fallback(
                client.new_order,
                position.position_side,
                symbol=position.symbol,
                side=_exit_side(position.position_side),
                order_type="MARKET",
                quantity=live.abs_quantity,
                reduce_only=True,
            )
            exit_exchange_id = ack.order_id
            if D(ack.avg_price) > 0:
                exit_price = D(ack.avg_price)
            # A market exit can come back merely ACKNOWLEDGED. Never book a
            # close (or PnL at the entry price) until the exchange confirms a
            # fill; if it stays unfilled, leave the position open for the
            # next patrol / reconciliation cycle instead of inventing a fill.
            if str(ack.status).upper() != "FILLED":
                try:
                    ack = await client.query_order(
                        symbol=position.symbol,
                        order_id=ack.order_id,
                        client_order_id=ack.client_order_id,
                    )
                    if D(ack.avg_price) > 0:
                        exit_price = D(ack.avg_price)
                    exit_exchange_id = ack.order_id or exit_exchange_id
                except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
                    logger.warning("exit fill confirm failed for %s", position.id)
                if str(ack.status).upper() != "FILLED" and D(ack.executed_qty) <= 0:
                    return False
            # Partial exit: only the executed portion leaves the exchange.
            # The remainder must stay OPEN and managed (audit BUG-05).
            executed_qty = min(D(ack.executed_qty), D(position.quantity))
            full_close = executed_qty >= D(position.quantity)
            if not full_close:
                async with session_factory() as db:
                    fresh = await db.get(LivePosition, position.id)
                    if fresh is None or fresh.status != "OPEN":
                        return False
                    order = Order(
                        client_order_id=f"aion-exit-{position.id.hex[:22]}"[:64],
                        exchange_order_id=ack.order_id,
                        intent_id=position.intent_id,
                        risk_decision_id=None,
                        symbol=position.symbol,
                        side=_exit_side(position.position_side),
                        position_side=position.position_side,
                        order_type="MARKET",
                        purpose="EXIT",
                        reduce_only=True,
                        quantity=executed_qty,
                        price=exit_price,
                        filled_quantity=executed_qty,
                        avg_fill_price=exit_price,
                        status="FILLED",
                        time_in_force="GTC",
                        mode=mode,
                        submit_attempts=1,
                        last_exchange_response={"raw": ack.raw, "partial": True},
                        submitted_at=utcnow(),
                        terminal_at=utcnow(),
                        user_id=user_id,
                    )
                    db.add(order)
                    await db.flush()
                    part_realized, part_fee = await _recent_trades(
                        client, position.symbol, ack.order_id
                    )
                    gross_part = (
                        (exit_price - D(fresh.entry_price)) * executed_qty
                        if fresh.position_side == "LONG"
                        else (D(fresh.entry_price) - exit_price) * executed_qty
                    )
                    part_pnl = (
                        part_realized - part_fee
                        if part_realized is not None
                        else gross_part - part_fee
                    )
                    db.add(
                        Fill(
                            order_id=order.id,
                            exchange_trade_id=f"live-exit-{ack.order_id or order.id}"[:64],
                            symbol=fresh.symbol,
                            side=_exit_side(fresh.position_side),
                            price=exit_price,
                            quantity=executed_qty,
                            quote_quantity=executed_qty * exit_price,
                            commission=part_fee,
                            commission_asset="USDT",
                            realized_pnl=part_pnl,
                            is_maker=False,
                            traded_at=utcnow(),
                        )
                    )
                    # Keep the remainder open and re-protect it.
                    fresh.quantity = D(fresh.quantity) - executed_qty
                    fresh.protection_status = "UNKNOWN"
                    await ensure_protection(client, db, fresh)
                    await db.commit()
                logger.warning(
                    "partial exit for %s: %s of %s filled; remainder stays open",
                    position.id, executed_qty, position.quantity,
                )
                return False
            async with session_factory() as db:
                order = Order(
                    client_order_id=f"aion-exit-{position.id.hex[:22]}"[:64],
                    exchange_order_id=ack.order_id,
                    intent_id=position.intent_id,
                    risk_decision_id=None,
                    symbol=position.symbol,
                    side=_exit_side(position.position_side),
                    position_side=position.position_side,
                    order_type="MARKET",
                    purpose="EXIT",
                    reduce_only=True,
                    quantity=executed_qty,
                    price=exit_price,
                    filled_quantity=executed_qty,
                    avg_fill_price=exit_price,
                    status="FILLED" if str(ack.status).upper() == "FILLED" else "ACKNOWLEDGED",
                    time_in_force="GTC",
                    mode=mode,
                    submit_attempts=1,
                    last_exchange_response={"raw": ack.raw},
                    submitted_at=utcnow(),
                    terminal_at=utcnow(),
                    user_id=user_id,
                )
                db.add(order)
                await db.flush()
                exit_order_pk = order.id
                db.add(
                    OrderEvent(
                        order_id=order.id,
                        event_type="EXIT_FILLED",
                        from_status="SUBMITTED",
                        to_status=order.status,
                        payload={"reason": reason, "raw": ack.raw},
                    )
                )
                await db.commit()
            realized, exit_fee = await _recent_trades(client, position.symbol, exit_exchange_id)

        async with session_factory() as db:
            fresh = await db.get(LivePosition, position.id)
            if fresh is None or fresh.status != "OPEN":
                return False
            fresh.status = "CLOSED"
            fresh.exit_reason = reason[:16]
            fresh.closed_at = utcnow()
            fresh.exit_order_id = exit_order_pk
            fresh.protection_status = "CLOSED"

            intent = (
                await db.get(TradeIntent, fresh.intent_id) if fresh.intent_id else None
            )
            entry_fee = D0
            if fresh.entry_order_id:
                entry_fill = await db.scalar(
                    select(Fill).where(Fill.order_id == fresh.entry_order_id).limit(1)
                )
                if entry_fill is not None:
                    entry_fee = D(entry_fill.commission)
            gross = (
                (exit_price - D(fresh.entry_price)) * D(fresh.quantity)
                if fresh.position_side == "LONG"
                else (D(fresh.entry_price) - exit_price) * D(fresh.quantity)
            )
            pnl = (
                (realized - exit_fee - entry_fee)
                if realized is not None
                else (gross - exit_fee - entry_fee)
            )
            r_multiple: Decimal | None = None
            if fresh.stop_loss:
                risk = abs(D(fresh.entry_price) - D(fresh.stop_loss)) * D(fresh.quantity)
                if risk > 0:
                    r_multiple = pnl / risk
            regime = None
            if intent is not None and intent.run_id is not None:
                msg = await db.scalar(
                    select(AgentMessage).where(
                        AgentMessage.run_id == intent.run_id,
                        AgentMessage.agent_name == "market_regime",
                        AgentMessage.status == "OK",
                    ).limit(1)
                )
                if msg is not None:
                    regime = str((msg.structured or {}).get("regime") or "")[:32] or None

            db.add(
                TradeOutcome(
                    user_id=user_id,
                    intent_id=fresh.intent_id,
                    run_id=intent.run_id if intent else None,
                    strategy_id=intent.strategy_id if intent else None,
                    symbol=fresh.symbol,
                    position_side=fresh.position_side,
                    quantity=D(fresh.quantity),
                    entry_price=D(fresh.entry_price),
                    exit_price=exit_price,
                    pnl_usd=pnl,
                    fee_usd=entry_fee + exit_fee,
                    r_multiple=r_multiple,
                    holding_minutes=D(
                        str((utcnow() - fresh.opened_at).total_seconds() / 60)
                    ),
                    regime=regime,
                    confidence=intent.confidence if intent else None,
                    exit_reason=reason[:16],
                    opened_at=fresh.opened_at,
                    closed_at=utcnow(),
                )
            )
            if exit_order_pk is not None:
                db.add(
                    Fill(
                        order_id=exit_order_pk,
                        exchange_trade_id=f"live-exit-{exit_exchange_id or exit_order_pk}"[:64],
                        symbol=fresh.symbol,
                        side=_exit_side(fresh.position_side),
                        price=exit_price,
                        quantity=D(fresh.quantity),
                        quote_quantity=D(fresh.quantity) * exit_price,
                        commission=exit_fee,
                        commission_asset="USDT",
                        realized_pnl=pnl,
                        is_maker=False,
                        traded_at=utcnow(),
                    )
                )
            await db.commit()

        await notify(
            session_factory,
            user_id=user_id,
            title=f"AI 已平仓 {position.symbol}（{reason}）",
            body=f"盈亏 {pnl:.2f} USDT，持仓 {position.quantity}",
            kind="trade",
            level="INFO" if pnl >= 0 else "WARNING",
            data={"position_id": str(position.id), "pnl": str(pnl), "mode": mode},
        )
        await publish_event(
            "order",
            {
                "event": "position_closed",
                "symbol": position.symbol,
                "mode": mode,
                "reason": reason,
                "pnl": str(pnl),
            },
            symbol=position.symbol,
        )
        return True
    finally:
        await client.aclose()


async def _sync_user_mode(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: uuid.UUID,
    mode: str,
) -> None:
    async with session_factory() as db:
        client = await resolve_live_client(db, settings, user_id, mode)
        cfg = await db.get(AutopilotConfig, user_id)
    if client is None:
        return
    hold_limit_minutes = (
        int(cfg.max_holding_minutes) if cfg and cfg.intraday else int(MAX_HOLDING_HOURS * 60)
    )
    try:
        info = await client.account_info()
        open_positions = [p for p in await client.positions() if p.is_open]

        async with session_factory() as db:
            previous_peak = await db.scalar(
                select(AccountSnapshot.equity_peak_usd)
                .where(AccountSnapshot.user_id == user_id, AccountSnapshot.mode == mode)
                .order_by(AccountSnapshot.captured_at.desc())
                .limit(1)
            )
            peak = max(D(previous_peak) if previous_peak is not None else D0, info.total_margin_balance)
            drawdown = (
                (peak - info.total_margin_balance) / peak * 100 if peak > 0 else D0
            )
            # A successful account sync IS a health check: refresh
            # last_checked_at so the risk engine's 5-minute freshness window
            # tracks real syncs instead of only key-verification time
            # (follow-up audit #2).
            accounts = (
                await db.execute(
                    select(ExchangeAccount).where(
                        ExchangeAccount.user_id == user_id,
                        ExchangeAccount.mode == mode,
                        ExchangeAccount.is_active.is_(True),
                    )
                )
            ).scalars().all()
            for account_row in accounts:
                account_row.last_checked_at = utcnow()
            # One shared batch timestamp: risk service picks the snapshots of
            # one sync round by exact captured_at, so per-row utcnow() could
            # leave earlier rows invisible to the risk context.
            batch_ts = utcnow()
            db.add(
                AccountSnapshot(
                    user_id=user_id,
                    mode=mode,
                    captured_at=batch_ts,
                    total_wallet_balance=info.total_wallet_balance,
                    total_unrealized_pnl=info.total_unrealized_pnl,
                    total_margin_balance=info.total_margin_balance,
                    available_balance=info.available_balance,
                    total_initial_margin=info.total_initial_margin,
                    total_maint_margin=info.total_maint_margin,
                    margin_ratio=(
                        # Fraction (0.15 = 15%), matching risk.engine semantics.
                        info.total_maint_margin / info.total_margin_balance
                        if info.total_margin_balance > 0
                        else None
                    ),
                    equity_peak_usd=peak,
                    drawdown_pct=drawdown,
                    # Real day-PnL from recorded fills, so DAILY_LOSS_LIMIT works live
                    # instead of always reading zero.
                    daily_realized_pnl=await _daily_realized_pnl(db, user_id, mode),
                    raw={"source": "exchange", "can_trade": info.can_trade},
                )
            )

            positions_by_symbol = {
                (p.symbol, str(p.position_side).upper()): p for p in open_positions
            }
            for pos in open_positions:
                protective = []
                with contextlib.suppress(BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
                    protective = [
                        o
                        for o in await client.open_orders(symbol=pos.symbol)
                        if str(o.order_type).upper()
                        in {"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"}
                    ]
                has_sl = any(
                    "STOP" in str(o.order_type).upper() and "TAKE" not in str(o.order_type).upper()
                    for o in protective
                )
                has_tp = any("TAKE_PROFIT" in str(o.order_type).upper() for o in protective)
                db.add(
                    PositionSnapshot(
                        user_id=user_id,
                        mode=mode,
                        captured_at=batch_ts,
                        symbol=pos.symbol,
                        position_side=pos.position_side,
                        quantity=pos.abs_quantity,
                        entry_price=pos.entry_price,
                        mark_price=pos.mark_price,
                        unrealized_pnl=pos.unrealized_pnl,
                        liquidation_price=pos.liquidation_price or None,
                        isolated_margin=pos.isolated_margin,
                        leverage=pos.leverage,
                        margin_mode=pos.margin_mode,
                        has_stop_loss=has_sl,
                        has_take_profit=has_tp,
                        protection_verified_at=batch_ts if has_sl else None,
                    )
                )

            live_rows = (
                await db.execute(
                    select(LivePosition).where(
                        LivePosition.user_id == user_id,
                        LivePosition.mode == mode,
                        LivePosition.status == "OPEN",
                    )
                )
            ).scalars().all()

            for position in live_rows:
                exchange_pos = positions_by_symbol.get(
                    (position.symbol, position.position_side)
                ) or positions_by_symbol.get((position.symbol, "BOTH"))
                if exchange_pos is None:
                    continue
                if exchange_pos.position_side not in (position.position_side, "BOTH"):
                    continue
                # Signed positionAmt must never leak into risk math: shorts
                # are negative on the exchange and would offset long notional.
                position.quantity = exchange_pos.abs_quantity
                position.entry_price = exchange_pos.entry_price
                position.leverage = exchange_pos.leverage or position.leverage
                await ensure_protection(client, db, position)
            await db.commit()

        # Close positions whose protection triggered or that timed out.
        async with session_factory() as db:
            live_rows = (
                await db.execute(
                    select(LivePosition).where(
                        LivePosition.user_id == user_id,
                        LivePosition.mode == mode,
                        LivePosition.status == "OPEN",
                    )
                )
            ).scalars().all()
            live_ids = [(p.id, p.symbol, p.position_side) for p in live_rows]

        for position_id, symbol, position_side in live_ids:
            try:
                exchange_positions = await client.positions(symbol=symbol)
            except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout):
                continue
            # Hedge mode: match the position side too, never just the symbol —
            # LONG and SHORT on one symbol are independent positions.
            live = next(
                (
                    p
                    for p in exchange_positions
                    if p.symbol == symbol
                    and p.quantity != D0
                    and str(p.position_side).upper()
                    in (position_side, "BOTH")
                ),
                None,
            )
            if live is None:
                await close_live_position(
                    session_factory, settings, position_id, reason="SYNC"
                )
                continue

            async with session_factory() as db:
                position = await db.get(LivePosition, position_id)
                if position is None or position.status != "OPEN":
                    continue
                mark = D(live.mark_price)
                exit_reason: str | None = None
                if position.position_side == "LONG":
                    if position.stop_loss is not None and mark <= D(position.stop_loss):
                        exit_reason = "SL"
                    elif position.take_profit is not None and mark >= D(position.take_profit):
                        exit_reason = "TP"
                else:
                    if position.stop_loss is not None and mark >= D(position.stop_loss):
                        exit_reason = "SL"
                    elif position.take_profit is not None and mark <= D(position.take_profit):
                        exit_reason = "TP"
                if exit_reason is None:
                    age_minutes = (utcnow() - position.opened_at).total_seconds() / 60
                    if age_minutes >= hold_limit_minutes:
                        exit_reason = "TIMEOUT"
            if exit_reason is not None:
                await close_live_position(
                    session_factory, settings, position_id, reason=exit_reason
                )

        # Full reconciliation against the exchange is the source of truth:
        # differences land in ReconciliationRun (blocking) and the risk engine
        # refuses new risk while unresolved (follow-up audit #1 — this module
        # existed but was never wired into the production loop).
        try:
            async with session_factory() as db:
                await reconcile(client, db, mode=mode, user_id=user_id)
        except ExchangeError:
            logger.warning("reconciliation failed user=%s mode=%s", user_id, mode)
    except (BinanceAPIError, ExchangeNetworkError, ExchangeTimeout) as exc:
        logger.warning("live sync failed user=%s mode=%s: %s", user_id, mode, exc)
    finally:
        await client.aclose()


async def reconcile_live_accounts(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    """Sync every user that has live credentials or open live positions."""
    if _RECONCILE_LOCK.locked():
        return 0
    async with _RECONCILE_LOCK:
        async with session_factory() as db:
            account_rows = (
                await db.execute(
                    select(ExchangeAccount.user_id, ExchangeAccount.mode).where(
                        ExchangeAccount.is_active.is_(True),
                        ExchangeAccount.mode.in_(LIVE_MODES),
                    )
                )
            ).all()
            position_rows = (
                await db.execute(
                    select(LivePosition.user_id, LivePosition.mode)
                    .where(LivePosition.status == "OPEN")
                    .distinct()
                )
            ).all()
        targets: set[tuple[uuid.UUID, str]] = set()
        for uid, mode in account_rows + position_rows:
            if uid is None or mode not in LIVE_MODES:
                continue
            targets.add((uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid)), str(mode)))
        for uid, mode in targets:
            await _sync_user_mode(session_factory, settings, uid, mode)
        return len(targets)


async def run_reconcile_forever(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    import os

    interval = int(os.environ.get("AION_ACCOUNT_SYNC_SECONDS", "30"))
    logger.info("live account reconcile loop started (interval=%ss)", interval)
    while True:
        try:
            await reconcile_live_accounts(session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("live account reconcile failed")
        await asyncio.sleep(interval)


async def _apply_order_update(
    db: AsyncSession, user_id: uuid.UUID, mode: str, update: Any
) -> bool:
    """Apply one ORDER_TRADE_UPDATE to the local order book. Returns applied."""
    from sqlalchemy import or_

    order = await db.scalar(
        select(Order).where(
            Order.user_id == user_id,
            Order.mode == mode,
            or_(
                Order.client_order_id == update.client_order_id,
                Order.exchange_order_id == update.order_id,
            ),
        )
    )
    if order is None:
        return False
    previous = order.status
    mapped = from_exchange_status(update.status).value
    if mapped != "UNKNOWN":
        order.status = mapped
    if update.cum_filled_qty is not None:
        order.filled_quantity = max(D(order.filled_quantity or 0), D(update.cum_filled_qty))
    if update.avg_price and update.avg_price > 0:
        order.avg_fill_price = update.avg_price

    # ORDER_TRADE_UPDATE carries the incremental last fill and Binance trade ID.
    # Persist each trade exactly once; cumulative quantity alone is not a fill.
    if (
        update.execution_type == "TRADE"
        and update.trade_id
        and update.last_filled_qty > 0
        and update.last_price > 0
    ):
        exchange_trade_id = f"{update.symbol}:{update.trade_id}"[:64]
        already_recorded = await db.scalar(
            select(Fill.id).where(Fill.exchange_trade_id == exchange_trade_id)
        )
        if already_recorded is None:
            qty = D(update.last_filled_qty)
            price = D(update.last_price)
            db.add(
                Fill(
                    order_id=order.id,
                    exchange_trade_id=exchange_trade_id,
                    symbol=order.symbol,
                    side=order.side,
                    price=price,
                    quantity=qty,
                    quote_quantity=qty * price,
                    commission=D(update.commission or 0),
                    commission_asset=update.commission_asset,
                    realized_pnl=D(update.realized_pnl or 0),
                    is_maker=bool(update.is_maker),
                    traded_at=update.trade_time or utcnow(),
                )
            )
    order.last_exchange_response = {
        "source": "user_stream",
        "status": update.status,
        "event_time": update.trade_time.isoformat() if update.trade_time else None,
    }
    db.add(
        OrderEvent(
            order_id=order.id,
            event_type="USER_STREAM_UPDATE",
            from_status=previous,
            to_status=order.status,
            payload={"status": update.status, "raw_status": update.status},
        )
    )
    await db.commit()
    return True


async def run_user_stream_forever(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: uuid.UUID,
    mode: str,
) -> None:
    """Consume the Binance user-data stream for one user/mode.

    Real-time fills/cancels/protection triggers update local order state;
    the periodic REST reconcile stays as the fallback source of truth.
    """
    from aion.exchange.streams import UserDataStream

    while True:
        async with session_factory() as db:
            client = await resolve_live_client(db, settings, user_id, mode)
        if client is None:
            await asyncio.sleep(60)
            continue
        try:
            stream = UserDataStream(client, max_reconnects=1000)
            async for event in stream.user_data_stream():
                if event.order is None:
                    continue
                try:
                    async with session_factory() as db:
                        applied = await _apply_order_update(
                            db, user_id, mode, event.order
                        )
                    if applied and str(event.order.status).upper() == "FILLED":
                        await publish_event(
                            "order",
                            {
                                "event": "stream_fill",
                                "symbol": event.order.symbol,
                                "mode": mode,
                                "filled": str(event.order.cum_filled_qty),
                            },
                            symbol=event.order.symbol,
                        )
                except Exception:  # noqa: BLE001 - one bad event must not kill the stream
                    logger.exception("user stream update failed user=%s", user_id)
        except asyncio.CancelledError:
            await client.aclose()
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect loop
            logger.warning(
                "user data stream interrupted user=%s mode=%s: %s", user_id, mode, exc
            )
        finally:
            with contextlib.suppress(Exception):
                await client.aclose()
        await asyncio.sleep(5)


async def run_streams_forever(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """Supervisor: keep one user-data-stream task alive per active live account."""
    tasks: dict[tuple[uuid.UUID, str], asyncio.Task[None]] = {}
    logger.info("user data stream supervisor started")
    while True:
        try:
            async with session_factory() as db:
                rows = (
                    await db.execute(
                        select(ExchangeAccount.user_id, ExchangeAccount.mode).where(
                            ExchangeAccount.is_active.is_(True),
                            ExchangeAccount.mode.in_(LIVE_MODES),
                        )
                    )
                ).all()
            wanted: set[tuple[uuid.UUID, str]] = set()
            for uid, mode in rows:
                if uid is None or mode not in LIVE_MODES:
                    continue
                key = (
                    uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid)),
                    str(mode),
                )
                wanted.add(key)
            for key in wanted - tasks.keys():
                tasks[key] = asyncio.create_task(
                    run_user_stream_forever(session_factory, settings, key[0], key[1])
                )
                logger.info("user stream started user=%s mode=%s", *key)
            for key in tasks.keys() - wanted:
                tasks.pop(key).cancel()
        except asyncio.CancelledError:
            for task in tasks.values():
                task.cancel()
            raise
        except Exception:  # noqa: BLE001 - keep the supervisor alive
            logger.exception("user stream supervisor tick failed")
        await asyncio.sleep(60)


__all__ = [
    "LIVE_MODES",
    "close_live_position",
    "ensure_protection",
    "execute_live_entry",
    "reconcile_live_accounts",
    "resolve_live_client",
    "run_reconcile_forever",
    "run_streams_forever",
    "run_user_stream_forever",
]
