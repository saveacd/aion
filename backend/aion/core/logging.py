from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Patterns that must never reach logs.
_SECRET_PATTERNS = [
    re.compile(r"(signature=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(X-MBX-APIKEY\s*[:=]\s*)[^\s\"',]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9/_\-]{12,}", re.IGNORECASE),
    re.compile(r"(api[_-]?secret\s*[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9/_\-+]{12,}", re.IGNORECASE),
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8})[A-Za-z0-9_\-]+"),
    re.compile(r"(sk-[A-Za-z0-9]{8})[A-Za-z0-9]{8,}"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]{12,}=*"),
    re.compile(r"(password\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.IGNORECASE),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + "***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(
                redact(a) if isinstance(a, str) else a for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if re.fullmatch(r"[A-Za-z0-9\-_.]{8,64}", incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def configure_logging(level: str = "INFO", *, json_logs: bool = False) -> None:
    handler = logging.StreamHandler()
    if json_logs:
        import json

        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return json.dumps(
                    {
                        "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                        "level": record.levelname,
                        "logger": record.name,
                        "request_id": getattr(record, "request_id", "-"),
                        "msg": record.getMessage(),
                    },
                    ensure_ascii=False,
                )

        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
                "%Y-%m-%dT%H:%M:%S%z",
            )
        )
    handler.addFilter(RequestIdLogFilter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
