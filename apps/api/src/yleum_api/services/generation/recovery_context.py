"""Bounded handoff from frozen scope, never a lossy replacement of the request."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from yleum_api.services.agent_progress import redact_sensitive_text
from yleum_api.services.generation.recovery_contract import RecoveryState

_CONTEXT_BYTES = 24_000
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S,
)
_ASSIGNMENT = re.compile(
    r"(?<![\w$])[\"']?(?P<key>[\w$-]+)[\"']?\s*[:=]\s*",
)
_SECRET_KEY = re.compile(
    r"secret|token|pass|dsn|(?:api|private|access)[_-]?key|authorization$|^database_url$",
    re.I,
)
_AUTH_VALUE = re.compile(r"(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/-]+=*(?=$|[\s;,}])", re.I)
_SENSITIVE_HINT = re.compile(
    r"secret|token|password|pass|private_?key|access_?key|api[_-]?key|dsn|authorization|"
    r"database_url|sk-|gh[pousr]_|://", re.I,
)


def _secret_value_end(
    text: str, start: int, *, preserve_scope: bool, authorization: bool,
) -> int:
    if start == len(text):
        return start
    quote = text[start] if text[start] in "\"'`" else None
    if authorization and not quote:
        if credential := _AUTH_VALUE.match(text, start):
            return credential.end()
        # Unknown schemes can carry multiple values. Never guess a one-word
        # boundary or expose the remainder; callers must supply explicit quotes.
        if preserve_scope:
            raise ValueError("ambiguous credential boundary in frozen scope")
        return len(text)
    position = start + 1 if quote else start
    while position < len(text):
        character = text[position]
        if quote:
            if character == "\\":
                position += 2
                continue
            if character == quote:
                return position + 1
            if quote == "`" and text.startswith("${", position):
                # Never interpret executable interpolation or mistake nested
                # backticks for the end of the secret. Omit the uncertain tail.
                if preserve_scope:
                    raise ValueError("ambiguous credential boundary in frozen scope")
                return len(text)
        elif character.isspace() or character in ";,}":
            return position
        position += 1
    if quote and preserve_scope:
        raise ValueError("ambiguous credential boundary in frozen scope")
    return len(text)  # An unfinished literal must not expose its remaining tail.


def _redact_assignments(text: str, *, preserve_scope: bool) -> str:
    pieces: list[str] = []
    cursor = search_from = 0
    while match := _ASSIGNMENT.search(text, search_from):
        search_from = match.end()
        if not _SECRET_KEY.search(match["key"]):
            continue
        end = _secret_value_end(
            text, match.end(), preserve_scope=preserve_scope,
            authorization=match["key"].lower().endswith("authorization"),
        )
        pieces.extend((text[cursor:match.end()], "[REDACTED]"))
        cursor = search_from = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _safe(text: str, *, preserve_scope: bool = False) -> str:
    if preserve_scope and any(
        "-----END " not in match[0] for match in _PRIVATE_KEY.finditer(text)
    ):
        raise ValueError("ambiguous credential boundary in frozen scope")
    text = _redact_assignments(
        _PRIVATE_KEY.sub("[REDACTED]", text), preserve_scope=preserve_scope,
    )
    if preserve_scope:
        # Assignment values are already masked. Apply the remaining DSN/provider
        # rules per token: legacy whole-line shell/header rules would eat scope.
        return re.sub(r"\S+", lambda match: redact_sensitive_text(match[0]), text)
    # Redact before any byte cut; skip unrelated minified/generated lines.
    return "".join(
        redact_sensitive_text(line)
        if _SENSITIVE_HINT.search(line) else line for line in text.splitlines(keepends=True)
    )


def _excerpt(safe: str, max_bytes: int) -> str:
    """Bound already-redacted text without rescanning raw output or credentials."""
    encoded = safe.encode()
    if len(encoded) <= max_bytes:
        return safe
    marker = b"\n[output omitted]\n"
    available = max_bytes - len(marker)
    head = available // 3
    return (encoded[:head].decode(errors="ignore") + marker.decode()
            + encoded[-(available - head):].decode(errors="ignore"))


def build_recovery_context(
    state: RecoveryState, *, relevant_files: Mapping[str, str], diagnostic: str,
) -> str:
    """Keep all mandatory scope/effects; fail closed if those exceed the byte budget.

    Task 3's frozen original-request requirement is the source of original text.
    Its exact admission hash is checked before redaction. Optional source and
    diagnostics are bounded only after secrets are removed; no raw transcript.
    """
    originals = [r for r in state.requirements if not r.required and r.check_kind == "manual"
                 and r.origin_prompt_sha256 == state.identity.prompt_hash
                 and hashlib.sha256(r.text.encode()).hexdigest() == state.identity.prompt_hash]
    if len(originals) != 1:
        raise ValueError("original request missing or inconsistent with admission hash")
    original = originals[0]
    if sum(len(r.text.encode()) for r in state.requirements) > _CONTEXT_BYTES - 512:
        raise ValueError("frozen scope exceeds recovery context budget")
    if any(r.origin_prompt_sha256 != state.identity.prompt_hash for r in state.requirements):
        raise ValueError("original request obligations have conflicting scope")
    approaches = dict.fromkeys((*state.tried_strategies, *(
        o.action_signature for o in state.observations if o.action_signature)))
    sections = [
        "Original request (frozen):\n" + _safe(original.text, preserve_scope=True),
        "Frozen obligations:\n" + "\n".join(
            _safe(f"{r.id} [{r.check_kind}; required={r.required}]: {r.text}", preserve_scope=True)
            for r in state.requirements if r.id != original.id),
        "Unresolved operation intents (do not replay):\n"
        + "\n".join(map(str, state.unresolved_operation_ids)),
        "Previous approaches (do not blindly repeat):\n" + "\n".join(map(_safe, approaches)),
        "Durable context reference: "
        + _safe(state.context_ref.object_key if state.context_ref else "none"),
        "Checkpoint: " + _safe(state.checkpoint.object_key if state.checkpoint else "none"),
    ]
    required = "\n\n".join(sections)
    if len(required.encode()) > _CONTEXT_BYTES - 512:
        raise ValueError("frozen scope exceeds recovery context budget; load scope explicitly")
    # Repeated compiler lines waste attention and can crowd out the actual cause.
    diagnostic = "\n".join(dict.fromkeys(_safe(diagnostic).splitlines()))
    remaining = _CONTEXT_BYTES - len(required.encode())
    detail = _excerpt(diagnostic, min(4000, remaining // 2))
    result = required + "\n\nCurrent diagnostic:\n" + detail + "\n\nRelevant source:\n"
    for path, source in sorted(relevant_files.items()):
        remaining = _CONTEXT_BYTES - len(result.encode())
        if remaining < 64:
            break
        fragment = _safe(path) + "\n" + _safe(source)
        result += _excerpt(fragment, min(6000, remaining - 2)) + "\n\n"
    return result
