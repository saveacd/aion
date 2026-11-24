from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, MetaData, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        Decimal: Numeric(38, 12),
    }


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class CreatedAt:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )


class Timestamped(CreatedAt):
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class OwnedMixin:
    """Marks a row as belonging to one user. ``NULL`` means admin/global data."""

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )


Money = Numeric(38, 12)


def money_col(**kwargs: Any) -> Any:
    return mapped_column(Numeric(38, 12), **kwargs)


def enum_str(*values: str) -> str:
    return String(32)


__all__ = [
    "Base",
    "CreatedAt",
    "Money",
    "OwnedMixin",
    "Timestamped",
    "UUIDPrimaryKey",
    "enum_str",
    "money_col",
    "utcnow",
]
