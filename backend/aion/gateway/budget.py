"""Per-run and per-day model spend accounting.

Daily totals live in Redis (``aion:budget:{date}``) so the budget survives
restarts and is shared by every worker. Every settled call is also mirrored to
the ``model_calls`` table when a session factory is supplied.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

D = Decimal
ZERO = D("0")
QUANT = D("0.00000001")

logger = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    """Raised before a call when the run or daily budget would be exceeded."""

    def __init__(self, scope: str, limit: Decimal, spent: Decimal) -> None:
        self.scope = scope
        self.limit = limit
        self.spent = spent
        super().__init__(f"{scope} model budget exceeded (spent={spent}, limit={limit})")


@dataclass(frozen=True, slots=True)
class Reservation:
    """A previously reserved spend that ``settle`` reconciles against actuals."""

    amount: Decimal
    daily_key: str
    run_key: str | None
    run_id: str | None


@dataclass(slots=True)
class CallRecord:
    """Metadata persisted to the ``model_calls`` table (never includes keys)."""

    provider: str
    model: str
    purpose: str = "agent"
    status: str = "OK"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = ZERO
    latency_ms: int | None = None
    attempts: int = 1
    error_code: str | None = None
    error_message: str | None = None
    run_id: uuid.UUID | None = None
    request_meta: dict[str, Any] = field(default_factory=dict)


class BudgetLedger:
    """Redis-backed spend counter with run-level and daily limits."""

    def __init__(
        self,
        redis: Any,
        *,
        daily_budget_usd: float | Decimal,
        run_budget_usd: float | Decimal,
        session_factory: Callable[[], Any] | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._redis = redis
        self.daily_budget = _q(daily_budget_usd)
        self.run_budget = _q(run_budget_usd)
        self._session_factory = session_factory
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))

    def _now(self) -> dt.datetime:
        return self._clock()

    def daily_key(self) -> str:
        return f"aion:budget:{self._now().date().isoformat()}"

    @staticmethod
    def run_key(run_id: str) -> str:
        return f"aion:budget:run:{run_id}"

    async def _spent(self, key: str) -> Decimal:
        raw = await self._redis.get(key)
        if raw is None:
            return ZERO
        return _q(raw)

    async def daily_spent(self) -> Decimal:
        return await self._spent(self.daily_key())

    async def run_spent(self, run_id: str) -> Decimal:
        return await self._spent(self.run_key(run_id))

    async def check_and_reserve(
        self, estimated_usd: float | Decimal, *, run_id: str | None = None
    ) -> Reservation:
        """Atomically reserve estimated spend or raise :class:`BudgetExceeded`."""

        amount = max(ZERO, _q(estimated_usd))
        daily_key = self.daily_key()
        run_key = self.run_key(run_id) if run_id else None

        daily_after = _q(await self._redis.incrbyfloat(daily_key, float(amount)))
        await self._redis.expire(daily_key, 2 * 86400)
        if daily_after > self.daily_budget:
            await self._redis.incrbyfloat(daily_key, -float(amount))
            raise BudgetExceeded("daily", self.daily_budget, daily_after - amount)

        if run_key is not None:
            run_after = _q(await self._redis.incrbyfloat(run_key, float(amount)))
            await self._redis.expire(run_key, 86400)
            if run_after > self.run_budget:
                await self._redis.incrbyfloat(run_key, -float(amount))
                await self._redis.incrbyfloat(daily_key, -float(amount))
                raise BudgetExceeded("run", self.run_budget, run_after - amount)

        return Reservation(amount=amount, daily_key=daily_key, run_key=run_key, run_id=run_id)

    async def settle(
        self,
        actual_usd: float | Decimal,
        reservation: Reservation | None = None,
        *,
        record: CallRecord | None = None,
    ) -> None:
        """Reconcile a reservation with the actual cost and persist the call."""

        actual = max(ZERO, _q(actual_usd))
        if reservation is not None:
            delta = actual - reservation.amount
            keys = [reservation.daily_key]
            if reservation.run_key is not None:
                keys.append(reservation.run_key)
            if delta != ZERO:
                for key in keys:
                    value = _q(await self._redis.incrbyfloat(key, float(delta)))
                    if value < ZERO:
                        await self._redis.set(key, "0")
        elif actual > ZERO:
            daily_key = self.daily_key()
            await self._redis.incrbyfloat(daily_key, float(actual))
            await self._redis.expire(daily_key, 2 * 86400)

        if record is not None:
            record.cost_usd = actual
            await self._persist_call(record)

    async def _persist_call(self, record: CallRecord) -> None:
        if self._session_factory is None:
            return
        from aion.models.agent import ModelCall

        try:
            async with self._session_factory() as session:
                session.add(
                    ModelCall(
                        run_id=record.run_id,
                        provider=record.provider,
                        model=record.model,
                        purpose=record.purpose,
                        status=record.status,
                        input_tokens=record.input_tokens,
                        output_tokens=record.output_tokens,
                        cost_usd=record.cost_usd,
                        latency_ms=record.latency_ms,
                        attempts=record.attempts,
                        error_code=record.error_code,
                        error_message=record.error_message,
                        request_meta=record.request_meta,
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - accounting must not crash the analysis
            logger.warning("failed to persist ModelCall (%s)", type(exc).__name__)


def _q(value: float | Decimal | str) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(QUANT)
    return D(str(value)).quantize(QUANT)
