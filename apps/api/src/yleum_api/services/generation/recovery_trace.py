"""Closed, bounded diagnostics for causal recovery traces; never provider content."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from yleum_api.services.agent_progress import REDACTED, bounded_redacted_text

_IDENTITY_FIELDS = (
    "run_id", "attempt_id", "operation_id", "provider_call_id", "provider_request_id",
    "requirement_id", "snapshot_id",
)
_HASH_FIELDS = ("prompt_hash", "source_hash", "proof_hash", "snapshot_hash")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}\Z")
_HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
_ENUMS = {
    "stage": {
        "working", "repair", "repairing", "verify", "verifying", "waiting_dependency",
        "reconciling", "needs_input", "provider_request", "provider_response", "build",
        "proof", "dispatch", "finalization", "unknown",
    },
    "error_origin": {"browser", "provider", "gateway", "transport", "runtime", "unknown"},
    "finish_reason": {
        "stop", "tool_calls", "length", "content_filter", "end_turn", "tool_use",
        "max_tokens", "refusal", "pause_turn", "stop_sequence", "unknown",
    },
    "max_tokens_mode": {"sent", "omitted"},
    "transport_stage": {"connect", "headers", "body", "complete", "unknown"},
    "retry_decision": {"retry", "stop", "wait", "reconcile", "unknown"},
    "error_code": {
        "model_unavailable", "model_not_found", "validation_failed", "wallet_empty",
        "billing_reconciliation_required", "provider_output_truncated",
        "provider_output_invalid", "provider_output_filtered", "unknown",
    },
    "provider_refusal_category": {"access", "funds", "rate_limit", "unknown"},
}
_INTEGER_RANGES = {
    "requested_max_tokens": (1, 2_000_000), "prompt_tokens": (0, 2_000_000),
    "completion_tokens": (0, 2_000_000), "total_tokens": (0, 4_000_000),
    "http_status": (100, 599), "upstream_http_status": (400, 599),
    "attempt": (0, 4),
    "fence": (0, 2**63 - 1),
}
_URL = re.compile(
    r"(?<![\w])(?:(?:[a-z][a-z0-9+.-]*:)?//|/|\.\.?/|[\w.@%+-]+[/\?#])"
    r"[^\s<>\"')]+",
    re.IGNORECASE,
)
_COOKIE_ASSIGNMENT = re.compile(r"(?i)(\b(?:cookies?|set-cookie)[\"']?\s*[=:]\s*)[^\r\n]*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\bauthorization\s*:\s*)[^\r\n]+|"
    r"(\b(?:[\w-]*(?:token|secret|password|api[_-]?key|access[_-]?key)|key|"
    r"authorization)[\"']?\s*[=:]\s*)"
    r"(?:\"[^\"\r\n]*\"?|'[^'\r\n]*'?|[^\s,;\"'}]+)"
)


def _safe_location(value: str) -> str:
    """Queries, fragments and userinfo are unnecessary for locating a source."""
    try:
        parsed = urlsplit(value)
        if parsed.netloc:
            # A bounded preview may end before a userinfo '@'. Reject an
            # invalid authority rather than treating a password as a port.
            _ = parsed.port  # Validates numeric syntax and the port's range.
            if parsed.hostname is None or len(parsed.hostname) > 253:
                return REDACTED
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    except ValueError:
        return REDACTED


def _browser_text(value: str, *, location: bool = False) -> str:
    # Inspect a bounded prefix before cutting so credentials crossing 4 KiB
    # cannot escape redaction. Retain only source location, never URL secrets.
    preview = value.encode("utf-8")[:5120].decode("utf-8", errors="ignore")
    preview = (
        _safe_location(preview)
        if location else _URL.sub(lambda match: _safe_location(match.group()), preview)
    )
    preview = _COOKIE_ASSIGNMENT.sub(lambda match: match[1] + REDACTED, preview)
    preview = _SECRET_ASSIGNMENT.sub(lambda match: (match[1] or match[2]) + REDACTED, preview)
    return bounded_redacted_text(preview, max_bytes=4096)


def safe_recovery_trace(payload: Mapping[str, object]) -> dict[str, object]:
    """Copy only validated protocol facts and explicitly marked browser errors.

    A status alone never establishes a refusal category or retry permission.
    Browser diagnostics are an explicit exception for bounded, sanitized text;
    provider messages, request/response bodies and arbitrary metadata are dropped.
    """
    safe: dict[str, object] = {}
    for key in _IDENTITY_FIELDS:
        value = payload.get(key)
        if (isinstance(value, str) and _IDENTITY.fullmatch(value)
                and not value.isdecimal()
                and bounded_redacted_text(value, max_bytes=512) == value):
            safe[key] = value
    for key in _HASH_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and _HASH.fullmatch(value):
            safe[key] = value.lower()
    revision = payload.get("release_revision")
    if isinstance(revision, str) and re.fullmatch(r"[a-fA-F0-9]{40,64}", revision):
        safe["release_revision"] = revision.lower()
    for key, allowed in _ENUMS.items():
        value = payload.get(key)
        if isinstance(value, str) and value in allowed:
            safe[key] = value
    for key, (minimum, maximum) in _INTEGER_RANGES.items():
        value = payload.get(key)
        if type(value) is int and minimum <= value <= maximum:
            safe[key] = value
    for key in ("will_retry", "retry_safe", "provider_charge_ambiguous"):
        value = payload.get(key)
        if type(value) is bool:
            safe[key] = value
    diagnostic = payload.get("console_error")
    if safe.get("error_origin") == "browser" and isinstance(diagnostic, Mapping):
        safe["console_error"] = {
            key: _browser_text(value, location=key in {"source", "url"})
            for key in ("message", "source", "stack", "url")
            if isinstance(value := diagnostic.get(key), str)
        }
    return safe


def bind_recovery_payload(
    payload: Mapping[str, object], *, run_id: str, event_type: str,
) -> dict[str, object]:
    """Bind canonical trace slots to the durable owner without mutating input."""
    if event_type in {"generation.trace", "recovery.trace"}:
        return safe_recovery_trace({**payload, "run_id": run_id})
    bound = dict(payload)
    trace = bound.get("recovery_trace")
    if isinstance(trace, Mapping):
        bound["recovery_trace"] = safe_recovery_trace({**trace, "run_id": run_id})
    return bound
