"""Autopilot + paper broker + learning loop tests (all PAPER, no network)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from aion.core.db import get_sessionmaker
from aion.execution.paper import PaperBroker
from aion.models import (
    AutopilotConfig,
    LearningLesson,
    MarketSnapshot,
    PaperPosition,
    TradeIntent,
    TradeOutcome,
    TradingControlState,
)
from aion.models.account import AccountSnapshot
from aion.models.base import utcnow
from aion.models.trading import Fill, Order, RiskDecision
from aion.services.autopilot import _consume_watch_triggers, run_user_cycle
from aion.services.learning import close_due_positions_and_record, performance_report

pytestmark = pytest.mark.asyncio(loop_scope="session")

D = Decimal


async def _seed_price(symbol: str, price: str) -> None:
    async with get_sessionmaker()() as db:
        db.add(MarketSnapshot(symbol=symbol, last_price=D(price), mark_price=D(price)))
        await db.commit()


async def _make_intent(user_id, *, notional: str = "1000", stop: str = "49000", tp: str = "52000"):
    async with get_sessionmaker()() as db:
        intent = TradeIntent(
            intent_key=f"test-{utcnow().timestamp()}-{user_id}",
            symbol="BTCUSDT",
            side="BUY",
            position_side="LONG",
            notional_usd=D(notional),
            requested_leverage=5,
            stop_loss=D(stop) if stop else None,
            take_profit=D(tp) if tp else None,
            confidence=D("0.8"),
            status="APPROVED",
            expires_at=utcnow() + dt.timedelta(minutes=5),
            mode="PAPER",
            user_id=user_id,
        )
        db.add(intent)
        await db.flush()
        decision = RiskDecision(
            intent_id=intent.id, symbol="BTCUSDT", approved=True, mode="PAPER",
            user_id=user_id,
        )
        db.add(decision)
        await db.commit()
        await db.refresh(intent)
        await db.refresh(decision)
        db.expunge(intent)
        db.expunge(decision)
        return intent, decision


async def test_paper_broker_entry_fill_math(trader_user):
    await _seed_price("BTCUSDT", "50000")
    intent, decision = await _make_intent(trader_user.id)
    broker = PaperBroker(get_sessionmaker())

    async with get_sessionmaker()() as db:
        order = await broker.submit_entry(db, intent, decision)
        assert order is not None
        await db.commit()

    async with get_sessionmaker()() as db:
        order = await db.scalar(select(Order).where(Order.intent_id == intent.id))
        assert order is not None and order.status == "FILLED" and order.mode == "PAPER"
        # BUY slips up by 2 bps.
        assert D(order.avg_fill_price) == D("50000") * D("1.0002")
        fill = await db.scalar(select(Fill).where(Fill.order_id == order.id))
        assert fill is not None
        assert D(fill.quote_quantity) == pytest.approx(D("1000"), rel=D("0.0001"))
        assert D(fill.commission) == pytest.approx(D("0.4"), rel=D("0.0001"))
        position = await db.scalar(
            select(PaperPosition).where(PaperPosition.intent_id == intent.id)
        )
        assert position is not None and position.status == "OPEN"
        assert position.leverage == 5
        snapshot = await db.scalar(
            select(AccountSnapshot)
            .where(AccountSnapshot.user_id == trader_user.id, AccountSnapshot.mode == "PAPER")
            .order_by(AccountSnapshot.captured_at.desc())
        )
        assert snapshot is not None
        # Entry fee is charged to equity immediately.
        assert D(snapshot.total_wallet_balance) == pytest.approx(
            D("10000") - D("0.4"), rel=D("0.0001")
        )


async def test_paper_broker_close_take_profit(trader_user):
    await _seed_price("BTCUSDT", "50000")
    intent, decision = await _make_intent(trader_user.id)
    broker = PaperBroker(get_sessionmaker())
    async with get_sessionmaker()() as db:
        assert await broker.submit_entry(db, intent, decision) is not None
        await db.commit()

    async with get_sessionmaker()() as db:
        position = (
            await db.execute(select(PaperPosition).where(PaperPosition.intent_id == intent.id))
        ).scalar_one()
        outcome = await broker.close_position(
            db, position, exit_price=D("52000"), reason="TP"
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        position = (
            await db.execute(select(PaperPosition).where(PaperPosition.intent_id == intent.id))
        ).scalar_one()
        assert position.status == "CLOSED" and position.exit_reason == "TP"
        outcome = (
            await db.execute(select(TradeOutcome).where(TradeOutcome.intent_id == intent.id))
        ).scalar_one()
        entry = D("50000") * D("1.0002")
        exit_price = D("52000") * D("0.9998")  # SELL slips down
        qty = D("1000") / entry
        expected_gross = (exit_price - entry) * qty
        expected_fee = exit_price * qty * D("0.0004")
        assert D(outcome.pnl_usd) == pytest.approx(expected_gross - expected_fee, rel=D("0.001"))
        assert outcome.exit_reason == "TP"
        assert outcome.r_multiple is not None and D(outcome.r_multiple) > 0


async def test_close_due_positions_stop_loss(trader_user):
    await _seed_price("BTCUSDT", "50000")
    intent, decision = await _make_intent(trader_user.id, stop="49500", tp="52000")
    broker = PaperBroker(get_sessionmaker())
    async with get_sessionmaker()() as db:
        assert await broker.submit_entry(db, intent, decision) is not None
        await db.commit()

    # Price drops through the stop.
    await _seed_price("BTCUSDT", "49400")
    closed = await close_due_positions_and_record(get_sessionmaker(), None)  # type: ignore[arg-type]
    assert closed == 1
    async with get_sessionmaker()() as db:
        position = (await db.execute(select(PaperPosition))).scalar_one()
        assert position.status == "CLOSED" and position.exit_reason == "SL"
        outcome = (await db.execute(select(TradeOutcome))).scalar_one()
        assert D(outcome.pnl_usd) < 0


async def test_close_due_positions_timeout(trader_user):
    await _seed_price("BTCUSDT", "50000")
    intent, decision = await _make_intent(trader_user.id, stop=None, tp=None)
    broker = PaperBroker(get_sessionmaker())
    async with get_sessionmaker()() as db:
        assert await broker.submit_entry(db, intent, decision) is not None
        position = (await db.execute(select(PaperPosition))).scalar_one()
        position.opened_at = utcnow() - dt.timedelta(hours=49)
        await db.commit()
    closed = await close_due_positions_and_record(get_sessionmaker(), None)  # type: ignore[arg-type]
    assert closed == 1
    async with get_sessionmaker()() as db:
        position = (await db.execute(select(PaperPosition))).scalar_one()
        assert position.exit_reason == "TIMEOUT"


async def test_autopilot_blocked_by_emergency_stop(settings, trader_user):
    async with get_sessionmaker()() as db:
        db.add(TradingControlState(id=1, emergency_stop=True))
        db.add(
            AutopilotConfig(
                user_id=trader_user.id, enabled=True, auto_execute=True,
                symbols=["BTCUSDT"],
            )
        )
        await db.commit()
    await run_user_cycle(get_sessionmaker(), settings, trader_user.id)
    async with get_sessionmaker()() as db:
        cfg = await db.get(AutopilotConfig, trader_user.id)
        assert cfg.last_error is not None and "EMERGENCY_STOP" in cfg.last_error


async def test_autopilot_rejects_non_paper_execution_mode(settings, trader_user):
    async with get_sessionmaker()() as db:
        db.add(AutopilotConfig(
            user_id=trader_user.id, enabled=True, auto_execute=True,
            execution_mode="TESTNET", symbols=["BTCUSDT"],
        ))
        await db.commit()

    await run_user_cycle(get_sessionmaker(), settings, trader_user.id)

    async with get_sessionmaker()() as db:
        cfg = await db.get(AutopilotConfig, trader_user.id)
        assert cfg.last_error is not None
        assert "仅允许 PAPER" in cfg.last_error


async def test_autopilot_requires_provider(settings, trader_user, monkeypatch):
    # The deployment-level env fallback must not mask a missing provider here.
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    async with get_sessionmaker()() as db:
        db.add(AutopilotConfig(user_id=trader_user.id, enabled=True, symbols=["BTCUSDT"]))
        await db.commit()
    await run_user_cycle(get_sessionmaker(), settings, trader_user.id)
    async with get_sessionmaker()() as db:
        cfg = await db.get(AutopilotConfig, trader_user.id)
        assert cfg.last_error is not None and "大模型" in cfg.last_error


async def test_autopilot_api_confirm_gate(client, trader_user, login):
    headers = await login("trader", "Aion-Trader-Passw0rd!")
    response = await client.get("/api/v1/autopilot", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False and body["auto_execute"] is False

    # Enabling auto_execute without the explicit confirmation is rejected.
    response = await client.put(
        "/api/v1/autopilot", headers=headers, json={"enabled": True, "auto_execute": True}
    )
    assert response.status_code == 422

    response = await client.put(
        "/api/v1/autopilot",
        headers=headers,
        json={
            "enabled": True,
            "auto_execute": True,
            "confirm_auto_execute": True,
            "symbols": ["btcusdt"],
            "cycle_seconds": 120,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True and body["auto_execute"] is True
    assert body["symbols"] == ["BTCUSDT"]

    # Disabling the pilot also disarms auto execution.
    response = await client.put("/api/v1/autopilot", headers=headers, json={"enabled": False})
    assert response.json()["auto_execute"] is False


async def test_learning_performance_and_lessons(client, trader_user, login):
    async with get_sessionmaker()() as db:
        now = utcnow()
        for idx, pnl in enumerate((D("50"), D("-20"), D("30"))):
            db.add(
                TradeOutcome(
                    user_id=trader_user.id,
                    symbol="BTCUSDT",
                    position_side="LONG",
                    quantity=D("0.1"),
                    entry_price=D("50000"),
                    exit_price=D("51000"),
                    pnl_usd=pnl,
                    fee_usd=D("0.8"),
                    r_multiple=D("1") if pnl > 0 else D("-1"),
                    exit_reason="TP" if pnl > 0 else "SL",
                    opened_at=now - dt.timedelta(hours=2 + idx),
                    closed_at=now - dt.timedelta(hours=1 + idx),
                )
            )
        db.add(
            LearningLesson(
                user_id=trader_user.id, lesson="震荡市降低仓位", category="risk_control",
                active=True,
            )
        )
        await db.commit()

    headers = await login("trader", "Aion-Trader-Passw0rd!")
    response = await client.get("/api/v1/learning/performance", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_trades"] == 3
    assert body["wins"] == 2
    assert body["win_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert D(body["total_pnl_usd"]) == D("60")
    assert len(body["equity_curve"]) == 3

    response = await client.get("/api/v1/learning/lessons", headers=headers)
    lessons = response.json()["items"]
    assert len(lessons) == 1 and lessons[0]["active"] is True

    response = await client.post(
        f"/api/v1/learning/lessons/{lessons[0]['id']}/toggle", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["active"] is False


async def test_performance_report_empty(trader_user):
    async with get_sessionmaker()() as db:
        report = await performance_report(db, trader_user.id)
    assert report["total_trades"] == 0
    assert report["win_rate"] is None
    assert report["equity_curve"] == []


async def _seed_watch_plan(user_id, *, expires_in_minutes: int = 60) -> None:
    async with get_sessionmaker()() as db:
        db.add(
            AutopilotConfig(
                user_id=user_id,
                enabled=True,
                symbols=["BTCUSDT"],
                watch_plans=[
                    {
                        "symbol": "BTCUSDT",
                        "side": "BUY",
                        "entry_low": "49900",
                        "entry_high": "50100",
                        "stop_loss": "49500",
                        "take_profit": "51000",
                        "leverage": 10,
                        "confidence": 0.72,
                        "reason": "回踩EMA20后做多",
                        "created_at": utcnow().isoformat(),
                        "expires_at": (
                            utcnow() + dt.timedelta(minutes=expires_in_minutes)
                        ).isoformat(),
                    }
                ],
            )
        )
        await db.commit()


async def test_watch_plan_fires_when_price_enters_zone(monkeypatch, trader_user):
    await _seed_watch_plan(trader_user.id)
    monkeypatch.setattr(
        "aion.services.autopilot.latest_price",
        lambda symbol: {"price": D("50000"), "ts": utcnow()},
    )

    triggered = await _consume_watch_triggers(get_sessionmaker())
    assert triggered == {trader_user.id: {"BTCUSDT"}}

    async with get_sessionmaker()() as db:
        cfg = await db.get(AutopilotConfig, trader_user.id)
        assert cfg.watch_plans == []  # one-shot plan is consumed


async def test_watch_plan_ignores_stale_or_expired_ticks(monkeypatch, trader_user):
    await _seed_watch_plan(trader_user.id, expires_in_minutes=-1)
    monkeypatch.setattr(
        "aion.services.autopilot.latest_price",
        lambda symbol: {"price": D("50000"), "ts": utcnow() - dt.timedelta(minutes=5)},
    )

    triggered = await _consume_watch_triggers(get_sessionmaker())
    assert triggered == {}
    async with get_sessionmaker()() as db:
        cfg = await db.get(AutopilotConfig, trader_user.id)
        assert cfg.watch_plans == []  # expired plan was dropped
