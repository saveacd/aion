"""Learning loop: close due paper positions, record outcomes, distill lessons.

Deterministic first: position exits (TP/SL/TIMEOUT) and all statistics are pure
Python/SQL — they work with or without a model provider. When a provider is
assigned, every ``LESSON_DISTILL_THRESHOLD`` new outcomes the server asks the
light model to distill lessons, which are then injected into future analysis
inputs (``build_inputs`` → ``learning`` section). LLM failure never breaks the
loop; it is recorded and skipped.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.core.config import Settings
from aion.execution.paper import PaperBroker
from aion.gateway.base import ChatMessage, parse_model_json
from aion.models import AutopilotConfig, LearningLesson, PaperPosition, TradeOutcome
from aion.models.base import utcnow

logger = logging.getLogger(__name__)

D = Decimal

#: A position older than this is closed at market (exit_reason=TIMEOUT).
MAX_HOLDING_HOURS = 48
#: Distill new lessons after this many new outcomes per user.
LESSON_DISTILL_THRESHOLD = 5
#: How many recent outcomes are injected into agent inputs.
RECENT_OUTCOMES_FOR_PROMPT = 10
#: How many active lessons are injected into agent inputs.
ACTIVE_LESSONS_FOR_PROMPT = 5


async def close_due_positions_and_record(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    max_holding_hours: int = MAX_HOLDING_HOURS,
) -> int:
    """Check every OPEN paper position against TP/SL/timeout and close due ones.

    Returns the number of positions closed. One transaction for the whole scan:
    a failure rolls back and is retried on the next tick.
    """
    broker = PaperBroker(session_factory)
    closed = 0
    async with session_factory() as db:
        positions = (
            await db.execute(
                select(PaperPosition).where(PaperPosition.status == "OPEN")
            )
        ).scalars().all()
        if not positions:
            return 0
        cfgs = {
            cfg.user_id: cfg
            for cfg in (
                await db.execute(
                    select(AutopilotConfig).where(
                        AutopilotConfig.user_id.in_({p.user_id for p in positions})
                    )
                )
            ).scalars().all()
        }
        now = utcnow()
        for position in positions:
            cfg = cfgs.get(position.user_id)
            limit_hours = (
                cfg.max_holding_minutes / 60
                if cfg is not None and cfg.intraday
                else max_holding_hours
            )
            mark = await broker.latest_price(db, position.symbol)
            if mark is None:
                continue
            exit_price: Decimal | None = None
            reason: str | None = None
            if position.position_side == "LONG":
                if position.stop_loss is not None and mark <= D(position.stop_loss):
                    exit_price, reason = D(position.stop_loss), "SL"
                elif position.take_profit is not None and mark >= D(position.take_profit):
                    exit_price, reason = D(position.take_profit), "TP"
            else:
                if position.stop_loss is not None and mark >= D(position.stop_loss):
                    exit_price, reason = D(position.stop_loss), "SL"
                elif position.take_profit is not None and mark <= D(position.take_profit):
                    exit_price, reason = D(position.take_profit), "TP"
            if reason is None:
                age_hours = (now - position.opened_at).total_seconds() / 3600
                if age_hours >= limit_hours:
                    exit_price, reason = mark, "TIMEOUT"
            if reason is None or exit_price is None:
                continue
            try:
                await broker.close_position(db, position, exit_price=exit_price, reason=reason)
            except ValueError:
                # Another path (manual close / autopilot) already closed it.
                continue
            closed += 1
            logger.info(
                "paper position %s closed (%s) symbol=%s",
                position.id,
                reason,
                position.symbol,
            )
        if closed:
            await db.commit()
    return closed


async def build_learning_context(
    db: AsyncSession, user_id: uuid.UUID, symbol: str | None = None
) -> dict[str, Any]:
    """Deterministic learning snapshot injected into agent inputs."""
    outcomes = (
        await db.execute(
            select(TradeOutcome)
            .where(TradeOutcome.user_id == user_id)
            .order_by(TradeOutcome.closed_at.desc())
            .limit(RECENT_OUTCOMES_FOR_PROMPT)
        )
    ).scalars().all()
    lessons = (
        await db.execute(
            select(LearningLesson)
            .where(LearningLesson.user_id == user_id, LearningLesson.active.is_(True))
            .order_by(LearningLesson.score.desc(), LearningLesson.created_at.desc())
            .limit(ACTIVE_LESSONS_FOR_PROMPT)
        )
    ).scalars().all()
    stats = await _aggregate_stats(db, user_id)

    return {
        "total_outcomes": stats["total"],
        "win_rate": stats["win_rate"],
        "avg_r_multiple": stats["avg_r"],
        "total_pnl_usd": stats["total_pnl"],
        "by_symbol": stats["by_symbol"],
        "recent_outcomes": [
            {
                "symbol": o.symbol,
                "position_side": o.position_side,
                "pnl_usd": str(o.pnl_usd),
                "r_multiple": str(o.r_multiple) if o.r_multiple is not None else None,
                "exit_reason": o.exit_reason,
                "regime": o.regime,
                "confidence": str(o.confidence) if o.confidence is not None else None,
                "holding_minutes": (
                    str(o.holding_minutes) if o.holding_minutes is not None else None
                ),
                "closed_at": o.closed_at.isoformat(),
            }
            for o in outcomes
        ],
        "active_lessons": [
            {"lesson": lesson.lesson, "category": lesson.category, "tags": list(lesson.tags or [])}
            for lesson in lessons
        ],
    }


async def _aggregate_stats(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(TradeOutcome).where(TradeOutcome.user_id == user_id)
        )
    ).scalars().all()
    total = len(rows)
    wins = sum(1 for o in rows if D(o.pnl_usd) > 0)
    r_values = [D(o.r_multiple) for o in rows if o.r_multiple is not None]
    by_symbol: dict[str, dict[str, Any]] = {}
    for outcome in rows:
        bucket = by_symbol.setdefault(
            outcome.symbol, {"trades": 0, "wins": 0, "pnl_usd": D("0")}
        )
        bucket["trades"] += 1
        bucket["wins"] += 1 if D(outcome.pnl_usd) > 0 else 0
        bucket["pnl_usd"] += D(outcome.pnl_usd)
    return {
        "total": total,
        "wins": wins,
        "win_rate": (round(wins / total, 4) if total else None),
        "avg_r": (
            str(sum(r_values) / len(r_values)) if r_values else None
        ),
        "total_pnl": str(sum((D(o.pnl_usd) for o in rows), D("0"))),
        "by_symbol": {
            symbol: {
                "trades": b["trades"],
                "win_rate": round(b["wins"] / b["trades"], 4) if b["trades"] else None,
                "pnl_usd": str(b["pnl_usd"]),
            }
            for symbol, b in sorted(by_symbol.items())
        },
    }


_DISTILL_SYSTEM = """你是 AION 交易系统的「经验提炼器」。根据用户最近的模拟盘交易结果，总结最多 5 条可用于未来交易决策的经验教训。
硬性规则：
1. 只基于输入 JSON 中真实存在的交易结果，不得编造数据。
2. 每条经验必须具体、可执行、可被未来的分析 Agent 使用（例如“在 RANGING 市场中降低置信度阈值”）。
3. 只输出一个 JSON 对象：{"lessons": [{"lesson": "...", "category": "...", "tags": ["..."]}]}，不要输出 Markdown 或解释。
4. category 取值：entry_timing | risk_control | regime_filter | symbol_selection | exit_management | general。
5. 如果交易结果太少或没有明显规律，输出更少的经验，不要硬凑。"""


async def maybe_distill_lessons(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: uuid.UUID,
) -> bool:
    """Distill new lessons when enough new outcomes exist. Never raises."""
    from aion.services.agent_service import build_gateway, resolve_provider_config

    try:
        async with session_factory() as db:
            total = int(
                await db.scalar(
                    select(func.count())
                    .select_from(TradeOutcome)
                    .where(TradeOutcome.user_id == user_id)
                )
                or 0
            )
            distilled = int(
                await db.scalar(
                    select(func.coalesce(func.max(LearningLesson.outcomes_count), 0)).where(
                        LearningLesson.user_id == user_id,
                        LearningLesson.source == "llm_distill",
                    )
                )
                or 0
            )
            if total - distilled < LESSON_DISTILL_THRESHOLD:
                return False
            provider = await resolve_provider_config(db, user_id)
            if provider is None:
                logger.info("learning: user %s has no provider, skip distillation", user_id)
                return False
            outcomes = (
                await db.execute(
                    select(TradeOutcome)
                    .where(TradeOutcome.user_id == user_id)
                    .order_by(TradeOutcome.closed_at.desc())
                    .limit(30)
                )
            ).scalars().all()
            stats = await _aggregate_stats(db, user_id)

        gateway = build_gateway(settings, session_factory, provider)
        payload = {
            "统计": stats,
            "最近交易结果": [
                {
                    "symbol": o.symbol,
                    "方向": o.position_side,
                    "盈亏USD": str(o.pnl_usd),
                    "R倍数": str(o.r_multiple) if o.r_multiple is not None else None,
                    "平仓原因": o.exit_reason,
                    "市场状态": o.regime,
                    "置信度": str(o.confidence) if o.confidence is not None else None,
                    "持仓分钟": str(o.holding_minutes) if o.holding_minutes is not None else None,
                }
                for o in outcomes
            ],
        }
        messages = [
            ChatMessage("system", _DISTILL_SYSTEM),
            ChatMessage("user", json.dumps(payload, ensure_ascii=False, default=str)),
        ]
        run_id = uuid.uuid4()
        response = await gateway.call(
            "learning", messages, None, run_id=run_id, max_tokens=1200, purpose="learning_distill"
        )
        parsed = parse_model_json(response.text)
        lessons_raw = parsed.get("lessons") if isinstance(parsed, dict) else None
        if not isinstance(lessons_raw, list) or not lessons_raw:
            logger.warning("learning: distillation returned no lessons for user %s", user_id)
            return False

        added = 0
        async with session_factory() as db:
            for item in lessons_raw[:5]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("lesson") or "").strip()
                if not text:
                    continue
                db.add(
                    LearningLesson(
                        user_id=user_id,
                        lesson=text[:2000],
                        category=str(item.get("category") or "general")[:32],
                        tags=[str(t)[:32] for t in (item.get("tags") or [])][:8]
                        if isinstance(item.get("tags"), list)
                        else [],
                        score=D("0"),
                        source="llm_distill",
                        outcomes_count=total,
                        active=True,
                    )
                )
                added += 1
            await db.commit()
        logger.info("learning: distilled %s lessons for user %s", added, user_id)
        return added > 0
    except Exception:  # noqa: BLE001 - learning must never break trading loops
        logger.exception("learning: distillation failed for user %s", user_id)
        return False


async def performance_report(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Full performance payload for the learning API page."""
    rows = (
        await db.execute(
            select(TradeOutcome)
            .where(TradeOutcome.user_id == user_id)
            .order_by(TradeOutcome.closed_at.asc())
        )
    ).scalars().all()
    stats = await _aggregate_stats(db, user_id)

    today = utcnow().date()
    today_pnl = sum((D(o.pnl_usd) for o in rows if o.closed_at.date() == today), D("0"))
    by_reason: dict[str, int] = {}
    for outcome in rows:
        by_reason[outcome.exit_reason] = by_reason.get(outcome.exit_reason, 0) + 1

    equity = D("0")
    curve: list[dict[str, str]] = []
    for outcome in rows:
        equity += D(outcome.pnl_usd)
        curve.append(
            {"t": outcome.closed_at.isoformat(), "pnl": str(outcome.pnl_usd), "equity": str(equity)}
        )

    return {
        "total_trades": stats["total"],
        "wins": stats["wins"],
        "win_rate": stats["win_rate"],
        "avg_r_multiple": stats["avg_r"],
        "total_pnl_usd": stats["total_pnl"],
        "today_pnl_usd": str(today_pnl),
        "by_symbol": stats["by_symbol"],
        "by_exit_reason": by_reason,
        "equity_curve": curve[-200:],
        "recent_outcomes": [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "position_side": o.position_side,
                "quantity": str(o.quantity),
                "entry_price": str(o.entry_price),
                "exit_price": str(o.exit_price),
                "pnl_usd": str(o.pnl_usd),
                "r_multiple": str(o.r_multiple) if o.r_multiple is not None else None,
                "exit_reason": o.exit_reason,
                "regime": o.regime,
                "confidence": str(o.confidence) if o.confidence is not None else None,
                "opened_at": o.opened_at.isoformat(),
                "closed_at": o.closed_at.isoformat(),
            }
            for o in reversed(rows[-20:])
        ],
    }


__all__ = [
    "ACTIVE_LESSONS_FOR_PROMPT",
    "LESSON_DISTILL_THRESHOLD",
    "MAX_HOLDING_HOURS",
    "RECENT_OUTCOMES_FOR_PROMPT",
    "build_learning_context",
    "close_due_positions_and_record",
    "maybe_distill_lessons",
    "performance_report",
]
