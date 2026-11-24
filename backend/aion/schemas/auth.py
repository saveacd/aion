from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field, field_validator

from aion.schemas.common import AionModel, ORMModel


class LoginRequest(AionModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=16)
    device_name: str | None = Field(default=None, max_length=128)


class RegisterRequest(AionModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=256)
    device_name: str | None = Field(default=None, max_length=128)


class TokenResponse(AionModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    access_expires_at: dt.datetime
    refresh_expires_at: dt.datetime
    session_id: uuid.UUID
    user: "UserPublic"
    totp_enabled: bool
    must_change_password: bool = False


class RefreshRequest(AionModel):
    refresh_token: str = Field(min_length=16, max_length=256)


class LogoutRequest(AionModel):
    refresh_token: str | None = None
    all_devices: bool = False


class UserPublic(ORMModel):
    id: uuid.UUID
    username: str
    is_admin: bool
    totp_enabled: bool
    last_login_at: dt.datetime | None = None
    created_at: dt.datetime
    # The backend decides which menus a role may render.
    role: str = "user"
    features: list[str] = Field(default_factory=list)


class PasswordChangeRequest(AionModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)

    @field_validator("new_password")
    @classmethod
    def _not_same(cls, v: str) -> str:
        return v


class TotpSetupResponse(AionModel):
    secret: str
    provisioning_uri: str


class TotpEnableRequest(AionModel):
    code: str = Field(min_length=6, max_length=8)


class TotpDisableRequest(AionModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=8)


class SessionInfo(ORMModel):
    id: uuid.UUID
    device_name: str | None
    user_agent: str | None
    ip_address: str | None
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    revoked_at: dt.datetime | None
    is_current: bool = False


class ExchangeAccountPublic(ORMModel):
    id: uuid.UUID
    exchange: str
    mode: str
    label: str | None
    api_key_masked: str = ""
    has_secret: bool = False
    permissions_checked: bool
    can_trade: bool | None
    can_withdraw: bool | None
    last_checked_at: dt.datetime | None
    is_active: bool


class ExchangeKeyUpsert(AionModel):
    """Credentials travel once, encrypted at rest, never returned."""

    mode: str = Field(pattern="^(TESTNET|MAINNET)$")
    label: str | None = Field(default=None, max_length=128)
    api_key: str = Field(min_length=8, max_length=256)
    api_secret: str = Field(min_length=8, max_length=256)
