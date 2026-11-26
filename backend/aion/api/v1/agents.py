"""Agent run inspection and analysis triggering."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from aion.agents.base import DbRunRecorder
from aion.api.deps import CurrentUser, DbSession, SettingsDep, scope_to_user
from aion.core.db import get_sessionmaker
from aion.core.errors import AppError, ErrorCode
from aion.models.agent import AgentRun, ModelCall
from aion.schemas.common import AionModel, ORMModel
from aion.services.agent_service import (
    AgentProviderUnavailable,
    build_orchestrator,
    resolve_provider_config,
    run_analysis,
)

logger = logging.getLogger(__name__)

#: Strong references to fire-and-forget analysis tasks (GC protection).
_background_tasks: set[asyncio.Task[None]] = set()

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRunOut(ORMModel):
    id: uuid.UUID
    symbol: str
    interval: str
    trigger: str
    status: str
    mode: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    duration_ms: int | None = None
    model_name: str | None = None
    provider: str | None = None
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal
    verdict: str | None = None
    confidence: Decimal | None = None
    summary: str | None = None
    data_quality: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    intent_id: uuid.UUID | None = None
    created_at: dt.datetime


class AgentMessageOut(ORMModel):
    id: uuid.UUID
    seq: int
    agent_name: str
    role: str
    status: str
    summary: str | None = None
    structured: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    confidence: Decimal | None = None
    input_refs: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    created_at: dt.datetime


class AgentRunDetail(AgentRunOut):
    messages: list[AgentMessageOut] = Field(default_factory=list)


class AgentRunList(AionModel):
    items: list[AgentRunOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class AgentRosterItem(AionModel):
    name: str
    title: str
    role: str
    purpose: str
    stage: str
    order: int
    veto: bool = False


AGENT_ROSTER: tuple[AgentRosterItem, ...] = (
    AgentRosterItem(
        name="market_regime",
        title="市场状态分析师",
        role="市场状态",
        purpose="判断趋势 / 震荡 / 高波动，决定其他 AI 该用哪套打法。",
        stage="感知",
        order=1,
    ),
    AgentRosterItem(
        name="technical_analysis",
        title="技术面分析师",
        role="技术面",
        purpose="读K线、均线、量价与关键支撑阻力，给出入场位置。",
        stage="感知",
        order=2,
    ),
    AgentRosterItem(
        name="derivatives",
        title="衍生品数据分析师",
        role="资金面",
        purpose="解读资金费率、持仓量与多空比，识别拥挤和逼空风险。",
        stage="感知",
        order=3,
    ),
    AgentRosterItem(
        name="strategy_research",
        title="策略研究员",
        role="策略",
        purpose="结合历史经验与当前行情，提出可执行的策略与参数。",
        stage="研判",
        order=4,
    ),
    AgentRosterItem(
        name="chief_decision",
        title="首席决策官",
        role="决策",
        purpose="汇总前四个 AI，输出做多/做空/观望、仓位、止损与止盈。",
        stage="决策",
        order=5,
    ),
    AgentRosterItem(
        name="adversarial_review",
        title="反方风控官",
        role="风控",
        purpose="专门挑刺，拥有一票否决权，防止追高、扛单与过度交易。",
        stage="风控",
        order=6,
        veto=True,
    ),
)


@router.get("/roster", response_model=list[AgentRosterItem])
async def roster(_user: CurrentUser) -> list[AgentRosterItem]:
    """The six cooperating agents, in execution order."""
    return list(AGENT_ROSTER)


class ModelCallOut(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID | None = None
    provider: str
    model: str
    purpose: str
    status: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal
    latency_ms: int | None = None
    attempts: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: dt.datetime


class ModelCallList(AionModel):
    items: list[ModelCallOut] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class AnalyzeRequest(AionModel):
    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9]{3,32}$")
    interval: str = Field(default="15m", pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|3d|1w)$")


@router.get("/runs", response_model=AgentRunList)
async def list_runs(
    db: DbSession,
    user: CurrentUser,
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentRunList:
    stmt = select(AgentRun)
    count_stmt = select(func.count()).select_from(AgentRun)
    if symbol:
        stmt = stmt.where(AgentRun.symbol == symbol.upper())
        count_stmt = count_stmt.where(AgentRun.symbol == symbol.upper())
    if status_filter:
        stmt = stmt.where(AgentRun.status == status_filter)
        count_stmt = count_stmt.where(AgentRun.status == status_filter)
    stmt = scope_to_user(stmt, AgentRun.user_id, user)
    count_stmt = scope_to_user(count_stmt, AgentRun.user_id, user)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return AgentRunList(
        items=[AgentRunOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
async def get_run(
    run_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
) -> AgentRunDetail:
    stmt = scope_to_user(
        select(AgentRun).options(selectinload(AgentRun.messages)).where(AgentRun.id == run_id),
        AgentRun.user_id,
        user,
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "Agent run not found.")

    return AgentRunDetail.model_validate(run)


@router.get("/model-calls", response_model=ModelCallList)
async def list_model_calls(
    db: DbSession,
    user: CurrentUser,
    run_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ModelCallList:
    stmt = select(ModelCall)
    count_stmt = select(func.count()).select_from(ModelCall)
    if run_id:
        stmt = stmt.where(ModelCall.run_id == run_id)
        count_stmt = count_stmt.where(ModelCall.run_id == run_id)
    if not user.is_admin:
        # Model calls are attributed through the run they belong to.
        own_runs = select(AgentRun.id).where(AgentRun.user_id == user.id)
        stmt = stmt.where(ModelCall.run_id.in_(own_runs))
        count_stmt = count_stmt.where(ModelCall.run_id.in_(own_runs))

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(ModelCall.created_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return ModelCallList(
        items=[ModelCallOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/analyze", status_code=202)
async def analyze(
    payload: AnalyzeRequest,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Start one orchestrated analysis cycle in the background.

    Returns a run id immediately; poll ``GET /agents/runs/{id}`` for the result.
    No order is placed by this endpoint — it only produces a proposal.
    """
    # Administrators may use the server's env-configured provider; everyone
    # else must have been assigned a provider by an administrator.
    provider_config = await resolve_provider_config(db, user.id)
    if provider_config is None and not user.is_admin:
        raise AppError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "管理员尚未为您分配大模型 API，请联系管理员。",
            details={"configured_providers": []},
            http_status=503,
        )

    try:
        build_orchestrator(settings, get_sessionmaker(), provider_config)
    except AgentProviderUnavailable as exc:
        raise AppError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            str(exc),
            details={"configured_providers": []},
            http_status=503,
        ) from exc

    symbol = payload.symbol.upper()
    run_id = uuid.uuid4()
    mode = settings.resolve_trading_mode().value
    recorder = DbRunRecorder(get_sessionmaker())
    await recorder.ensure_run(
        run_id=run_id,
        symbol=symbol,
        interval=payload.interval,
        trigger="manual",
        mode=mode,
        user_id=user.id,
    )

    async def _task() -> None:
        started = dt.datetime.now(dt.UTC)
        try:
            await run_analysis(
                settings=settings,
                session_factory=get_sessionmaker(),
                symbol=symbol,
                interval=payload.interval,
                run_id=run_id,
                timeout_seconds=settings.agent_run_timeout_seconds,
                provider_config=provider_config,
                user_id=user.id,
                mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never silently swallowed
            logger.exception("agent run %s failed", run_id)
            try:
                duration_ms = int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)
                await recorder.finish_run(
                    run_id=run_id,
                    verdict="ERROR",
                    confidence=None,
                    summary="分析任务失败，未产生交易建议。",
                    duration_ms=duration_ms,
                    error=str(exc)[:500],
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to record terminal state for run %s", run_id)

    # Keep a reference: fire-and-forget tasks can be garbage-collected
    # mid-run by CPython when nothing holds them.
    _background_tasks.add(asyncio.create_task(_task()))

    return {
        "run_id": str(run_id),
        "status": "STARTED",
        "symbol": symbol,
        "interval": payload.interval,
        "note": "分析在后台运行，结果通过 GET /api/v1/agents/runs/{run_id} 获取；本接口不会下单。",
    }
