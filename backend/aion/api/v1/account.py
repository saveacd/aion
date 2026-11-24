"""Account overview, positions and reconciliation state.

All data comes from persisted exchange snapshots. When no snapshot exists the
endpoint reports ``has_data: false`` instead of inventing numbers.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.api.deps import CurrentUser, DbSession, SettingsDep, scope_to_user
from aion.models.account import AccountSnapshot, PositionSnapshot, ReconciliationRun
from aion.models.auth import User
from aion.models.base import utcnow
from aion.schemas.common import AionModel
from aion.schemas.trading import AccountOut, PositionOut

router = APIRouter(prefix="/account", tags=["account"])

# Account snapshots are written by the portfolio sync job; allow a wider window
# than market data before flagging them stale.
ACCOUNT_STALE_SECONDS = 300

#: PAPER accounts are only re-marked on trades, so the dashboard would always
#: look stale; refresh them at most once a minute while someone is looking.
PAPER_REFRESH_SECONDS = 60


class AccountOverview(AionModel):
    has_data: bool
    mode: str
    account: AccountOut | None = None
    positions: list[PositionOut] = Field(default_factory=list)
    stale: bool = False


class PositionList(AionModel):
    has_data: bool
    mode: str
    positions: list[PositionOut] = Field(default_factory=list)
    stale: bool = False


class ReconciliationOut(AionModel):
    id: uuid.UUID
    mode: str
    status: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    differences: list[Any] = Field(default_factory=list)
    blocking: bool
    resolved: bool
    notes: str | None = None


class ReconciliationResponse(AionModel):
    has_data: bool
    run: ReconciliationOut | None = None


def _to_position(row: PositionSnapshot) -> PositionOut:
    notional: Decimal | None = None
    reference_price = row.mark_price or row.entry_price
    if reference_price is not None and row.quantity:
        notional = reference_price * row.quantity

    pnl_pct: Decimal | None = None
    if row.quantity and row.entry_price:
        cost = row.entry_price * row.quantity
        if cost:
            pnl_pct = (row.unrealized_pnl / cost) * Decimal("100")

    return PositionOut(
        symbol=row.symbol,
        position_side=row.position_side,
        quantity=row.quantity,
        entry_price=row.entry_price,
        mark_price=row.mark_price,
        unrealized_pnl=row.unrealized_pnl,
        unrealized_pnl_pct=pnl_pct,
        liquidation_price=row.liquidation_price,
        leverage=row.leverage,
        margin_mode=row.margin_mode,
        notional_usd=notional,
        has_stop_loss=row.has_stop_loss,
        has_take_profit=row.has_take_profit,
        stop_loss_price=row.stop_loss_price,
        take_profit_price=row.take_profit_price,
        protection_verified=row.protection_verified_at is not None,
    )


def _is_stale(captured_at: dt.datetime | None) -> bool:
    if captured_at is None:
        return True
    return (utcnow() - captured_at).total_seconds() > ACCOUNT_STALE_SECONDS


def _paper_refresh_due(captured_at: dt.datetime | None) -> bool:
    if captured_at is None:
        return True
    return (utcnow() - captured_at).total_seconds() > PAPER_REFRESH_SECONDS


async def _refresh_paper_if_due(
    mode: str, user: User, captured_at: dt.datetime | None
) -> bool:
    """Re-mark a PAPER account against live prices when its snapshot is old."""
    if mode != "PAPER" or not _paper_refresh_due(captured_at):
        return False
    from aion.core.db import get_sessionmaker
    from aion.execution.paper import PaperBroker

    await PaperBroker(get_sessionmaker()).refresh_snapshots(user.id)
    return True


async def _latest_account(
    db: AsyncSession, mode: str, user: User
) -> AccountSnapshot | None:
    stmt = scope_to_user(
        select(AccountSnapshot).where(AccountSnapshot.mode == mode),
        AccountSnapshot.user_id,
        user,
    )
    stmt = stmt.order_by(AccountSnapshot.captured_at.desc()).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _latest_positions(
    db: AsyncSession, mode: str, user: User
) -> list[PositionSnapshot]:
    stmt = scope_to_user(
        select(PositionSnapshot)
        .where(PositionSnapshot.mode == mode)
        .distinct(PositionSnapshot.symbol, PositionSnapshot.position_side),
        PositionSnapshot.user_id,
        user,
    )
    stmt = stmt.order_by(
        PositionSnapshot.symbol,
        PositionSnapshot.position_side,
        PositionSnapshot.captured_at.desc(),
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/overview", response_model=AccountOverview)
async def overview(
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> AccountOverview:
    mode = settings.resolve_trading_mode().value
    snapshot = await _latest_account(db, mode, user)
    if await _refresh_paper_if_due(mode, user, snapshot.captured_at if snapshot else None):
        snapshot = await _latest_account(db, mode, user)
    positions = [_to_position(row) for row in await _latest_positions(db, mode, user)]

    if snapshot is None:
        return AccountOverview(
            has_data=False,
            mode=mode,
            account=None,
            positions=positions,
            stale=True,
        )

    account = AccountOut.model_validate(snapshot)
    account.open_positions = len(positions)
    account.stale = _is_stale(snapshot.captured_at)
    return AccountOverview(
        has_data=True,
        mode=mode,
        account=account,
        positions=positions,
        stale=account.stale,
    )


@router.get("/positions", response_model=PositionList)
async def positions(
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> PositionList:
    mode = settings.resolve_trading_mode().value
    rows = await _latest_positions(db, mode, user)
    latest_captured = max((row.captured_at for row in rows), default=None)
    if rows and await _refresh_paper_if_due(mode, user, latest_captured):
        rows = await _latest_positions(db, mode, user)
        latest_captured = max((row.captured_at for row in rows), default=None)
    return PositionList(
        has_data=bool(rows),
        mode=mode,
        positions=[_to_position(row) for row in rows],
        stale=_is_stale(latest_captured) if rows else False,
    )


@router.get("/reconciliation", response_model=ReconciliationResponse)
async def reconciliation(
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> ReconciliationResponse:
    mode = settings.resolve_trading_mode().value
    stmt = scope_to_user(
        select(ReconciliationRun).where(ReconciliationRun.mode == mode),
        ReconciliationRun.user_id,
        user,
    )
    stmt = stmt.order_by(ReconciliationRun.started_at.desc()).limit(1)
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        return ReconciliationResponse(has_data=False, run=None)
    return ReconciliationResponse(
        has_data=True,
        run=ReconciliationOut(
            id=run.id,
            mode=run.mode,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            differences=list(run.differences or []),
            blocking=run.blocking,
            resolved=run.resolved,
            notes=run.notes,
        ),
    )
