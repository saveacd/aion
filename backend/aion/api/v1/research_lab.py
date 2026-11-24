"""Research lab and isolated PAPER ledger APIs (never call the exchange)."""
from __future__ import annotations
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, Literal
from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import select
from aion.api.deps import CurrentUser, DbSession, record_audit
from aion.core.errors import AppError, ErrorCode
from aion.models.research import StrategyCandidate, PaperStrategySession, PaperTradeRecord, FactorEnsembleVersion, StrategyApproval, StrategyBacktest
from aion.schemas.common import AionModel, ORMModel

router = APIRouter(prefix="/research-lab", tags=["research-lab"])


class CandidateCreate(AionModel):
    name: str = Field(min_length=3, max_length=96)
    specification: dict[str, Any] = Field(default_factory=dict)


class CandidateOut(ORMModel):
    id: uuid.UUID
    name: str
    status: str
    specification: dict[str, Any]
    evaluation: dict[str, Any]
    created_at: dt.datetime


class PaperSessionCreate(AionModel):
    name: str = Field(min_length=3, max_length=96)
    initial_cash: Decimal = Field(gt=0, le=Decimal("1000000000"))
    candidate_id: uuid.UUID | None = None


class PaperSessionOut(ORMModel):
    id: uuid.UUID
    name: str
    status: str
    initial_cash: Decimal
    current_cash: Decimal
    metrics: dict[str, Any]
    created_at: dt.datetime


class PaperTradeCreate(AionModel):
    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]{3,32}$")
    side: Literal["LONG", "SHORT"]
    quantity: Decimal = Field(gt=0, le=Decimal("1000000000"))
    entry_price: Decimal = Field(gt=0, le=Decimal("1000000000"))
    fees: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("1000000"))


class PaperTradeClose(AionModel):
    exit_price: Decimal = Field(gt=0, le=Decimal("1000000000"))
    exit_fee: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("1000000"))


class PaperTradeOut(ORMModel):
    id: uuid.UUID
    session_id: uuid.UUID
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal | None
    fees: Decimal
    pnl: Decimal | None
    status: str
    opened_at: dt.datetime
    closed_at: dt.datetime | None


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(user: CurrentUser, db: DbSession):
    rows = (await db.execute(
        select(StrategyCandidate).where(StrategyCandidate.user_id == user.id)
        .order_by(StrategyCandidate.created_at.desc()).limit(200)
    )).scalars().all()
    return [CandidateOut.model_validate(row) for row in rows]


@router.post("/candidates", response_model=CandidateOut, status_code=201)
async def create_candidate(body: CandidateCreate, request: Request, user: CurrentUser, db: DbSession):
    # Candidates are research-only records. Their payload cannot be executed.
    row = StrategyCandidate(
        user_id=user.id, name=body.name, status="PROPOSED",
        specification=body.specification, evaluation={"execution_enabled": False},
    )
    db.add(row)
    await db.flush()
    await record_audit(db, actor=user.username, action="research_candidate.created",
                       target=str(row.id), after={"name": row.name, "status": row.status}, request=request)
    await db.commit()
    await db.refresh(row)
    return CandidateOut.model_validate(row)


@router.get("/paper/sessions", response_model=list[PaperSessionOut])
async def list_paper_sessions(user: CurrentUser, db: DbSession):
    rows = (await db.execute(
        select(PaperStrategySession).where(PaperStrategySession.user_id == user.id)
        .order_by(PaperStrategySession.created_at.desc()).limit(200)
    )).scalars().all()
    return [PaperSessionOut.model_validate(row) for row in rows]


@router.post("/paper/sessions", response_model=PaperSessionOut, status_code=201)
async def create_paper_session(body: PaperSessionCreate, request: Request, user: CurrentUser, db: DbSession):
    candidate = None
    if body.candidate_id is not None:
        candidate = await db.scalar(select(StrategyCandidate).where(
            StrategyCandidate.id == body.candidate_id,
            StrategyCandidate.user_id == user.id,
        ))
        if candidate is None:
            raise AppError(ErrorCode.NOT_FOUND, "Strategy candidate not found.")
        candidate_gate = ((candidate.evaluation or {}).get("backtest") or {}).get("evaluation") or {}
        if not candidate_gate.get("passed"):
            raise AppError(ErrorCode.CONFLICT, "Candidate has not passed the out-of-sample research gates.")
    row = PaperStrategySession(
        user_id=user.id, candidate_id=body.candidate_id, name=body.name, status="OBSERVING",
        initial_cash=body.initial_cash, current_cash=body.initial_cash,
        metrics={"realized_pnl": "0", "trades": 0, "execution_mode": "PAPER", "max_leverage": "1x"},
    )
    db.add(row)
    await db.flush()
    await record_audit(db, actor=user.username, action="paper_session.created",
                       target=str(row.id), after={"name": row.name, "initial_cash": str(row.initial_cash)}, request=request)
    await db.commit()
    await db.refresh(row)
    return PaperSessionOut.model_validate(row)


async def _owned_session(session_id: uuid.UUID, user: CurrentUser, db):
    row = await db.scalar(select(PaperStrategySession).where(
        PaperStrategySession.id == session_id, PaperStrategySession.user_id == user.id
    ))
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "PAPER session not found.")
    if row.status != "OBSERVING":
        raise AppError(ErrorCode.CONFLICT, "PAPER session is not accepting trades.")
    return row


@router.get("/paper/sessions/{session_id}/trades", response_model=list[PaperTradeOut])
async def list_paper_trades(session_id: uuid.UUID, user: CurrentUser, db: DbSession):
    await _owned_session(session_id, user, db)
    rows = (await db.execute(
        select(PaperTradeRecord).where(
            PaperTradeRecord.session_id == session_id, PaperTradeRecord.user_id == user.id
        ).order_by(PaperTradeRecord.opened_at.desc()).limit(500)
    )).scalars().all()
    return [PaperTradeOut.model_validate(row) for row in rows]


@router.post("/paper/sessions/{session_id}/trades", response_model=PaperTradeOut, status_code=201)
async def open_paper_trade(session_id: uuid.UUID, body: PaperTradeCreate, request: Request, user: CurrentUser, db: DbSession):
    session = await db.scalar(select(PaperStrategySession).where(
        PaperStrategySession.id == session_id,
        PaperStrategySession.user_id == user.id,
    ).with_for_update())
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, "PAPER session not found.")
    if session.status != "OBSERVING":
        raise AppError(ErrorCode.CONFLICT, "PAPER session is not accepting trades.")
    open_rows = (await db.execute(select(PaperTradeRecord).where(
        PaperTradeRecord.session_id == session_id,
        PaperTradeRecord.user_id == user.id,
        PaperTradeRecord.status == "OPEN",
    ))).scalars().all()
    used_notional = sum((Decimal(item.quantity) * Decimal(item.entry_price) for item in open_rows), Decimal("0"))
    requested_notional = body.quantity * body.entry_price
    if used_notional + requested_notional > Decimal(session.current_cash):
        raise AppError(ErrorCode.CONFLICT, "PAPER exposure exceeds available cash; leverage is disabled (1x).")
    row = PaperTradeRecord(
        user_id=user.id, session_id=session_id, symbol=body.symbol.upper(),
        side=body.side, quantity=body.quantity, entry_price=body.entry_price,
        fees=body.fees, status="OPEN",
    )
    db.add(row)
    await db.flush()
    await record_audit(db, actor=user.username, action="paper_trade.opened",
                       target=str(row.id), after={"session_id": str(session_id), "symbol": row.symbol,
                       "side": row.side, "quantity": str(row.quantity), "entry_price": str(row.entry_price)},
                       request=request)
    await db.commit()
    await db.refresh(row)
    return PaperTradeOut.model_validate(row)


@router.post("/paper/sessions/{session_id}/trades/{trade_id}/close", response_model=PaperTradeOut)
async def close_paper_trade(
    session_id: uuid.UUID, trade_id: uuid.UUID, body: PaperTradeClose,
    request: Request, user: CurrentUser, db: DbSession,
):
    session = await _owned_session(session_id, user, db)
    trade = await db.scalar(select(PaperTradeRecord).where(
        PaperTradeRecord.id == trade_id, PaperTradeRecord.session_id == session_id,
        PaperTradeRecord.user_id == user.id,
    ))
    if trade is None:
        raise AppError(ErrorCode.NOT_FOUND, "PAPER trade not found.")
    if trade.status != "OPEN":
        raise AppError(ErrorCode.CONFLICT, "PAPER trade is already closed.")
    direction = Decimal("1") if trade.side == "LONG" else Decimal("-1")
    gross = (body.exit_price - trade.entry_price) * trade.quantity * direction
    total_fees = trade.fees + body.exit_fee
    realized = gross - total_fees
    trade.exit_price = body.exit_price
    trade.fees = total_fees
    trade.pnl = realized
    trade.status = "CLOSED"
    trade.closed_at = dt.datetime.now(dt.UTC)
    session.current_cash = Decimal(session.current_cash) + realized
    metrics = dict(session.metrics or {})
    metrics["realized_pnl"] = str(Decimal(metrics.get("realized_pnl", "0")) + realized)
    metrics["trades"] = int(metrics.get("trades", 0)) + 1
    metrics["execution_mode"] = "PAPER"
    session.metrics = metrics
    await record_audit(db, actor=user.username, action="paper_trade.closed",
                       target=str(trade.id), after={"exit_price": str(body.exit_price),
                       "realized_pnl": str(realized), "fees": str(total_fees)}, request=request)
    await db.commit()
    await db.refresh(trade)
    return PaperTradeOut.model_validate(trade)


@router.get("/ensembles")
async def list_ensemble_versions(
    user: CurrentUser, db: DbSession,
    symbol: str | None = Query(default=None, max_length=32),
):
    stmt = select(FactorEnsembleVersion).where(FactorEnsembleVersion.user_id == user.id)
    if symbol:
        stmt = stmt.where(FactorEnsembleVersion.symbol == symbol.upper())
    rows = (await db.execute(
        stmt.order_by(FactorEnsembleVersion.created_at.desc()).limit(100)
    )).scalars().all()
    return [{
        "id": str(row.id), "symbol": row.symbol, "interval": row.interval,
        "regime": row.regime, "weights": row.weights,
        "diagnostics": row.diagnostics, "created_at": row.created_at,
    } for row in rows]


@router.get("/candidates/{candidate_id}/backtests")
async def list_candidate_backtests(candidate_id: uuid.UUID, user: CurrentUser, db: DbSession):
    owned = await db.scalar(select(StrategyCandidate.id).where(
        StrategyCandidate.id == candidate_id, StrategyCandidate.user_id == user.id
    ))
    if owned is None:
        raise AppError(ErrorCode.NOT_FOUND, "Strategy candidate not found.")
    rows = (await db.execute(
        select(StrategyBacktest).where(
            StrategyBacktest.candidate_id == candidate_id,
            StrategyBacktest.user_id == user.id,
        ).order_by(StrategyBacktest.created_at.desc()).limit(100)
    )).scalars().all()
    return [{
        "id": str(row.id), "candidate_id": str(row.candidate_id),
        "symbol": row.symbol, "interval": row.interval, "sample": row.sample,
        "status": row.status, "metrics": row.metrics,
        "assumptions": row.assumptions, "error": row.error,
        "created_at": row.created_at, "finished_at": row.finished_at,
    } for row in rows]


@router.get("/compare")
async def compare_strategy_candidates(user: CurrentUser, db: DbSession):
    """Return a user-scoped comparison of independent research and PAPER results."""
    candidates = (await db.execute(
        select(StrategyCandidate).where(StrategyCandidate.user_id == user.id)
        .order_by(StrategyCandidate.created_at.desc()).limit(200)
    )).scalars().all()
    sessions = (await db.execute(
        select(PaperStrategySession).where(
            PaperStrategySession.user_id == user.id,
            PaperStrategySession.candidate_id.is_not(None),
        ).order_by(PaperStrategySession.created_at.desc())
    )).scalars().all()
    paper_by_candidate = {}
    for session in sessions:
        paper_by_candidate.setdefault(str(session.candidate_id), session)
    results = []
    for candidate in candidates:
        evaluation = dict(candidate.evaluation or {})
        backtest = dict(evaluation.get("backtest") or {})
        gate = dict(backtest.get("evaluation") or {})
        paper = paper_by_candidate.get(str(candidate.id))
        results.append({
            "candidate_id": str(candidate.id), "name": candidate.name,
            "strategy_kind": (candidate.specification or {}).get("strategy_kind", "unknown"),
            "applicable_market": (candidate.specification or {}).get("applicable_market", "UNKNOWN"),
            "status": candidate.status, "gate": gate.get("gate", evaluation.get("gate", "RESEARCH_ONLY")),
            "passed": bool(gate.get("passed")), "score": gate.get("score", 0),
            "in_sample": backtest.get("in_sample", {}),
            "out_of_sample": backtest.get("out_of_sample", {}),
            "walk_forward": backtest.get("walk_forward", []),
            "paper": ({
                "session_id": str(paper.id), "status": paper.status,
                "initial_cash": str(paper.initial_cash), "current_cash": str(paper.current_cash),
                "metrics": paper.metrics or {},
            } if paper else None),
            "created_at": candidate.created_at,
        })
    results.sort(key=lambda row: (bool(row["passed"]), float(row["score"] or 0),
                                  int((row["out_of_sample"] or {}).get("trade_count", 0))), reverse=True)
    return results


@router.get("/approvals")
async def list_strategy_approvals(user: CurrentUser, db: DbSession):
    rows = (await db.execute(
        select(StrategyApproval).where(StrategyApproval.user_id == user.id)
        .order_by(StrategyApproval.created_at.desc()).limit(200)
    )).scalars().all()
    return [{
        "id": str(row.id), "strategy_version_id": str(row.strategy_version_id),
        "decision": row.decision, "reviewer": row.reviewer, "note": row.note,
        "risk_check": row.risk_check, "created_at": row.created_at,
    } for row in rows]
