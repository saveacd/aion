"""FastAPI application factory and entry point."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, update
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from aion import __version__
from aion.api.v1.router import api_router
from aion.core.config import Settings, get_settings
from aion.core.db import dispose_engine, get_engine, init_engine
from aion.core.errors import AppError, ErrorCode, app_error_handler, unhandled_error_handler
from aion.core.logging import RequestContextMiddleware, configure_logging, get_logger
from aion.core.redis import close_client, init_client
from aion.core.redis import ping as redis_ping

logger = get_logger(__name__)

_HTTP_STATUS_CODES = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": list(item.get("loc", ())), "msg": item.get("msg", ""), "type": item.get("type", "")}
        for item in exc.errors()
    ]
    error = AppError(
        ErrorCode.VALIDATION_ERROR,
        "Request validation failed.",
        details={"errors": errors},
    )
    return JSONResponse(status_code=422, content=error.payload(_request_id(request)))


async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": {},
                "request_id": _request_id(request),
            }
        },
        headers=getattr(exc, "headers", None),
    )


async def _database_ready() -> bool:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - readiness must report, not raise
        return False
    return True


async def _mark_orphaned_runs() -> None:
    """A run still marked RUNNING at startup was cut off by a restart."""

    from aion.core.db import get_sessionmaker
    from aion.models.agent import AgentRun

    try:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                update(AgentRun)
                .where(AgentRun.status == "RUNNING")
                .values(
                    status="ERROR",
                    finished_at=dt.datetime.now(dt.UTC),
                    error="interrupted by backend restart",
                )
            )
            await session.commit()
            if result.rowcount:
                logger.warning("marked %s orphaned agent run(s) as ERROR", result.rowcount)
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        logger.exception("failed to clean up orphaned agent runs")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(
        level="DEBUG" if settings.debug else "INFO",
        json_logs=settings.is_prod,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_engine(settings)
        init_client(settings)
        await _mark_orphaned_runs()
        research_task: asyncio.Task[None] | None = None
        if os.environ.get("AION_RESEARCH_WORKER_ENABLED", "true").lower() not in ("false", "0", "off"):
            from aion.core.db import get_sessionmaker
            from aion.services.research_worker import recover_interrupted_tasks, run_forever as research_forever

            recovered = await recover_interrupted_tasks(get_sessionmaker())
            if recovered:
                logger.warning("requeued %s stale factor research task(s)", recovered)
            research_task = asyncio.create_task(research_forever(get_sessionmaker(), settings))
        ingest_task: asyncio.Task[None] | None = None
        if os.environ.get("AION_INGEST_ENABLED", "true").lower() not in ("false", "0", "off"):
            from aion.core.db import get_sessionmaker
            from aion.services.market_ingest import run_forever

            interval = int(os.environ.get("AION_INGEST_INTERVAL_SECONDS", "60"))
            ingest_task = asyncio.create_task(
                run_forever(get_sessionmaker(), list(settings.default_symbols), interval)
            )
        autopilot_task: asyncio.Task[None] | None = None
        if os.environ.get("AION_AUTOPILOT_ENABLED", "true").lower() not in ("false", "0", "off"):
            from aion.core.db import get_sessionmaker
            from aion.services.autopilot import run_forever as autopilot_forever

            autopilot_task = asyncio.create_task(
                autopilot_forever(get_sessionmaker(), settings)
            )
        reconcile_task: asyncio.Task[None] | None = None
        if os.environ.get("AION_ACCOUNT_SYNC_ENABLED", "false").lower() not in (
            "false",
            "0",
            "off",
        ):
            from aion.core.db import get_sessionmaker
            from aion.services.live_trading import run_reconcile_forever

            reconcile_task = asyncio.create_task(
                run_reconcile_forever(get_sessionmaker(), settings)
            )
        user_stream_task: asyncio.Task[None] | None = None
        if os.environ.get("AION_USER_STREAM_ENABLED", "false").lower() not in (
            "false",
            "0",
            "off",
        ):
            # Real-time fills/cancels/protection triggers via Binance
            # user-data streams; REST reconcile stays the fallback
            # (follow-up audit #9).
            from aion.core.db import get_sessionmaker
            from aion.services.live_trading import run_streams_forever

            user_stream_task = asyncio.create_task(
                run_streams_forever(get_sessionmaker(), settings)
            )
        stream_task: asyncio.Task[None] | None = None
        if os.environ.get("AION_LIVE_STREAM_ENABLED", "false").lower() not in (
            "false",
            "0",
            "off",
        ):
            from aion.core.db import get_sessionmaker
            from aion.services.market_stream import run_forever as stream_forever

            stream_task = asyncio.create_task(
                stream_forever(get_sessionmaker(), list(settings.default_symbols))
            )
        logger.info("AION backend started (env=%s)", settings.environment.value)
        try:
            yield
        finally:
            for task in (
                research_task,
                ingest_task,
                autopilot_task,
                reconcile_task,
                user_stream_task,
                stream_task,
            ):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            await dispose_engine()
            await close_client()
            logger.info("AION backend stopped")

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        debug=settings.debug,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # Added first => innermost, so request IDs exist before host/CORS checks.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(api_router, prefix=settings.api_prefix.rstrip("/"))

    # Public download folder (APK builds). Only mounted when it exists.
    downloads_dir = Path(__file__).resolve().parents[2] / "dist"
    if downloads_dir.is_dir():
        app.mount("/downloads", StaticFiles(directory=downloads_dir), name="downloads")

    # Mobile web console (single-page, talks to the same /api/v1).
    web_dir = Path(__file__).resolve().parent / "web"
    if web_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=web_dir, html=True), name="ui")

        # Official website (static). Root redirects here.
        site_dir = web_dir / "site"
        if site_dir.is_dir():
            app.mount("/site", StaticFiles(directory=site_dir, html=True), name="site")

            @app.get("/", include_in_schema=False)
            async def root() -> RedirectResponse:
                return RedirectResponse(url="/site/")

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> PlainTextResponse:
        """Minimal Prometheus exposition (aggregate counts only).

        Disabled by default: set ``AION_METRICS_TOKEN`` and have the scraper
        send ``X-Metrics-Token`` to expose it (audit BUG-23 —运营指标不再裸奔).
        """
        token = os.environ.get("AION_METRICS_TOKEN", "")
        if not token or request.headers.get("x-metrics-token") != token:
            return PlainTextResponse("metrics disabled\n", status_code=404)
        from sqlalchemy import func, select

        from aion.core.db import get_sessionmaker
        from aion.models import (
            AutopilotConfig,
            LivePosition,
            Order,
            PaperPosition,
            TradeOutcome,
            User,
        )

        lines: list[str] = ["# HELP aion_up 1 when the process is serving", "# TYPE aion_up gauge", "aion_up 1"]
        try:
            async with get_sessionmaker()() as session:
                async def count(model: Any, *where: Any) -> int:
                    stmt = select(func.count()).select_from(model)
                    for condition in where:
                        stmt = stmt.where(condition)
                    return int((await session.execute(stmt)).scalar_one())

                values = {
                    "users_total": await count(User),
                    "autopilot_enabled": await count(AutopilotConfig, AutopilotConfig.enabled.is_(True)),
                    "auto_execute_enabled": await count(
                        AutopilotConfig, AutopilotConfig.auto_execute.is_(True)
                    ),
                    "live_positions_open": await count(
                        LivePosition, LivePosition.status == "OPEN"
                    ),
                    "paper_positions_open": await count(
                        PaperPosition, PaperPosition.status == "OPEN"
                    ),
                    "orders_total": await count(Order),
                    "trade_outcomes_total": await count(TradeOutcome),
                }
                for name, value in values.items():
                    lines.append(f"# TYPE aion_{name} gauge")
                    lines.append(f"aion_{name} {value}")
        except Exception:  # noqa: BLE001 - metrics must not fail requests
            lines.append("# database unavailable")
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up. No dependency checks."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readyz() -> JSONResponse:
        """Readiness: database and Redis are reachable."""
        database_ok = await _database_ready()
        redis_ok = await redis_ping()
        ready = database_ok and redis_ok
        payload: dict[str, Any] = {
            "status": "ok" if ready else "degraded",
            "database": database_ok,
            "redis": redis_ok,
            "checked_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    return app


app = create_app()
