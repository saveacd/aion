"""Market-candle-driven PAPER observer for research-approved candidates.

This module is deliberately isolated from exchange execution. It consumes only
persisted final candles and writes only PAPER ledger rows. It never constructs
or submits exchange orders.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

D = Decimal
FEE_BPS = D("4")
SLIPPAGE_BPS = D("2")
VOLUME_PARTICIPATION = D("0.10")


def paper_signal_for(kind: str, values: Sequence[float | None], threshold: float) -> str:
    """Translate causal completed-bar factor values into a PAPER side."""
    if not values:
        return "FLAT"
    current = values[-1]
    if current is None:
        return "FLAT"
    if kind == "trend_confirmation":
        if len(values) < 2 or values[-2] is None:
            return "FLAT"
        previous = values[-2]
        if current * previous <= 0 or min(abs(current), abs(previous)) < threshold:
            return "FLAT"
        value = current
    elif kind == "range_reversion":
        if abs(current) < threshold:
            return "FLAT"
        value = -current
    elif kind == "high_volatility_defensive":
        if abs(current) < threshold:
            return "FLAT"
        value = current
    else:
        return "FLAT"
    return "LONG" if value > 0 else "SHORT"


def _profile(kind: str) -> tuple[Decimal, Decimal, Decimal]:
    """Return notional fraction, stop loss %, take profit % for PAPER only."""
    return {
        "trend_confirmation": (D("0.50"), D("3"), D("6")),
        "range_reversion": (D("0.35"), D("1.5"), D("2.5")),
        "high_volatility_defensive": (D("0.20"), D("2"), D("4")),
    }.get(kind, (D("0.10"), D("1"), D("1")))


def is_new_observation_candle(
    candle_open_time: dt.datetime,
    last_processed: dt.datetime | None,
    session_created_at: dt.datetime,
) -> bool:
    """Only process bars opened after the PAPER session began.

    Historical candles may be used to warm up factor calculations, but must
    never create retrospective PAPER fills for a newly-created session.
    """
    def aware(value: dt.datetime) -> dt.datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)

    candle_time = aware(candle_open_time)
    if last_processed is not None:
        return candle_time > aware(last_processed)
    return candle_time >= aware(session_created_at)


async def process_paper_sessions(session_factory: async_sessionmaker) -> int:
    """Advance up to 100 opted-in auto-PAPER sessions by newly closed candles."""
    from aion.models.factors import FactorRecord
    from aion.models.market import MarketCandle
    from aion.models.research import PaperStrategySession, PaperTradeRecord, StrategyCandidate
    from aion.quant.strategy_research import as_backtest_candles, _build_consensus

    advanced = 0
    async with session_factory() as db:
        sessions = (await db.execute(
            select(PaperStrategySession).where(PaperStrategySession.status == "OBSERVING")
            .order_by(PaperStrategySession.created_at.asc())
            .with_for_update(skip_locked=True).limit(100)
        )).scalars().all()
        for session in sessions:
            metrics = dict(session.metrics or {})
            if metrics.get("engine") != "AUTO_MARKET_PAPER_V1" or not session.candidate_id:
                continue
            candidate = await db.scalar(select(StrategyCandidate).where(
                StrategyCandidate.id == session.candidate_id,
                StrategyCandidate.user_id == session.user_id,
            ))
            if candidate is None:
                session.status = "STOPPED"
                metrics["stop_reason"] = "CANDIDATE_MISSING"
                session.metrics = metrics
                continue
            spec = dict(candidate.specification or {})
            symbol, interval = spec.get("symbol"), spec.get("interval")
            if not symbol or not interval:
                continue
            candles_rows = (await db.execute(
                select(MarketCandle).where(
                    MarketCandle.symbol == symbol,
                    MarketCandle.interval == interval,
                    MarketCandle.is_final.is_(True),
                ).order_by(MarketCandle.open_time.desc()).limit(600)
            )).scalars().all()
            candles_rows = list(reversed(candles_rows))
            if len(candles_rows) < 3:
                continue
            factors = (await db.execute(select(FactorRecord).where(
                FactorRecord.user_id == session.user_id,
                FactorRecord.symbol == symbol,
                FactorRecord.interval == interval,
                FactorRecord.status == "ACTIVE",
            ))).scalars().all()
            candles = as_backtest_candles(candles_rows)
            signals = _build_consensus(factors, candles)
            kind = str(spec.get("strategy_kind", "trend_confirmation"))
            threshold = {"trend_confirmation": 0.18, "range_reversion": 0.22,
                         "high_volatility_defensive": 0.65}.get(kind, 1.0)
            notional_fraction, stop_pct, target_pct = _profile(kind)
            last_raw = metrics.get("last_processed_candle")
            last_time = dt.datetime.fromisoformat(last_raw) if last_raw else None
            session_created_at = session.created_at or dt.datetime.now(dt.UTC)
            open_trade = await db.scalar(select(PaperTradeRecord).where(
                PaperTradeRecord.session_id == session.id,
                PaperTradeRecord.user_id == session.user_id,
                PaperTradeRecord.status == "OPEN",
            ).order_by(PaperTradeRecord.opened_at.desc()).limit(1).with_for_update())
            current_cash = D(session.current_cash)
            realized_pnl = D(str(metrics.get("realized_pnl", "0")))
            peak_equity = D(str(metrics.get("peak_equity", session.initial_cash)))
            max_drawdown = D(str(metrics.get("max_drawdown_pct", "0")))
            trade_count = int(metrics.get("trades", 0))
            for index, candle in enumerate(candles):
                if not is_new_observation_candle(candle.open_time, last_time, session_created_at):
                    continue
                if index == 0:
                    continue
                previous_values = [signals.get(candles[index - 1].open_time)]
                if index >= 2:
                    previous_values.insert(0, signals.get(candles[index - 2].open_time))
                side = paper_signal_for(kind, previous_values, threshold)
                # Protective exits are checked against the current bar; if both
                # barriers are touched, stop loss is conservatively first.
                blocked_reentry = False
                if open_trade is not None:
                    long_side = open_trade.side == "LONG"
                    entry = D(open_trade.entry_price)
                    stop = entry * (D("1") - stop_pct / 100) if long_side else entry * (D("1") + stop_pct / 100)
                    target = entry * (D("1") + target_pct / 100) if long_side else entry * (D("1") - target_pct / 100)
                    exit_price = None
                    if long_side and candle.low <= stop:
                        exit_price = min(stop, candle.open)
                        blocked_reentry = True
                    elif not long_side and candle.high >= stop:
                        exit_price = max(stop, candle.open)
                        blocked_reentry = True
                    elif long_side and candle.high >= target:
                        exit_price = max(target, candle.open)
                        blocked_reentry = True
                    elif not long_side and candle.low <= target:
                        exit_price = min(target, candle.open)
                        blocked_reentry = True
                    elif side != open_trade.side:
                        exit_price = candle.open
                    if exit_price is not None:
                        slip = SLIPPAGE_BPS / D("10000")
                        fill = exit_price * (D("1") - slip if long_side else D("1") + slip)
                        exit_fee = fill * D(open_trade.quantity) * FEE_BPS / D("10000")
                        direction = D("1") if long_side else D("-1")
                        gross = (fill - entry) * D(open_trade.quantity) * direction
                        total_fees = D(open_trade.fees) + exit_fee
                        pnl = gross - total_fees
                        open_trade.exit_price = fill
                        open_trade.fees = total_fees
                        open_trade.pnl = pnl
                        open_trade.status = "CLOSED"
                        open_trade.closed_at = candle.open_time
                        current_cash += pnl
                        realized_pnl += pnl
                        trade_count += 1
                        open_trade = None
                if open_trade is None and not blocked_reentry and side in {"LONG", "SHORT"}:
                    buying = side == "LONG"
                    slip = SLIPPAGE_BPS / D("10000")
                    fill = candle.open * (D("1") + slip if buying else D("1") - slip)
                    if fill > 0 and current_cash > 0:
                        quantity = min(
                            current_cash * notional_fraction / fill,
                            candle.volume * VOLUME_PARTICIPATION,
                        )
                        if quantity > 0:
                            entry_fee = fill * quantity * FEE_BPS / D("10000")
                            open_trade = PaperTradeRecord(
                                user_id=session.user_id, session_id=session.id, symbol=symbol,
                                side=side, quantity=quantity, entry_price=fill,
                                fees=entry_fee, status="OPEN", opened_at=candle.open_time,
                            )
                            db.add(open_trade)
                unrealized = D("0")
                if open_trade is not None:
                    direction = D("1") if open_trade.side == "LONG" else D("-1")
                    unrealized = (candle.close - D(open_trade.entry_price)) * D(open_trade.quantity) * direction - D(open_trade.fees)
                equity = current_cash + unrealized
                peak_equity = max(peak_equity, equity)
                drawdown = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else D("0")
                max_drawdown = max(max_drawdown, drawdown)
                last_time = candle.open_time
                metrics.update({
                    "realized_pnl": str(realized_pnl), "trades": trade_count,
                    "unrealized_pnl": str(unrealized), "equity": str(equity),
                    "peak_equity": str(peak_equity), "max_drawdown_pct": str(max_drawdown),
                    "last_processed_candle": last_time.isoformat(),
                    "last_price": str(candle.close), "execution_mode": "PAPER",
                    "max_leverage": "1x", "engine": "AUTO_MARKET_PAPER_V1",
                })
                if max_drawdown >= D("20") or (trade_count >= 30 and realized_pnl <= 0):
                    session.status = "STOPPED"
                    candidate.status = "RETIRED"
                    metrics["stop_reason"] = "MAX_DRAWDOWN" if max_drawdown >= D("20") else "NON_POSITIVE_30_TRADE_PNL"
                    if open_trade is not None:
                        # PAPER liquidation is priced from the last completed bar;
                        # this is a simulated close, never an exchange instruction.
                        long_side = open_trade.side == "LONG"
                        slip = SLIPPAGE_BPS / D("10000")
                        fill = candle.close * (D("1") - slip if long_side else D("1") + slip)
                        exit_fee = fill * D(open_trade.quantity) * FEE_BPS / D("10000")
                        direction = D("1") if long_side else D("-1")
                        gross = (fill - D(open_trade.entry_price)) * D(open_trade.quantity) * direction
                        total_fees = D(open_trade.fees) + exit_fee
                        pnl = gross - total_fees
                        open_trade.exit_price = fill
                        open_trade.fees = total_fees
                        open_trade.pnl = pnl
                        open_trade.status = "CLOSED"
                        open_trade.closed_at = candle.close_time
                        current_cash += pnl
                        realized_pnl += pnl
                        trade_count += 1
                        metrics["realized_pnl"] = str(realized_pnl)
                        metrics["trades"] = trade_count
                        metrics["unrealized_pnl"] = "0"
                        metrics["equity"] = str(current_cash)
                        peak_equity = max(peak_equity, current_cash)
                        metrics["peak_equity"] = str(peak_equity)
                        metrics["max_drawdown_pct"] = str(max_drawdown)
                    break
            session.current_cash = current_cash
            session.metrics = metrics
            advanced += 1
        await db.commit()
    return advanced
