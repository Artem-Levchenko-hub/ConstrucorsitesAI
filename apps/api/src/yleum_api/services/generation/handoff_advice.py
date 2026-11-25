"""Optional, unverified suggestions from the author of the current candidate."""
from __future__ import annotations

import re

from yleum_api.services.agent_progress import redact_sensitive_text
from yleum_api.services.secret_safety import redact_provider_secrets

HANDOFF_FIELDS = ("primary_action", "builder_next", "owner_input", "owner_request_quote")
HANDOFF_NOTE = (
    "Optional handoff object, in the owner's language: primary_action is one thing to try "
    "in this app; builder_next is one concrete remaining change for the builder. Only if "
    "the owner explicitly promised material only they can supply, give owner_input and "
    "owner_request_quote (their exact words). Never ask the owner to fix code, run tests, "
    "or supply credentials. Omit irrelevant fields. Use short plain sentences, no links "
    "or code. These are suggestions, not test results."
)

_LINE = re.compile(r"""[^\W_](?:[^\W_]|[ .,!?%:'"«»“”()—–…-])*""")
_UNSAFE = re.compile(
    r"\b(?:[\w-]+\.)+[^\W\d_]{2,}\b|\b(?:\d{1,3}\.){3}\d{1,3}\b|[A-Za-z0-9-]{28,}|"
    r"\b(?:bearer|password|credentials?|secret|api[ -]?key|access[ -]?token|"
    r"парол\w*|токен\w*|секретн\w* ключ\w*)\b|"
    r"\b(?:javascript|data|https?|file):|\w\([^\s)]*\)|"
    r"\b(?:pnpm|npm|npx|docker|kubectl|sudo)\s+\S|\b\d{10,}\b",
    re.IGNORECASE,
)
_BUILDER_TASK = re.compile(
    r"\b(?:компилятор\w*|депло\w*|отладчик\w*|unit tests?|typecheck|build failure)\b|"
    r"\b(?:исправ\w*|допиш\w*|почин\w*|перезапуст\w*)\s+"
    r"(?:код|тест\w*|обработчик\w*|сборк\w*)\b",
    re.IGNORECASE,
)


def normalize_handoff(raw: object) -> dict[str, str] | None:
    """Drop unsuitable fields entirely. Never shorten a credential or an owner quote."""
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in HANDOFF_FIELDS:
        value = raw.get(key)
        if not isinstance(value, str) or not 1 <= len(value) <= 240:
            continue
        line = value.strip(" ")
        if (
            _LINE.fullmatch(line) and not _UNSAFE.search(line)
            and redact_sensitive_text(line) == line and redact_provider_secrets(line) == line
        ):
            result[key] = line
    if (not ("owner_input" in result and "owner_request_quote" in result)
            or _BUILDER_TASK.search(result["owner_input"])
            or _BUILDER_TASK.search(result["owner_request_quote"])):
        result.pop("owner_input", None)
        result.pop("owner_request_quote", None)
    return result or None
