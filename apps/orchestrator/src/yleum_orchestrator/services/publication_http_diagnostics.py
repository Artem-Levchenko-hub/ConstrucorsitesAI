"""Safe private diagnostics for the fixed publication boundary-health GET.

No raw SDK exception, header mapping, reason text or response body is persisted.
Capture must never alter the original exception or cause another proxy request.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_ATTRIBUTE = "_omnia_boundary_http_failure"
_LIMIT = 8192
_CODES = frozenset(
    {
        "Forbidden",
        "Unauthorized",
        "NotFound",
        "BadRequest",
        "MethodNotAllowed",
        "ServiceUnavailable",
        "InternalError",
        "Timeout",
        "ServerTimeout",
        "UnexpectedServerResponse",
        "internal_error",
        "unauthorized",
        "forbidden",
        "not_found",
        "invalid_request",
        "no_identity",
        "invalid_session",
        "auth_required",
        "service_unavailable",
    }
)
_REASONS = frozenset(
    {
        "Unauthorized",
        "Forbidden",
        "Not Found",
        "Bad Request",
        "Method Not Allowed",
        "Internal Server Error",
        "Bad Gateway",
        "Service Unavailable",
        "Gateway Timeout",
        "OTHER_OR_NOT_RECORDED",
    }
)
_MIMES = frozenset({"application/json", "text/plain", "text/html", "OTHER_OR_NOT_RECORDED"})
_KEYS = frozenset(
    {
        "version",
        "operation",
        "status",
        "safe_http_reason",
        "content_type",
        "body_error_code",
        "body_sha256",
        "body_budget_exceeded",
    }
)


def validate_http_failure(value: Any) -> dict[str, Any] | None:
    if type(value) is not dict or set(value) != _KEYS:
        return None
    if (
        type(value["version"]) is not int
        or value["version"] != 1
        or value["operation"] != "boundary_health"
    ):
        return None
    status = value["status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return None
    if (
        not isinstance(value["safe_http_reason"], str)
        or value["safe_http_reason"] not in _REASONS
        or not isinstance(value["content_type"], str)
        or value["content_type"] not in _MIMES
    ):
        return None
    if value["body_error_code"] is not None and (
        not isinstance(value["body_error_code"], str) or value["body_error_code"] not in _CODES
    ):
        return None
    digest = value["body_sha256"]
    if digest is not None and (
        not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        return None
    if type(value["body_budget_exceeded"]) is not bool:
        return None
    return dict(value)


def http_failure_from_exception(error: BaseException) -> dict[str, Any] | None:
    try:
        return validate_http_failure(getattr(error, _ATTRIBUTE, None))
    except Exception:
        return None


def capture_boundary_http_failure(error: BaseException) -> None:
    try:
        status = getattr(error, "status", None)
        status = status if type(status) is int and 100 <= status <= 599 else None
        reason = getattr(error, "reason", None)
        reason = (
            reason if isinstance(reason, str) and reason in _REASONS else "OTHER_OR_NOT_RECORDED"
        )
        headers = getattr(error, "headers", None)
        mime = (
            headers.get("Content-Type") or headers.get("content-type")
            if headers is not None and hasattr(headers, "get")
            else None
        )
        mime = mime.split(";", 1)[0].strip().lower() if isinstance(mime, str) else None
        mime = mime if mime in _MIMES else "OTHER_OR_NOT_RECORDED"
        body = getattr(error, "body", None)
        over = isinstance(body, (bytes, str)) and len(body) > _LIMIT
        raw = None
        if not over:
            raw = (
                body.encode("utf-8")
                if isinstance(body, str)
                else body
                if isinstance(body, bytes)
                else None
            )
            over = raw is not None and len(raw) > _LIMIT
        code = None
        if raw is not None and not over:
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = None
            if type(payload) is dict:
                candidate = payload.get("reason") or payload.get("code")
                nested = payload.get("error")
                if candidate is None:
                    candidate = nested.get("code") if type(nested) is dict else nested
                code = candidate if isinstance(candidate, str) and candidate in _CODES else None
        safe = {
            "version": 1,
            "operation": "boundary_health",
            "status": status,
            "safe_http_reason": reason,
            "content_type": mime,
            "body_error_code": code,
            "body_sha256": hashlib.sha256(raw).hexdigest()
            if raw is not None and not over
            else None,
            "body_budget_exceeded": bool(over),
        }
        setattr(error, _ATTRIBUTE, safe)
    except Exception:
        # Diagnostic collection cannot replace the primary SDK failure.
        pass
