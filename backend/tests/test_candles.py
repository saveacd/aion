from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aion.quant.candles import (
    Candle,
    IssueCode,
    aggregate,
    detect_gaps,
    validate_candles,
)

BASE = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
D = Decimal


def make_candle(
    minute: int,
    *,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
    volume: str = "1",
    interval: int = 60,
) -> Candle:
    return Candle(
        open_time=BASE + dt.timedelta(minutes=minute),
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(volume),
        interval_seconds=interval,
    )


def codes(issues: list) -> list[str]:
    return [issue.code for issue in issues]


def test_close_time_property() -> None:
    candle = make_candle(0)
    assert candle.close_time == BASE + dt.timedelta(minutes=1)


def test_naive_open_time_is_rejected() -> None:
    with pytest.raises(ValueError):
        Candle(
            open_time=dt.datetime(2024, 1, 1),
            open=D("1"),
            high=D("2"),
            low=D("1"),
            close=D("2"),
            volume=D("1"),
        )


def test_valid_candles_have_no_issues() -> None:
    candles = [make_candle(i) for i in range(5)]
    assert validate_candles(candles) == []


def test_duplicate_timestamp_is_flagged() -> None:
    candles = [make_candle(0), make_candle(0)]
    issues = validate_candles(candles)
    assert IssueCode.DUPLICATE_TIMESTAMP in codes(issues)
    assert issues[-1].index == 1


def test_non_monotonic_timestamps_are_flagged() -> None:
    candles = [make_candle(2), make_candle(1)]
    assert IssueCode.NON_MONOTONIC in codes(validate_candles(candles))


def test_ohlc_logic_violations_are_flagged() -> None:
    high_bad = make_candle(0, open_="100", high="100.5", low="99", close="101")
    low_bad = make_candle(0, open_="100", high="101", low="100.5", close="99")
    assert IssueCode.OHLC_HIGH in codes(validate_candles([high_bad]))
    assert IssueCode.OHLC_LOW in codes(validate_candles([low_bad]))


def test_non_positive_price_and_negative_volume_are_flagged() -> None:
    candle = make_candle(0, open_="0", high="101", low="99", close="100", volume="-5")
    found = codes(validate_candles([candle]))
    assert IssueCode.NON_POSITIVE_PRICE in found
    assert IssueCode.NEGATIVE_VOLUME in found


def test_large_single_bar_move_is_flagged() -> None:
    candles = [make_candle(0, close="100"), make_candle(1, open_="100", high="130", low="99", close="130")]
    issues = validate_candles(candles)
    assert IssueCode.PRICE_JUMP in codes(issues)
    assert issues[-1].index == 1


def test_exactly_twenty_percent_move_is_not_flagged() -> None:
    candles = [make_candle(0, close="100"), make_candle(1, open_="100", high="120", low="99", close="120")]
    assert IssueCode.PRICE_JUMP not in codes(validate_candles(candles))


def test_detect_gaps_returns_missing_interval() -> None:
    candles = [make_candle(0), make_candle(1), make_candle(3), make_candle(4)]
    gaps = detect_gaps(candles, 60)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.start == BASE + dt.timedelta(minutes=2)
    assert gap.end == BASE + dt.timedelta(minutes=3)
    assert gap.missing == 1


def test_detect_gaps_rejects_bad_interval() -> None:
    with pytest.raises(ValueError):
        detect_gaps([make_candle(0)], 0)


def test_aggregate_ohlcv_and_alignment() -> None:
    candles = [
        make_candle(
            i,
            open_=str(i + 1),
            high=str(i + 3),
            low=str(i),
            close=str(i + 1.5),
            volume="2",
        )
        for i in range(10)
    ]
    bars = aggregate(candles, 60, 300)
    assert len(bars) == 2

    first, second = bars
    assert first.open_time == BASE
    assert first.open == D("1")
    assert first.high == D("7")  # minute 4: high 7
    assert first.low == D("0")  # minute 0: low 0
    assert first.close == D("5.5")  # minute 4: close 5.5
    assert first.volume == D("10")

    assert second.open_time == BASE + dt.timedelta(minutes=5)
    assert second.open == D("6")
    assert second.close == D("10.5")


def test_aggregate_drops_incomplete_last_bar() -> None:
    complete = [make_candle(i) for i in range(10)]
    assert len(aggregate(complete, 60, 300)) == 2

    one_extra = [make_candle(i) for i in range(11)]
    assert len(aggregate(one_extra, 60, 300)) == 2

    fully_covered = [make_candle(i) for i in range(15)]
    assert len(aggregate(fully_covered, 60, 300)) == 3


def test_aggregate_rejects_bad_intervals() -> None:
    candles = [make_candle(0)]
    with pytest.raises(ValueError):
        aggregate(candles, 0, 300)
    with pytest.raises(ValueError):
        aggregate(candles, 300, 120)
    with pytest.raises(ValueError):
        aggregate(candles, 60, 90)


def test_aggregate_empty_input() -> None:
    assert aggregate([], 60, 300) == []
