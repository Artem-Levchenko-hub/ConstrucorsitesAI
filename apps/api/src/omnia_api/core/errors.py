from typing import Any, Literal

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ErrorCode = Literal[
    "validation_failed",
    "unauthorized",
    "forbidden",
    "not_found",
    "rate_limited",
    "wallet_empty",
    "model_unavailable",
    "internal_error",
    "conflict",
    # V2: orchestrator-proxy errors surfaced through apps/api/services/orchestrator_client.
    # `unavailable` = transport / 5xx / token missing (503). `rejected` = orchestrator
    # returned 4xx that we passed through (400/404/etc).
    "orchestrator_unavailable",
    "orchestrator_rejected",
]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    body = ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": body.model_dump(exclude_none=True)},
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    body = ErrorBody(
        code="validation_failed",
        message="request validation failed",
        details={"errors": exc.errors()},
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": body.model_dump(exclude_none=True)},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    body = ErrorBody(code="internal_error", message="internal server error")
    return JSONResponse(status_code=500, content={"error": body.model_dump(exclude_none=True)})
