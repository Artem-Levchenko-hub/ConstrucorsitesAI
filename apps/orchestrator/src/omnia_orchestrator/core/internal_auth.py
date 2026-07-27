"""Authentication shared by every privileged orchestrator route."""

from __future__ import annotations

from hmac import compare_digest

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError


def verify_internal_token(token: str | None) -> None:
    expected = get_settings().internal_token.get_secret_value()
    if not token or not compare_digest(token, expected):
        raise OrchestratorError(
            code="unauthorized",
            message="missing or invalid X-Internal-Token",
            status_code=401,
        )
