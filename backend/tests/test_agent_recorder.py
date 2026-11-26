"""Persist messages produced by parallel roles in a shared research run."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from aion.agents.base import DbRunRecorder
from aion.core.db import get_sessionmaker
from aion.models.agent import AgentMessage


@pytest.mark.asyncio(loop_scope="session")
async def test_parallel_recorders_allocate_unique_message_sequences(clean_db):
    factory = get_sessionmaker()
    run_id = uuid.uuid4()
    await DbRunRecorder(factory).ensure_run(
        run_id=run_id, symbol="BTCUSDT", interval="15m", trigger="market_research",
    )
    roles = ["market_regime", "technical_analysis", "derivatives"]

    await asyncio.gather(*(
        DbRunRecorder(factory).record_message(run_id=run_id, agent_name=role, output=None)
        for role in roles
    ))

    async with factory() as db:
        messages = (await db.scalars(
            select(AgentMessage).where(AgentMessage.run_id == run_id).order_by(AgentMessage.seq)
        )).all()
    assert [message.seq for message in messages] == [1, 2, 3]
    assert {message.agent_name for message in messages} == set(roles)
