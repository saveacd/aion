"""Immutable, point-in-time OHLCV inputs for research runs."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal

from aion.quant.candles import Candle, detect_gaps, validate_candles

INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
    "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}


def freeze_candles(rows, *, symbol: str, interval: str, as_of: dt.datetime, funding=()) -> dict:
    bars = [
        {
            "t": row.open_time.astimezone(dt.UTC).isoformat(), "end": row.close_time.astimezone(dt.UTC).isoformat(), "source": row.source,
            **{key: str(getattr(row, field)) for key, field in (
                ("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")
            )},
        }
        for row in rows if row.is_final and row.close_time <= as_of
    ]
    period = INTERVAL_SECONDS.get(interval)
    manifest = {"schema_version": 1, "symbol": symbol, "interval": interval,
                "as_of": as_of.astimezone(dt.UTC).isoformat(), "bars": bars,
                "funding": [
                    {"t": row.settled_at.astimezone(dt.UTC).isoformat(), "rate": str(row.rate)}
                    for row in funding if row.settled_at <= as_of
                ]}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not period:
        return {**manifest, "dataset_id": digest, "quality": {"eligible": False, "issues": ["unknown_interval"]}}
    invalid = [
        {"code": "NON_FINITE_OHLCV", "index": index}
        for index, row in enumerate(bars)
        if any(not Decimal(row[key]).is_finite() for key in ("o", "h", "l", "c", "v"))
    ]
    if invalid:
        return {**manifest, "dataset_id": digest, "quality": {"eligible": False, "issues": invalid}}
    candles = restore_candles(manifest)
    issues = [
        {"code": issue.code.value, "index": issue.index, "message": issue.message}
        for issue in validate_candles(candles)
    ]
    for i, (older, newer) in enumerate(zip(candles, candles[1:], strict=False), start=1):
        seconds = (newer.open_time - older.open_time).total_seconds()
        if seconds % period != 0:
            issues.append({"code": "IRREGULAR_OPEN_TIMESTAMP", "index": i})
    gaps = detect_gaps(candles, period) if not issues else []
    for i, (row, candle) in enumerate(zip(bars, candles, strict=True)):
        observed = dt.datetime.fromisoformat(row["end"])
        if not dt.timedelta(0) <= candle.close_time - observed <= dt.timedelta(milliseconds=1):
            issues.append({"code": "CLOSE_TIMESTAMP_MISMATCH", "index": i})
    quality = {
        "eligible": bool(candles) and not issues and not gaps,
        "issues": issues,
        "gaps": [{"start": gap.start.isoformat(), "end": gap.end.isoformat(), "missing": gap.missing} for gap in gaps],
        "samples": len(candles),
    }
    return {**manifest, "dataset_id": digest, "quality": quality}


def restore_candles(dataset: dict) -> list[Candle]:
    """Restore the precise OHLCV rows stored with the experiment."""
    period = INTERVAL_SECONDS[dataset["interval"]]
    return [
        Candle(dt.datetime.fromisoformat(row["t"]), *(Decimal(row[key]) for key in ("o", "h", "l", "c", "v")), period)
        for row in dataset["bars"]
    ]
