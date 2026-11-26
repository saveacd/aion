"""Publish one public market research result per symbol and ten-minute window."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.agents.base import AgentContext, DbRunRecorder
from aion.core.config import Settings
from aion.core.redis import distributed_lock, get_redis
from aion.services.agent_service import build_inputs, build_orchestrator, resolve_provider_config
from aion.services.market_ingest import FAPI
from aion.services.market_stream import recent_market_events

logger = logging.getLogger(__name__)
PERIOD_SECONDS = 600
RESEARCH_MAX_AGE_SECONDS = 900
INTERVAL = "15m"


def research_symbols(settings: Settings) -> list[str]:
    return list(dict.fromkeys(("BTCUSDT", "ETHUSDT", *settings.default_symbols)))


def _key(symbol: str) -> str:
    return f"aion:market-research:v1:{symbol}"


def recent(timestamp: str, *, max_age: int) -> bool:
    age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(timestamp)).total_seconds()
    return 0 <= age <= max_age


async def read_research(symbol: str) -> dict[str, Any] | None:
    try:
        raw = await get_redis().get(_key(symbol))
        if raw:
            snapshot = json.loads(raw)
            if recent(snapshot["as_of"], max_age=RESEARCH_MAX_AGE_SECONDS):
                return snapshot
    except Exception:
        logger.warning("Shared market research unavailable for %s", symbol, exc_info=True)
    return None


async def _order_book(symbol: str) -> dict[str, Any] | None:
    depth = recent_market_events(symbol).get("depthUpdate")
    if depth is not None:
        return depth
    try:
        async with httpx.AsyncClient(base_url=FAPI, timeout=5) as client:
            response = await client.get("/fapi/v1/depth", params={"symbol": symbol, "limit": 10})
            response.raise_for_status()
            data = response.json()
            return {"bids": data["bids"], "asks": data["asks"], "captured_at": dt.datetime.now(dt.UTC).isoformat()}
    except (httpx.HTTPError, KeyError, ValueError):
        return None


async def _refresh(
    factory: async_sessionmaker[AsyncSession], settings: Settings, symbol: str,
) -> None:
    key = _key(symbol)
    slot = int(time.time()) // PERIOD_SECONDS
    redis = get_redis()
    claim = f"{key}:slot:{slot}"
    if await redis.exists(claim):
        return
    async with distributed_lock(key, ttl_seconds=90, wait_seconds=0) as acquired:
        if not acquired or await redis.exists(claim):
            return
        async with factory() as db:
            inputs = await build_inputs(db, symbol, INTERVAL, public_only=True)
            provider = None
            if not (settings.openai_api_key or settings.anthropic_api_key):
                provider = await resolve_provider_config(db, None)
        market = inputs["market"]
        if not market or not recent(market["captured_at"], max_age=120) or not inputs["candles"]["count"]:
            return
        inputs["order_book"] = await _order_book(symbol)
        live = recent_market_events(symbol)
        inputs["market_events"] = {kind: event for kind, event in live.items() if kind != "depthUpdate"}
        orchestrator = build_orchestrator(settings, factory, provider)
        if int(time.time()) // PERIOD_SECONDS != slot:
            return
        if not await redis.set(claim, "1", nx=True, ex=2 * PERIOD_SECONDS):
            return
        run_id = uuid.uuid4()
        recorder = DbRunRecorder(factory)
        await recorder.ensure_run(run_id=run_id, symbol=symbol, interval=INTERVAL, trigger="market_research")
        started = time.monotonic()
        try:
            async with asyncio.timeout(min(settings.agent_run_timeout_seconds, PERIOD_SECONDS - 30)):
                conclusions = await orchestrator.research_market(
                    AgentContext(symbol=symbol, interval=INTERVAL, run_id=run_id, inputs=inputs)
                )
        except TimeoutError:
            await recorder.finish_run(
                run_id=run_id, verdict="RESEARCH", confidence=None,
                summary="共享市场研究超时",
                duration_ms=int((time.monotonic() - started) * 1000),
                status="DEGRADED",
            )
            return
        if conclusions and recent(market["captured_at"], max_age=RESEARCH_MAX_AGE_SECONDS):
            snapshot = {
                "version": str(run_id), "as_of": market["captured_at"], "slot": slot,
                "inputs": inputs, "conclusions": conclusions,
            }
            await redis.set(key, json.dumps(snapshot), ex=RESEARCH_MAX_AGE_SECONDS)


async def run_forever(factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    symbols = research_symbols(settings)
    while True:
        outcomes = await asyncio.gather(
            *(_refresh(factory, settings, symbol) for symbol in symbols), return_exceptions=True
        )
        for symbol, outcome in zip(symbols, outcomes, strict=True):
            if isinstance(outcome, Exception):
                logger.warning("Shared market research failed for %s: %s", symbol, type(outcome).__name__)
        await asyncio.sleep(10)
