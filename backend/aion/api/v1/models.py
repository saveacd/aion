"""Administrator-managed LLM providers and per-user model assignments.

Keys are encrypted with ``SecretBox`` and only ever returned masked. An
administrator assigns a provider (plus optional model overrides and a daily
budget) to each user; the agent runtime resolves that assignment per run.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from aion.api.deps import AdminUser, CurrentUser, DbSession, SettingsDep, record_audit
from aion.core.crypto import SecretBox
from aion.core.errors import AppError, ErrorCode
from aion.models.auth import ModelProvider, User, UserModelSetting
from aion.schemas.common import AionModel, ORMModel

router = APIRouter(prefix="/models", tags=["models"])

ProviderKind = Literal[
    "anthropic",
    "openai",
    "deepseek",
    "gemini",
    "xai",
    "moonshot",
    "minimax",
    "qwen",
]


class ProviderOut(ORMModel):
    id: uuid.UUID
    name: str
    kind: str
    base_url: str | None = None
    light_model: str
    heavy_model: str
    is_enabled: bool
    has_api_key: bool = False
    api_key_masked: str = ""
    created_by: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ProviderList(AionModel):
    items: list[ProviderOut] = Field(default_factory=list)
    total: int = 0


class ProviderCreate(AionModel):
    name: str = Field(min_length=2, max_length=64)
    kind: ProviderKind = "openai"
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str = Field(min_length=8, max_length=512)
    light_model: str = Field(min_length=1, max_length=128)
    heavy_model: str = Field(min_length=1, max_length=128)
    is_enabled: bool = True


class ProviderUpdate(AionModel):
    name: str | None = Field(default=None, min_length=2, max_length=64)
    kind: ProviderKind | None = None
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    light_model: str | None = Field(default=None, min_length=1, max_length=128)
    heavy_model: str | None = Field(default=None, min_length=1, max_length=128)
    is_enabled: bool | None = None


class AssignmentOut(AionModel):
    user_id: uuid.UUID
    username: str | None = None
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    provider_kind: str | None = None
    light_model: str | None = None
    heavy_model: str | None = None
    role_models: dict[str, str] = Field(default_factory=dict)
    daily_budget_usd: Decimal
    enabled: bool
    configured: bool = False


class AssignmentList(AionModel):
    items: list[AssignmentOut] = Field(default_factory=list)
    total: int = 0


class AssignmentIn(AionModel):
    provider_id: uuid.UUID | None = None
    light_model: str | None = Field(default=None, max_length=128)
    heavy_model: str | None = Field(default=None, max_length=128)
    role_models: dict[str, str] = Field(default_factory=dict)
    daily_budget_usd: Decimal = Field(default=Decimal("5"), ge=0, le=10000)
    enabled: bool = True


def _provider_out(row: ModelProvider, box: SecretBox) -> ProviderOut:
    key = box.try_decrypt(row.api_key_enc)
    return ProviderOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        light_model=row.light_model,
        heavy_model=row.heavy_model,
        is_enabled=row.is_enabled,
        has_api_key=bool(row.api_key_enc),
        api_key_masked=SecretBox.mask(key) if key else "",
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _assignment_out(
    row: UserModelSetting | None,
    user: User,
    provider: ModelProvider | None,
) -> AssignmentOut:
    return AssignmentOut(
        user_id=user.id,
        username=user.username,
        provider_id=row.provider_id if row else None,
        provider_name=provider.name if provider else None,
        provider_kind=provider.kind if provider else None,
        light_model=row.light_model if row else None,
        heavy_model=row.heavy_model if row else None,
        role_models=dict(row.role_models or {}) if row else {},
        daily_budget_usd=row.daily_budget_usd if row else Decimal("5"),
        enabled=row.enabled if row else False,
        configured=bool(row and row.provider_id and provider and provider.is_enabled),
    )


async def _get_provider(db: DbSession, provider_id: uuid.UUID) -> ModelProvider:
    provider = await db.get(ModelProvider, provider_id)
    if provider is None:
        raise AppError(ErrorCode.NOT_FOUND, "Model provider not found.")
    return provider


@router.get("/providers", response_model=ProviderList)
async def list_providers(
    db: DbSession,
    settings: SettingsDep,
    admin: AdminUser,
) -> ProviderList:
    rows = (
        await db.execute(select(ModelProvider).order_by(ModelProvider.created_at.asc()))
    ).scalars().all()
    box = SecretBox(settings)
    return ProviderList(items=[_provider_out(row, box) for row in rows], total=len(rows))


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    payload: ProviderCreate,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    admin: AdminUser,
) -> ProviderOut:
    exists = (
        await db.execute(select(ModelProvider).where(ModelProvider.name == payload.name))
    ).scalar_one_or_none()
    if exists is not None:
        raise AppError(ErrorCode.CONFLICT, "A provider with this name already exists.")

    provider = ModelProvider(
        name=payload.name.strip(),
        kind=payload.kind,
        base_url=payload.base_url,
        api_key_enc=SecretBox(settings).encrypt(payload.api_key),
        light_model=payload.light_model,
        heavy_model=payload.heavy_model,
        is_enabled=payload.is_enabled,
        created_by=admin.username,
    )
    db.add(provider)
    await db.flush()
    await record_audit(
        db,
        actor=admin.username,
        action="models.provider_created",
        target=str(provider.id),
        after={"name": provider.name, "kind": provider.kind},
        request=request,
    )
    await db.commit()
    await db.refresh(provider)
    return _provider_out(provider, SecretBox(settings))


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    admin: AdminUser,
) -> ProviderOut:
    provider = await _get_provider(db, provider_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("api_key"):
        provider.api_key_enc = SecretBox(settings).encrypt(changes.pop("api_key"))
    else:
        changes.pop("api_key", None)
    for key, value in changes.items():
        if value is not None:
            setattr(provider, key, value)

    await record_audit(
        db,
        actor=admin.username,
        action="models.provider_updated",
        target=str(provider.id),
        after={key: value for key, value in changes.items() if key != "api_key_enc"},
        request=request,
    )
    await db.commit()
    await db.refresh(provider)
    return _provider_out(provider, SecretBox(settings))


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: uuid.UUID,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> dict[str, bool]:
    provider = await _get_provider(db, provider_id)
    assignments = (
        await db.execute(
            select(UserModelSetting).where(UserModelSetting.provider_id == provider.id)
        )
    ).scalars().all()
    for assignment in assignments:
        assignment.provider_id = None
        assignment.enabled = False

    await db.delete(provider)
    await record_audit(
        db,
        actor=admin.username,
        action="models.provider_deleted",
        target=str(provider_id),
        before={"name": provider.name},
        request=request,
    )
    await db.commit()
    return {"ok": True}


async def _assignment_rows(
    db: DbSession,
) -> list[tuple[User, UserModelSetting | None, ModelProvider | None]]:
    users = (await db.execute(select(User).order_by(User.created_at.asc()))).scalars().all()
    settings = (await db.execute(select(UserModelSetting))).scalars().all()
    providers = (await db.execute(select(ModelProvider))).scalars().all()
    setting_by_user = {row.user_id: row for row in settings}
    provider_by_id = {row.id: row for row in providers}
    return [
        (
            user,
            setting_by_user.get(user.id),
            provider_by_id.get(setting_by_user[user.id].provider_id)
            if setting_by_user.get(user.id)
            else None,
        )
        for user in users
    ]


@router.get("/assignments", response_model=AssignmentList)
async def list_assignments(db: DbSession, admin: AdminUser) -> AssignmentList:
    rows = await _assignment_rows(db)
    items = [_assignment_out(setting, user, provider) for user, setting, provider in rows]
    return AssignmentList(items=items, total=len(items))


@router.get("/assignments/{user_id}", response_model=AssignmentOut)
async def get_assignment(
    user_id: uuid.UUID,
    db: DbSession,
    admin: AdminUser,
) -> AssignmentOut:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "User not found.")
    setting = (
        await db.execute(select(UserModelSetting).where(UserModelSetting.user_id == user_id))
    ).scalar_one_or_none()
    provider = (
        await db.get(ModelProvider, setting.provider_id)
        if setting is not None and setting.provider_id is not None
        else None
    )
    return _assignment_out(setting, user, provider)


@router.put("/assignments/{user_id}", response_model=AssignmentOut)
async def upsert_assignment(
    user_id: uuid.UUID,
    payload: AssignmentIn,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> AssignmentOut:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "User not found.")

    provider = await _get_provider(db, payload.provider_id) if payload.provider_id else None
    setting = (
        await db.execute(select(UserModelSetting).where(UserModelSetting.user_id == user_id))
    ).scalar_one_or_none()
    if setting is None:
        setting = UserModelSetting(user_id=user_id)
        db.add(setting)

    setting.provider_id = provider.id if provider else None
    setting.light_model = payload.light_model or None
    setting.heavy_model = payload.heavy_model or None
    setting.role_models = {
        role: model.strip()[:128]
        for role, model in payload.role_models.items()
        if isinstance(model, str) and model.strip()
    }
    setting.daily_budget_usd = payload.daily_budget_usd
    setting.enabled = payload.enabled

    await record_audit(
        db,
        actor=admin.username,
        action="models.assignment_updated",
        target=str(user_id),
        after={
            "provider": provider.name if provider else None,
            "light_model": setting.light_model,
            "heavy_model": setting.heavy_model,
            "role_models": setting.role_models,
            "daily_budget_usd": str(setting.daily_budget_usd),
            "enabled": setting.enabled,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(setting)
    return _assignment_out(setting, user, provider)


@router.get("/me", response_model=AssignmentOut)
async def my_assignment(
    db: DbSession,
    user: CurrentUser,
) -> AssignmentOut:
    setting = (
        await db.execute(select(UserModelSetting).where(UserModelSetting.user_id == user.id))
    ).scalar_one_or_none()
    provider = (
        await db.get(ModelProvider, setting.provider_id)
        if setting is not None and setting.provider_id is not None
        else None
    )
    return _assignment_out(setting, user, provider)


__all__ = ["router", "AssignmentOut", "ProviderOut"]
