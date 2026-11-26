"""Factor research endpoints: run the six researchers, read the library."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aion.api.deps import CurrentUser, DbSession, SettingsDep
from aion.core.db import get_sessionmaker
from aion.core.errors import AppError, ErrorCode
from aion.models.factors import FactorRecord
from aion.models.market import MarketCandle
from aion.models.research import (
    FactorLifecycleEvent,
    FactorResearchTask,
    FactorValidationResult,
    FactorVersion,
    ResearchTaskLog,
)
from aion.schemas.common import AionModel
from aion.services import factor_service
from aion.services.agent_service import (
    AgentProviderUnavailable,
    build_gateway,
    resolve_provider_config,
)

router = APIRouter(prefix="/factors", tags=["factors"])


class FactorResearchRequest(AionModel):
    symbol: str = Field(min_length=3, max_length=32)
    interval: str = Field(min_length=1, max_length=8)
    horizon_bars: int = Field(default=8, ge=1, le=100)


class FactorEvaluationOut(AionModel):
    factor_name: str
    domain: str
    agent: str
    formula: str
    lookback_bars: int
    direction: str
    hypothesis: str
    ic_mean: float | None
    ic_ir: float | None
    hit_rate: float | None
    coverage: float
    accepted: bool
    notes: list[str]


class FactorResearchReportOut(AionModel):
    run_id: str
    symbol: str
    interval: str
    horizon_bars: int
    degraded: bool
    reasons: list[str]
    proposals: list[dict[str, Any]]
    evaluations: list[FactorEvaluationOut]
    accepted_count: int
    rejected_count: int
    duration_ms: int
    candles_used: int = 0


class FactorLibraryItem(AionModel):
    id: uuid.UUID
    symbol: str
    interval: str
    factor_name: str
    domain: str
    agent: str
    formula: str
    direction: str
    lookback_bars: int
    horizon_bars: int
    ic_mean: Decimal | None
    ic_ir: Decimal | None
    hit_rate: Decimal | None
    coverage: Decimal
    hypothesis: str
    status: str
    expression: list[Any] | None = None
    discovery_state: dict[str, Any] | None = None
    created_at: dt.datetime


class FactorLibraryList(AionModel):
    items: list[FactorLibraryItem] = Field(default_factory=list)


@router.post("/research", response_model=FactorResearchReportOut)
async def run_research(
    body: FactorResearchRequest,
    user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
):
    provider_config = await resolve_provider_config(db, user.id)
    if provider_config is None and not user.is_admin:
        raise AppError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "管理员尚未为您分配大模型 API，请联系管理员。",
            details={"configured_providers": []},
            http_status=503,
        )
    try:
        gateway = build_gateway(settings, get_sessionmaker(), provider_config)
    except AgentProviderUnavailable as exc:
        raise AppError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            str(exc),
            details={"configured_providers": []},
            http_status=503,
        ) from exc
    report = await factor_service.run_factor_research(
        db=db,
        gateway=gateway,
        symbol=body.symbol.upper(),
        interval=body.interval,
        horizon_bars=body.horizon_bars,
        user_id=user.id,
        session_factory=get_sessionmaker,
    )
    if report is None:
        raise AppError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            "未配置模型供应商，无法运行因子研究。",
            details={"configured_providers": []},
            http_status=503,
        )
    return FactorResearchReportOut.model_validate(report)


@router.get("/library", response_model=FactorLibraryList)
async def list_library(
    user: CurrentUser,
    db: DbSession,
    symbol: str | None = Query(default=None),
):
    rows = await factor_service.list_factors(db, user.id, symbol)
    return FactorLibraryList(
        items=[FactorLibraryItem.model_validate(row) for row in rows]
    )


class FactorLifecycleReport(AionModel):
    checked: int
    changed: int
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/lifecycle/refresh", response_model=FactorLifecycleReport)
async def refresh_lifecycle(
    user: CurrentUser, db: DbSession, symbol: str | None = Query(default=None),
):
    """Revalidate recent factor performance; weak/reversed factors become WATCH."""
    result = await factor_service.refresh_factor_lifecycle(db, user.id, symbol)
    return FactorLifecycleReport.model_validate(result)


class FactorIntelligenceOut(AionModel):
    symbol: str
    interval: str
    candles_used: int
    last_candle_at: dt.datetime | None = None
    market_regime: dict[str, Any]
    factor_signals: dict[str, Any]
    strategy_evolution: dict[str, Any]


@router.get("/intelligence", response_model=FactorIntelligenceOut)
async def factor_intelligence(
    user: CurrentUser,
    db: DbSession,
    symbol: str = Query(min_length=3, max_length=32),
    interval: str = Query(default="15m", min_length=1, max_length=8),
):
    """Read-only live factor intelligence for the mobile/web clients.

    Uses finalized candles and the authenticated user's ACTIVE factors only.
    It computes evidence for display; it never creates orders or changes risk.
    """
    from aion.quant.factor_ensemble import detect_market_regime
    from aion.quant.factor_signals import build_factor_signals
    from aion.quant.strategy_evolution import build_strategy_evolution

    target = symbol.upper()
    candles = list(reversed((await db.execute(
        select(MarketCandle)
        .where(
            MarketCandle.symbol == target,
            MarketCandle.interval == interval,
            MarketCandle.is_final.is_(True),
        )
        .order_by(MarketCandle.open_time.desc())
        .limit(300)
    )).scalars().all()))
    records = (await db.execute(
        select(FactorRecord)
        .where(
            FactorRecord.user_id == user.id,
            FactorRecord.symbol == target,
            FactorRecord.interval == interval,
            FactorRecord.status == "ACTIVE",
        )
        .order_by(FactorRecord.created_at.desc())
        .limit(100)
    )).scalars().all()
    regime = detect_market_regime(candles)
    signals = build_factor_signals(records, candles) if candles else {
        "available": False, "market_regime": regime, "factor_count": 0,
        "composite_signal": None, "bias": "NO_VALIDATED_FACTORS", "factors": [],
        "policy": "无已收盘 K 线或 ACTIVE 因子；不得虚构因子信号。",
    }
    from aion.services.learning import build_learning_context
    learning = await build_learning_context(db, user.id, target)
    evolution = build_strategy_evolution(signals, regime, learning)
    return FactorIntelligenceOut(
        symbol=target,
        interval=interval,
        candles_used=len(candles),
        last_candle_at=candles[-1].open_time if candles else None,
        market_regime=regime,
        factor_signals=signals,
        strategy_evolution=evolution,
    )


__all__ = ["router", "FactorRecord", "FactorIntelligenceOut"]


class FactorResearchTaskCreate(AionModel):
    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]{3,32}$")
    interval: str = Field(default="15m", min_length=1, max_length=8)
    horizon_bars: int = Field(default=8, ge=1, le=100)


class FactorResearchTaskOut(AionModel):
    id: uuid.UUID
    symbol: str
    interval: str
    horizon_bars: int
    status: str
    attempts: int
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


class ResearchTaskLogOut(AionModel):
    id: uuid.UUID
    level: str
    event: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime


@router.post("/research/tasks", response_model=FactorResearchTaskOut, status_code=202)
async def enqueue_factor_research(
    body: FactorResearchTaskCreate,
    user: CurrentUser,
    db: DbSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Queue a durable research task; it never submits or modifies exchange orders."""
    key = (idempotency_key or str(uuid.uuid4())).strip()
    if not key or len(key) > 128:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Idempotency-Key must be 1-128 characters.")
    existing = await db.scalar(select(FactorResearchTask).where(
        FactorResearchTask.user_id == user.id,
        FactorResearchTask.idempotency_key == key,
    ))
    if existing is not None:
        return FactorResearchTaskOut.model_validate(existing)
    row = FactorResearchTask(
        user_id=user.id, symbol=body.symbol.upper(), interval=body.interval,
        horizon_bars=body.horizon_bars, idempotency_key=key,
        status="QUEUED", attempts=0, result={},
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # Two identical requests can race between the initial lookup and insert.
        # Resolve the unique-key winner instead of returning a 500.
        await db.rollback()
        existing = await db.scalar(select(FactorResearchTask).where(
            FactorResearchTask.user_id == user.id,
            FactorResearchTask.idempotency_key == key,
        ))
        if existing is None:
            raise
        return FactorResearchTaskOut.model_validate(existing)
    await db.refresh(row)
    return FactorResearchTaskOut.model_validate(row)


@router.get("/research/tasks", response_model=list[FactorResearchTaskOut])
async def list_factor_research_tasks(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = (await db.execute(
        select(FactorResearchTask).where(FactorResearchTask.user_id == user.id)
        .order_by(FactorResearchTask.created_at.desc()).limit(limit)
    )).scalars().all()
    summaries = []
    for row in rows:
        item = FactorResearchTaskOut.model_validate(row)
        result = dict(item.result)
        if dataset := result.get("dataset"):
            result["dataset"] = {key: value for key, value in dataset.items() if key != "bars"}
        if peers := result.get("peer_datasets"):
            result["peer_datasets"] = {
                symbol: {key: value for key, value in data.items() if key != "bars"}
                for symbol, data in peers.items()
            }
        if evidence := result.get("discovery_library"):
            result["discovery_library"] = [
                {key: event[key] for key in ("factor_id", "status", "previous_status", "verdict")}
                for event in evidence
            ]
        if discovery := result.get("discovery"):
            candidates = discovery["candidates"]
            result["discovery"] = {
                **{key: value for key, value in discovery.items() if key != "candidates"},
                "candidate_count": len(candidates),
                "watch_count": sum(event["status"] == "WATCH" for event in (evidence or [])),
            }
        summaries.append(item.model_copy(update={"result": result}))
    return summaries


@router.get("/research/tasks/{task_id}", response_model=FactorResearchTaskOut)
async def get_factor_research_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession):
    row = await db.scalar(select(FactorResearchTask).where(
        FactorResearchTask.id == task_id, FactorResearchTask.user_id == user.id
    ))
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "Research task not found.")
    return FactorResearchTaskOut.model_validate(row)


@router.post("/research/tasks/{task_id}/replay")
async def replay_discovery(task_id: uuid.UUID, user: CurrentUser, db: DbSession):
    """Recompute the archived discovery without changing library state."""
    from aion.quant.factor_discovery import VERSION, discover
    from aion.services.discovery_library import replay_evidence

    row = await db.scalar(select(FactorResearchTask).where(
        FactorResearchTask.id == task_id, FactorResearchTask.user_id == user.id,
    ))
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "Research task not found.")
    stored = row.result.get("discovery")
    dataset = row.result.get("dataset")
    if not stored or not dataset or stored.get("algorithm_version") != VERSION:
        raise AppError(ErrorCode.CONFLICT, "Archived run needs its original evaluator version.")
    peers = row.result.get("peer_datasets") or {}
    evidence = [item for item in row.result.get("discovery_library", []) if item.get("validation_request")]
    recomputed = await asyncio.to_thread(discover, dataset, row.horizon_bars)
    matched_evidence = await asyncio.to_thread(lambda: sum(replay_evidence(dataset, peers, item) for item in evidence))
    return {"dataset_id": dataset["dataset_id"], "match": recomputed == stored and matched_evidence == len(evidence),
            "algorithm_version": VERSION, "validation_runs": len(evidence), "matched_validation_runs": matched_evidence}


@router.get("/research/tasks/{task_id}/logs", response_model=list[ResearchTaskLogOut])
async def get_factor_research_task_logs(task_id: uuid.UUID, user: CurrentUser, db: DbSession):
    owned = await db.scalar(select(FactorResearchTask.id).where(
        FactorResearchTask.id == task_id, FactorResearchTask.user_id == user.id
    ))
    if owned is None:
        raise AppError(ErrorCode.NOT_FOUND, "Research task not found.")
    rows = (await db.execute(
        select(ResearchTaskLog).where(
            ResearchTaskLog.task_id == task_id, ResearchTaskLog.user_id == user.id
        ).order_by(ResearchTaskLog.created_at.asc()).limit(500)
    )).scalars().all()
    return [ResearchTaskLogOut.model_validate(row) for row in rows]


class FactorValidationOut(AionModel):
    id: uuid.UUID
    factor_id: uuid.UUID
    metrics: dict[str, Any]
    passed: bool
    sample_definition: dict[str, Any]
    warnings: list[Any]
    created_at: dt.datetime


class FactorHistoryOut(AionModel):
    versions: list[dict[str, Any]] = Field(default_factory=list)
    lifecycle_events: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/library/{factor_id}/validations", response_model=list[FactorValidationOut])
async def factor_validation_history(factor_id: uuid.UUID, user: CurrentUser, db: DbSession):
    owned = await db.scalar(select(FactorRecord.id).where(
        FactorRecord.id == factor_id, FactorRecord.user_id == user.id
    ))
    if owned is None:
        raise AppError(ErrorCode.NOT_FOUND, "Factor not found.")
    rows = (await db.execute(
        select(FactorValidationResult).where(
            FactorValidationResult.factor_id == factor_id,
            FactorValidationResult.user_id == user.id,
        ).order_by(FactorValidationResult.created_at.desc()).limit(100)
    )).scalars().all()
    return [FactorValidationOut.model_validate(row) for row in rows]


@router.get("/library/{factor_id}/history", response_model=FactorHistoryOut)
async def factor_history(factor_id: uuid.UUID, user: CurrentUser, db: DbSession):
    owned = await db.scalar(select(FactorRecord.id).where(
        FactorRecord.id == factor_id, FactorRecord.user_id == user.id
    ))
    if owned is None:
        raise AppError(ErrorCode.NOT_FOUND, "Factor not found.")
    versions = (await db.execute(
        select(FactorVersion).where(
            FactorVersion.factor_id == factor_id, FactorVersion.user_id == user.id
        ).order_by(FactorVersion.version.desc()).limit(100)
    )).scalars().all()
    events = (await db.execute(
        select(FactorLifecycleEvent).where(
            FactorLifecycleEvent.factor_id == factor_id, FactorLifecycleEvent.user_id == user.id
        ).order_by(FactorLifecycleEvent.created_at.desc()).limit(200)
    )).scalars().all()
    return FactorHistoryOut(
        versions=[{"version": v.version, "snapshot": v.snapshot, "reason": v.reason, "created_at": v.created_at} for v in versions],
        lifecycle_events=[{"from_status": e.from_status, "to_status": e.to_status, "reason": e.reason, "created_at": e.created_at} for e in events],
    )
