"""Authentication endpoints: password login, refresh, logout, TOTP."""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aion.api.deps import (
    CurrentUser,
    DbSession,
    SettingsDep,
    get_client_ip,
    get_current_session_id,
    get_token_service,
    record_audit,
)
from aion.core.config import Settings
from aion.core.crypto import SecretBox
from aion.core.errors import AppError, ErrorCode
from aion.core.redis import RateLimiter, get_redis
from aion.core.security import (
    TokenPair,
    check_password_strength,
    generate_totp_secret,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from aion.models.auth import DeviceSession, User
from aion.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    TotpEnableRequest,
    TotpSetupResponse,
    UserPublic,
)
from aion.schemas.common import Ack

router = APIRouter(prefix="/auth", tags=["auth"])

# Rebuild the forward reference declared in schemas.auth.
TokenResponse.model_rebuild()

LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


async def _enforce_rate_limit(request: Request, settings: Settings) -> None:
    """Fixed-window limit in Redis. If Redis is down the DB lockout still applies."""
    try:
        limiter = RateLimiter(get_redis(), namespace="login")
        allowed, _ = await limiter.hit(
            get_client_ip(request) or "unknown",
            limit=settings.login_rate_limit_per_minute,
            window_seconds=60,
        )
    except Exception:  # noqa: BLE001 - cache failure must not block authentication
        return
    if not allowed:
        raise AppError(ErrorCode.RATE_LIMITED, "Too many login attempts. Try again in a minute.")


def _token_response(user: User, pair: TokenPair, session_id: uuid.UUID) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
        session_id=session_id,
        user=UserPublic.model_validate(user),
        totp_enabled=user.totp_enabled,
        must_change_password=user.password_changed_at is None,
    )


async def _register_failed_login(
    db: AsyncSession, user: User, request: Request, *, reason: str
) -> None:
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= LOCKOUT_THRESHOLD:
        user.locked_until = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=LOCKOUT_MINUTES)
    await record_audit(
        db,
        actor=user.username,
        action="auth.login",
        target=str(user.id),
        after={"failed_login_count": user.failed_login_count, "reason": reason},
        request=request,
        ok=False,
    )
    await db.commit()


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
) -> TokenResponse:
    """Public self-service registration (can be disabled by the operator)."""
    if not settings.registration_enabled:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "当前部署未开放注册，请联系管理员开通账号。",
        )
    await _enforce_rate_limit(request, settings)

    username = payload.username.strip()
    existing = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(ErrorCode.CONFLICT, "该用户名已被注册。")

    now = dt.datetime.now(dt.UTC)
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        is_admin=False,
        is_active=True,
        password_changed_at=now,
        last_login_at=now,
    )
    db.add(user)
    await db.flush()

    token_service = get_token_service()
    refresh_raw, refresh_hash, refresh_exp = token_service.generate_refresh_token()
    session = DeviceSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        device_name=payload.device_name or "web",
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
        ip_address=get_client_ip(request),
        expires_at=refresh_exp,
        last_seen_at=now,
    )
    db.add(session)
    await db.flush()
    access, access_exp = token_service.issue_access_token(str(user.id), str(session.id))
    pair = TokenPair(access, refresh_raw, access_exp, refresh_exp)

    await record_audit(
        db,
        actor=user.username,
        action="auth.register",
        target=str(user.id),
        after={"public": True},
        request=request,
    )
    await db.commit()

    from aion.core.db import get_sessionmaker
    from aion.execution.paper import PaperBroker
    from aion.services.notifications import notify

    with contextlib.suppress(Exception):
        await PaperBroker(get_sessionmaker()).ensure_account(user.id)
    await notify(
        get_sessionmaker(),
        user_id=user.id,
        title="欢迎加入 AION",
        body="模拟账户已开通。六个 AI 已就位，你可以先在模拟盘体验全自动托管。",
        kind="welcome",
    )
    return _token_response(user, pair, session.id)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
) -> TokenResponse:
    await _enforce_rate_limit(request, settings)
    now = dt.datetime.now(dt.UTC)

    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    if user is None:
        # Do not reveal whether the username exists.
        await record_audit(
            db,
            actor=payload.username,
            action="auth.login",
            request=request,
            ok=False,
        )
        await db.commit()
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid username or password.")

    if user.locked_until is not None and user.locked_until > now:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            "Account temporarily locked after repeated failed logins.",
        )
    if not user.is_active:
        raise AppError(ErrorCode.FORBIDDEN, "Account is disabled.")

    if not verify_password(payload.password, user.password_hash):
        await _register_failed_login(db, user, request, reason="bad_password")
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid username or password.")

    if user.totp_enabled:
        secret = SecretBox(settings).try_decrypt(user.totp_secret_enc)
        if not payload.totp_code or not secret or not verify_totp(secret, payload.totp_code):
            await _register_failed_login(db, user, request, reason="bad_totp")
            raise AppError(ErrorCode.UNAUTHORIZED, "Invalid TOTP code.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    token_service = get_token_service()
    refresh_raw, refresh_hash, refresh_exp = token_service.generate_refresh_token()
    session = DeviceSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        device_name=payload.device_name,
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
        ip_address=get_client_ip(request),
        expires_at=refresh_exp,
        last_seen_at=now,
    )
    db.add(session)
    await db.flush()

    access, access_exp = token_service.issue_access_token(str(user.id), str(session.id))
    pair = TokenPair(access, refresh_raw, access_exp, refresh_exp)

    await record_audit(
        db,
        actor=user.username,
        action="auth.login",
        target=str(user.id),
        after={"device_name": payload.device_name},
        request=request,
    )
    await db.commit()
    return _token_response(user, pair, session.id)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: DbSession,
) -> TokenResponse:
    digest = hash_refresh_token(payload.refresh_token)
    session = (
        await db.execute(
            select(DeviceSession).where(DeviceSession.refresh_token_hash == digest)
        )
    ).scalar_one_or_none()

    now = dt.datetime.now(dt.UTC)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise AppError(ErrorCode.UNAUTHORIZED, "Refresh token is invalid or expired.")

    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise AppError(ErrorCode.UNAUTHORIZED, "User account is not active.")

    token_service = get_token_service()
    refresh_raw, refresh_hash, refresh_exp = token_service.generate_refresh_token()
    session.refresh_token_hash = refresh_hash
    session.expires_at = refresh_exp
    session.last_seen_at = now

    access, access_exp = token_service.issue_access_token(str(user.id), str(session.id))
    pair = TokenPair(access, refresh_raw, access_exp, refresh_exp)

    await record_audit(
        db,
        actor=user.username,
        action="auth.refresh",
        target=str(session.id),
        request=request,
    )
    await db.commit()
    return _token_response(user, pair, session.id)


@router.post("/logout", response_model=Ack)
async def logout(
    payload: LogoutRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> Ack:
    now = dt.datetime.now(dt.UTC)
    revoked = 0

    if payload.all_devices:
        sessions = (
            await db.execute(
                select(DeviceSession).where(
                    DeviceSession.user_id == user.id,
                    DeviceSession.revoked_at.is_(None),
                )
            )
        ).scalars().all()
    elif payload.refresh_token:
        digest = hash_refresh_token(payload.refresh_token)
        sessions = (
            await db.execute(
                select(DeviceSession).where(
                    DeviceSession.user_id == user.id,
                    DeviceSession.refresh_token_hash == digest,
                )
            )
        ).scalars().all()
    else:
        session_id = get_current_session_id(request)
        current = await db.get(DeviceSession, session_id) if session_id else None
        sessions = [current] if current is not None else []

    for session in sessions:
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoked_reason = "logout"
            revoked += 1

    await record_audit(
        db,
        actor=user.username,
        action="auth.logout",
        target=str(user.id),
        after={"revoked_sessions": revoked, "all_devices": payload.all_devices},
        request=request,
    )
    await db.commit()
    return Ack(ok=True, message=f"Revoked {revoked} session(s).")


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)


@router.post("/password", response_model=Ack)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> Ack:
    if not verify_password(payload.current_password, user.password_hash):
        raise AppError(ErrorCode.UNAUTHORIZED, "Current password is incorrect.")

    problems = check_password_strength(payload.new_password)
    if problems:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "New password does not meet the strength requirements.",
            details={"required": problems},
        )
    if payload.new_password == payload.current_password:
        raise AppError(ErrorCode.VALIDATION_ERROR, "New password must differ from the current one.")

    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = dt.datetime.now(dt.UTC)

    current_session_id = get_current_session_id(request)
    sessions = (
        await db.execute(
            select(DeviceSession).where(
                DeviceSession.user_id == user.id,
                DeviceSession.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    now = dt.datetime.now(dt.UTC)
    for session in sessions:
        if session.id != current_session_id:
            session.revoked_at = now
            session.revoked_reason = "password_changed"

    await record_audit(
        db,
        actor=user.username,
        action="auth.password_changed",
        target=str(user.id),
        request=request,
    )
    await db.commit()
    return Ack(ok=True, message="Password updated; other sessions were revoked.")


@router.post("/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> TotpSetupResponse:
    if user.totp_enabled:
        raise AppError(ErrorCode.CONFLICT, "TOTP is already enabled for this account.")

    secret = generate_totp_secret()
    user.totp_secret_enc = SecretBox(settings).encrypt(secret)

    await record_audit(
        db,
        actor=user.username,
        action="auth.totp_setup",
        target=str(user.id),
        request=request,
    )
    await db.commit()
    return TotpSetupResponse(
        secret=secret,
        provisioning_uri=totp_provisioning_uri(secret, user.username),
    )


@router.post("/totp/enable", response_model=Ack)
async def totp_enable(
    payload: TotpEnableRequest,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
) -> Ack:
    if user.totp_enabled:
        raise AppError(ErrorCode.CONFLICT, "TOTP is already enabled for this account.")

    secret = SecretBox(settings).try_decrypt(user.totp_secret_enc)
    if not secret:
        raise AppError(ErrorCode.CONFLICT, "Run TOTP setup before enabling it.")
    if not verify_totp(secret, payload.code):
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid TOTP code.")

    user.totp_enabled = True
    await record_audit(
        db,
        actor=user.username,
        action="auth.totp_enabled",
        target=str(user.id),
        request=request,
    )
    await db.commit()
    return Ack(ok=True, message="TOTP enabled.")
