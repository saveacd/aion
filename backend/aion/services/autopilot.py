"""Autopilot: the fully-automatic AI trading agent.

Per user opt-in config, each cycle: run the six-agent analysis for every
configured symbol, turn a confident TRADE proposal into a TradeIntent, pass it
through the independent risk engine, and — only when the user explicitly
enabled ``auto_execute`` — place it on the selected account:

* ``PAPER``  — simulated fills in the internal paper broker;
* ``TESTNET``/``MAINNET`` — real exchange orders through the execution engine,
  with stop-loss and take-profit protective orders attached immediately.

Every step is persisted (agent run, intent, risk decision, order, fill) so the
learning loop can later score the outcome.

Safety invariants:
* the emergency controls (``trading_control_state``) always win;
* real execution requires the user to pick the mode explicitly, a stored API
  key, and (for mainnet) the server-wide unlock;
* per-day trade count and min-confidence are hard gates;
* one open position per symbol per user at a time.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.agents.base import DbRunRecorder
from aion.core.config import Settings
from aion.execution.paper import PaperBroker
from aion.models import (
    AgentRun,
    AutopilotConfig,
    LivePosition,
    PaperPosition,
    RiskDecision,
    TradeIntent,
    TradingControlState,
)
from aion.models.base import utcnow
from aion.risk.service import evaluate_intent
from aion.services.agent_service import resolve_provider_config, run_analysis
from aion.services.learning import close_due_positions_and_record, maybe_distill_lessons
from aion.services.live_trading import (
    LIVE_MODES,
    execute_live_entry,
    resolve_live_client,
)
from aion.services.market_stream import latest_price

logger = logging.getLogger(__name__)

D = Decimal
INTENT_TTL = dt.timedelta(minutes=5)
DEFAULT_TICK_SECONDS = 10
MIN_CYCLE_SECONDS = 60
#: live tick older than this is treated as stale and never fires an entry plan
TICK_MAX_AGE_SECONDS = 15
#: small tolerance so a touch of the zone edge counts as an entry
ENTRY_ZONE_BUFFER = D("0.0005")
#: a waiting zone below this confidence is not worth a triggered re-analysis
WATCH_PLAN_MIN_CONFIDENCE = 0.55


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    tick_seconds = int(os.environ.get("AION_AUTOPILOT_TICK_SECONDS", str(DEFAULT_TICK_SECONDS)))
    logger.info("autopilot loop started (tick=%ss)", tick_seconds)
    while True:
        try:
            await tick_once(session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive a bad tick
            logger.exception("autopilot tick failed")
        await asyncio.sleep(tick_seconds)


async def tick_once(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    closed = await close_due_positions_and_record(session_factory, settings)
    if closed:
        logger.info("autopilot: closed %s due paper position(s)", closed)

    # AI 等待区：价格进入计划区间时立即重新分析（不等周期）。
    triggered = await _consume_watch_triggers(session_factory)

    async with session_factory() as db:
        configs = (
            await db.execute(
                select(AutopilotConfig).where(AutopilotConfig.enabled.is_(True))
            )
        ).scalars().all()
        snapshot = [
            (c.user_id, c.last_run_at, c.cycle_seconds) for c in configs
        ]

    now = utcnow()
    for user_id, last_run_at, cycle_seconds in snapshot:
        focus = triggered.get(user_id)
        if focus is None and last_run_at is not None:
            due_at = last_run_at + dt.timedelta(seconds=max(cycle_seconds, MIN_CYCLE_SECONDS))
            if due_at > now:
                continue
        lock_ttl = max(int(cycle_seconds), MIN_CYCLE_SECONDS, LOCK_MIN_TTL_SECONDS)
        lock_token = uuid.uuid4().hex
        if not await _acquire_cycle_lock(user_id, lock_ttl, lock_token):
            # Another worker instance owns this user's cycle right now
            # (follow-up audit #3 — multi-replica deployments must not
            # double-fire; the TTL covers worst-case model latency too).
            continue
        cycle_task = asyncio.create_task(
            _run_locked_user_cycle(session_factory, settings, user_id, focus, lock_ttl, lock_token)
        )
        try:
            await cycle_task
        except LockLeaseLost:
            logger.error("autopilot cycle stopped after lock lease loss user=%s", user_id)
        finally:
            await _release_cycle_lock(user_id, lock_token)


class LockLeaseLost(RuntimeError):
    """Raised when this worker can no longer prove ownership of its lease."""


async def _run_locked_user_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: uuid.UUID,
    focus: set[str] | None,
    ttl_seconds: int,
    token: str,
) -> None:
    lost = asyncio.Event()
    work = asyncio.create_task(_do_user_cycle(session_factory, settings, user_id, focus))
    heartbeat = asyncio.create_task(
        _renew_cycle_lock_forever(user_id, token, ttl_seconds, lost)
    )
    try:
        done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done and lost.is_set():
            work.cancel()
            try:
                await work
            except asyncio.CancelledError:
                pass
            raise LockLeaseLost(f"Autopilot lease lost for user {user_id}")
        await work
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        if not work.done():
            work.cancel()
            try:
                await work
            except asyncio.CancelledError:
                pass


async def _do_user_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: uuid.UUID,
    focus: set[str] | None,
) -> None:
    await run_user_cycle(session_factory, settings, user_id, focus_symbols=focus)
    await maybe_distill_lessons(session_factory, settings, user_id)


async def _renew_cycle_lock_forever(
    user_id: uuid.UUID, token: str, ttl_seconds: int, lost: asyncio.Event
) -> None:
    """Renew the Redis lease only while this worker still owns its token."""
    from aion.core.redis import get_redis

    interval = max(5, ttl_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        try:
            renewed = await get_redis().eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                1, f"aion:autopilot:lock:{user_id}", token, ttl_seconds,
            )
        except Exception:  # noqa: BLE001
            renewed = 0
        if not renewed:
            logger.error("autopilot lock lease lost user=%s; stopping cycle", user_id)
            lost.set()
            return


# Longer than the worst-case model timeout (180s) plus analysis overhead so
# the lock cannot expire mid-cycle even when cycle_seconds is small.
LOCK_MIN_TTL_SECONDS = 600


async def _acquire_cycle_lock(user_id: uuid.UUID, ttl_seconds: int, token: str) -> bool:
    """Redis SET-NX lock so two replicas never run one user's cycle twice.

    Fails CLOSED when Redis is unreachable: a trading system must prefer
    skipping a cycle over the risk of two replicas trading simultaneously.
    Set ``AION_AUTOPILOT_LOCK_STRICT=0`` to restore fail-open behaviour for
    single-instance dev deployments (follow-up audit #3).
    """
    try:
        from aion.core.redis import get_redis

        return bool(
            await get_redis().set(
                f"aion:autopilot:lock:{user_id}", token, nx=True, ex=ttl_seconds
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("autopilot lock unavailable; skipping cycle (fail-closed)")
        return False


async def _release_cycle_lock(user_id: uuid.UUID, token: str) -> None:
    """Compare-and-delete: only the holder releases, never another replica."""
    try:
        from aion.core.redis import get_redis

        await get_redis().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            f"aion:autopilot:lock:{user_id}",
            token,
        )
    except Exception:  # noqa: BLE001, S110 - TTL expires on its own anyway
        pass


async def _consume_watch_triggers(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[uuid.UUID, set[str]]:
    """Check every stored AI entry plan against the live 1-second price feed.

    Returns ``{user_id: {symbol, ...}}`` for plans whose entry zone was reached.
    Triggered and expired plans are removed so each plan fires at most once.
    """
    async with session_factory() as db:
        if await _controls_block(db) is not None:
            return {}
        configs = (
            await db.execute(
                select(AutopilotConfig).where(AutopilotConfig.enabled.is_(True))
            )
        ).scalars().all()
        now = utcnow()
        triggered: dict[uuid.UUID, set[str]] = {}
        changed = False
        for cfg in configs:
            plans = list(cfg.watch_plans or [])
            if not plans:
                continue
            allowed = {str(s).upper() for s in (cfg.symbols or []) if str(s).strip()}
            keep: list[dict[str, Any]] = []
            for plan in plans:
                symbol = str(plan.get("symbol") or "").upper()
                low = _plan_price(plan, "entry_low")
                high = _plan_price(plan, "entry_high")
                expires_at = _plan_time(plan, "expires_at")
                if not symbol or symbol not in allowed or low is None or high is None:
                    changed = True
                    continue
                if expires_at is not None and expires_at <= now:
                    changed = True
                    logger.info("autopilot: watch plan expired user=%s symbol=%s", cfg.user_id, symbol)
                    continue
                tick = latest_price(symbol)
                if tick is not None:
                    tick_ts = tick.get("ts")
                    fresh = tick_ts is not None and (
                        now - tick_ts
                    ).total_seconds() <= TICK_MAX_AGE_SECONDS
                    price = D(str(tick["price"]))
                    if fresh and low * (1 - ENTRY_ZONE_BUFFER) <= price <= high * (
                        1 + ENTRY_ZONE_BUFFER
                    ):
                        triggered.setdefault(cfg.user_id, set()).add(symbol)
                        changed = True
                        logger.info(
                            "autopilot: watch plan hit user=%s symbol=%s price=%s zone=[%s, %s]",
                            cfg.user_id,
                            symbol,
                            price,
                            low,
                            high,
                        )
                        continue
                keep.append(plan)
            if len(keep) != len(plans):
                cfg.watch_plans = keep
                changed = True
        if changed:
            await db.commit()
        return triggered


def _plan_price(plan: dict[str, Any], key: str) -> Decimal | None:
    value = plan.get(key)
    if value is None:
        return None
    try:
        return D(str(value))
    except Exception:  # noqa: BLE001 - malformed stored plan is discarded
        return None


def _plan_time(plan: dict[str, Any], key: str) -> dt.datetime | None:
    value = plan.get(key)
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


async def _save_watch_plan(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    symbol: str,
    plan: Any,
) -> None:
    """Persist the AI entry plan, replacing an older plan for the same symbol."""
    low, high = D(str(plan.entry_low)), D(str(plan.entry_high))
    tick = latest_price(symbol)
    if tick is not None:
        price = D(str(tick["price"]))
        if low <= price <= high:
            # The price is already there: this must be an immediate decision,
            # not a resting plan, or the trigger would loop every few seconds.
            logger.info(
                "autopilot: skip watch plan already at market user=%s symbol=%s price=%s",
                user_id,
                symbol,
                price,
            )
            return
    entry = {
        "symbol": symbol,
        "side": plan.side,
        "entry_low": str(plan.entry_low),
        "entry_high": str(plan.entry_high),
        "stop_loss": str(plan.stop_loss) if plan.stop_loss is not None else None,
        "take_profit": str(plan.take_profit) if plan.take_profit is not None else None,
        "leverage": int(plan.leverage),
        "confidence": float(plan.confidence),
        "reason": plan.reason,
        "created_at": utcnow().isoformat(),
        "expires_at": (
            utcnow() + dt.timedelta(minutes=int(plan.expires_in_minutes))
        ).isoformat(),
    }
    async with session_factory() as db:
        cfg = await db.get(AutopilotConfig, user_id)
        if cfg is None:
            return
        cfg.watch_plans = [
            p
            for p in (cfg.watch_plans or [])
            if str(p.get("symbol") or "").upper() != symbol
        ] + [entry]
        await db.commit()


async def _clear_watch_plan(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    symbol: str,
) -> None:
    async with session_factory() as db:
        cfg = await db.get(AutopilotConfig, user_id)
        if cfg is None:
            return
        plans = cfg.watch_plans or []
        kept = [p for p in plans if str(p.get("symbol") or "").upper() != symbol]
        if len(kept) != len(plans):
            cfg.watch_plans = kept
            await db.commit()


async def _controls_block(db: AsyncSession) -> str | None:
    control = await db.get(TradingControlState, 1)
    if control is None:
        return None
    if control.emergency_stop:
        return "EMERGENCY_STOP"
    if control.pause_ai:
        return "AI_PAUSED"
    if control.stop_new_trades:
        return "STOP_NEW_TRADES"
    return None


async def run_user_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: uuid.UUID,
    focus_symbols: set[str] | None = None,
) -> None:
    """One autopilot cycle for one user. Each phase uses a fresh session so a
    slow LLM run never holds a database transaction open.

    ``focus_symbols`` restricts the round to the symbols whose AI entry zone was
    just reached (watch-plan trigger); ``None`` means the normal full cycle.
    """
    async with session_factory() as db:
        cfg = await db.get(AutopilotConfig, user_id)
        if cfg is None or not cfg.enabled:
            return
        blocked = await _controls_block(db)
        if blocked is not None:
            cfg.last_error = f"已暂停：{blocked}"
            await db.commit()
            return
        today = utcnow().date()
        if cfg.trades_today_date != today:
            cfg.trades_today = 0
            cfg.trades_today_date = today
        cfg.last_run_at = utcnow()  # claim the slot; prevents overlapping runs
        await db.commit()
        symbols = [str(s).upper() for s in (cfg.symbols or []) if str(s).strip()]
        if focus_symbols is not None:
            # watch-plan trigger: only re-analyze the symbol(s) that reached the zone
            symbols = [s for s in symbols if s in focus_symbols]
            if not symbols:
                return
        interval = cfg.interval
        auto_execute = cfg.auto_execute
        min_confidence = float(cfg.min_confidence)
        strategy_id = cfg.strategy_id
        trades_left = max(0, cfg.max_trades_per_day - cfg.trades_today)
        mode = cfg.execution_mode or "PAPER"
        intraday = bool(cfg.intraday)
        max_holding_minutes = int(cfg.max_holding_minutes or 240)

    # This build's unattended autopilot is deliberately PAPER-only.  Keep the
    # guard here (before provider lookup or any exchange-client construction)
    # so a stale database setting cannot silently route an autonomous cycle to
    # an exchange account.  The live execution modules remain separate, but
    # they are not reachable from this autopilot path.
    if mode != "PAPER":
        await _set_error(
            session_factory, user_id,
            f"当前自动驾驶构建仅允许 PAPER 模拟模式；检测到 {mode}，本轮已安全停止。",
        )
        return

    if not symbols:
        await _set_error(session_factory, user_id, "未配置交易对")
        return

    async with session_factory() as db:
        provider = await resolve_provider_config(db, user_id)
    if provider is None and not (settings.anthropic_api_key or settings.openai_api_key):
        await _set_error(
            session_factory,
            user_id,
            "未配置大模型 API：请在模型 Provider 页面添加密钥，或设置 AION_ANTHROPIC_API_KEY",
        )
        return

    broker = PaperBroker(session_factory)
    if mode == "PAPER":
        await broker.ensure_account(user_id)
    last_error: str | None = None

    for symbol in symbols:
        if trades_left <= 0:
            break
        try:
            placed = await _analyze_symbol(
                session_factory=session_factory,
                settings=settings,
                broker=broker,
                provider=provider,
                user_id=user_id,
                symbol=symbol,
                interval=interval,
                mode=mode,
                min_confidence=min_confidence,
                auto_execute=auto_execute,
                strategy_id=strategy_id,
                intraday=intraday,
                max_holding_minutes=max_holding_minutes,
            )
            if placed:
                trades_left -= 1
        except Exception as exc:  # noqa: BLE001 - one symbol must not kill the cycle
            logger.exception("autopilot cycle failed for user=%s symbol=%s", user_id, symbol)
            last_error = f"{symbol}: {type(exc).__name__}"

    await _set_error(session_factory, user_id, last_error)


async def _analyze_symbol(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    broker: PaperBroker,
    provider: Any,
    user_id: uuid.UUID,
    symbol: str,
    interval: str,
    mode: str,
    min_confidence: float,
    auto_execute: bool,
    strategy_id: uuid.UUID | None,
    intraday: bool = True,
    max_holding_minutes: int = 240,
) -> bool:
    """Analyze one symbol and maybe place one paper trade. Returns True when an
    order was filled."""
    async with session_factory() as db:
        open_position = await db.scalar(
            select(PaperPosition.id).where(
                PaperPosition.user_id == user_id,
                PaperPosition.symbol == symbol,
                PaperPosition.status == "OPEN",
            )
        )
        if open_position is None:
            open_position = await db.scalar(
                select(LivePosition.id).where(
                    LivePosition.user_id == user_id,
                    LivePosition.symbol == symbol,
                    LivePosition.status == "OPEN",
                )
            )
    if open_position is not None:
        return False

    run_id = uuid.uuid4()
    recorder = DbRunRecorder(session_factory)
    await recorder.ensure_run(
        run_id=run_id, symbol=symbol, interval=interval, trigger="autopilot",
        mode=mode, user_id=user_id,
    )
    result = await run_analysis(
        settings=settings,
        session_factory=session_factory,
        symbol=symbol,
        interval=interval,
        run_id=run_id,
        timeout_seconds=settings.agent_run_timeout_seconds,
        provider_config=provider,
        user_id=user_id,
        strategy_id=strategy_id,
        intraday=intraday,
        max_holding_minutes=max_holding_minutes,
    )
    if result is None:
        return False

    # 日内抓点：AI 暂不入场但给出了等待区时，交给实时行情监视。
    if result.watch_plan is not None:
        if float(result.watch_plan.confidence) >= WATCH_PLAN_MIN_CONFIDENCE:
            await _save_watch_plan(session_factory, user_id, symbol, result.watch_plan)
    elif result.is_trade:
        await _clear_watch_plan(session_factory, user_id, symbol)

    if not result.is_trade or result.trade_intent is None:
        return False
    confidence = float(result.confidence or 0.0)
    if confidence < min_confidence:
        return False

    candidate = result.trade_intent
    async with session_factory() as db:
        intent = TradeIntent(
            intent_key=f"auto-{run_id}",
            run_id=run_id,
            strategy_id=strategy_id,
            symbol=candidate.symbol,
            side=candidate.side,
            position_side=candidate.position_side,
            intent_type="ENTRY",
            order_type=candidate.order_type,
            quantity=None,
            notional_usd=D(str(candidate.notional_usd)),
            requested_leverage=int(candidate.leverage),
            entry_price=None,
            stop_loss=D(str(candidate.stop_loss)) if candidate.stop_loss else None,
            take_profit=D(str(candidate.take_profit)) if candidate.take_profit else None,
            time_in_force=candidate.time_in_force,
            confidence=D(str(confidence)),
            reason=candidate.reason[:2000],
            evidence_refs=list(candidate.evidence_refs),
            status="PENDING",
            expires_at=utcnow() + INTENT_TTL,
            mode=mode,
            user_id=user_id,
        )
        db.add(intent)
        await db.flush()
        decision = await evaluate_intent(db, intent)
        intent.status = "APPROVED" if decision.approved else "REJECTED"
        if not decision.approved:
            codes = [str(c) for c in (decision.reason_codes or [])]
            intent.rejection_code = codes[0] if codes else "RISK_REJECTED"
            intent.rejection_detail = {"reason_codes": codes}
            await db.commit()
            return False

        await db.commit()
        intent_id = intent.id
        decision_id = decision.id

    filled = False
    if auto_execute:
        if mode == "PAPER":
            async with session_factory() as exec_db:
                live_intent = await exec_db.get(TradeIntent, intent_id)
                live_decision = await exec_db.get(RiskDecision, decision_id)
                if live_intent is not None and live_decision is not None:
                    order = await broker.submit_entry(exec_db, live_intent, live_decision)
                    if order is not None:
                        live_intent.status = "EXECUTED"
                        await exec_db.execute(
                            update(AgentRun)
                            .where(AgentRun.id == run_id)
                            .values(intent_id=intent_id)
                        )
                        await exec_db.commit()
                        filled = True
        elif mode in LIVE_MODES:
            async with session_factory() as exec_db:
                live_intent = await exec_db.get(TradeIntent, intent_id)
                live_decision = await exec_db.get(RiskDecision, decision_id)
            if live_intent is not None and live_decision is not None:
                try:
                    order = await execute_live_entry(
                        session_factory,
                        settings,
                        user_id=user_id,
                        intent=live_intent,
                        decision=live_decision,
                        mode=mode,
                    )
                except PermissionError as exc:
                    # MAINNET armed marker missing: skip this cycle, never
                    # guess (audit #8).
                    logger.warning("mainnet execution refused: %s", exc)
                    await exec_db.rollback()
                    return False
                if order is not None and order.status == "FILLED":
                    async with session_factory() as exec_db:
                        fresh = await exec_db.get(TradeIntent, intent_id)
                        if fresh is not None:
                            fresh.status = "EXECUTED"
                        await exec_db.execute(
                            update(AgentRun)
                            .where(AgentRun.id == run_id)
                            .values(intent_id=intent_id)
                        )
                        await exec_db.commit()
                    filled = True

    if filled:
        async with session_factory() as db:
            cfg = await db.get(AutopilotConfig, user_id)
            if cfg is not None:
                cfg.trades_today += 1
                await db.commit()
    return filled


async def _provider(session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID) -> Any:
    async with session_factory() as db:
        return await resolve_provider_config(db, user_id)


async def _set_error(
    session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID, message: str | None
) -> None:
    async with session_factory() as db:
        cfg = await db.get(AutopilotConfig, user_id)
        if cfg is not None:
            cfg.last_error = message
            await db.commit()


__all__ = ["run_forever", "run_user_cycle", "tick_once"]
