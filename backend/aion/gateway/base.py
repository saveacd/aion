"""Shared model-gateway primitives: messages, responses, pricing and JSON validation.

This module deliberately has no network imports so the gateway can be unit
tested with a local stub provider.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

D = Decimal
MTOK = D(1_000_000)

#: Estimated USD price per one million tokens: ``(input, output)``.
#: These are cost *estimates* used for budgeting, not exchange-grade billing.
PRICING_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus": (D("15"), D("75")),
    "claude-sonnet": (D("3"), D("15")),
    "claude-haiku": (D("0.8"), D("4")),
    "gpt-4.1-mini": (D("0.4"), D("1.6")),
    "gpt-4.1": (D("2"), D("8")),
    "gpt-4o-mini": (D("0.15"), D("0.6")),
    "gpt-4o": (D("2.5"), D("10")),
    "gemini-2.0-flash": (D("0.1"), D("0.4")),
    "gemini-1.5-flash": (D("0.075"), D("0.3")),
}
DEFAULT_PRICE: tuple[Decimal, Decimal] = (D("1"), D("3"))


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One chat turn. Roles are restricted to what providers accept."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("system", "user", "assistant"):
            raise ValueError(f"invalid chat role: {self.role}")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalised provider output plus cost/latency accounting."""

    text: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """Minimal contract implemented by every provider adapter."""

    name: str

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None = None,
    ) -> ModelResponse: ...


class ProviderError(RuntimeError):
    """Raised when a provider call fails. Never carries credentials."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(f"provider={provider} {message}")


class InvalidModelOutput(RuntimeError):
    """Raised when a model response is not valid JSON or violates its schema."""

    def __init__(self, provider: str, model: str, problems: Sequence[str]) -> None:
        self.provider = provider
        self.model = model
        self.problems = list(problems)
        detail = "; ".join(self.problems[:5])
        super().__init__(f"invalid model output provider={provider} model={model}: {detail}")


def price_for(model: str) -> tuple[Decimal, Decimal]:
    """Return ``(input, output)`` USD per million tokens for a model name."""

    lowered = model.lower()
    for prefix in sorted(PRICING_PER_MTOK, key=len, reverse=True):
        if lowered.startswith(prefix) or prefix in lowered:
            return PRICING_PER_MTOK[prefix]
    return DEFAULT_PRICE


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate USD cost for a call, quantised to 8 decimal places."""

    price_in, price_out = price_for(model)
    cost = (D(max(0, input_tokens)) * price_in + D(max(0, output_tokens)) * price_out) / MTOK
    return cost.quantize(D("0.00000001"))


def estimate_prompt_tokens(messages: Sequence[ChatMessage]) -> int:
    """Rough character/4 estimate. Only used to reserve budget up front."""

    chars = sum(len(m.content) for m in messages)
    return max(1, (chars + 3) // 4)


def parse_model_json(text: str) -> Any:
    """Parse JSON, tolerating Markdown fences and surrounding prose."""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        newline = cleaned.find("\n")
        if newline != -1:
            cleaned = cleaned[newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        candidate = _extract_json_object(cleaned)
        if candidate is None:
            raise exc
        return json.loads(candidate)


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` block, ignoring braces inside strings."""

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None
    return None


def validate_json(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Validate ``instance`` against the subset of JSON Schema we emit.

    Supports local ``$ref``/``$defs``, type, enum, const, anyOf/oneOf,
    properties, required, additionalProperties, items, length and numeric
    bounds — enough for Pydantic-generated agent schemas without pulling in a
    third-party validator.
    """

    problems: list[str] = []
    _validate(instance, schema, schema, "$", problems, 0)
    return problems


def _validate(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    problems: list[str],
    depth: int,
) -> None:
    if depth > 32 or not isinstance(schema, dict):
        return

    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_ref(ref, root)
        if resolved is None:
            problems.append(f"{path}: unresolved $ref {ref}")
            return
        _validate(value, resolved, root, path, problems, depth + 1)
        return

    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: expected const {schema['const']!r}")

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        problems.append(f"{path}: {value!r} is not an allowed value")

    for keyword in ("anyOf", "oneOf"):
        options = schema.get(keyword)
        if isinstance(options, list) and options and not any(
            _matches(value, option, root, depth) for option in options
        ):
            problems.append(f"{path}: does not match an allowed variant")

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, t) for t in allowed):
            problems.append(f"{path}: expected type {expected}")
            return

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    problems.append(f"{path}.{key}: required field missing")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, subschema in properties.items():
                if key in value:
                    _validate(value[key], subschema, root, f"{path}.{key}", problems, depth + 1)
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            for key in value:
                if key not in properties:
                    problems.append(f"{path}.{key}: additional property not allowed")

    if isinstance(value, list):
        _check_length(value, schema, path, problems)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(item, items, root, f"{path}[{index}]", problems, depth + 1)

    if isinstance(value, str):
        _check_length(value, schema, path, problems)

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        _check_number(value, schema, path, problems)


def _matches(value: Any, schema: Any, root: dict[str, Any], depth: int) -> bool:
    if not isinstance(schema, dict):
        return False
    found: list[str] = []
    _validate(value, schema, root, "$", found, depth + 1)
    return not found


def _type_ok(value: Any, expected: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _check_length(value: Any, schema: dict[str, Any], path: str, problems: list[str]) -> None:
    minimum = schema.get("minItems", schema.get("minLength"))
    maximum = schema.get("maxItems", schema.get("maxLength"))
    if isinstance(minimum, int) and len(value) < minimum:
        problems.append(f"{path}: too short (min {minimum})")
    if isinstance(maximum, int) and len(value) > maximum:
        problems.append(f"{path}: too long (max {maximum})")


def _check_number(value: Any, schema: dict[str, Any], path: str, problems: list[str]) -> None:
    try:
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            problems.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            problems.append(f"{path}: must be < {schema['exclusiveMaximum']}")
    except TypeError:
        problems.append(f"{path}: value is not comparable to schema bounds")


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None
