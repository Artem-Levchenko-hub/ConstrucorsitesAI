"""Deterministic guardrails for credentials submitted to the app builder.

Credentials belong in Omnia's encrypted Integration Hub, never in a generated
project repository.  Prompt instructions alone are not a sufficient boundary:
the writer itself must reject secret files and secret-shaped literals before a
container or git object ever sees them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_SECRET_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
)

_MAX_RUNTIME_SECRET_ACCESS_RE = re.compile(
    r"(?i)\b(?:process\.env|import\.meta\.env|Bun\.env|Deno\.env)\b"
)
_MAX_PRIVILEGED_RUNTIME_IMPORT_RE = re.compile(
    r"(?i)(?:from\s+|require\s*\(|import\s*\()[\"']"
    r"(?:node:)?(?:fs|child_process|cluster|worker_threads)[\"']"
)
# A valid directive may share its line with exports or comments, and may appear
# inside a function body.  Matching the exact string literal anywhere is
# deliberately conservative: product copy almost never needs to render this
# phrase, while missing one occurrence creates a secret-bearing Server Action.
_MAX_SERVER_ACTION_DIRECTIVE_RE = re.compile(r"(?i)[\"']use[ \t]+server[\"']")
_MAX_SERVER_RUNTIME_IMPORT_RE = re.compile(
    r"(?i)(?:from\s+|require\s*\(|import\s*(?:\(\s*)?)[\"'](?:"
    r"server-only|next/(?:server|headers|cache)|"
    r"@/lib/max/(?:bot-api|session|validate-init-data)"
    r")[\"']"
)
_MAX_RAW_DB_IMPORT_RE = re.compile(
    r"(?i)(?:from\s+|require\s*\(|import\s*\()[\"'](?:"
    r"@/lib/db|(?:\.\.?/)+[^\"']*lib/db|drizzle-orm(?:/[^\"']*)?|pg|postgres"
    r")[\"']"
)

_MANAGED_AI_HINT = (
    "Секрет провайдера был удалён до передачи модели. Пользователю нужна AI-функция "
    "внутри MAX Mini App: реализуй её через управляемый requestOmniaAI из "
    "@/lib/omnia/integration-client. Не проси и не сохраняй внешний ключ, не создавай "
    ".env и не имитируй AI локальными ответами."
)


@dataclass(frozen=True)
class SafeMaxPrompt:
    chat_text: str
    model_text: str
    credential_removed: bool


def contains_provider_secret(value: str) -> bool:
    """Return true only for high-confidence credential shapes.

    The deliberately conservative patterns avoid intercepting ordinary product
    briefs that merely mention an API key or an environment-variable name.
    """

    return any(pattern.search(value or "") for pattern in _SECRET_TOKEN_PATTERNS)


def redact_provider_secrets(value: str) -> str:
    """Remove recognised credentials before chat persistence or display."""

    redacted = value or ""
    for pattern in _SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[CREDENTIAL REDACTED]", redacted)
    return redacted


def prepare_safe_max_prompt(value: str) -> SafeMaxPrompt:
    """Redact a pasted credential but keep the product request buildable.

    The chat stores only ``chat_text``.  The model receives ``model_text`` with
    an explicit managed-runtime instruction, never the credential.  This turns
    an accidental secret paste into a working AI-native app request instead of
    a dead-end configuration reply.
    """

    removed = contains_provider_secret(value)
    chat_text = redact_provider_secrets(value) if removed else value
    model_text = f"{chat_text}\n\n{_MANAGED_AI_HINT}" if removed else chat_text
    return SafeMaxPrompt(
        chat_text=chat_text,
        model_text=model_text,
        credential_removed=removed,
    )


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
    if _MAX_RUNTIME_SECRET_ACCESS_RE.search(candidate):
        return (
            "Generated MAX product code cannot read runtime environment variables. "
            "Use the managed MAX/integration client; secrets remain server-owned."
        )
    if _MAX_PRIVILEGED_RUNTIME_IMPORT_RE.search(candidate):
        return (
            "Generated MAX product code cannot import filesystem/process runtime modules. "
            "Use the managed MAX/integration client instead."
        )
    if _MAX_SERVER_ACTION_DIRECTIVE_RE.search(candidate):
        return (
            "Generated MAX product code is browser-only and cannot declare Server Actions. "
            "Use the managed MAX/integration client instead."
        )
    if _MAX_SERVER_RUNTIME_IMPORT_RE.search(candidate):
        return (
            "Generated MAX product code cannot import server-only Next.js or MAX modules. "
            "Use the managed MAX/integration client instead."
        )
    if _MAX_RAW_DB_IMPORT_RE.search(candidate):
        return (
            "Generated MAX product code cannot import a raw database client. "
            "Use createMaxAction/getMaxActions from the managed integration client."
        )
    return None


__all__ = [
    "SafeMaxPrompt",
    "contains_provider_secret",
    "is_secret_file",
    "max_model_write_rejection",
    "prepare_safe_max_prompt",
    "redact_provider_secrets",
]
