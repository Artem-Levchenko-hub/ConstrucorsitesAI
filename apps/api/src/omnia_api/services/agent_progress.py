"""Safe persistence helpers for the user-visible agent transcript.

Agent tools execute inside project containers, where application credentials are
available as environment variables.  A command such as ``env`` must never turn
those credentials into a persisted/chat-visible tool observation.  Redaction is
therefore applied both before persistence and again at the public schema edge.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

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


def sanitize_agent_step(step: dict[str, Any]) -> dict[str, Any]:
    """Copy one agent-step payload while redacting every textual surface."""

    safe = dict(step)
    for key in ("action", "tool", "path", "detail", "human"):
        value = safe.get(key)
        if isinstance(value, str):
            safe[key] = redact_sensitive_text(value)
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

    from omnia_api.core.db import get_engine
    from omnia_api.models.message import Message

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
