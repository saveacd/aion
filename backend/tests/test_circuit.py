"""Circuit breaker tests with a controllable clock."""

from __future__ import annotations

import asyncio

from aion.gateway.circuit import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from tests.fakes import FakeClock, FakeRedis


def make_breaker(clock: FakeClock, *, threshold: int = 3) -> CircuitBreaker:
    return CircuitBreaker(
        FakeRedis(clock=clock),
        failure_threshold=threshold,
        window_seconds=600.0,
        open_seconds=60.0,
        half_open_ttl_seconds=30.0,
        clock=clock,
    )


def test_opens_after_consecutive_failures() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)

    assert asyncio.run(breaker.state("p")) == CLOSED
    assert asyncio.run(breaker.allow("p")) is True

    for _ in range(3):
        asyncio.run(breaker.record_failure("p"))

    assert asyncio.run(breaker.state("p")) == OPEN
    assert asyncio.run(breaker.allow("p")) is False


def test_half_open_after_cooldown_and_success_closes() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(3):
        asyncio.run(breaker.record_failure("p"))
    assert asyncio.run(breaker.state("p")) == OPEN

    clock.advance(61)

    assert asyncio.run(breaker.state("p")) == HALF_OPEN
    assert asyncio.run(breaker.allow("p")) is True  # single probe
    assert asyncio.run(breaker.allow("p")) is False  # probe already in flight

    asyncio.run(breaker.record_success("p"))
    assert asyncio.run(breaker.state("p")) == CLOSED
    assert asyncio.run(breaker.allow("p")) is True


def test_failed_probe_reopens() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(3):
        asyncio.run(breaker.record_failure("p"))
    clock.advance(61)
    assert asyncio.run(breaker.allow("p")) is True

    asyncio.run(breaker.record_failure("p"))

    assert asyncio.run(breaker.state("p")) == OPEN


def test_success_resets_failure_window() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    asyncio.run(breaker.record_failure("p"))
    asyncio.run(breaker.record_failure("p"))

    asyncio.run(breaker.record_success("p"))

    assert asyncio.run(breaker.failure_count("p")) == 0
    assert asyncio.run(breaker.state("p")) == CLOSED
