"""Administrator-only user management.

Normal accounts created here start as non-admin users and only see their own
data. Only an administrator can change roles, reset passwords or remove users.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import func, select, update

from aion.api.deps import AdminUser, DbSession, record_audit
from aion.core.errors import AppError, ErrorCode
from aion.core.security import check_password_strength, hash_password
from aion.models.auth import DeviceSession, User, UserModelSetting
from aion.schemas.common import AionModel, ORMModel

router = APIRouter(prefix="/users", tags=["users"])


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    is_admin: bool
    is_active: bool
    totp_enabled: bool
    last_login_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    role: str = "user"


class UserList(AionModel):
    items: list[UserOut] = Field(default_factory=list)
    total: int = 0


class UserCreate(AionModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    is_admin: bool = False


class UserUpdate(AionModel):
    is_active: bool | None = None
    is_admin: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class ResetPasswordRequest(AionModel):
    password: str = Field(min_length=12, max_length=256)


def _to_out(row: User) -> UserOut:
    return UserOut(
        id=row.id,
        username=row.username,
        is_admin=row.is_admin,
        is_active=row.is_active,
        totp_enabled=row.totp_enabled,
        last_login_at=row.last_login_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        role="admin" if row.is_admin else "user",
    )


async def _get_user(db: DbSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "User not found.")
    return user


async def _set_password(db: DbSession, user: User, password: str) -> None:
    problems = check_password_strength(password)
    if problems:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Password does not meet the strength requirements.",
            details={"required": problems},
        )
    user.password_hash = hash_password(password)
    user.password_changed_at = dt.datetime.now(dt.UTC)
    user.failed_login_count = 0
    user.locked_until = None
    # Force a fresh login everywhere after an administrative reset.
    await db.execute(
        update(DeviceSession)
        .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
        .values(revoked_at=dt.datetime.now(dt.UTC), revoked_reason="admin_reset")
    )


@router.get("", response_model=UserList)
async def list_users(db: DbSession, admin: AdminUser) -> UserList:
    rows = (
        await db.execute(select(User).order_by(User.created_at.asc()))
    ).scalars().all()
    return UserList(items=[_to_out(row) for row in rows], total=len(rows))


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> UserOut:
    username = payload.username.strip()
    exists = (
        await db.execute(select(User).where(func.lower(User.username) == username.lower()))
    ).scalar_one_or_none()
    if exists is not None:
        raise AppError(ErrorCode.CONFLICT, "Username already exists.")

    problems = check_password_strength(payload.password)
    if problems:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Password does not meet the strength requirements.",
            details={"required": problems},
        )

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        is_active=True,
        password_changed_at=dt.datetime.now(dt.UTC),
    )
    db.add(user)
    await db.flush()
    await record_audit(
        db,
        actor=admin.username,
        action="users.created",
        target=str(user.id),
        after={"username": username, "is_admin": user.is_admin},
        request=request,
    )
    await db.commit()
    await db.refresh(user)
    return _to_out(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> UserOut:
    user = await _get_user(db, user_id)
    changes = payload.model_dump(exclude_unset=True)

    if user.id == admin.id and (
        changes.get("is_active") is False or changes.get("is_admin") is False
    ):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, "You cannot disable or demote your own account."
        )

    before = {"is_active": user.is_active, "is_admin": user.is_admin}
    if "is_active" in changes and changes["is_active"] is not None:
        user.is_active = changes["is_active"]
        if not user.is_active:
            await db.execute(
                update(DeviceSession)
                .where(DeviceSession.user_id == user.id, DeviceSession.revoked_at.is_(None))
                .values(
                    revoked_at=dt.datetime.now(dt.UTC),
                    revoked_reason="account_disabled",
                )
            )
    if "is_admin" in changes and changes["is_admin"] is not None:
        user.is_admin = changes["is_admin"]
    if changes.get("password"):
        await _set_password(db, user, changes["password"])

    await record_audit(
        db,
        actor=admin.username,
        action="users.updated",
        target=str(user.id),
        before=before,
        after={
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "password_reset": bool(changes.get("password")),
        },
        request=request,
    )
    await db.commit()
    await db.refresh(user)
    return _to_out(user)


@router.post("/{user_id}/password", response_model=UserOut)
async def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> UserOut:
    user = await _get_user(db, user_id)
    await _set_password(db, user, payload.password)
    await record_audit(
        db,
        actor=admin.username,
        action="users.password_reset",
        target=str(user.id),
        request=request,
    )
    await db.commit()
    await db.refresh(user)
    return _to_out(user)


@router.delete("/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: DbSession,
    admin: AdminUser,
) -> dict[str, bool]:
    user = await _get_user(db, user_id)
    if user.id == admin.id:
        raise AppError(ErrorCode.VALIDATION_ERROR, "You cannot delete your own account.")

    if user.is_admin:
        admins = int(
            (
                await db.execute(
                    select(func.count()).select_from(User).where(User.is_admin.is_(True))
                )
            ).scalar_one()
        )
        if admins <= 1:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, "At least one administrator must remain."
            )

    username = user.username
    # Explicitly remove the model assignment first; remaining owned rows keep
    # their history with user_id set to NULL (admin-only visibility).
    assignment = (
        await db.execute(select(UserModelSetting).where(UserModelSetting.user_id == user.id))
    ).scalar_one_or_none()
    if assignment is not None:
        await db.delete(assignment)
    await db.delete(user)
    await record_audit(
        db,
        actor=admin.username,
        action="users.deleted",
        target=str(user_id),
        before={"username": username},
        request=request,
    )
    await db.commit()
    return {"ok": True}
