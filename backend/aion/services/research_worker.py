"""Redis-coordinated durable worker for factor research tasks."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from bisect import bisect_right
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.core.redis import distributed_lock
from aion.models.research import FactorResearchTask, ResearchTaskLog

logger = logging.getLogger(__name__)


async def _log(db: AsyncSession, task: FactorResearchTask, event: str, message: str, level: str = "INFO", details=None) -> None:
    db.add(ResearchTaskLog(
        user_id=task.user_id, task_id=task.id, level=level, event=event,
        message=message, details=details or {},
    ))


async def process_one(session_factory: async_sessionmaker, settings) -> bool:
    """Claim and process at most one queued task. Returns whether work was found."""
    from aion.quant.datasets import restore_candles
    from aion.quant.factor_discovery import discover
    from aion.services.agent_service import (
        build_gateway,
        resolve_provider_config,
    )
    from aion.services.discovery_library import persist_discovery
    from aion.services.factor_service import freeze_research_dataset, run_factor_research
    from aion.services.market_research import research_symbols

    async with session_factory() as db:
        task = await db.scalar(
            select(FactorResearchTask).where(FactorResearchTask.status == "QUEUED")
            .order_by(FactorResearchTask.created_at.asc()).with_for_update(skip_locked=True).limit(1)
        )
        if task is None:
            return False
        # Claim is committed before external model calls; restart recovery can requeue stale work.
        task.status = "RUNNING"
        task.attempts += 1
        task.started_at = dt.datetime.now(dt.UTC)
        task.error = None
        dataset = task.result.get("dataset")
        peers = task.result.get("peer_datasets")
        if dataset is None and task.user_id is not None:
            dataset = await freeze_research_dataset(db, task.symbol, task.interval, task.started_at)
        if peers is None and dataset is not None:
            cutoff = dt.datetime.fromisoformat(dataset["as_of"])
            cohort = [symbol for symbol in research_symbols(settings) if symbol != task.symbol][:2]
            peers = {}
            for symbol in cohort:
                peers[symbol] = await freeze_research_dataset(db, symbol, task.interval, cutoff)
        task.result = {**task.result, "dataset": dataset, "peer_datasets": peers}
        await _log(db, task, "TASK_STARTED", "Factor research task claimed.")
        await db.commit()
        task_id, user_id = task.id, task.user_id
        symbol, interval, horizon = task.symbol, task.interval, task.horizon_bars

    if user_id is None:
        async with session_factory() as db:
            row = await db.get(FactorResearchTask, task_id)
            if row:
                row.status, row.error, row.finished_at = "FAILED", "task has no owner", dt.datetime.now(dt.UTC)
                await _log(db, row, "TASK_FAILED", row.error, "ERROR")
                await db.commit()
        return True

    try:
        if dataset is None:
            raise RuntimeError("research task did not capture a dataset")
        discovery = await asyncio.to_thread(discover, dataset, horizon)
        peers = peers or {}
        report = {"dataset": dataset, "peer_datasets": peers, "discovery": discovery,
                  "candles_used": len(dataset["bars"])}
        async with session_factory() as db:
            task_row = await db.get(FactorResearchTask, task_id, with_for_update=True)
            if task_row is not None:
                if "discovery_adoption" in task_row.result:
                    report.update({key: task_row.result.get(key) for key in (
                        "discovery_adoption", "discovery_library", "discovery_watermark"
                    )})
                else:
                    persisted = await persist_discovery(db, task_row, dataset, peers, discovery)
                    report["discovery_library"] = persisted["changes"]
                    report["discovery_watermark"] = persisted["discovery_watermark"]
                    report["discovery_adoption"] = persisted["adoption"]
                task_row.result = report
                await db.commit()

            provider_config = await resolve_provider_config(db, user_id)
            if provider_config is not None and dataset["quality"]["eligible"]:
                gateway = build_gateway(settings, session_factory, provider_config)
                legacy = await asyncio.wait_for(
                    run_factor_research(
                        db=db, gateway=gateway, symbol=symbol, interval=interval,
                        horizon_bars=horizon, user_id=user_id, session_factory=session_factory,
                        dataset=dataset,
                    ),
                    timeout=max(60, int(os.environ.get("AION_RESEARCH_TASK_TIMEOUT_SECONDS", "900"))),
                )
                if legacy is not None:
                    report.update(legacy)
            # Deterministic OOS lifecycle check follows every successful mining run.
            from aion.quant.factor_signals import build_factor_signals
            from aion.quant.strategy_evolution import build_strategy_evolution
            from aion.services.factor_service import list_factors, refresh_factor_lifecycle
            if dataset["quality"]["eligible"]:
                await refresh_factor_lifecycle(db, user_id, symbol)
                await db.commit()
            factors = await list_factors(db, user_id, symbol, interval=interval, active_only=True)
            candles = restore_candles(dataset) if dataset["quality"]["eligible"] else []
            if factors and candles:
                intelligence = build_factor_signals(
                    [f for f in factors if f.status == "ACTIVE"], candles
                )
                from aion.models.research import FactorEnsembleVersion
                db.add(FactorEnsembleVersion(
                    user_id=user_id, symbol=symbol, interval=interval,
                    regime=intelligence.get("market_regime", {}).get("label", "UNKNOWN"),
                    weights={row["factor_name"]: row["weight"] for row in intelligence.get("factors", [])},
                    diagnostics={
                        "correlations": intelligence.get("correlations", []),
                        "signal_conflicts": intelligence.get("signal_conflicts", []),
                        "composite_signal": intelligence.get("composite_signal"),
                        "policy": "research snapshot only",
                    },
                ))
                evolution = build_strategy_evolution(
                    intelligence.get("factor_signals", intelligence),
                    intelligence.get("market_regime", {}),
                )
                from aion.models.research import StrategyBacktest, StrategyCandidate
                from aion.quant.strategy_research import evaluate_factor_strategy

                settlements = [
                    (dt.datetime.fromisoformat(item["t"]), Decimal(item["rate"]))
                    for item in dataset.get("funding", [])
                ]
                openings = [candle.open_time for candle in candles]
                funding_by_index = {}
                for ts, rate in settlements:
                    i = bisect_right(openings, ts) - 1
                    if 0 <= i < len(candles) and ts <= candles[i].close_time:
                        funding_by_index[i] = funding_by_index.get(i, Decimal(0)) + rate
                max_gap = dt.timedelta(hours=8, minutes=2)
                funding_complete = (
                    bool(funding_by_index) and settlements[0][0] <= candles[0].open_time
                    and candles[-1].close_time - settlements[-1][0] <= max_gap
                    and all(b[0] - a[0] <= max_gap for a, b in zip(settlements, settlements[1:], strict=False))
                )
                strategy_items = evolution.get("candidate_strategies", [])
                active_factors = [f for f in factors if f.status == "ACTIVE"]
                if candles and strategy_items:
                    # The candidate family is deliberately bounded to three jobs;
                    # each CPU backtest runs independently and cannot place orders.
                    evaluated = await asyncio.gather(*(asyncio.to_thread(
                        evaluate_factor_strategy, active_factors, candles,
                        funding_rate_by_index=funding_by_index,
                        funding_history_complete=funding_complete,
                        strategy_kind=item.get("strategy_kind", "trend_confirmation"),
                    ) for item in strategy_items))
                    strategy_evaluations = {
                        item.get("strategy_kind", "trend_confirmation"): result
                        for item, result in zip(strategy_items, evaluated, strict=True)
                    }
                else:
                    strategy_evaluations = {
                        item.get("strategy_kind", "trend_confirmation"): {
                            "status": "BLOCKED", "reason": "NO_CANDLES", "execution_enabled": False
                        } for item in strategy_items
                    }
                for item in strategy_items:
                    strategy_kind = item.get("strategy_kind", "trend_confirmation")
                    strategy_evaluation = strategy_evaluations[strategy_kind]
                    risk_assumptions = strategy_evaluation.get("assumptions", {})
                    candidate_evaluation = {
                        "status": strategy_evaluation.get("status", "BLOCKED"),
                        "backtest": strategy_evaluation,
                        "gate": (strategy_evaluation.get("evaluation") or {}).get("gate", "RESEARCH_ONLY"),
                        "execution_enabled": False,
                        "approval_required": True,
                    }
                    candidate_name = f"{item.get('name', 'research_candidate')}_{str(task_id)[:8]}"
                    existing_candidate = await db.scalar(select(StrategyCandidate.id).where(
                        StrategyCandidate.user_id == user_id,
                        StrategyCandidate.source_research_task_id == task_id,
                        StrategyCandidate.name == candidate_name,
                    ))
                    if existing_candidate is not None:
                        continue
                    candidate = StrategyCandidate(
                        user_id=user_id,
                        name=candidate_name,
                        status="PROPOSED",
                        source_research_task_id=task_id,
                        evaluation=candidate_evaluation,
                        specification={
                            **item,
                            "strategy_logic": "Causal factor-consensus strategy backtested on a chronological 70/30 split; candidate remains research-only until all gates and PAPER observation pass.",
                            "used_factors": [row.get("factor_name") for row in intelligence.get("factors", [])],
                            "entry_conditions": item.get("entry_confirmation", "RESEARCH_REQUIRED"),
                            "exit_conditions": "RESEARCH_REQUIRED: define deterministic exit and invalidation rules before backtest",
                            "stop_loss_rule": f"{risk_assumptions.get('stop_loss_pct', 'N/A')}% hard stop in the research/PAPER model",
                            "take_profit_rule": f"{risk_assumptions.get('take_profit_pct', 'N/A')}% take-profit in the research/PAPER model",
                            "position_management": f"1x maximum; {risk_assumptions.get('max_notional_fraction', 'N/A')} of equity notional; {risk_assumptions.get('max_volume_participation', 'N/A')} candle-volume participation cap; production execution remains behind the independent risk engine",
                            "applicable_market": intelligence.get("market_regime", {}).get("label", "UNKNOWN"),
                            "parameters": {},
                            "risk_notes": "Candidate only; not executable. Require walk-forward, fees/slippage, drawdown and PAPER evaluation.",
                            "research_run_id": report.get("run_id"),
                            "dataset_id": dataset["dataset_id"],
                            "symbol": symbol,
                            "interval": interval,
                            "market_regime": intelligence.get("market_regime", {}),
                            "factor_signal": intelligence,
                            "execution_enabled": False,
                            "approval_required": True,
                        },
                    )
                    db.add(candidate)
                    await db.flush()
                    # Passing candidates enter isolated PAPER observation only.
                    # This never creates or modifies exchange orders.
                    passed = bool((strategy_evaluation.get("evaluation") or {}).get("passed"))
                    if passed:
                        from aion.models.research import PaperStrategySession
                        existing_session = await db.scalar(select(PaperStrategySession.id).where(
                            PaperStrategySession.user_id == user_id,
                            PaperStrategySession.candidate_id == candidate.id,
                        ))
                        if existing_session is None:
                            db.add(PaperStrategySession(
                                user_id=user_id, candidate_id=candidate.id,
                                name=f"PAPER {candidate.name}"[:96], status="OBSERVING",
                                initial_cash=Decimal("10000"), current_cash=Decimal("10000"),
                                metrics={"realized_pnl": "0", "trades": 0,
                                         "execution_mode": "PAPER", "max_leverage": "1x",
                                         "engine": "AUTO_MARKET_PAPER_V1"},
                            ))
                            candidate.status = "PAPER"
                    assumptions = risk_assumptions
                    samples = [
                        ("in_sample", strategy_evaluation.get("in_sample")),
                        ("out_of_sample", strategy_evaluation.get("out_of_sample")),
                    ]
                    if strategy_evaluation.get("status") == "COMPLETED":
                        for sample_name, sample_metrics in samples:
                            db.add(StrategyBacktest(
                                user_id=user_id, candidate_id=candidate.id, symbol=symbol,
                                interval=interval, sample=sample_name, status="COMPLETED",
                                metrics=sample_metrics or {}, assumptions=assumptions,
                                finished_at=dt.datetime.now(dt.UTC),
                            ))
                    else:
                        db.add(StrategyBacktest(
                            user_id=user_id, candidate_id=candidate.id, symbol=symbol,
                            interval=interval, sample="combined", status="BLOCKED",
                            metrics={}, assumptions={"holdout_split": "chronological 70/30"},
                            error=str(strategy_evaluation.get("reason", "research backtest blocked"))[:1000],
                            finished_at=dt.datetime.now(dt.UTC),
                        ))
                task_row = await db.get(FactorResearchTask, task_id)
                if task_row is not None:
                    await _log(db, task_row, "STRATEGY_BACKTESTED",
                               "Distinct strategy candidates were evaluated with separate rules and risk profiles.",
                               details={"candidate_count": len(strategy_evaluations),
                                        "results": {kind: {"status": value.get("status"),
                                            "gate": (value.get("evaluation") or {}).get("gate", "RESEARCH_ONLY")}
                                            for kind, value in strategy_evaluations.items()},
                                        "funding_history_complete": funding_complete})
                await db.commit()
        async with session_factory() as db:
            row = await db.get(FactorResearchTask, task_id)
            if row:
                row.status = "SUCCEEDED"
                row.result = report
                row.finished_at = dt.datetime.now(dt.UTC)
                await _log(db, row, "TASK_SUCCEEDED", "Factor research completed.", details={
                    "accepted_count": report.get("accepted_count", 0),
                    "rejected_count": report.get("rejected_count", 0),
                })
                await db.commit()
    except Exception as exc:  # task failure is persisted and does not kill the worker
        logger.exception("factor research task failed task_id=%s", task_id)
        async with session_factory() as db:
            row = await db.get(FactorResearchTask, task_id)
            if row:
                row.status = "QUEUED" if row.attempts < 3 else "FAILED"
                row.error = f"{type(exc).__name__}: research task failed; check protected server logs"
                row.finished_at = dt.datetime.now(dt.UTC) if row.status == "FAILED" else None
                await _log(db, row, "TASK_RETRY" if row.status == "QUEUED" else "TASK_FAILED",
                           row.error, "WARNING" if row.status == "QUEUED" else "ERROR",
                           {"attempt": row.attempts})
                await db.commit()
    return True


async def schedule_daily_research(session_factory: async_sessionmaker, settings) -> int:
    """Create one budget-bounded daily task per configured user and default symbol."""
    from aion.models.auth import User
    from aion.models.factors import FactorRecord
    from aion.quant.factor_values import DSL_FORMULA
    from aion.services.discovery_library import OBSERVABLE

    today = dt.datetime.now(dt.UTC).date().isoformat()
    created = 0
    async with session_factory() as db:
        users = (await db.scalars(select(User.id).where(User.is_active.is_(True)))).all()
        default = {(user, symbol, "1h", 8) for user in users for symbol in settings.default_symbols[:1]}
        monitored = (await db.execute(select(
            FactorRecord.user_id, FactorRecord.symbol, FactorRecord.interval, FactorRecord.horizon_bars,
        ).where(FactorRecord.user_id.in_(users), FactorRecord.formula == DSL_FORMULA,
                FactorRecord.status.in_(OBSERVABLE)).distinct())).all()
        for user_id, symbol, interval, horizon in default.union(tuple(row) for row in monitored):
            key = f"auto:{today}:{symbol}:{interval}:{horizon}"
            legacy_key = f"auto:{today}:{symbol}:{interval}" if horizon == 8 else key
            existing = await db.scalar(select(FactorResearchTask.id).where(
                FactorResearchTask.user_id == user_id,
                FactorResearchTask.idempotency_key.in_((key, legacy_key)),
            ))
            if existing:
                continue
            db.add(FactorResearchTask(user_id=user_id, symbol=symbol, interval=interval,
                                      horizon_bars=horizon, idempotency_key=key,
                                      status="QUEUED", attempts=0, result={}))
            created += 1
        await db.commit()
    return created


async def run_forever(session_factory: async_sessionmaker, settings, poll_seconds: int = 5) -> None:
    """Single logical worker across replicas, protected by Redis distributed lock."""
    last_schedule = 0.0
    last_paper_cycle = 0.0
    last_recovery = 0.0
    while True:
        try:
            loop = asyncio.get_running_loop()
            now = loop.time()
            # Recover stale RUNNING rows periodically as well as at process startup.
            # This covers task crashes that do not terminate the API process.
            if now - last_recovery >= 60:
                recovered = await recover_interrupted_tasks(session_factory)
                if recovered:
                    logger.warning("requeued %s stale factor research task(s)", recovered)
                last_recovery = now
            if now - last_schedule >= 3600:
                if __import__("os").environ.get("AION_RESEARCH_AUTOSCHEDULE_ENABLED", "true").lower() not in ("false", "0", "off"):
                    async with distributed_lock("scheduler:daily-factor-research", ttl_seconds=120, wait_seconds=0) as schedule_lock:
                        if schedule_lock:
                            created = await schedule_daily_research(session_factory, settings)
                            if created:
                                logger.info("scheduled %s daily factor research task(s)", created)
                last_schedule = now
            async with distributed_lock("worker:factor-research", ttl_seconds=900, wait_seconds=0) as acquired:
                if acquired:
                    did_work = await process_one(session_factory, settings)
                    if did_work:
                        continue
            if now - last_paper_cycle >= 30:
                async with distributed_lock("worker:paper-observer", ttl_seconds=120, wait_seconds=0) as paper_lock:
                    if paper_lock:
                        from aion.services.paper_observer import process_paper_sessions
                        advanced = await process_paper_sessions(session_factory)
                        if advanced:
                            logger.info("advanced %s PAPER observation session(s)", advanced)
                last_paper_cycle = now
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep the worker alive, but never hide operational failures.
            logger.exception("research worker loop failed; retrying after poll interval")
        await asyncio.sleep(max(1, poll_seconds))


async def recover_interrupted_tasks(session_factory: async_sessionmaker) -> int:
    """Requeue RUNNING work left behind by process termination."""
    async with session_factory() as db:
        stale_before = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
        result = await db.execute(
            update(FactorResearchTask).where(
                FactorResearchTask.status == "RUNNING",
                (FactorResearchTask.started_at.is_(None)) | (FactorResearchTask.started_at < stale_before),
            ).values(
            status="QUEUED", error="recovered after stale worker lease", started_at=None,
            finished_at=None,
        )
        )
        await db.commit()
        return int(result.rowcount or 0)
