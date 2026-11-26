"""Agent runtime: context, prompt assembly and run/message persistence."""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from aion.agents.schemas import AgentOutput
from aion.gateway.base import ChatMessage, InvalidModelOutput, ModelResponse, parse_model_json
from aion.gateway.gateway import ModelGateway
from aion.models.agent import AgentMessage, AgentRun


@dataclass(slots=True)
class AgentContext:
    """Everything an agent is allowed to see. No database handles, no secrets."""

    symbol: str
    interval: str
    run_id: uuid.UUID
    inputs: dict[str, Any] = field(default_factory=dict)
    upstream: dict[str, Any] = field(default_factory=dict)

    def with_upstream(self, upstream: dict[str, Any]) -> AgentContext:
        return AgentContext(
            symbol=self.symbol,
            interval=self.interval,
            run_id=self.run_id,
            inputs=dict(self.inputs),
            upstream=dict(upstream),
        )


@runtime_checkable
class RunRecorder(Protocol):
    async def ensure_run(
        self,
        *,
        run_id: uuid.UUID,
        symbol: str,
        interval: str,
        trigger: str = "manual",
        mode: str = "PAPER",
        user_id: uuid.UUID | None = None,
    ) -> None: ...

    async def record_message(
        self,
        *,
        run_id: uuid.UUID,
        agent_name: str,
        output: BaseModel | None,
        status: str = "OK",
        error: str | None = None,
        latency_ms: int | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None: ...

    async def finish_run(
        self,
        *,
        run_id: uuid.UUID,
        verdict: str,
        confidence: float | None,
        summary: str,
        duration_ms: int,
        error: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
        status: str | None = None,
    ) -> None: ...


class NullRunRecorder:
    """No-op recorder used by unit tests and dry runs."""

    async def ensure_run(self, **_: Any) -> None:
        return None

    async def record_message(self, **_: Any) -> None:
        return None

    async def finish_run(self, **_: Any) -> None:
        return None


class DbRunRecorder:
    """Persists AgentRun and AgentMessage rows (one message per agent step)."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def ensure_run(
        self,
        *,
        run_id: uuid.UUID,
        symbol: str,
        interval: str,
        trigger: str = "manual",
        mode: str = "PAPER",
        user_id: uuid.UUID | None = None,
    ) -> None:
        async with self._session_factory() as session:
            if await session.get(AgentRun, run_id) is not None:
                return
            session.add(
                AgentRun(
                    id=run_id,
                    symbol=symbol,
                    interval=interval,
                    trigger=trigger,
                    status="RUNNING",
                    mode=mode,
                    user_id=user_id,
                )
            )
            await session.commit()

    async def record_message(
        self,
        *,
        run_id: uuid.UUID,
        agent_name: str,
        output: BaseModel | None,
        status: str = "OK",
        error: str | None = None,
        latency_ms: int | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        structured: dict[str, Any] = {}
        stats: dict[str, Any] = {}
        if output is not None:
            structured = output.model_dump(mode="json")
            stats = dict(structured.get("input_refs") or {})
        if provider:
            stats["provider"] = provider
        if model:
            stats["model"] = model

        async with self._session_factory() as session:
            # Parallel agents share a run. Serialize sequence allocation on the
            # parent row without blocking foreign-key checks from model calls.
            await session.execute(
                select(AgentRun.id).where(AgentRun.id == run_id).with_for_update(key_share=True)
            )
            current = await session.scalar(
                select(func.coalesce(func.max(AgentMessage.seq), 0)).where(
                    AgentMessage.run_id == run_id
                )
            )
            seq = int(current or 0) + 1
            session.add(
                AgentMessage(
                    run_id=run_id,
                    seq=seq,
                    agent_name=agent_name,
                    role="analysis",
                    status=status,
                    summary=(getattr(output, "summary", None) or error or "")[:2000],
                    structured=structured,
                    evidence=list(getattr(output, "evidence", []) or []),
                    warnings=list(getattr(output, "warnings", []) or []),
                    confidence=(
                        Decimal(str(output.confidence))
                        if output is not None and getattr(output, "confidence", None) is not None
                        else None
                    ),
                    input_refs=stats,
                    latency_ms=latency_ms,
                )
            )
            await session.commit()

    async def finish_run(
        self,
        *,
        run_id: uuid.UUID,
        verdict: str,
        confidence: float | None,
        summary: str,
        duration_ms: int,
        error: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
        status: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            run.status = status or ("COMPLETED" if error is None else "DEGRADED")
            run.finished_at = dt.datetime.now(dt.UTC)
            run.duration_ms = duration_ms
            run.verdict = verdict
            run.confidence = Decimal(str(confidence)) if confidence is not None else None
            run.summary = summary
            run.error = error
            run.provider = provider
            run.model_name = model
            run.total_input_tokens = input_tokens
            run.total_output_tokens = output_tokens
            run.total_cost_usd = cost_usd
            await session.commit()


class BaseAgent:
    """Shared execution path for all six agents."""

    name: ClassVar[str]
    role: ClassVar[str]
    output_model: ClassVar[type[BaseModel]]
    system_prompt: ClassVar[str]
    max_tokens: ClassVar[int] = 1024

    #: Filled in by the runtime from trustworthy context, never by the model.
    IDENTITY_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"agent_name", "run_id", "symbol", "interval", "timestamp", "input_refs"}
    )

    def __init__(self, gateway: ModelGateway, recorder: RunRecorder | None = None) -> None:
        self.gateway = gateway
        self.recorder: RunRecorder = recorder or NullRunRecorder()

    async def run(self, ctx: AgentContext) -> AgentOutput:
        schema = self.model_schema()
        messages = self.build_messages(ctx, schema)
        await self.recorder.ensure_run(
            run_id=ctx.run_id, symbol=ctx.symbol, interval=ctx.interval
        )

        started = time.perf_counter()
        try:
            response = await self.gateway.call(
                self.role,
                messages,
                schema,
                run_id=ctx.run_id,
                max_tokens=self.max_tokens,
                purpose=self.name,
            )
        except InvalidModelOutput as exc:
            # A truncated or malformed answer must not fail the whole run:
            # ask again, explicitly shorter, with more room for the JSON.
            response = await self._retry_concise(ctx, messages, schema, exc.problems)
        except Exception as exc:
            await self.recorder.record_message(
                run_id=ctx.run_id,
                agent_name=self.name,
                output=None,
                status="ERROR",
                error=f"{type(exc).__name__}: {exc}"[:300],
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

        try:
            parsed = parse_model_json(response.text)
        except ValueError:
            response = await self._retry_concise(
                ctx, messages, schema, ["response is not valid JSON"]
            )
            try:
                parsed = parse_model_json(response.text)
            except ValueError as exc:
                raise InvalidModelOutput(
                    response.provider, response.model, ["response is not valid JSON"]
                ) from exc
        if not isinstance(parsed, dict):
            raise InvalidModelOutput(
                response.provider, response.model, ["response must be a JSON object"]
            )

        payload = dict(parsed)
        # Identity fields are authoritative and cannot be spoofed by the model.
        payload["agent_name"] = self.name
        payload["run_id"] = str(ctx.run_id)
        payload["symbol"] = ctx.symbol
        payload["interval"] = ctx.interval
        payload["timestamp"] = dt.datetime.now(dt.UTC)
        payload["input_refs"] = {
            "inputs": sorted(ctx.inputs),
            "upstream": sorted(ctx.upstream),
        }

        if research := ctx.inputs.get("market_research"):
            payload["input_refs"]["market_research_version"] = research["version"]

        try:
            output = self.output_model.model_validate(payload)
        except ValidationError as exc:
            raise InvalidModelOutput(
                response.provider,
                response.model,
                [f"schema violation ({exc.error_count()} issue(s))"],
            ) from exc

        problems = self.post_validate(output, ctx)
        if problems:
            raise InvalidModelOutput(response.provider, response.model, problems)

        await self.recorder.record_message(
            run_id=ctx.run_id,
            agent_name=self.name,
            output=output,
            status="OK",
            latency_ms=response.latency_ms,
            provider=response.provider,
            model=response.model,
        )
        return output

    async def _retry_concise(
        self,
        ctx: AgentContext,
        messages: Sequence[ChatMessage],
        schema: dict[str, Any],
        problems: Sequence[str],
    ) -> ModelResponse:
        detail = "; ".join(list(problems)[:3]) or "输出格式不符合要求"
        hint = (
            f"上一次输出不可用：{detail}。"
            "请重新输出一个完整、合法的 JSON 对象，并且比上次更精简："
            "summary 不超过 300 字，evidence 与 warnings 各不超过 4 条，不要输出任何解释、Markdown 或代码块。"
        )
        repaired = [*messages, ChatMessage("user", hint)]
        return await self.gateway.call(
            self.role,
            repaired,
            schema,
            run_id=ctx.run_id,
            max_tokens=min(self.max_tokens * 2, 2400),
            purpose=f"{self.name}:repair",
        )

    def model_schema(self) -> dict[str, Any]:
        """Output JSON Schema with identity fields made optional.

        ``agent_name``/``run_id``/``symbol``/``interval`` are injected from the
        trusted context after validation, so the model is not asked to guess
        (or spoof) them.
        """

        schema = self.output_model.model_json_schema()
        required = [name for name in schema.get("required", []) if name not in self.IDENTITY_FIELDS]
        relaxed = dict(schema)
        if required:
            relaxed["required"] = required
        else:
            relaxed.pop("required", None)
        return relaxed

    def build_messages(
        self, ctx: AgentContext, schema: dict[str, Any]
    ) -> list[ChatMessage]:
        snapshot = {
            "symbol": ctx.symbol,
            "interval": ctx.interval,
            "provided_inputs": ctx.inputs,
            "upstream_agent_outputs": ctx.upstream,
        }
        user = json.dumps(snapshot, ensure_ascii=False, default=str, sort_keys=True)
        schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True)
        system = f"{self.system_prompt}\n\n必须满足的 JSON Schema：\n{schema_text}"
        return [ChatMessage("system", system), ChatMessage("user", user)]

    def post_validate(self, output: BaseModel, ctx: AgentContext) -> list[str]:
        """Hook for cross-checking model output against the provided inputs."""

        return []


def cited_inputs_are_known(cited: list[str], ctx: AgentContext) -> list[str]:
    """Return citations that do not correspond to any supplied input field."""

    allowed = set(ctx.inputs)
    allowed |= {f"upstream.{name}" for name in ctx.upstream}
    allowed |= set(ctx.upstream)
    unknown: list[str] = []
    for citation in cited:
        if citation in allowed:
            continue
        root = citation.split(".", 1)[0]
        if root in ctx.inputs or citation.startswith("upstream."):
            continue
        unknown.append(citation)
    return unknown
