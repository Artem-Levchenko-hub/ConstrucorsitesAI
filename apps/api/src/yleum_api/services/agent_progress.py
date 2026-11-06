"""Safe persistence helpers for the user-visible agent transcript.

Agent tools execute inside project containers, where application credentials are
available as environment variables.  A command such as ``env`` must never turn
those credentials into a persisted/chat-visible tool observation.  Redaction is
therefore applied both before persistence and again at the public schema edge.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

REDACTED = "[REDACTED]"
PROVIDER_RESPONSE_DETAIL = "Ответ модели проверен перед применением действий."
PROVIDER_ERROR_DETAILS = {
    "access": "Провайдер отказал в доступе к запросу.",
    "funds": "Провайдер сообщил о недостатке средств.",
    "rate_limit": "Провайдер ограничил частоту запросов.",
}
PROVIDER_ERROR_DETAIL = "Не получен подтверждённый результат запроса к модели."
PROVIDER_HTTP_422_DETAIL = (
    "Провайдер модели отклонил запрос (HTTP 422). Требуется проверить параметры запроса."
)

_SENSITIVE_NAME = (
    r"(?:"
    r"DATABASE_URL|AUTH_SECRET|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|PRIVATE_KEY|ACCESS_KEY|API_KEY|DSN)"
    r"[A-Z0-9_]*"
    r")"
)
_SHELL_ASSIGNMENT_RE = re.compile(rf"(?im)^(\s*(?:export\s+)?{_SENSITIVE_NAME}\s*=\s*)([^\r\n]*)$")
_JSON_ASSIGNMENT_RE = re.compile(
    rf"""(?ix)
    (
      ["']?{_SENSITIVE_NAME}["']?
      \s*:\s*
      ["']
    )
    ([^"'\r\n]*)
    (["'])
    """
)
_DSN_PASSWORD_RE = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|redis|mongodb)"
    r"(?:\+[a-z0-9_]+)?://[^:\s/@]+:)([^@\s/]+)(@)"
)
_AUTH_HEADER_RE = re.compile(
    r"(?im)^(\s*(?:authorization|x-api-key|x-max-bot-api-secret)\s*:\s*)"
    r"([^\r\n]+)$"
)
_KNOWN_TOKEN_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})")
_PARTIAL_DSN_PASSWORD_RE = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|redis|mongodb)"
    r"(?:\+[a-z0-9_]+)?://[^:\s/@]+:)([^@\s/]+)$"
)
_PARTIAL_JSON_ASSIGNMENT_RE = re.compile(
    rf"(?is)([\"']?{_SENSITIVE_NAME}[\"']?\s*:\s*[\"'])([^\"'\r\n]*)$"
)


def redact_sensitive_text(value: str) -> str:
    """Return a transcript-safe representation of arbitrary tool output."""

    if not value:
        return value
    redacted = _SHELL_ASSIGNMENT_RE.sub(rf"\1{REDACTED}", value)
    redacted = _JSON_ASSIGNMENT_RE.sub(rf"\1{REDACTED}\3", redacted)
    redacted = _DSN_PASSWORD_RE.sub(rf"\1{REDACTED}\3", redacted)
    redacted = _AUTH_HEADER_RE.sub(rf"\1{REDACTED}", redacted)
    return _KNOWN_TOKEN_RE.sub(REDACTED, redacted)


def bounded_redacted_text(value: str, *, max_bytes: int, lookahead_bytes: int = 1024) -> str:
    """Redact a bounded prefix, including secrets that cross the storage boundary."""

    if max_bytes <= 0 or lookahead_bytes < 0:
        raise ValueError("redaction byte limits must be valid")
    preview = value.encode("utf-8")[: max_bytes + lookahead_bytes].decode(
        "utf-8",
        errors="ignore",
    )
    redacted = redact_sensitive_text(preview)
    redacted = _PARTIAL_DSN_PASSWORD_RE.sub(rf"\1{REDACTED}", redacted)
    redacted = _PARTIAL_JSON_ASSIGNMENT_RE.sub(rf"\1{REDACTED}", redacted)
    encoded = redacted.encode("utf-8")
    marker_start = redacted.rfind(REDACTED)
    if marker_start >= 0:
        prefix = redacted[:marker_start].encode("utf-8")
        if len(prefix) < max_bytes < len(prefix) + len(REDACTED):
            kept = prefix[: max_bytes - len(REDACTED)].decode("utf-8", errors="ignore")
            return kept + REDACTED
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def bounded_redacted_diagnostic(value: str, *, max_bytes: int) -> str:
    """Keep both the cause prefix and final compiler/command error, after redaction."""
    marker = b"\n[output omitted]\n"
    if max_bytes <= len(marker):
        return bounded_redacted_text(value, max_bytes=max_bytes)
    # Redact before splitting: a credential crossing a cut must never become an
    # unlabelled suffix. The caller already holds this command result in memory.
    safe = redact_sensitive_text(value).encode("utf-8")
    if len(safe) <= max_bytes:
        return safe.decode("utf-8")
    available = max_bytes - len(marker)
    head = available // 3
    return (
        safe[:head].decode("utf-8", errors="ignore")
        + marker.decode()
        + safe[-(available - head):].decode("utf-8", errors="ignore")
    )


def provider_error_origin(payload: dict[str, Any]) -> str:
    """Keep the known error hop without enabling browser-text diagnostics."""
    for source in (payload, payload.get("recovery_trace")):
        value = source.get("error_origin") if isinstance(source, dict) else None
        if isinstance(value, str) and value in {"provider", "gateway", "transport", "unknown"}:
            return value
    return "provider"


def _provider_error_attribution(step: dict[str, Any]) -> tuple[str, bool]:
    """Keep missing/conflicting 422 attribution unknown across public re-sanitization."""
    origin = provider_error_origin(step)
    trace = step.get("recovery_trace")
    sources = (step, trace) if isinstance(trace, dict) else (step,)
    statuses = [source["upstream_http_status"] for source in sources
                if "upstream_http_status" in source]
    if not any(type(value) is int and value == 422 for value in statuses):
        return origin, False
    origins = [source["error_origin"] for source in sources if "error_origin" in source]
    if (not origins
            or not all(type(value) is int and value == 422 for value in statuses)
            or not all(value == origin for value in origins)):
        return "unknown", False
    return origin, origin == "provider"


def _provider_error_display(category: object, known_upstream_422: bool) -> tuple[str, str]:
    """Name only the explicit upstream status, never parse a provider body."""
    action = "Запрос к модели не завершён"
    if isinstance(category, str) and category in PROVIDER_ERROR_DETAILS:
        return action, PROVIDER_ERROR_DETAILS[category]
    if known_upstream_422:
        return "Провайдер отклонил запрос", PROVIDER_HTTP_422_DETAIL
    return action, PROVIDER_ERROR_DETAIL


def _step_identity(step: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    for key in ("eventId", "runId"):
        value = step.get(key)
        if not isinstance(value, str) or len(value) != 36:
            return {}
        try:
            if str(UUID(value)) != value:
                return {}
        except ValueError:
            return {}
        identity[key] = value
    seq = step.get("seq")
    if type(seq) is int and seq > 0:
        identity["seq"] = seq
    count = step.get("actionCount")
    if type(count) is int and 0 <= count <= 2**31 - 1:
        identity["actionCount"] = count
    if type(step.get("historyTruncated")) is bool:
        identity["historyTruncated"] = step["historyTruncated"]
    return identity


def sanitize_agent_step(step: dict[str, Any]) -> dict[str, Any]:
    """Copy one agent-step payload while redacting every textual surface."""

    identity = _step_identity(step)
    safe = {key: value for key, value in step.items()
            if key not in {"eventId", "runId", "seq", "actionCount", "historyTruncated"}}
    provider_error = safe.get("kind") == "provider_error"
    provider_trace = provider_error or safe.get("tool") == "provider_response"
    error_origin, known_upstream_422 = (
        _provider_error_attribution(step) if provider_trace else ("unknown", False)
    )
    if provider_trace:
        from yleum_api.services.generation.recovery_trace import safe_recovery_trace

        # Historical rows cross the same public boundary as new observations.
        # A provider's arbitrary text must not survive in display/extra fields.
        safe = {
            key: value for key, value in safe.items()
            if key in {"step", "kind", "tool", "ok", "skipped", "message_id", "recovery_trace"}
        }
        safe.update(safe_recovery_trace({**step, "error_origin": error_origin}))
        safe["path"] = ""
        safe["tool"] = "provider_request" if provider_error else "provider_response"
        safe["action"] = (
            "Запрос к модели не завершён" if provider_error else "Проверяю ответ модели"
        )
        category = safe.get("provider_refusal_category")
        safe["detail"] = PROVIDER_RESPONSE_DETAIL
        if provider_error:
            safe["action"], safe["detail"] = _provider_error_display(category, known_upstream_422)
            if (safe.get("error_code") == "deadline_before_dispatch"
                    and safe.get("error_origin") == "gateway"
                    and safe.get("provider_charge_ambiguous") is False):
                safe["action"] = "Время запроса истекло"
                safe["detail"] = (
                    "Новый запрос к модели не отправлен: недостаточно оставшегося времени."
                )
        reason = step.get("reason")
        if isinstance(reason, str) and reason in {
            "output_limit", "invalid_tool_arguments", "missing_tool_arguments",
        }:
            safe["reason"] = reason
        attempt = step.get("recovery_attempt")
        if type(attempt) is int and 1 <= attempt <= 3:
            safe["recovery_attempt"] = attempt
    if "recovery_trace" in safe:
        from yleum_api.services.generation.recovery_trace import safe_recovery_trace

        trace = safe["recovery_trace"]
        if isinstance(trace, dict) and provider_trace:
            trace = {**trace, "error_origin": error_origin}
        safe["recovery_trace"] = safe_recovery_trace(trace) if isinstance(trace, dict) else {}
    for key in ("action", "tool", "path", "detail", "human"):
        value = safe.get(key)
        if isinstance(value, str):
            safe[key] = redact_sensitive_text(value)
    safe.update(identity)
    return safe


def sanitize_agent_steps(value: object) -> list[dict[str, Any]] | None:
    """Normalize and redact a possibly-untrusted persisted transcript."""

    if value is None:
        return None
    if not isinstance(value, list):
        return None
    rows = [sanitize_agent_step(row) for row in value if isinstance(row, dict)]
    return rows or None


async def scrub_persisted_agent_steps() -> tuple[int, int]:
    """Idempotently remove secrets from historical persisted observations."""

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.core.db import get_engine
    from yleum_api.models.message import Message

    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    scanned = 0
    changed = 0
    async with factory() as session:
        rows = list(
            (await session.execute(select(Message).where(Message.agent_steps.is_not(None))))
            .scalars()
            .all()
        )
        for message in rows:
            scanned += 1
            safe = sanitize_agent_steps(message.agent_steps)
            if safe != message.agent_steps:
                message.agent_steps = safe
                changed += 1
        await session.commit()
    return scanned, changed


__all__ = [
    "REDACTED",
    "bounded_redacted_text",
    "redact_sensitive_text",
    "sanitize_agent_step",
    "sanitize_agent_steps",
    "scrub_persisted_agent_steps",
]
