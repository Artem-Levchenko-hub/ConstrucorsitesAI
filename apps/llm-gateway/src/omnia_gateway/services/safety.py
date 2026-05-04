"""Prompt-injection guard — regex filter applied to user messages only.

R-10 fail fast: rather than refusing the request, we replace the offending
substring with `[фильтровано]` so the request still completes — but we log so
the security team sees attack patterns.

Only user-role messages are filtered; system prompts come from the gateway and
are trusted. Disable globally with `SAFETY_FILTER_ENABLED=false`.
"""

from __future__ import annotations

import re

import structlog

from omnia_gateway.core.config import get_settings

log = structlog.get_logger(__name__)

REPLACEMENT = "[фильтровано]"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"^\s*system\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"</file\s*>", re.IGNORECASE),
    # base64-ish blob over 1024 chars — likely smuggled payload
    re.compile(r"[A-Za-z0-9+/=]{1024,}"),
]


def sanitize(content: str) -> tuple[str, list[str]]:
    """Return (cleaned_text, list_of_pattern_names_triggered)."""
    triggered: list[str] = []
    cleaned = content
    for idx, pattern in enumerate(_PATTERNS):
        if pattern.search(cleaned):
            triggered.append(f"P{idx}")
            cleaned = pattern.sub(REPLACEMENT, cleaned)
    return cleaned, triggered


def sanitize_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Apply sanitize() to user-role messages only. No-op if filter is disabled."""
    if not get_settings().safety_filter_enabled:
        return messages

    out: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") != "user":
            out.append(m)
            continue
        cleaned, triggered = sanitize(m.get("content", ""))
        if triggered:
            log.warning("safety.injection_detected", patterns=triggered)
        out.append({**m, "content": cleaned})
    return out
