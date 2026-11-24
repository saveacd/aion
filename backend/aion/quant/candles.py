"""OHLCV candle structures, data-quality validation and time-frame aggregation.

``validate_candles`` never raises: it returns a list of structured issues so a
caller can decide whether bad data is a warning (skip a bar) or a hard stop
(no trading). Bad market data must never silently become a signal.

See MASTER_SPEC §7 (Quant Engine) — data validation, multi-timeframe aggregation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

D = Decimal

#: A single candle whose close moved more than this fraction is flagged.
MAX_SINGLE_BAR_MOVE = D("0.20")


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar.

    ``open_time`` is the bar's opening timestamp and must be timezone-aware;
    it is normalised to UTC. ``interval_seconds`` lets :attr:`close_time` be
    derived without a separate series-level configuration.
    """

    open_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    interval_seconds: int = 60

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0, got {self.interval_seconds}")
        if self.open_time.tzinfo is None:
            raise ValueError("open_time must be timezone-aware (UTC)")
        object.__setattr__(self, "open_time", self.open_time.astimezone(dt.UTC))

    @property
    def close_time(self) -> dt.datetime:
        """Exclusive end of the bar (open time + interval)."""
        return self.open_time + dt.timedelta(seconds=self.interval_seconds)


class IssueCode(StrEnum):
    NON_MONOTONIC = "NON_MONOTONIC"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    OHLC_HIGH = "OHLC_HIGH"
    OHLC_LOW = "OHLC_LOW"
    INVALID_RANGE = "INVALID_RANGE"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    PRICE_JUMP = "PRICE_JUMP"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    index: int


@dataclass(frozen=True, slots=True)
class Gap:
    """A missing stretch of candles.

    ``start`` is the open time of the first missing bar, ``end`` is the open
    time of the first bar after the gap (exclusive), ``missing`` is how many
    bars are absent.
    """

    start: dt.datetime
    end: dt.datetime
    missing: int


def validate_candles(candles: Sequence[Candle]) -> list[ValidationIssue]:
    """Return every data-quality issue found (never raises)."""
    issues: list[ValidationIssue] = []
    prev: Candle | None = None

    for index, candle in enumerate(candles):
        if candle.high < max(candle.open, candle.close):
            issues.append(
                ValidationIssue(
                    IssueCode.OHLC_HIGH,
                    f"high {candle.high} < max(open, close) at index {index}",
                    index,
                )
            )
        if candle.low > min(candle.open, candle.close):
            issues.append(
                ValidationIssue(
                    IssueCode.OHLC_LOW,
                    f"low {candle.low} > min(open, close) at index {index}",
                    index,
                )
            )
        if candle.high < candle.low:
            issues.append(
                ValidationIssue(
                    IssueCode.INVALID_RANGE,
                    f"high {candle.high} < low {candle.low} at index {index}",
                    index,
                )
            )
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            issues.append(
                ValidationIssue(
                    IssueCode.NON_POSITIVE_PRICE,
                    f"non-positive price at index {index}",
                    index,
                )
            )
        if candle.volume < 0:
            issues.append(
                ValidationIssue(
                    IssueCode.NEGATIVE_VOLUME,
                    f"negative volume {candle.volume} at index {index}",
                    index,
                )
            )

        if prev is not None:
            if candle.open_time == prev.open_time:
                issues.append(
                    ValidationIssue(
                        IssueCode.DUPLICATE_TIMESTAMP,
                        f"duplicate open_time {candle.open_time.isoformat()} at index {index}",
                        index,
                    )
                )
            elif candle.open_time < prev.open_time:
                issues.append(
                    ValidationIssue(
                        IssueCode.NON_MONOTONIC,
                        f"open_time goes backwards at index {index}",
                        index,
                    )
                )
            elif prev.close > 0:
                change = abs(candle.close - prev.close) / prev.close
                if change > MAX_SINGLE_BAR_MOVE:
                    issues.append(
                        ValidationIssue(
                            IssueCode.PRICE_JUMP,
                            f"close moved {change:.2%} from previous bar at index {index}",
                            index,
                        )
                    )
        prev = candle

    return issues


def detect_gaps(candles: Sequence[Candle], interval_seconds: int) -> list[Gap]:
    """Return missing bar stretches for an expected fixed interval."""
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

    gaps: list[Gap] = []
    expected = dt.timedelta(seconds=interval_seconds)
    for prev, cur in zip(candles, candles[1:], strict=False):
        delta = cur.open_time - prev.open_time
        missing = int(delta.total_seconds() // interval_seconds) - 1
        if missing > 0:
            gaps.append(Gap(start=prev.open_time + expected, end=cur.open_time, missing=missing))
    return gaps


def aggregate(
    candles: Sequence[Candle],
    from_seconds: int,
    to_seconds: int,
) -> list[Candle]:
    """Aggregate ``from_seconds`` bars into ``to_seconds`` bars.

    Bucket boundaries are aligned to the Unix epoch. The final bucket is only
    emitted when it is fully covered by data; a partial trailing bar is
    dropped so no incomplete candle ever reaches an indicator.
    """
    if from_seconds <= 0 or to_seconds <= 0:
        raise ValueError("from_seconds and to_seconds must be > 0")
    if to_seconds % from_seconds != 0:
        raise ValueError("to_seconds must be an integer multiple of from_seconds")
    if to_seconds < from_seconds:
        raise ValueError("to_seconds must be >= from_seconds")
    if not candles:
        return []

    ordered = sorted(candles, key=lambda c: c.open_time)
    buckets: dict[int, list[Candle]] = {}
    for candle in ordered:
        bucket_start = int(candle.open_time.timestamp()) // to_seconds * to_seconds
        buckets.setdefault(bucket_start, []).append(candle)

    last_end = ordered[-1].open_time + dt.timedelta(seconds=from_seconds)

    result: list[Candle] = []
    for bucket_start in sorted(buckets):
        bucket_end = bucket_start + to_seconds
        if bucket_end > last_end.timestamp():
            # Incomplete trailing bucket: discard it.
            continue
        group = buckets[bucket_start]
        result.append(
            Candle(
                open_time=dt.datetime.fromtimestamp(bucket_start, tz=dt.UTC),
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum((c.volume for c in group), D("0")),
                interval_seconds=to_seconds,
            )
        )
    return result
