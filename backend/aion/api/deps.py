"""Shared FastAPI dependencies and small cross-router helpers."""

from __future__ import annotations

import datetime as dt
import uuid
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from aion.core.config import Settings, get_settings
from aion.core.db import get_db
from aion.core.errors import AppError, ErrorCode
from aion.core.logging import request_id_ctx
from aion.core.security import TokenService
from aion.models.account import AuditLog, SystemEvent
from aion.models.auth import DeviceSession, User
from aion.models.base import utcnow

_bearer = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

SESSION_TOUCH_INTERVAL = dt.timedelta(minutes=5)


@lru_cache(maxsize=1)
def get_token_service() -> TokenService:
    return TokenService(get_settings())


def get_client_ip(request: Request) -> str | None:
    """Trust the first hop of X-Forwarded-For (set by the reverse proxy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or None
    return request.client.host[:64] if request.client and request.client.host else None


def get_request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None) or request_id_ctx.get()
    return None if not value or value == "-" else value


async def get_current_user(
    request: Request,
    credentials: CredentialsDep,
    db: DbSession,
    settings: SettingsDep,
) -> User:
    """Validate the Bearer access token and re-check user/session state in the DB."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            "Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = TokenService(settings).decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(str(claims.get("sub")))
        session_id = uuid.UUID(str(claims.get("sid")))
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid session token.") from exc

    session = await db.get(DeviceSession, session_id)
    if session is None or session.user_id != user_id or session.revoked_at is not None:
        raise AppError(ErrorCode.UNAUTHORIZED, "Session has been revoked. Please sign in again.")
    now = utcnow()
    if session.expires_at <= now:
        raise AppError(ErrorCode.UNAUTHORIZED, "Session expired. Please sign in again.")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(ErrorCode.UNAUTHORIZED, "User account is not active.")

    if session.last_seen_at is None or now - session.last_seen_at > SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now

    request.state.current_user = user
    request.state.session_id = session_id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise AppError(ErrorCode.FORBIDDEN, "Administrator privileges are required.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def scope_to_user(stmt: Any, column: Any, user: User) -> Any:
    """Admins see every row; a normal user only sees rows they own."""
    if getattr(user, "is_admin", False):
        return stmt
    return stmt.where(column == user.id)


def assert_owner(user: User, owner_id: uuid.UUID | None, message: str = "Not found.") -> None:
    """Hide rows owned by someone else instead of revealing their existence."""
    if getattr(user, "is_admin", False):
        return
    if owner_id != user.id:
        raise AppError(ErrorCode.NOT_FOUND, message)


def get_current_session_id(request: Request) -> uuid.UUID | None:
    value = getattr(request.state, "session_id", None)
    return value if isinstance(value, uuid.UUID) else None


async def record_audit(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    target: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: Request | None = None,
    ok: bool = True,
) -> AuditLog:
    """Queue an audit row; the caller owns the transaction/commit."""
    entry = AuditLog(
        actor=actor[:64],
        action=action[:96],
        target=target[:128] if target else None,
        before=before,
        after=after,
        ip_address=get_client_ip(request) if request is not None else None,
        request_id=get_request_id(request) if request is not None else None,
        ok=ok,
    )
    db.add(entry)
    return entry


def record_system_event(
    db: AsyncSession,
    *,
    category: str,
    message: str,
    level: str = "INFO",
    data: dict[str, Any] | None = None,
) -> SystemEvent:
    """Queue a system event row; the caller owns the transaction/commit."""
    event = SystemEvent(
        category=category[:32],
        level=level[:16],
        message=message,
        data=data or {},
    )
    db.add(event)
    return event


__all__ = [
    "AdminUser",
    "CredentialsDep",
    "CurrentUser",
    "DbSession",
    "SettingsDep",
    "get_client_ip",
    "get_current_session_id",
    "get_current_user",
    "get_request_id",
    "get_token_service",
    "record_audit",
    "record_system_event",
    "require_admin",
    "scope_to_user",
    "assert_owner",
]
