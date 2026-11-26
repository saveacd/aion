"""Factor research service: DB candles in, deterministic factor library out."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal as D
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.agents.base import DbRunRecorder, NullRunRecorder
from aion.agents.factor_research import FactorResearchOrchestrator
from aion.models.factors import FactorRecord
from aion.models.market import MarketCandle, MarketFundingSettlement
from aion.models.research import FactorLifecycleEvent, FactorValidationResult, FactorVersion
from aion.quant.factor_lifecycle import assess_factor_lifecycle
from aion.quant.factor_values import DSL_FORMULA

#: Enough history for lookback*3 windows plus horizon labels.
CANDLE_LOAD_LIMIT = 1800


async def load_candles(
    db: AsyncSession,
    symbol: str,
    interval: str,
    limit: int = CANDLE_LOAD_LIMIT,
    *, as_of: dt.datetime | None = None,
) -> list[MarketCandle]:
    rows = (
        (
            await db.execute(
                select(MarketCandle)
                .where(
                    MarketCandle.symbol == symbol,
                    MarketCandle.interval == interval,
                    MarketCandle.is_final.is_(True),
                    MarketCandle.close_time <= (as_of or dt.datetime.now(dt.UTC)),
                )
                .order_by(MarketCandle.open_time.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


async def freeze_research_dataset(db: AsyncSession, symbol: str, interval: str, as_of: dt.datetime) -> dict:
    from aion.quant.datasets import freeze_candles

    candles = await load_candles(db, symbol, interval, as_of=as_of)
    funding = []
    if candles:
        funding = (await db.scalars(select(MarketFundingSettlement).where(
            MarketFundingSettlement.symbol == symbol, MarketFundingSettlement.settled_at <= as_of,
            MarketFundingSettlement.settled_at >= candles[0].open_time - dt.timedelta(hours=8),
        ).order_by(MarketFundingSettlement.settled_at))).all()
    return freeze_candles(candles, symbol=symbol, interval=interval, as_of=as_of, funding=funding)


async def run_factor_research(
    *,
    db: AsyncSession,
    gateway: Any,
    symbol: str,
    interval: str,
    horizon_bars: int,
    user_id: uuid.UUID | None = None,
    session_factory: Any | None = None,
    dataset: dict | None = None,
) -> dict[str, Any] | None:
    """One research cycle; returns the report dict or None when it can't start.

    ``gateway`` is built by the caller (which owns provider resolution). The
    request-scoped ``db`` session is used for candle loading and factor
    persistence, so nothing here touches a globally-bound engine.
    ``session_factory`` (optional) enables the DB run recorder so every
    research run leaves a persisted audit trail instead of NullRunRecorder
    (audit BUG-29).
    """
    if session_factory is not None:
        recorder: Any = DbRunRecorder(session_factory)
    else:
        recorder = NullRunRecorder()
    orchestrator = FactorResearchOrchestrator(gateway, recorder)

    candles = await load_candles(db, symbol, interval) if dataset is None else []
    payload = dataset["bars"] if dataset is not None else [
        {
            "t": c.open_time.isoformat(),
            "o": str(c.open),
            "h": str(c.high),
            "l": str(c.low),
            "c": str(c.close),
            "v": str(c.volume),
        }
        for c in candles
    ]

    report = await orchestrator.run(
        symbol,
        interval,
        payload,
        horizon_bars,
        user_id=user_id,
    )
    report_dict = report.to_dict()
    report_dict["candles_used"] = len(payload)
    await persist_factors(db, report_dict, user_id=user_id)
    return report_dict


async def persist_factors(
    db: AsyncSession,
    report: dict[str, Any],
    *,
    user_id: uuid.UUID | None = None,
) -> None:
    """Upsert accepted factors into the library (run_id stamped)."""
    if user_id is None:
        return
    accepted = [
        row for row in report.get("evaluations", []) if row.get("accepted")
    ]
    if not accepted:
        return
    for row in accepted:
        existing = await db.scalar(
            select(FactorRecord).where(
                FactorRecord.user_id == user_id,
                FactorRecord.symbol == report["symbol"],
                FactorRecord.interval == report["interval"],
                FactorRecord.factor_name == row["factor_name"],
            )
        )
        if existing is not None and existing.formula == DSL_FORMULA:
            continue
        record = existing or FactorRecord(
            user_id=user_id,
            symbol=report["symbol"],
            interval=report["interval"],
            factor_name=row["factor_name"],
        )
        previous_status = record.status if existing is not None else None
        definition_changed = existing is None or any((
            record.formula != row["formula"],
            record.direction != row["direction"],
            record.lookback_bars != row["lookback_bars"],
            record.horizon_bars != report["horizon_bars"],
        ))
        record.domain = row["domain"]
        record.agent = row["agent"]
        record.formula = row["formula"]
        record.direction = row["direction"]
        record.lookback_bars = row["lookback_bars"]
        record.horizon_bars = report["horizon_bars"]
        record.ic_mean = (
            D(str(round(row["ic_mean"], 6))) if row["ic_mean"] is not None else None
        )
        record.ic_ir = (
            D(str(round(row["ic_ir"], 6))) if row["ic_ir"] is not None else None
        )
        record.hit_rate = (
            D(str(round(row["hit_rate"], 4))) if row["hit_rate"] is not None else None
        )
        record.coverage = D(str(round(row["coverage"], 4)))
        record.hypothesis = row["hypothesis"]
        if definition_changed:
            record.status = "TESTING"
        record.run_id_text = str(report["run_id"])
        db.add(record)
        await db.flush()
        latest_version = await db.scalar(
            select(FactorVersion.version).where(FactorVersion.factor_id == record.id)
            .order_by(FactorVersion.version.desc()).limit(1)
        )
        db.add(FactorVersion(
            user_id=user_id, factor_id=record.id, version=int(latest_version or 0) + 1,
            snapshot={
                "factor_name": record.factor_name, "formula": record.formula,
                "direction": record.direction, "lookback_bars": record.lookback_bars,
                "horizon_bars": record.horizon_bars,
                "ic_mean": str(record.ic_mean) if record.ic_mean is not None else None,
                "ic_ir": str(record.ic_ir) if record.ic_ir is not None else None,
                "coverage": str(record.coverage), "run_id": record.run_id_text,
            }, reason="factor_definition_changed" if definition_changed else "research_refresh",
        ))
        if previous_status != record.status:
            db.add(FactorLifecycleEvent(
                user_id=user_id, factor_id=record.id, from_status=previous_status,
                to_status=record.status,
                reason=("new_or_changed_candidate_requires_holdout_validation"
                        if definition_changed else "research_refresh"),
            ))
    await db.commit()


async def refresh_factor_lifecycle(
    db: AsyncSession, user_id: uuid.UUID, symbol: str | None = None,
) -> dict[str, Any]:
    """Recheck ACTIVE/WATCH factors on recent candles and update eligibility.

    Only factors with enough out-of-sample observations can change status.
    WATCH factors are excluded from trading signals by the existing ACTIVE-only
    query and can be reactivated only after passing the same holdout gate.
    """
    stmt = select(FactorRecord).where(
        FactorRecord.user_id == user_id, FactorRecord.formula != DSL_FORMULA,
        FactorRecord.status.in_(["TESTING", "ACTIVE", "WATCH"])
    )
    if symbol:
        stmt = stmt.where(FactorRecord.symbol == symbol.upper())
    records = list((await db.execute(stmt.order_by(FactorRecord.created_at.desc()).limit(200))).scalars().all())
    decisions: list[dict[str, Any]] = []
    candle_cache: dict[tuple[str, str], list[MarketCandle]] = {}
    changed = 0
    for record in records:
        key = (record.symbol, record.interval)
        if key not in candle_cache:
            candle_cache[key] = await load_candles(db, record.symbol, record.interval)
        decision = assess_factor_lifecycle(record, candle_cache[key])
        validation_metrics: dict[str, Any] = {
            "holdout_ic": decision.holdout_ic,
            "holdout_samples": decision.samples,
            "lifecycle_reason": decision.reason,
            "rank_ic_definition": "Spearman rank correlation",
            "future_data_policy": "factor values use current and prior bars; labels use strictly future horizon",
        }
        try:
            from aion.quant.factor_lifecycle import should_retire_after_failures
            from aion.quant.factors import FactorFormula, evaluate_factor
            stats = evaluate_factor(
                candle_cache[key], FactorFormula(record.formula), int(record.lookback_bars),
                int(record.horizon_bars), direction=str(record.direction),
                min_samples=30, holdout_fraction=0.4,
            )
            validation_metrics.update({
                "rank_ic": stats.ic_full, "ic_mean": stats.ic_mean,
                "icir": stats.ic_ir, "segment_ics": stats.segment_ics,
                "hit_rate": stats.hit_rate, "coverage": stats.coverage,
                "sample_count": stats.samples,
                "out_of_sample_ic": stats.ic_holdout,
                "walk_forward_segments": stats.segment_ics,
            })
            validation_passed = bool(
                stats.accepted and decision.samples >= 40
                and decision.reason in {"validation_passed_to_watch", "holdout_revalidated"}
            )
            previous_results = (await db.execute(
                select(FactorValidationResult.passed).where(
                    FactorValidationResult.user_id == user_id,
                    FactorValidationResult.factor_id == record.id,
                ).order_by(FactorValidationResult.created_at.desc()).limit(2)
            )).scalars().all()
            if should_retire_after_failures(previous_results, validation_passed):
                decision = decision.__class__(
                    factor_name=decision.factor_name,
                    previous_status=decision.previous_status,
                    new_status="RETIRED",
                    holdout_ic=decision.holdout_ic,
                    samples=decision.samples,
                    reason="three_consecutive_validation_failures",
                )
            db.add(FactorValidationResult(
                user_id=user_id, factor_id=record.id, metrics=validation_metrics,
                passed=validation_passed,
                sample_definition={"symbol": record.symbol, "interval": record.interval,
                                   "horizon_bars": record.horizon_bars, "holdout_fraction": 0.4},
                warnings=[] if validation_passed else list(stats.notes),
            ))
        except (ValueError, TypeError, AttributeError) as exc:
            validation_metrics["validation_error"] = str(exc)[:300]
        if decision.new_status != decision.previous_status:
            record.status = decision.new_status
            db.add(FactorLifecycleEvent(
                user_id=user_id, factor_id=record.id,
                from_status=decision.previous_status, to_status=decision.new_status,
                reason=decision.reason,
            ))
            changed += 1
        decisions.append({
            "factor_name": decision.factor_name,
            "symbol": record.symbol,
            "interval": record.interval,
            "previous_status": decision.previous_status,
            "status": decision.new_status,
            "holdout_ic": decision.holdout_ic,
            "samples": decision.samples,
            "reason": decision.reason,
        })
    # Validation snapshots must persist even when lifecycle state is unchanged.
    await db.commit()
    return {"checked": len(decisions), "changed": changed, "items": decisions}


async def list_factors(
    db: AsyncSession,
    user_id: uuid.UUID,
    symbol: str | None = None,
    *,
    interval: str | None = None,
    active_only: bool = False,
) -> list[FactorRecord]:
    stmt = (
        select(FactorRecord)
        .where(FactorRecord.user_id == user_id)
        .order_by(FactorRecord.created_at.desc())
        .limit(200)
    )
    if symbol:
        stmt = stmt.where(FactorRecord.symbol == symbol)
    if interval:
        stmt = stmt.where(FactorRecord.interval == interval)
    if active_only:
        stmt = stmt.where(FactorRecord.status == "ACTIVE")
    return list((await db.execute(stmt)).scalars().all())


__all__ = [
    "CANDLE_LOAD_LIMIT",
    "list_factors",
    "refresh_factor_lifecycle",
    "load_candles",
    "persist_factors",
    "run_factor_research",
]
