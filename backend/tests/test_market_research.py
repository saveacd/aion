"""Shared research publication and freshness with real Redis coordination."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from aion.core.db import get_sessionmaker
from aion.core.redis import get_redis
from aion.services import market_research


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_workers_publish_one_public_run(database, settings, monkeypatch):
    symbol = f"TEST{uuid.uuid4().hex[:8].upper()}"
    calls = []
    timestamp = dt.datetime.now(dt.UTC).isoformat()

    async def public_inputs(db, input_symbol, interval, *, public_only):
        assert public_only and input_symbol == symbol
        return {
            "market": {"captured_at": timestamp},
            "candles": {"count": 50},
            "interval": interval,
        }

    async def order_book(input_symbol):
        return {"bids": [], "asks": []}

    class ResearchRunner:
        async def research_market(self, context):
            calls.append(context.run_id)
            await asyncio.sleep(0.02)
            return {"strategy_research": {"summary": "public hypothesis"}}

    monkeypatch.setattr(market_research, "build_inputs", public_inputs)
    monkeypatch.setattr(market_research, "_order_book", order_book)
    monkeypatch.setattr(market_research, "build_orchestrator", lambda *args: ResearchRunner())
    wall_time = time.time()
    monkeypatch.setattr(market_research, "time", SimpleNamespace(time=lambda: wall_time, monotonic=time.monotonic))
    factory = get_sessionmaker()
    await asyncio.gather(*(market_research._refresh(factory, settings, symbol) for _ in range(5)))
    await market_research._refresh(factory, settings, symbol)
    result = await market_research.read_research(symbol)
    assert len(calls) == 1
    assert result is not None
    assert result["version"] == str(calls[0])
    assert result["inputs"]["order_book"] == {"bids": [], "asks": []}

    expired = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=market_research.RESEARCH_MAX_AGE_SECONDS + 1)
    await get_redis().set(
        market_research._key(symbol), json.dumps({**result, "as_of": expired.isoformat()}), ex=60
    )
    assert await market_research.read_research(symbol) is None
