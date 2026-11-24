from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    CONFLICT = "CONFLICT"
    SAFETY_LOCK = "SAFETY_LOCK"
    RISK_REJECTED = "RISK_REJECTED"
    EXCHANGE_ERROR = "EXCHANGE_ERROR"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_STATUS_BY_CODE = {
    ErrorCode.UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ErrorCode.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.SAFETY_LOCK: status.HTTP_423_LOCKED,
    ErrorCode.RISK_REJECTED: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ErrorCode.EXCHANGE_ERROR: status.HTTP_502_BAD_GATEWAY,
    ErrorCode.UPSTREAM_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class AppError(HTTPException):
    """Application error with a stable machine-readable code."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(
            status_code=http_status or _STATUS_BY_CODE[code],
            detail=message,
            headers=headers,
        )

    def payload(self, request_id: str | None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
                "request_id": request_id,
            }
        }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.payload(getattr(request.state, "request_id", None)),
        headers=exc.headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    err = AppError(ErrorCode.INTERNAL_ERROR, "Internal server error.")
    return JSONResponse(status_code=500, content=err.payload(request_id))
