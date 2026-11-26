from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aion.models.base import Base, CreatedAt, OwnedMixin, Timestamped, UUIDPrimaryKey, utcnow

# Menus/features are decided by the backend; the web and Android clients only
# render what the server says the current role may access.
FEATURES_USER: tuple[str, ...] = (
    "home",
    "agents",
    "market",
    "trade",
    "risk",
    "strategies",
    "ai",
    "autopilot",
    "learning",
    "factors",
    "research-lab",
    "notifications",
    "exchange",
    "me",
)
FEATURES_ADMIN: tuple[str, ...] = (
    *FEATURES_USER,
    "users",
    "models",
    "system",
)


class User(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(String(512))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list[DeviceSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role(self) -> str:
        return "admin" if self.is_admin else "user"

    @property
    def features(self) -> list[str]:
        return list(FEATURES_ADMIN if self.is_admin else FEATURES_USER)


class DeviceSession(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "device_sessions"
    __table_args__ = (Index("ix_device_sessions_user_expires", "user_id", "expires_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="sessions")


class SystemSetting(Timestamped, Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(512))


class ExchangeAccount(OwnedMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "exchange_accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "exchange", "mode", name="uq_exchange_accounts_user_exchange_mode"
        ),
    )

    exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="binance")
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    api_key_enc: Mapped[str | None] = mapped_column(String(1024))
    api_secret_enc: Mapped[str | None] = mapped_column(String(1024))
    permissions_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_trade: Mapped[bool | None] = mapped_column(Boolean)
    can_withdraw: Mapped[bool | None] = mapped_column(Boolean)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ModelProvider(UUIDPrimaryKey, Timestamped, Base):
    """An administrator-owned LLM endpoint. The API key never leaves the server."""

    __tablename__ = "model_providers"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    base_url: Mapped[str | None] = mapped_column(String(512))
    api_key_enc: Mapped[str | None] = mapped_column(String(2048))
    light_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    heavy_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64))


class UserModelSetting(UUIDPrimaryKey, Timestamped, Base):
    """Per-user model assignment: which provider, which models, which budget."""

    __tablename__ = "user_model_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_providers.id", ondelete="SET NULL")
    )
    light_model: Mapped[str | None] = mapped_column(String(128))
    heavy_model: Mapped[str | None] = mapped_column(String(128))
    # Optional per-agent override: {"market_regime": "model-a", ...}.
    # Agents not listed fall back to light/heavy.
    role_models: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    daily_budget_usd: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
