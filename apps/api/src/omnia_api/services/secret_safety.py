"""Deterministic guardrails for credentials submitted to the app builder.

Credentials belong in Omnia's encrypted Integration Hub, never in a generated
project repository.  Prompt instructions alone are not a sufficient boundary:
the writer itself must reject secret files and secret-shaped literals before a
container or git object ever sees them.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_SECRET_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
)
_LABELLED_SECRET = re.compile(
    r"(?i)(\b(?:api[_ -]?(?:key|ключ)|token|secret|password|ключ|токен|пароль)"
    r"\s*(?::|=|—|-)\s*)([A-Za-z0-9][A-Za-z0-9._~+/=-]{15,})"
)


def contains_provider_secret(value: str) -> bool:
    """Return true only for high-confidence credential shapes.

    The deliberately conservative patterns avoid intercepting ordinary product
    briefs that merely mention an API key or an environment-variable name.
    """

    candidate = value or ""
    return bool(_LABELLED_SECRET.search(candidate)) or any(
        pattern.search(candidate) for pattern in _SECRET_TOKEN_PATTERNS
    )


def redact_provider_secrets(value: str) -> str:
    """Remove recognised credentials before chat persistence or display."""

    redacted = _LABELLED_SECRET.sub(
        lambda match: f"{match.group(1)}[CREDENTIAL REDACTED]",
        value or "",
    )
    for pattern in _SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[CREDENTIAL REDACTED]", redacted)
    return redacted


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
]
