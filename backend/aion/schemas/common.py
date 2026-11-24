"""Shared schema primitives: strict decimals, UTC datetimes, envelope models."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

T = TypeVar("T")

# Decimals cross the wire as strings so no float rounding can ever touch money.
Money = Annotated[
    Decimal,
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
Price = Money
Qty = Money


class AionModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
    )


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PageMeta(AionModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)
    has_more: bool = False


class Page(AionModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class Ack(AionModel):
    ok: bool = True
    message: str | None = None


class ErrorBody(AionModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorEnvelope(AionModel):
    error: ErrorBody


class HealthResponse(AionModel):
    status: str
    version: str
    environment: str
    trading_mode: str
    mainnet_unlocked: bool
    database: bool
    redis: bool
    checked_at: dt.datetime
