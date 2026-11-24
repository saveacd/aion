"""Application settings and exchange credential visibility.

Secret values never leave the server: they are masked here and encrypted at rest
by ``aion.core.crypto``. The exchange endpoint only reports configuration state.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import and_, select

from aion.api.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    SettingsDep,
    record_audit,
)
from aion.core.config import TradingMode
from aion.core.crypto import SecretBox
from aion.core.errors import AppError, ErrorCode
from aion.models.auth import ExchangeAccount, SystemSetting
from aion.schemas.common import AionModel
from aion.services.binance_credentials import verify_binance_credentials

router = APIRouter(prefix="/settings", tags=["settings"])

SENSITIVE_KEY = re.compile(
    r"(secret|password|passwd|token|api[_-]?key|credential|private[_-]?key)", re.IGNORECASE
)

MASK = "***REDACTED***"


def _mask_value(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            masked[key] = MASK if SENSITIVE_KEY.search(str(key)) else _mask_value(item)
        return masked
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    return value


class SettingOut(AionModel):
    key: str
    value: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    updated_by: str | None = None
    updated_at: dt.datetime | None = None


class SettingList(AionModel):
    items: list[SettingOut] = Field(default_factory=list)
    total: int = 0


class SettingUpsert(AionModel):
    key: str = Field(min_length=1, max_length=128)
    value: dict[str, Any] = Field(default_factory=dict)
    description: str | None = Field(default=None, max_length=512)


class SettingUpdateRequest(AionModel):
    items: list[SettingUpsert] = Field(min_length=1, max_length=100)


class ExchangeSettingOut(AionModel):
    mode: str
    label: str | None = None
    has_api_key: bool
    has_api_secret: bool
    permissions_checked: bool
    can_trade: bool | None = None
    can_withdraw: bool | None = None
    last_checked_at: dt.datetime | None = None
    is_active: bool


class ExchangeSettingsResponse(AionModel):
    items: list[ExchangeSettingOut] = Field(default_factory=list)
    configured: dict[str, bool] = Field(default_factory=dict)


class ExchangeKeyUpsert(AionModel):
    mode: Literal["TESTNET", "MAINNET"] = "TESTNET"
    api_key: str = Field(min_length=8, max_length=256)
    api_secret: str = Field(min_length=8, max_length=256)
    label: str | None = Field(default=None, max_length=128)
    verify: bool = True


class ExchangeSaveResult(AionModel):
    mode: str
    saved: bool
    verified: bool
    message: str
    item: ExchangeSettingOut | None = None


def _exchange_out(row: ExchangeAccount) -> ExchangeSettingOut:
    return ExchangeSettingOut(
        mode=row.mode,
        label=row.label,
        has_api_key=bool(row.api_key_enc),
        has_api_secret=bool(row.api_secret_enc),
        permissions_checked=row.permissions_checked,
        can_trade=row.can_trade,
        can_withdraw=row.can_withdraw,
        last_checked_at=row.last_checked_at,
        is_active=row.is_active,
    )


async def _user_exchange_accounts(
    db: DbSession, user_id: Any
) -> list[ExchangeAccount]:
    rows = (
        await db.execute(
            select(ExchangeAccount)
            .where(ExchangeAccount.user_id == user_id)
            .order_by(ExchangeAccount.mode)
        )
    ).scalars().all()
    return list(rows)


def _setting_out(row: SystemSetting) -> SettingOut:
    return SettingOut(
        key=row.key,
        value=_mask_value(row.value or {}),
        description=row.description,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


@router.get("", response_model=SettingList)
async def list_settings(db: DbSession, user: CurrentUser) -> SettingList:
    rows = (
        await db.execute(select(SystemSetting).order_by(SystemSetting.key))
    ).scalars().all()
    return SettingList(items=[_setting_out(row) for row in rows], total=len(rows))


@router.put("", response_model=SettingList)
async def update_settings(
    payload: SettingUpdateRequest,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> SettingList:
    keys: list[str] = []
    for item in payload.items:
        row = await db.get(SystemSetting, item.key)
        before = _mask_value(row.value or {}) if row is not None else None
        if row is None:
            row = SystemSetting(
                key=item.key,
                value=item.value,
                description=item.description,
                updated_by=admin.username,
            )
            db.add(row)
        else:
            row.value = item.value
            if item.description is not None:
                row.description = item.description
            row.updated_by = admin.username

        await record_audit(
            db,
            actor=admin.username,
            action="settings.updated",
            target=item.key,
            before=before,
            after=_mask_value(item.value),
            request=request,
        )
        keys.append(item.key)

    await db.commit()

    rows = (
        await db.execute(select(SystemSetting).where(SystemSetting.key.in_(keys)))
    ).scalars().all()
    updated = [_setting_out(row) for row in rows]
    return SettingList(items=updated, total=len(updated))


@router.get("/exchange", response_model=ExchangeSettingsResponse)
async def exchange_settings(
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> ExchangeSettingsResponse:
    """Report only the caller's own exchange credentials, never another user's."""
    rows = await _user_exchange_accounts(db, user.id)
    items = [_exchange_out(row) for row in rows]
    configured = {mode.value: False for mode in (TradingMode.TESTNET, TradingMode.MAINNET)}
    for row in rows:
        configured[row.mode] = bool(row.api_key_enc and row.api_secret_enc and row.is_active)
    return ExchangeSettingsResponse(items=items, configured=configured)


@router.put("/exchange", response_model=ExchangeSaveResult)
async def save_exchange_credentials(
    payload: ExchangeKeyUpsert,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> ExchangeSaveResult:
    """Encrypt and store the caller's Binance API key pair for one mode."""
    mode = payload.mode
    row = (
        await db.execute(
            select(ExchangeAccount).where(
                and_(
                    ExchangeAccount.user_id == user.id,
                    ExchangeAccount.exchange == "binance",
                    ExchangeAccount.mode == mode,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ExchangeAccount(user_id=user.id, exchange="binance", mode=mode)
        db.add(row)

    box = SecretBox(settings)
    row.label = payload.label or row.label or f"Binance {mode}"
    row.api_key_enc = box.encrypt(payload.api_key.strip())
    row.api_secret_enc = box.encrypt(payload.api_secret.strip())
    row.is_active = True

    verified = False
    message = "已保存，未执行联网验证。"
    if payload.verify:
        verified, message = await verify_binance_credentials(
            payload.api_key.strip(), payload.api_secret.strip(), mode
        )
        row.permissions_checked = verified
        row.last_checked_at = dt.datetime.now(dt.UTC)
        if verified:
            row.can_trade = True
            row.can_withdraw = False

    await record_audit(
        db,
        actor=user.username,
        action="settings.exchange_saved",
        target=mode,
        after={"mode": mode, "verified": verified},
        request=request,
    )
    await db.commit()
    await db.refresh(row)
    return ExchangeSaveResult(
        mode=mode,
        saved=True,
        verified=verified,
        message=message,
        item=_exchange_out(row),
    )


@router.delete("/exchange/{mode}")
async def delete_exchange_credentials(
    mode: Literal["TESTNET", "MAINNET"],
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, bool]:
    row = (
        await db.execute(
            select(ExchangeAccount).where(
                and_(
                    ExchangeAccount.user_id == user.id,
                    ExchangeAccount.exchange == "binance",
                    ExchangeAccount.mode == mode,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "No exchange credentials stored for this mode.")

    await db.delete(row)
    await record_audit(
        db,
        actor=user.username,
        action="settings.exchange_deleted",
        target=mode,
        request=request,
    )
    await db.commit()
    return {"ok": True}
