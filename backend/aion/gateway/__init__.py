"""Model gateway package.

Provider adapters import ``httpx``; import them lazily so the rest of the
gateway (routing, budget, breaker, schema validation) has no network
dependency and can be unit tested with a local stub provider.
"""

from __future__ import annotations

from typing import Any

from aion.gateway.base import (
    ChatMessage,
    InvalidModelOutput,
    ModelResponse,
    Provider,
    ProviderError,
    estimate_cost,
    validate_json,
)
from aion.gateway.budget import BudgetExceeded, BudgetLedger, CallRecord, Reservation
from aion.gateway.circuit import CLOSED, HALF_OPEN, OPEN, CircuitBreaker, CircuitOpen
from aion.gateway.gateway import (
    DEFAULT_ROUTES,
    ModelGateway,
    ProviderNotConfigured,
    Route,
    UnknownRoute,
)

__all__ = [
    "CLOSED",
    "DEFAULT_ROUTES",
    "HALF_OPEN",
    "OPEN",
    "BudgetExceeded",
    "BudgetLedger",
    "CallRecord",
    "ChatMessage",
    "CircuitBreaker",
    "CircuitOpen",
    "InvalidModelOutput",
    "ModelGateway",
    "ModelResponse",
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "Reservation",
    "Route",
    "UnknownRoute",
    "estimate_cost",
    "validate_json",
]


def __getattr__(name: str) -> Any:
    # Lazy import keeps httpx optional for offline tests.
    if name == "AnthropicProvider":
        from aion.gateway.anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "OpenAICompatProvider":
        from aion.gateway.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
