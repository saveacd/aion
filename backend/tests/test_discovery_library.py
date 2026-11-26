"""Phase v2.16 acceptance: replay, cross-market lifecycle and signal consumers."""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from aion.core.db import get_sessionmaker
from aion.models.factors import FactorRecord
from aion.models.research import (
    FactorLifecycleEvent,
    FactorResearchTask,
    FactorValidationResult,
    FactorVersion,
)
from aion.quant.datasets import freeze_candles, restore_candles
from aion.quant.factor_discovery import discover, parse_expression
from aion.quant.factor_signals import build_factor_signals
from aion.quant.factor_values import factor_series
from aion.quant.strategy_research import _build_consensus
from aion.services.agent_service import build_inputs
from aion.services.discovery_library import persist_discovery, replay_evidence

STEP = dt.timedelta(minutes=15)


def pattern(count, start, symbol, seed, reverse_after=660):
    rng = random.Random(seed)  # noqa: S311 - deterministic research fixture
    price = Decimal(40000 if symbol == "BTCUSDT" else 3000)
    previous = 0
    for index in range(count):
        x = rng.uniform(-1, 1)
        ratio = Decimal(str(previous * (.012 if index < reverse_after else -.012)))
        closing = price * (1 + ratio)
        t = start + STEP * index
        yield SimpleNamespace(
            symbol=symbol, interval="15m", open_time=t, close_time=t+STEP, open=price,
            high=max(price, closing)*Decimal("1.0001"), low=min(price, closing)*Decimal(".9999"),
            close=closing, volume=Decimal(str(1000+400*x)), source="synthetic", is_final=True,
        )
        price = closing
        previous = x


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_asset_lifecycle_and_existing_strategy_consumers(trader_user):
    factory = get_sessionmaker()
    start = dt.datetime.now(dt.UTC).replace(second=0, microsecond=0) - STEP * 675

    def dataset(symbol, seed, count):
        return freeze_candles(list(pattern(count, start, symbol, seed)),
                              symbol=symbol, interval="15m", as_of=start + STEP * count)

    async def research(count, suffix, *, replicate=True):
        data = dataset("BTCUSDT", 44, count)
        peers = {"ETHUSDT": dataset("ETHUSDT", 45, count)} if replicate else {}
        report = discover(data, 1)
        async with factory() as db:
            task = FactorResearchTask(
                user_id=trader_user.id, symbol="BTCUSDT", interval="15m", horizon_bars=1,
                status="RUNNING", idempotency_key=suffix, started_at=start + STEP * count,
            )
            db.add(task)
            await db.flush()
            outcome = await persist_discovery(db, task, data, peers, report)
            task.result = outcome
            task.status = "SUCCEEDED"
            await db.commit()
        return outcome

    first = await research(600, "first")
    assert len(first["changes"]) == 3
    assert {change["status"] for change in first["changes"]} == {"WATCH"}
    for change in first["changes"]:
        assert change["assets"]["ETHUSDT"]["passed"]
        assert change["primary"]["by_regime"]
        assert len(change["primary"]["by_time"]) == 3
        assert change["portfolio"]["status"] == "SCORED"
        assert replay_evidence(dataset("BTCUSDT", 44, 600), {"ETHUSDT": dataset("ETHUSDT", 45, 600)}, change)
    repeated = await research(600, "repeat")
    assert repeated["adoption"] == "HOLDOUT_ALREADY_USED"
    assert repeated["changes"] == []

    promoted = await research(670, "fresh")
    assert all(change["status"] == "ACTIVE" for change in promoted["changes"])
    async with factory() as db:
        records = (await db.scalars(select(FactorRecord).where(FactorRecord.status == "ACTIVE"))).all()
        assert len(records) == 3
        before = restore_candles(dataset("BTCUSDT", 44, 600))
        after = restore_candles(dataset("BTCUSDT", 44, 675))
        assert factor_series(records[0], before) == [None] * len(before)
        assert build_factor_signals(records, after)["factor_count"] == 3
        assert not _build_consensus(records, before)
        assert after[-1].open_time in _build_consensus(records, after)

    # An actual new sample with reversed effects removes the signal, and
    # repeated independent failures retire it while retaining experiment data.
    degraded = await research(730, "degrade")
    assert {change["status"] for change in degraded["changes"]} == {"DEGRADED"}
    await research(790, "failure2")
    retired = await research(850, "failure3")
    assert {change["status"] for change in retired["changes"]} == {"RETIRED"}
    async with factory() as db:
        for record in (await db.scalars(select(FactorRecord))).all():
            events = (await db.scalars(select(FactorLifecycleEvent.to_status).where(
                FactorLifecycleEvent.factor_id == record.id,
            ))).all()
            assert set(events) == {"CANDIDATE", "VALIDATING", "WATCH", "ACTIVE", "DEGRADED", "RETIRED"}
            versions = (await db.scalars(select(FactorVersion.version)
                        .where(FactorVersion.factor_id == record.id).order_by(FactorVersion.version))).all()
            assert versions == [1, 2, 3, 4, 5]
            assert len((await db.scalars(select(FactorValidationResult).where(
                FactorValidationResult.factor_id == record.id))).all()) == 5
            assert not any(factor_series(record, after))


@pytest.mark.asyncio(loop_scope="session")
async def test_active_expression_enters_personal_decision_context(trader_user):
    from aion.models.market import MarketCandle

    factory = get_sessionmaker()
    start = dt.datetime.now(dt.UTC) - STEP * 81
    async with factory() as db:
        for bar in pattern(80, start, "BTCUSDT", 44):
            db.add(MarketCandle(**vars(bar), quote_volume=bar.volume * bar.close))
        db.add(FactorRecord(
            user_id=trader_user.id, symbol="BTCUSDT", interval="15m", factor_name="accepted_dsl",
            formula="FACTOR_DSL", domain="discovery", agent="deterministic_search", expression=["VOLUME_Z", 8],
            direction="POSITIVE", lookback_bars=8, horizon_bars=1, status="ACTIVE", coverage=Decimal(1),
            ic_mean=Decimal("0.1"), ic_ir=Decimal(1), hypothesis="accepted test expression",
            discovery_state={"active_since": start.isoformat(), "active_until": (start + STEP*90).isoformat()},
        ))
        await db.commit()
        inputs = await build_inputs(db, "BTCUSDT", "15m", user_id=trader_user.id, market_research={"inputs": {}})
        assert inputs["factor_signals"]["factors"][0]["factor_name"] == "accepted_dsl"


def test_unsafe_scale_rejected():
    for coefficient in (float("nan"), float("inf"), 5, True, "1"):
        with pytest.raises(ValueError):
            parse_expression(["scale", coefficient, ["MOMENTUM", 8]])


def test_funding_cost_uses_published_settlement_and_stress_for_uncovered_bars():
    from aion.quant.factor_validation import aligned_observations

    start = dt.datetime(2026, 11, 26, tzinfo=dt.UTC)
    series = list(pattern(7, start, "BTCUSDT", 47))
    snapshot = freeze_candles(series, symbol="BTCUSDT", interval="15m", as_of=start + STEP * 7)
    funding = [
        {"t": (start + STEP * step).isoformat(), "rate": "0.001"}
        for step in (0, 2, 6)
    ]
    observations = aligned_observations(snapshot["bars"], [1] * 7, [0], 2, funding)
    assert observations[0]["funding_covered"]
    assert observations[0]["funding_return_long"] == pytest.approx(-.001)
    assert observations[0]["funding_stress"] == 0
    uncovered = aligned_observations(snapshot["bars"], [1] * 7, [0], 2)
    assert not uncovered[0]["funding_covered"]
    assert uncovered[0]["funding_stress"] > 0
