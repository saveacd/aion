from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from aion.core.config import Settings
from aion.core.errors import AppError, ErrorCode

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def totp_provisioning_uri(secret: str, account: str, issuer: str = "AION") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: dt.datetime
    refresh_expires_at: dt.datetime


class TokenService:
    """Issues signed access tokens and opaque (hashed) refresh tokens."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ---- access tokens (JWT) ----
    def issue_access_token(self, user_id: str, session_id: str, *, scope: str = "user") -> tuple[str, dt.datetime]:
        now = dt.datetime.now(dt.timezone.utc)
        expires_at = now + dt.timedelta(seconds=self._settings.access_token_ttl_seconds)
        claims: dict[str, Any] = {
            "sub": user_id,
            "sid": session_id,
            "scope": scope,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": secrets.token_hex(8),
            "iss": "aion",
        }
        token = jwt.encode(claims, self._settings.secret_key, algorithm=self._settings.jwt_algorithm)
        return token, expires_at

    def decode_access_token(self, token: str) -> dict[str, Any]:
        try:
            return jwt.decode(
                token,
                self._settings.secret_key,
                algorithms=[self._settings.jwt_algorithm],
                issuer="aion",
                options={"require": ["exp", "sub", "sid", "iat"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AppError(ErrorCode.UNAUTHORIZED, "Session expired. Please sign in again.") from exc
        except jwt.InvalidTokenError as exc:
            raise AppError(ErrorCode.UNAUTHORIZED, "Invalid session token.") from exc

    # ---- refresh tokens (opaque) ----
    def generate_refresh_token(self) -> tuple[str, str, dt.datetime]:
        raw = secrets.token_urlsafe(48)
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=self._settings.refresh_token_ttl_seconds
        )
        return raw, hash_refresh_token(raw), expires_at

    def issue_pair(self, user_id: str, session_id: str) -> TokenPair:
        access, access_exp = self.issue_access_token(user_id, session_id)
        refresh, _, refresh_exp = self.generate_refresh_token()
        return TokenPair(access, refresh, access_exp, refresh_exp)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


def check_password_strength(password: str) -> list[str]:
    problems: list[str] = []
    if len(password) < 12:
        problems.append("at least 12 characters")
    if not any(c.islower() for c in password):
        problems.append("a lowercase letter")
    if not any(c.isupper() for c in password):
        problems.append("an uppercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if not any(c in "!@#$%^&*()-_=+[]{};:,.?/\\|~`" for c in password):
        problems.append("a symbol")
    return problems
