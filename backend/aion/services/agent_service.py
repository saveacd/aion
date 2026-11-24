"""Construction and execution of one AI analysis cycle.

The orchestrator is expensive and slow (tens of seconds to minutes), so
``/agents/analyze`` starts it as a background task and returns a run id that the
client polls through ``GET /agents/runs/{id}``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.agents.base import AgentContext, DbRunRecorder
from aion.agents.orchestrator import Orchestrator, OrchestratorResult
from aion.agents.specialists import (
    AdversarialReviewAgent,
    ChiefDecisionAgent,
    DerivativesAgent,
    MarketRegimeAgent,
    StrategyResearchAgent,
    TechnicalAnalysisAgent,
)
from aion.core.config import Settings, get_settings
from aion.core.crypto import SecretBox
from aion.core.redis import get_redis
from aion.gateway.anthropic import AnthropicProvider
from aion.gateway.budget import BudgetLedger
from aion.gateway.circuit import CircuitBreaker
from aion.gateway.gateway import DEFAULT_ROUTES, ModelGateway, Route
from aion.gateway.openai_compat import OpenAICompatProvider
from aion.models import (
    AccountSnapshot,
    DerivativesSnapshot,
    MarketCandle,
    MarketSnapshot,
    ModelProvider,
    PositionSnapshot,
    UserModelSetting,
)
from aion.quant import atr, ema, macd, rsi
from aion.risk.service import build_context

D = Decimal


class AgentProviderUnavailable(RuntimeError):
    """No model provider is configured; analysis cannot run honestly."""


@dataclasses.dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Resolved, ready-to-use provider for one user's analysis run."""

    kind: str
    api_key: str
    base_url: str | None
    light_model: str | None
    heavy_model: str | None
    daily_budget_usd: Decimal | None = None
    # role -> model overrides, e.g. {"market_regime": "deepseek-chat"}.
    role_models: dict[str, str] | None = None


async def resolve_provider_config(
    db: AsyncSession, user_id: uuid.UUID
) -> ProviderConfig | None:
    """Return the provider for ``user_id``.

    Administrators can pin a provider per user. Public deployments without a
    per-user assignment fall back to the first enabled provider so a newly
    registered account can use the six-agent analysis immediately.
    """
    setting = (
        await db.execute(select(UserModelSetting).where(UserModelSetting.user_id == user_id))
    ).scalar_one_or_none()
    if setting is not None and setting.enabled and setting.provider_id is not None:
        provider = await db.get(ModelProvider, setting.provider_id)
        if provider is None or not provider.is_enabled or not provider.api_key_enc:
            return None
        key = SecretBox(get_settings()).try_decrypt(provider.api_key_enc)
        if not key:
            return None
        return ProviderConfig(
            kind=provider.kind,
            api_key=key,
            base_url=provider.base_url,
            light_model=setting.light_model or provider.light_model or None,
            heavy_model=setting.heavy_model or provider.heavy_model or None,
            daily_budget_usd=Decimal(str(setting.daily_budget_usd)),
            role_models=dict(setting.role_models or {}),
        )

    provider = await db.scalar(
        select(ModelProvider)
        .where(ModelProvider.is_enabled.is_(True), ModelProvider.api_key_enc.isnot(None))
        .order_by(ModelProvider.created_at.asc())
        .limit(1)
    )
    if provider is None or not provider.api_key_enc:
        return None
    key = SecretBox(get_settings()).try_decrypt(provider.api_key_enc)
    if not key:
        return None
    return ProviderConfig(
        kind=provider.kind,
        api_key=key,
        base_url=provider.base_url,
        light_model=provider.light_model or None,
        heavy_model=provider.heavy_model or None,
        daily_budget_usd=Decimal(str(get_settings().model_daily_budget_usd)),
    )


def _routes(
    default_provider: str,
    light: str | None = None,
    heavy: str | None = None,
    role_models: dict[str, str] | None = None,
) -> dict[str, Route]:
    """Allow per-deployment and per-agent model overrides without code changes."""
    light = (light or os.environ.get("AION_MODEL_LIGHT", "")).strip()
    heavy = (heavy or os.environ.get("AION_MODEL_HEAVY", "")).strip()
    overrides = {
        role: model.strip()
        for role, model in (role_models or {}).items()
        if role in DEFAULT_ROUTES and isinstance(model, str) and model.strip()
    }
    routes: dict[str, Route] = {}
    for role, route in DEFAULT_ROUTES.items():
        model = overrides.get(role, heavy if role in ("chief_decision", "strategy_research") else light)
        routes[role] = Route(default_provider, model) if model else Route(default_provider, route.model)
    # The learning loop uses the light route; it is not part of DEFAULT_ROUTES.
    routes["learning"] = routes["market_regime"]
    return routes


PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "xai": "https://api.x.ai/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "minimax": "https://api.minimax.chat/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


def build_gateway(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    provider_config: ProviderConfig | None = None,
) -> ModelGateway:
    providers: dict[str, Any] = {}
    if provider_config is not None:
        timeout = settings.agent_run_timeout_seconds / 3
        if provider_config.base_url:
            base_url = provider_config.base_url
        elif provider_config.kind == "anthropic":
            base_url = settings.anthropic_base_url
        else:
            base_url = PROVIDER_BASE_URLS.get(
                provider_config.kind, settings.openai_base_url
            )
        provider_key = (
            "anthropic" if provider_config.kind == "anthropic" else provider_config.kind
        )
        if provider_config.kind == "anthropic":
            providers["anthropic"] = AnthropicProvider(
                api_key=provider_config.api_key, base_url=base_url, timeout=timeout
            )
        else:
            providers[provider_key] = OpenAICompatProvider(
                api_key=provider_config.api_key, base_url=base_url, timeout=timeout
            )
        default_provider = provider_key
        light = provider_config.light_model
        heavy = provider_config.heavy_model
        daily_budget = (
            float(provider_config.daily_budget_usd)
            if provider_config.daily_budget_usd is not None
            else settings.model_daily_budget_usd
        )
    else:
        if settings.anthropic_api_key:
            providers["anthropic"] = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
                timeout=settings.agent_run_timeout_seconds / 3,
            )
        if settings.openai_api_key:
            providers["openai"] = OpenAICompatProvider(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout=settings.agent_run_timeout_seconds / 3,
            )
        if not providers:
            raise AgentProviderUnavailable(
                "未配置任何模型供应商（AION_ANTHROPIC_API_KEY / AION_OPENAI_API_KEY），"
                "无法运行 AI 分析。"
            )
        default_provider = "anthropic" if "anthropic" in providers else "openai"
        light = None
        heavy = None
        daily_budget = settings.model_daily_budget_usd

    redis = get_redis()
    return ModelGateway(
        providers,
        BudgetLedger(
            redis,
            daily_budget_usd=daily_budget,
            run_budget_usd=settings.model_run_budget_usd,
            session_factory=session_factory,
        ),
        CircuitBreaker(redis),
        routes=_routes(
            default_provider,
            light,
            heavy,
            provider_config.role_models if provider_config else None,
        ),
    )


def build_orchestrator(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    provider_config: ProviderConfig | None = None,
) -> Orchestrator:
    gateway = build_gateway(settings, session_factory, provider_config)
    recorder = DbRunRecorder(session_factory)
    agents = [
        MarketRegimeAgent(gateway, recorder),
        TechnicalAnalysisAgent(gateway, recorder),
        DerivativesAgent(gateway, recorder),
        StrategyResearchAgent(gateway, recorder),
        ChiefDecisionAgent(gateway, recorder),
        AdversarialReviewAgent(gateway, recorder),
    ]
    return Orchestrator(gateway, agents, recorder=recorder, settings=settings)


def _series(candles: Sequence[MarketCandle], field: str) -> list[float]:
    return [float(getattr(c, field)) for c in candles]


def _recent_bars(candles: Sequence[MarketCandle], limit: int = 40) -> list[dict[str, Any]]:
    """Compact OHLCV tail so agents actually see the K-line, not just aggregates."""
    tail = list(candles)[-limit:]
    return [
        {
            "t": c.open_time.isoformat(),
            "o": str(c.open),
            "h": str(c.high),
            "l": str(c.low),
            "c": str(c.close),
            "v": str(c.volume),
        }
        for c in tail
    ]


async def build_inputs(
    db: AsyncSession,
    symbol: str,
    interval: str,
    user_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
    intraday: bool | None = None,
    max_holding_minutes: int | None = None,
) -> dict[str, Any]:
    """Assemble everything the agents may see. Missing data is declared, not filled."""
    snapshot = await db.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == symbol)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(1)
    )
    from aion.services.market_stream import fetch_live_klines, latest_price

    live_tick = latest_price(symbol)
    live_bars = await fetch_live_klines(symbol, interval="1m", limit=60)
    candle_rows = (
        await db.execute(
            select(MarketCandle)
            .where(
                MarketCandle.symbol == symbol,
                MarketCandle.interval == interval,
                MarketCandle.is_final.is_(True),
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(300)
        )
    ).scalars().all()
    candles = list(reversed(candle_rows))

    htf_interval = "1h"
    htf_rows = (
        await db.execute(
            select(MarketCandle)
            .where(
                MarketCandle.symbol == symbol,
                MarketCandle.interval == htf_interval,
                MarketCandle.is_final.is_(True),
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(120)
        )
    ).scalars().all()
    htf_candles = list(reversed(htf_rows))

    derivatives = await db.scalar(
        select(DerivativesSnapshot)
        .where(DerivativesSnapshot.symbol == symbol)
        .order_by(DerivativesSnapshot.captured_at.desc())
        .limit(1)
    )
    account_stmt = select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
    if user_id is not None:
        account_stmt = (
            select(AccountSnapshot)
            .where(AccountSnapshot.user_id == user_id)
            .order_by(AccountSnapshot.captured_at.desc())
            .limit(1)
        )
    account = await db.scalar(account_stmt)
    positions = []
    if account is not None:
        position_stmt = select(PositionSnapshot).where(
            PositionSnapshot.captured_at == account.captured_at
        )
        if user_id is not None:
            position_stmt = position_stmt.where(PositionSnapshot.user_id == user_id)
        positions = (await db.execute(position_stmt)).scalars().all()

    closes = _series(candles, "close")
    highs = _series(candles, "high")
    lows = _series(candles, "low")

    indicators: dict[str, Any] = {
        "ema20": _guarded(lambda: _tail(ema(closes, 20))),
        "ema50": _guarded(lambda: _tail(ema(closes, 50))),
        "rsi14": _guarded(lambda: _tail(rsi(closes, 14))),
        "atr14": _guarded(lambda: _tail(atr(highs, lows, closes, 14))),
        "macd_hist": None,
        "macd_line": None,
        "macd_signal": None,
    }
    if len(closes) >= 35:
        macd_line, signal_line, hist = macd(closes)
        indicators["macd_hist"] = _tail(list(hist))
        indicators["macd_line"] = _tail(list(macd_line))
        indicators["macd_signal"] = _tail(list(signal_line))

    htf_indicators: dict[str, Any] = {}
    if htf_candles:
        htf_closes = _series(htf_candles, "close")
        htf_highs = _series(htf_candles, "high")
        htf_lows = _series(htf_candles, "low")
        htf_indicators = {
            "ema20": _guarded(lambda: _tail(ema(htf_closes, 20))),
            "ema50": _guarded(lambda: _tail(ema(htf_closes, 50))),
            "rsi14": _guarded(lambda: _tail(rsi(htf_closes, 14))),
            "atr14": _guarded(lambda: _tail(atr(htf_highs, htf_lows, htf_closes, 14))),
        }

    ctx, limits = await build_context(db, symbol=symbol, mode="PAPER", user_id=user_id)

    learning: dict[str, Any] | None = None
    if user_id is not None:
        from aion.services.learning import build_learning_context

        learning = await build_learning_context(db, user_id, symbol)

    factor_signals: dict[str, Any] = {
        "available": False,
        "factor_count": 0,
        "composite_signal": None,
        "bias": "NO_VALIDATED_FACTORS",
        "factors": [],
        "note": "没有可用的已验证 ACTIVE 因子；不得虚构因子信号。",
    }
    if user_id is not None and candles:
        from aion.models.factors import FactorRecord
        from aion.quant.factor_signals import build_factor_signals

        active_factor_rows = (
            await db.execute(
                select(FactorRecord)
                .where(
                    FactorRecord.user_id == user_id,
                    FactorRecord.symbol == symbol,
                    FactorRecord.interval == interval,
                    FactorRecord.status == "ACTIVE",
                )
                .order_by(FactorRecord.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        factor_signals = build_factor_signals(active_factor_rows, candles)

    from aion.quant.factor_ensemble import detect_market_regime
    from aion.quant.strategy_evolution import build_strategy_evolution
    market_regime = detect_market_regime(candles)
    strategy_evolution = build_strategy_evolution(factor_signals, market_regime, learning)

    strategy_section: dict[str, Any] | None = None
    strategies_available: list[dict[str, Any]] = []
    if user_id is not None:
        from aion.models import Strategy, StrategyVersion

        owned = (
            await db.execute(
                select(Strategy)
                .where(Strategy.user_id == user_id)
                .order_by(Strategy.updated_at.desc())
                .limit(20)
            )
        ).scalars().all()
        strategies_available = [
            {
                "id": str(s.id),
                "name": s.name,
                "state": s.state,
                "symbols": list(s.symbols or []),
                "interval": s.interval,
            }
            for s in owned
        ]
        chosen = next((s for s in owned if strategy_id is not None and s.id == strategy_id), None)
        if chosen is not None:
            version = (
                await db.get(StrategyVersion, chosen.active_version_id)
                if chosen.active_version_id
                else None
            )
            strategy_section = {
                "id": str(chosen.id),
                "name": chosen.name,
                "state": chosen.state,
                "interval": chosen.interval,
                "params": (version.params if version else {}),
                "risk_assumptions": (version.risk_assumptions if version else {}),
                "invalidation": (version.invalidation if version else None),
            }

    missing = [
        name
        for name, value in (
            ("market_snapshot", snapshot or live_tick),
            ("candles", candles),
            ("candles_1h", htf_candles),
            ("derivatives", derivatives),
            ("account", account),
        )
        if not value
    ]
    if learning is not None and not learning["total_outcomes"] and not learning["active_lessons"]:
        missing.append("learning_history")

    return {
        "symbol": symbol,
        "interval": interval,
        "market": (
            {
                "last_price": str(
                    live_tick["price"] if live_tick else snapshot.last_price
                ),
                "mark_price": str(snapshot.mark_price) if snapshot and snapshot.mark_price is not None else None,
                "index_price": (
                    str(snapshot.index_price) if snapshot and snapshot.index_price is not None else None
                ),
                "volume_24h": (
                    str(snapshot.volume_24h) if snapshot and snapshot.volume_24h is not None else None
                ),
                "captured_at": (
                    live_tick["ts"].isoformat()
                    if live_tick
                    else snapshot.captured_at.isoformat()
                ),
                "source": "binance_live" if live_tick else "snapshot",
            }
            if (snapshot or live_tick)
            else None
        ),
        "realtime": (
            {
                "last_price": str(live_tick["price"]),
                "change_pct_24h": (
                    str(live_tick["change_pct"])
                    if live_tick.get("change_pct") is not None
                    else None
                ),
                "captured_at": live_tick["ts"].isoformat(),
                "source": "binance_live_ticker",
                "live_1m": live_bars,
                "live_1m_count": len(live_bars),
                "note": "1 分钟 K 线直接来自 Binance，包含当前尚未收盘的 K 线；分析以这里的最后价格为基准。",
            }
            if live_tick
            else None
        ),
        "candles": {
            "count": len(candles),
            "last_close": str(candles[-1].close) if candles else None,
            "last_open_time": candles[-1].open_time.isoformat() if candles else None,
            "high_100": str(max((c.high for c in candles[-100:]), default=D("0"))),
            "low_100": str(min((c.low for c in candles[-100:]), default=D("0"))),
            "recent": _recent_bars(candles),
        },
        "higher_timeframe": (
            {
                "interval": htf_interval,
                "count": len(htf_candles),
                "last_close": str(htf_candles[-1].close),
                "indicators": htf_indicators,
                "recent": _recent_bars(htf_candles, 24),
            }
            if htf_candles
            else None
        ),
        "session": (
            {
                "mode": "INTRADAY",
                "max_holding_minutes": int(max_holding_minutes or 240),
                "note": "日内模式：只做当天可平仓的机会，避免隔夜持仓风险。",
            }
            if intraday
            else None
        ),
        "indicators": indicators,
        "derivatives": (
            {
                "funding_rate": (
                    str(derivatives.funding_rate) if derivatives.funding_rate is not None else None
                ),
                "open_interest": (
                    str(derivatives.open_interest) if derivatives.open_interest is not None else None
                ),
                "basis_pct": str(derivatives.basis_pct) if derivatives.basis_pct is not None else None,
            }
            if derivatives
            else None
        ),
        "account": (
            {
                "equity_usd": str(account.total_wallet_balance),
                "available_usd": str(account.available_balance),
                "unrealized_pnl_usd": str(account.total_unrealized_pnl),
                "open_positions": [
                    {
                        "symbol": p.symbol,
                        "side": p.position_side,
                        "qty": str(p.quantity),
                        "entry": str(p.entry_price),
                        "unrealized_pnl": str(p.unrealized_pnl),
                    }
                    for p in positions
                ],
            }
            if account
            else None
        ),
        "constraints": {
            "mode": "PAPER",
            "max_leverage": limits.max_leverage,
            "max_position_notional_usd": str(limits.max_position_notional_usd),
            "max_total_exposure_usd": str(limits.max_total_exposure_usd),
            "required_stop_loss": limits.required_stop_loss,
            "allowed_symbols": list(ctx.allowed_symbols),
        },
        "learning": learning,
        "factor_signals": factor_signals,
        "market_regime": market_regime,
        "strategy_evolution": strategy_evolution,
        "strategy": strategy_section,
        "strategies_available": strategies_available,
        "data_missing": missing,
    }


def _tail(values: Sequence[float | None]) -> float | None:
    if not values:
        return None
    last = values[-1]
    return float(last) if last is not None else None


def _guarded(fn: Any) -> float | None:
    """Indicators need a minimum sample; report "not available" instead of raising."""
    try:
        return fn()
    except ValueError:
        return None


async def run_analysis(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    interval: str,
    run_id: uuid.UUID,
    timeout_seconds: int,
    provider_config: ProviderConfig | None = None,
    user_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
    intraday: bool | None = None,
    max_holding_minutes: int | None = None,
) -> OrchestratorResult | None:
    """Execute one cycle end to end. Returns None when the run could not start."""
    try:
        orchestrator = build_orchestrator(settings, session_factory, provider_config)
    except AgentProviderUnavailable:
        return None

    async with session_factory() as db:
        inputs = await build_inputs(
            db,
            symbol,
            interval,
            user_id=user_id,
            strategy_id=strategy_id,
            intraday=intraday,
            max_holding_minutes=max_holding_minutes,
        )

    ctx = AgentContext(symbol=symbol, interval=interval, run_id=run_id, inputs=inputs)
    try:
        return await asyncio.wait_for(
            orchestrator.analyze(ctx), timeout=max(timeout_seconds, 30)
        )
    except TimeoutError:
        return None
