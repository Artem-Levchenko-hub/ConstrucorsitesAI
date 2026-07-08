"""Error envelope — mirrors apps/api/src/omnia_api/core/errors.py shape.

Same response format so apps/api can transparently proxy orchestrator
errors back to the web client without translation.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ErrorCode = Literal[
    "validation_failed",
    "unauthorized",
    "not_found",
    "conflict",
    "container_failure",
    # A dev container that is stopped/paused and could not be woken in-line —
    # distinct from container_failure so apps/api can tell "the world is gone"
    # (abort the build honestly) from a transient exec problem (retryable).
    "container_not_running",
    "docker_unavailable",
    "postgres_unavailable",
    "port_exhausted",
    "internal_error",
]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class OrchestratorError(Exception):
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


async def orchestrator_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, OrchestratorError)
    body = ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": body.model_dump(exclude_none=True)},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    body = ErrorBody(code="internal_error", message="orchestrator internal error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": body.model_dump(exclude_none=True)},
    )
