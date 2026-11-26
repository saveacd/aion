"""Repeatable datasets, causal discovery and research-task integration."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from aion.core.db import get_sessionmaker
from aion.models.market import MarketCandle, MarketCandleRevision
from aion.quant.datasets import freeze_candles
from aion.quant.factor_discovery import discover, evaluate_expression, parse_expression
from aion.services.market_ingest import _upsert_candles
from aion.services.research_worker import process_one

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
STEP = dt.timedelta(minutes=15)


def candles(n: int) -> list[MarketCandle]:
    return [
        MarketCandle(symbol="BTCUSDT", interval="15m", open_time=START + i * STEP,
                     close_time=START + (i + 1) * STEP, open=price, high=price + 1,
                     low=price - 1, close=price, volume=D(20) + i % 12, quote_volume=price * 20,
                     is_final=True, source="test")
        for i in range(n)
        for price in (D(str(100 + 3 * math.sin(i / 13) + i / 100)),)
    ]


def test_canonical_expression_is_causal_and_holdout_does_not_select_candidates():
    source = candles(480)
    cutoff = START + 480 * STEP
    dataset = freeze_candles(source + candles(481)[-1:], symbol="BTCUSDT", interval="15m", as_of=cutoff)
    assert len(dataset["bars"]) == 480
    lhs, rhs = ["MOMENTUM", 8], ["VOLUME_Z", 8]
    expression = parse_expression(["mul", lhs, rhs])
    assert expression == parse_expression(["mul", rhs, lhs])
    with pytest.raises(ValueError):
        parse_expression(["exec", lhs])
    with pytest.raises(ValueError):
        parse_expression(["MOMENTUM", -1])
    values = evaluate_expression(expression, dataset["bars"])
    assert evaluate_expression(expression, dataset["bars"][:100]) == values[:100]

    result = discover(dataset, 2)
    assert result["status"] == "COMPLETE"
    assert discover(dataset, 2) == result
    assert any("holdout" in row for row in result["candidates"])
    for row in source[result["holdout_start_index"]:]:
        row.volume *= 2
    altered = freeze_candles(source, symbol="BTCUSDT", interval="15m", as_of=cutoff)
    assert altered["dataset_id"] != dataset["dataset_id"]
    changed = discover(altered, 2)
    assert [r["train_rank_ic"] for r in changed["candidates"]] == [r["train_rank_ic"] for r in result["candidates"]]
    assert [r["expression"] for r in changed["candidates"] if "holdout" in r] == [
        r["expression"] for r in result["candidates"] if "holdout" in r
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_market_backfill_and_revision(clean_db):
    factory = get_sessionmaker()
    end = START + 3 * STEP
    def raw(index, price):
        opening = int((START + index * STEP).timestamp() * 1000)
        closing = int((START + (index + 1) * STEP).timestamp() * 1000) - 1
        return [opening, str(price), str(price + 1), str(price - 1), str(price),
                "10", closing, "1000", 2]

    async with factory() as db:
        first = await _upsert_candles(db, "BTCUSDT", "15m", [raw(0, 100), raw(2, 100)], end)
        assert first["candles"] == 2
        await db.commit()
        rows = (await db.scalars(select(MarketCandle).order_by(MarketCandle.open_time))).all()
        assert freeze_candles(rows, symbol="BTCUSDT", interval="15m", as_of=end)["quality"]["gaps"][0]["missing"] == 1
        repaired = await _upsert_candles(db, "BTCUSDT", "15m", [raw(0, 101), raw(1, 100), raw(2, 100)], end)
        assert repaired == {"candles": 2, "invalid_candles": 0, "revisions": 1}
        await db.commit()
    async with factory() as db:
        revision = await db.scalar(select(MarketCandleRevision))
        rows = (await db.scalars(select(MarketCandle).order_by(MarketCandle.open_time))).all()
        assert D(revision.previous["close"]) == 100
        assert D(revision.replacement["close"]) == 101
        assert freeze_candles(rows, symbol="BTCUSDT", interval="15m", as_of=end)["quality"]["eligible"]


@pytest.mark.asyncio(loop_scope="session")
async def test_authenticated_research_task_publishes_replayable_results(client, auth_headers, settings):
    factory = get_sessionmaker()
    async with factory() as db:
        db.add_all(candles(480))
        await db.commit()
    path = "/api/v1/factors/research/tasks"
    queued = await client.post(path, headers=auth_headers, json={"symbol": "BTCUSDT", "horizon_bars": 2})
    assert queued.status_code == 202, queued.text
    assert await process_one(factory, settings)
    response = await client.get(f"{path}/{queued.json()['id']}", headers=auth_headers)
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["status"] == "SUCCEEDED", task["error"]
    report = task["result"]
    assert report["discovery"] == discover(report["dataset"], 2)
    assert report["discovery"]["status"] == "COMPLETE"
    replay = await client.post(f"{path}/{queued.json()['id']}/replay", headers=auth_headers)
    assert replay.status_code == 200 and replay.json()["match"]
    listed = (await client.get(path, headers=auth_headers)).json()
    assert "bars" not in listed[0]["result"]["dataset"]
    assert listed[0]["result"]["discovery"]["candidate_count"] > 0
