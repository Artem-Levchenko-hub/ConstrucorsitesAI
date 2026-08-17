"""Deterministic guardrails for credentials submitted to the app builder.

Credentials belong in Omnia's encrypted Integration Hub, never in a generated
project repository.  Prompt instructions alone are not a sufficient boundary:
the writer itself must reject secret files and secret-shaped literals before a
container or git object ever sees them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

_SECRET_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
)
_LABELLED_SECRET_RE = re.compile(
    r"((?:api[\s_-]*key|ключ|token|токен)\s*(?:[:=—–-]|это)?\s*[\"'`]?)"
    r"([^\s\"'`,;]{16,})",
    re.IGNORECASE,
)


def contains_provider_secret(value: str) -> bool:
    """Return true only for high-confidence credential shapes.

    The deliberately conservative patterns avoid intercepting ordinary product
    briefs that merely mention an API key or an environment-variable name.
    """

    candidate = value or ""
    return any(pattern.search(candidate) for pattern in _SECRET_TOKEN_PATTERNS) or bool(
        _LABELLED_SECRET_RE.search(candidate)
    )


def redact_provider_secrets(value: str) -> str:
    """Remove recognised credentials before chat persistence or display."""

    redacted = value or ""
    for pattern in _SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[CREDENTIAL REDACTED]", redacted)
    redacted = _LABELLED_SECRET_RE.sub(
        lambda match: f"{match.group(1)}[CREDENTIAL REDACTED]",
        redacted,
    )
    return redacted


def selected_elements_contain_provider_secret(
    elements: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Detect credentials in every user-controlled select-mode field."""

    return any(
        contains_provider_secret(value)
        for element in elements or ()
        for value in element.values()
        if isinstance(value, str)
    )


def redact_selected_element_secrets(
    elements: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Return a persistence/model-safe copy of select-mode metadata."""

    if not elements:
        return None
    return [
        {
            key: redact_provider_secrets(value) if isinstance(value, str) else value
            for key, value in element.items()
        }
        for element in elements
    ]


def is_secret_file(path: str) -> bool:
    """Detect repository paths that may contain runtime credentials."""

    normalized = (path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    name = PurePosixPath(normalized).name.lower()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name
        in {
            "secrets.json",
            "secrets.yaml",
            "secrets.yml",
        }
    )


def max_model_write_rejection(path: str, candidate: str) -> str | None:
    """Explain why a MAX model write is unsafe, or return ``None``."""

    if is_secret_file(path):
        return (
            "Credential files are managed by Omnia and cannot be written by the "
            "generation agent. Use the Studio Integration Hub; never create .env files."
        )
    if contains_provider_secret(candidate):
        return (
            "A provider credential was detected and the write was blocked before it "
            "reached the project repository. Use the Studio Integration Hub instead."
        )
    return None


__all__ = [
    "contains_provider_secret",
    "is_secret_file",
    "max_model_write_rejection",
    "redact_provider_secrets",
    "redact_selected_element_secrets",
    "selected_elements_contain_provider_secret",
]
