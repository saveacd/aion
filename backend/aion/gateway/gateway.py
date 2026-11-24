"""The model gateway: routing, budget, circuit breaking and schema checks."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aion.gateway.base import (
    ChatMessage,
    InvalidModelOutput,
    ModelResponse,
    Provider,
    ProviderError,
    estimate_cost,
    estimate_prompt_tokens,
    parse_model_json,
    validate_json,
)
from aion.gateway.budget import BudgetLedger, CallRecord
from aion.gateway.circuit import CircuitBreaker, CircuitOpen

D = Decimal
ZERO = D("0")


@dataclass(frozen=True, slots=True)
class Route:
    provider: str
    model: str


#: Role -> provider/model. Override per deployment without touching code.
DEFAULT_ROUTES: dict[str, Route] = {
    "market_regime": Route("anthropic", "claude-sonnet-4-20250514"),
    "technical_analysis": Route("anthropic", "claude-sonnet-4-20250514"),
    "derivatives": Route("anthropic", "claude-sonnet-4-20250514"),
    "strategy_research": Route("anthropic", "claude-sonnet-4-20250514"),
    "chief_decision": Route("anthropic", "claude-opus-4-1"),
    "adversarial_review": Route("anthropic", "claude-sonnet-4-20250514"),
}


class UnknownRoute(RuntimeError):
    def __init__(self, role: str) -> None:
        self.role = role
        super().__init__(f"no model route configured for role={role}")


class ProviderNotConfigured(RuntimeError):
    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"provider={provider} is not configured")


class ModelGateway:
    """Uniform entry point for every agent model call."""

    def __init__(
        self,
        providers: Mapping[str, Provider],
        ledger: BudgetLedger,
        breaker: CircuitBreaker,
        *,
        routes: Mapping[str, Route] | None = None,
        default_max_tokens: int = 1024,
        default_temperature: float = 0.2,
    ) -> None:
        self.providers = dict(providers)
        self.ledger = ledger
        self.breaker = breaker
        self.routes: dict[str, Route] = dict(routes or DEFAULT_ROUTES)
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature
        self._usage: dict[str, tuple[int, int]] = {}

    def set_route(self, role: str, provider: str, model: str) -> None:
        self.routes[role] = Route(provider, model)

    def run_usage(self, run_id: uuid.UUID | str) -> tuple[int, int]:
        return self._usage.get(_run_key(run_id) or "", (0, 0))

    async def call(
        self,
        role: str,
        messages: Sequence[ChatMessage],
        json_schema: dict[str, Any] | None = None,
        *,
        run_id: uuid.UUID | str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        purpose: str | None = None,
    ) -> ModelResponse:
        route = self.routes.get(role)
        if route is None:
            raise UnknownRoute(role)
        provider = self.providers.get(route.provider)
        if provider is None:
            raise ProviderNotConfigured(route.provider)

        run_key = _run_key(run_id)
        max_out = max_tokens or self.default_max_tokens
        temp = self.default_temperature if temperature is None else temperature
        label = purpose or role

        if not await self.breaker.allow(route.provider):
            raise CircuitOpen(route.provider)

        for attempt in (1, 2):
            estimated = estimate_cost(
                route.model, estimate_prompt_tokens(messages), max_out
            )
            reservation = await self.ledger.check_and_reserve(estimated, run_id=run_key)
            started = time.perf_counter()
            try:
                response = await provider.complete(
                    messages,
                    model=route.model,
                    max_tokens=max_out,
                    temperature=temp,
                    json_schema=json_schema,
                )
            except ProviderError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                await self.breaker.record_failure(route.provider)
                await self.ledger.settle(
                    ZERO,
                    reservation,
                    record=self._record(
                        route,
                        label,
                        run_id,
                        status="ERROR",
                        latency_ms=latency_ms,
                        attempts=attempt,
                        error_code="PROVIDER_ERROR",
                        error_message=str(exc)[:300],
                        meta={"role": role, "attempt": attempt},
                    ),
                )
                raise

            await self.breaker.record_success(route.provider)
            self._track_usage(run_key, response)
            latency_ms = response.latency_ms or int((time.perf_counter() - started) * 1000)

            if json_schema is None:
                await self.ledger.settle(
                    response.cost_usd,
                    reservation,
                    record=self._record(
                        route,
                        label,
                        run_id,
                        status="OK",
                        latency_ms=latency_ms,
                        attempts=attempt,
                        response=response,
                        meta={"role": role, "attempt": attempt},
                    ),
                )
                return response

            problems = _schema_problems(response.text, json_schema)
            status = "OK" if not problems else "INVALID"
            await self.ledger.settle(
                response.cost_usd,
                reservation,
                record=self._record(
                    route,
                    label,
                    run_id,
                    status=status,
                    latency_ms=latency_ms,
                    attempts=attempt,
                    response=response,
                    error_code=None if not problems else "INVALID_OUTPUT",
                    error_message=None if not problems else "; ".join(problems[:3])[:300],
                    meta={"role": role, "attempt": attempt, "schema": True},
                ),
            )
            if not problems:
                return response
            if attempt == 2:
                raise InvalidModelOutput(response.provider, response.model, problems)

        # The loop always returns or raises; keep static analysis honest.
        raise InvalidModelOutput(route.provider, route.model, ["retry budget exhausted"])

    async def aclose(self) -> None:
        for provider in self.providers.values():
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                await closer()

    def _track_usage(self, run_key: str | None, response: ModelResponse) -> None:
        if run_key is None:
            return
        prev_in, prev_out = self._usage.get(run_key, (0, 0))
        self._usage[run_key] = (
            prev_in + max(0, response.input_tokens),
            prev_out + max(0, response.output_tokens),
        )

    def _record(
        self,
        route: Route,
        purpose: str,
        run_id: uuid.UUID | str | None,
        *,
        status: str,
        latency_ms: int | None,
        attempts: int,
        response: ModelResponse | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallRecord:
        return CallRecord(
            provider=response.provider if response else route.provider,
            model=response.model if response else route.model,
            purpose=purpose,
            status=status,
            input_tokens=response.input_tokens if response else 0,
            output_tokens=response.output_tokens if response else 0,
            latency_ms=latency_ms,
            attempts=attempts,
            error_code=error_code,
            error_message=error_message,
            run_id=_uuid_or_none(run_id),
            request_meta=meta or {},
        )


def _schema_problems(text: str, schema: dict[str, Any]) -> list[str]:
    try:
        parsed = parse_model_json(text)
    except ValueError:
        return ["response is not valid JSON"]
    return validate_json(parsed, schema)


def _run_key(run_id: uuid.UUID | str | None) -> str | None:
    if run_id is None:
        return None
    return str(run_id)


def _uuid_or_none(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
