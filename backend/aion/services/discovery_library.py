"""Persist discovered expressions and advance them on non-overlapping evidence."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.models.factors import FactorRecord
from aion.models.research import (
    FactorLifecycleEvent,
    FactorResearchTask,
    FactorValidationResult,
    FactorVersion,
)
from aion.quant.datasets import INTERVAL_SECONDS, restore_candles
from aion.quant.factor_discovery import HOLDOUT_COUNT, parse_expression
from aion.quant.factor_signals import normalize_factor_value
from aion.quant.factor_validation import (
    MIN_LABELS,
    aligned_observations,
    portfolio_contribution,
    validate_window,
)
from aion.quant.factor_values import DSL_FORMULA, factor_series
from aion.quant.factors import FactorFormula, compute_series

OBSERVABLE = {"VALIDATING", "WATCH", "ACTIVE", "DEGRADED"}


def next_status(previous: str, passed: bool, consecutive_passes: int, consecutive_failures: int) -> str:
    if passed:
        return "ACTIVE" if previous == "ACTIVE" or consecutive_passes >= 2 else "WATCH"
    if consecutive_failures >= 3:
        return "RETIRED"
    if previous in {"ACTIVE", "DEGRADED"}:
        return "DEGRADED"
    return "VALIDATING" if previous == "VALIDATING" else "WATCH"


def _expression_hash(expression: list, horizon: int, direction: int) -> str:
    encoded = json.dumps([parse_expression(expression).encode(), horizon, direction], separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _portfolio(dataset: dict, rows: list[dict], records: list, horizon: int, direction: int) -> dict:
    # Eligibility is frozen at the first evaluated signal; later activations
    # cannot be used to improve a historical benchmark retroactively.
    start = dt.datetime.fromisoformat(rows[0]["time"])
    eligible = [
        record for record in records if record.created_at <= start and record.horizon_bars == horizon
        and record.status == "ACTIVE"
    ]
    candles = restore_candles(dataset)
    weights = []
    for record in eligible:
        values = factor_series(record, candles)
        sign = -1 if record.direction == "NEGATIVE" else 1
        weights.append([None if value is None else sign * normalize_factor_value(record.formula, float(value))
                        for value in values])
    combined = (
        [sum(valid) / len(valid) if (valid := [x for x in column if x is not None]) else None
         for column in zip(*weights, strict=True)]
        if weights else compute_series(FactorFormula.MOMENTUM, candles, 16)
    )
    target_times = {row["time"] for row in rows}
    bars = dataset["bars"]
    indices = [index for index, bar in enumerate(bars[:-horizon]) if bar["end"] in target_times]
    baseline = aligned_observations(bars, combined, indices, horizon, dataset.get("funding", ()))
    return {**portfolio_contribution(rows, baseline, direction),
            "baseline_factors": [str(record.id) for record in eligible],
            "baseline_definitions": [
                {"id": str(record.id), "created_at": record.created_at.isoformat(), "formula": record.formula,
                 "direction": record.direction, "lookback_bars": record.lookback_bars, "horizon_bars": record.horizon_bars,
                 "status": record.status, "expression": record.expression, "discovery_state": record.discovery_state}
                for record in eligible
            ],
            "baseline_kind": "eligible_library" if eligible else "fixed_momentum_16"}


def _evaluate(dataset, peers, expression, direction, horizon, start_after, end_at, baseline,
              family_size=HOLDOUT_COUNT):
    primary, rows = validate_window(
        dataset, expression, direction, horizon, start_after=start_after, end_at=end_at,
        family_size=family_size, label_limit=None if end_at else max(MIN_LABELS, 20 * family_size),
    )
    if primary["status"] != "SCORED":
        return None
    replication = {}
    for symbol, peer in peers.items():
        replication[symbol] = validate_window(
            peer, expression, direction, horizon, start_after=start_after,
            end_at=primary["last_label_at"], family_size=family_size,
        )[0]
    portfolio = _portfolio(dataset, rows, baseline, horizon, direction)
    peer_scored = any(row["status"] == "SCORED" for row in replication.values())
    peer_passed = any(row.get("passed", False) for row in replication.values())
    if primary["passed"] and not peer_scored:
        verdict = "AWAITING_REPLICATION"
    elif primary["passed"] and peer_passed:
        verdict = "PASSED"
    else:
        verdict = "FAILED"
    return {
        "primary": primary, "assets": replication, "portfolio": portfolio, "verdict": verdict,
        "validation_request": {
            "expression": expression, "direction": direction, "horizon": horizon, "start_after": start_after,
            "end_at": end_at, "family_size": family_size,
        },
    }


def replay_evidence(dataset: dict, peers: dict, expected: dict) -> bool:
    request = expected.get("validation_request")
    if request is None:
        return False
    baseline = [
        SimpleNamespace(**{**item, "created_at": dt.datetime.fromisoformat(item["created_at"])})
        for item in expected["portfolio"]["baseline_definitions"]
    ]
    recovered = _evaluate(dataset, peers, baseline=baseline, **request)
    return recovered is not None and all(expected[key] == value for key, value in recovered.items())


def _transition(db, record, status, reason):
    if record.status != status:
        db.add(FactorLifecycleEvent(user_id=record.user_id, factor_id=record.id,
                                    from_status=record.status, to_status=status, reason=reason))
        record.status = status


def _record_evidence(db, record, task, dataset, peer_datasets, observation):
    state = dict(record.discovery_state or {})
    primary = observation["primary"]
    passed = observation["verdict"] == "PASSED"
    valid = observation["verdict"] != "AWAITING_REPLICATION"
    previous = record.status
    passes = state.get("passes", 0) + 1 if passed else 0
    failures = state.get("failures", 0) + 1 if valid and not passed else 0
    status = next_status(previous, passed, passes, failures) if valid else previous
    state.update({
        "last_label_at": primary["last_label_at"], "passes": passes, "failures": failures,
        "version": int(state.get("version", 0)) + 1, "latest_task_id": str(task.id),
    })
    if status == "ACTIVE":
        end = dt.datetime.fromisoformat(primary["last_label_at"])
        active_until = end + dt.timedelta(seconds=INTERVAL_SECONDS[record.interval] * record.horizon_bars * (MIN_LABELS + 2))
        state["active_since"] = (
            state["active_since"] if previous == "ACTIVE"
            else max(end, task.started_at).isoformat()
        )
        state["active_until"] = active_until.isoformat()
    record.discovery_state = state
    record.ic_mean = Decimal(str(primary["rank_ic"])) if primary["rank_ic"] is not None else None
    icir = primary["icir"] if primary["icir"] is not None else 1.0
    record.ic_ir = Decimal(str(max(-999, min(999, icir))))
    record.hit_rate = Decimal(str(primary["hit_rate"]))
    record.coverage = Decimal(str(primary["feasible_ratio"]))
    record.run_id_text = str(task.id)
    _transition(db, record, status, observation["verdict"])
    db.add(FactorValidationResult(
        user_id=record.user_id, factor_id=record.id, passed=passed, metrics=observation,
        sample_definition={
            "task_id": str(task.id), "dataset_id": dataset["dataset_id"],
            "peer_dataset_ids": {key: value["dataset_id"] for key, value in peer_datasets.items()},
            "first_signal_at": primary["first_signal_at"], "last_label_at": primary["last_label_at"],
        }, warnings=primary["reasons"],
    ))
    db.add(FactorVersion(
        user_id=record.user_id, factor_id=record.id, version=state["version"],
        snapshot={"expression": record.expression, "status": status, "state": state,
                  "dataset_id": dataset["dataset_id"], "horizon_bars": record.horizon_bars,
                  "direction": record.direction},
        reason=observation["verdict"],
    ))
    return {"factor_id": str(record.id), "factor_name": record.factor_name,
            "status": status, "previous_status": previous, **observation}


async def persist_discovery(db: AsyncSession, task: FactorResearchTask, dataset: dict,
                            peers: dict, discovery: dict) -> dict[str, Any]:
    """Write the audit trail and state changes inside the caller's transaction."""
    scope = f"factor-discovery:{task.user_id}:{task.symbol}:{task.interval}:{task.horizon_bars}"
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(scope, 0))))
    watermark = await db.scalar(select(
        func.max(FactorResearchTask.result["discovery_watermark"].astext)
    ).where(
        FactorResearchTask.id != task.id, FactorResearchTask.user_id == task.user_id,
        FactorResearchTask.symbol == task.symbol, FactorResearchTask.interval == task.interval,
        FactorResearchTask.horizon_bars == task.horizon_bars,
    ))
    owned = list((await db.scalars(
        select(FactorRecord).where(FactorRecord.user_id == task.user_id, FactorRecord.symbol == task.symbol,
                                   FactorRecord.interval == task.interval)
        .order_by(FactorRecord.factor_name).with_for_update()
    )).all())
    by_name = {row.factor_name: row for row in owned}
    baseline = [row for row in owned if row.status == "ACTIVE"]
    changes = []
    monitored = [record for record in owned if record.formula == DSL_FORMULA
                 and record.status in OBSERVABLE and record.horizon_bars == task.horizon_bars]
    for record in monitored:
        state = record.discovery_state or {}
        until = state.get("last_label_at")
        if not until or state.get("latest_task_id") == str(task.id):
            continue
        evidence = await asyncio.to_thread(
            _evaluate, dataset, peers, record.expression,
            -1 if record.direction == "NEGATIVE" else 1,
            record.horizon_bars, until, None, [row for row in baseline if row.id != record.id],
            max(HOLDOUT_COUNT, len(monitored)),
        )
        if evidence:
            changes.append(_record_evidence(db, record, task, dataset, peers, evidence))
        elif record.status == "ACTIVE" and state["active_until"] < dataset["as_of"]:
            _transition(db, record, "DEGRADED", "VALIDATION_WINDOW_EXPIRED")
            state = {**state, "version": state["version"] + 1, "passes": 0, "latest_task_id": str(task.id)}
            record.discovery_state = state
            db.add(FactorVersion(
                user_id=record.user_id, factor_id=record.id, version=state["version"],
                snapshot={"expression": record.expression, "status": record.status, "state": state},
                reason="VALIDATION_WINDOW_EXPIRED",
            ))
            changes.append({"factor_id": str(record.id), "factor_name": record.factor_name,
                            "status": "DEGRADED", "previous_status": "ACTIVE",
                            "verdict": "VALIDATION_WINDOW_EXPIRED"})

    split = discovery["holdout_start_index"]
    if watermark and (not split or dataset["bars"][split]["end"] <= watermark):
        return {"changes": changes, "discovery_watermark": watermark, "adoption": "HOLDOUT_ALREADY_USED"}

    consumed = []
    for candidate in discovery["candidates"]:
        if "holdout" not in candidate or candidate["holdout"]["samples"] < MIN_LABELS:
            continue
        direction = candidate["direction"]
        expression = candidate["expression"]
        identity = _expression_hash(expression, task.horizon_bars, direction)
        # Full SHA-256 encoded as 64 hex characters fits the existing name column.
        name = identity
        if name in by_name:
            continue
        record = FactorRecord(
            user_id=task.user_id, symbol=task.symbol, interval=task.interval, factor_name=name,
            formula=DSL_FORMULA, expression=expression, domain="discovery", agent="deterministic_search",
            direction="POSITIVE" if direction > 0 else "NEGATIVE", lookback_bars=64,
            horizon_bars=task.horizon_bars, status="CANDIDATE", coverage=Decimal(0),
            hypothesis="Bounded expression selected using training data.",
        )
        db.add(record)
        await db.flush()
        db.add(FactorLifecycleEvent(user_id=task.user_id, factor_id=record.id, from_status=None,
                                    to_status="CANDIDATE", reason="TRAIN_SELECTED"))
        _transition(db, record, "VALIDATING", "INDEPENDENT_HOLDOUT_STARTED")
        start_after = dataset["bars"][split - 1]["end"]
        evidence = await asyncio.to_thread(
            _evaluate, dataset, peers, expression, direction, task.horizon_bars, start_after,
            candidate["holdout"]["last_label_at"], baseline,
        )
        if evidence:
            changes.append(_record_evidence(db, record, task, dataset, peers, evidence))
            consumed.append(evidence["primary"]["last_label_at"])
    return {"changes": changes, "discovery_watermark": max(consumed, default=watermark),
            "adoption": "VALIDATED" if consumed else "NO_NEW_EVIDENCE"}
