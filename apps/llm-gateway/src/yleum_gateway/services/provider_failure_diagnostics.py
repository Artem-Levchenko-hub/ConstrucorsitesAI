"""Best-effort operator-only HTTP failure evidence, never a provider action.

This projection does not read streams, retain request text, or change an
exception/response. Sanitized provider text goes only to the private store;
stdout contains metadata and an opaque artifact reference, never body text.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
import structlog

from yleum_gateway.services.provider_failure_store import StoreResult, store_provider_failure
from yleum_gateway.services.provider_validation_diagnostics import (
    _body,
    _redact,
    provider_diagnostic_scope,
    provider_secret_forms,
)

log = structlog.get_logger(__name__)
_MAX_RESPONSE = 16 * 1024
_MAX_REQUEST_SHAPE = 2 * 1024 * 1024
_HEADER_NAMES = ("request-id", "x-request-id", "x-llmgw-request-id", "x-trace-id")
_HEADER_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_CREDENTIAL_NAME = re.compile(
    r"(?:authorization|cookie|password|passwd|credential|secret|token|api[_-]?key|"
    r"private[_-]?key|auth[_-]?key|session[_-]?key)", re.I,
)
_ASSIGNMENT = re.compile(
    r"(\b[A-Za-z0-9_-]*(?:authorization|cookie|password|passwd|credentials?|secret|"
    r"token|api[_-]?key|private[_-]?key|auth[_-]?key|session[_-]?key)[A-Za-z0-9_-]*"
    r"\b[\"']?\s*[:=]\s*)"
    r"(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\r\n,;]+)", re.I,
)
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/%=-]+", re.I)
_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{0,31}:(?://|\\/\\/)[^\s<>\"']+", re.I)
_FIELDS = ("model", "messages", "stream", "max_tokens", "max_completion_tokens",
           "temperature", "top_p", "tools", "tool_choice", "response_format",
           "stop", "system", "metadata", "user", "thinking")
_ROLES = ("system", "developer", "user", "assistant", "tool", "function")
_CONTENT_TYPES = ("text", "image_url", "input_text", "input_image", "tool_use", "tool_result")
_MODELS = {
    "claude-sonnet-5": "claude-sonnet-5",
    "anthropic/claude-sonnet-5": "claude-sonnet-5",
    "claude-sonnet-5.5": "claude-sonnet-5.5",
    "anthropic/claude-sonnet-5.5": "claude-sonnet-5.5",
    "~anthropic/claude-sonnet-latest": "claude-sonnet-latest",
    "gemini-3.1-pro-preview-customtools": "gemini-3.1-pro-preview-customtools",
    "google/gemini-3.1-pro-preview-customtools": "gemini-3.1-pro-preview-customtools",
}
_STORE_REASONS = {"store_unavailable", "unsafe_store", "store_busy", "store_capacity_exceeded",
                  "invalid_packet", "packet_budget_exceeded", "store_write_failed", "artifact_collision"}


@dataclass(frozen=True)
class _Pairs:
    values: list[tuple[str, object]]


def _text(value: str, secrets: set[str]) -> str:
    value = _URL.sub("[REDACTED_URL]", _redact(value, secrets))
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _ASSIGNMENT.sub(lambda match: match[1] + "[REDACTED]", value)


def _json_text(value: object, secrets: set[str], *, sanitize: bool = True) -> str:
    # Object pairs preserve duplicate diagnostic fields instead of silently
    # dropping an earlier provider error while sanitizing a later one.
    if isinstance(value, _Pairs):
        pairs = []
        for key, item in value.values:
            clean = "[REDACTED]" if sanitize and _CREDENTIAL_NAME.search(key) else item
            pairs.append(json.dumps(_text(key, secrets) if sanitize else key, ensure_ascii=False)
                         + ":" + _json_text(clean, secrets, sanitize=sanitize))
        return "{" + ",".join(pairs) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_json_text(item, secrets, sanitize=sanitize) for item in value) + "]"
    return json.dumps(_text(value, secrets) if sanitize and isinstance(value, str) else value,
                      ensure_ascii=False, allow_nan=False)


def _response_body(raw: bytes | None, secrets: set[str]) -> dict[str, object]:
    if raw is None:
        return {"sha256": None, "bytes": None, "content_state": "unavailable",
                "reason": "response_not_read"}
    body = _body(raw, secrets, within_budget=len(raw) <= _MAX_RESPONSE)
    if "utf8" not in body:
        return body
    original = str(body["utf8"])
    try:
        try:
            parsed = json.loads(original, object_pairs_hook=_Pairs)
        except (ValueError, RecursionError):
            cleaned = _text(original, secrets)
        else:
            # Keep exact bytes when no JSON value/key needs sanitization;
            # compare decoded strings too, including escaped URL spellings.
            cleaned_json = _json_text(parsed, secrets)
            baseline_json = _json_text(parsed, set(), sanitize=False)
            cleaned = original if cleaned_json == baseline_json else cleaned_json
    except Exception:
        return {"sha256": body["sha256"], "bytes": body["bytes"],
                "content_state": "withheld", "reason": "sanitization_failed"}
    try:
        cleaned_bytes = len(cleaned.encode("utf-8"))
    except UnicodeError:
        return {"sha256": body["sha256"], "bytes": body["bytes"],
                "content_state": "withheld", "reason": "sanitization_failed"}
    if cleaned_bytes > _MAX_RESPONSE:
        return {"sha256": body["sha256"], "bytes": body["bytes"],
                "content_state": "withheld", "reason": "sanitized_body_budget_exceeded"}
    return {**body, "content_state": "redacted" if cleaned != raw.decode("utf-8")
            else "exact_utf8", "utf8": cleaned}


def _headers(headers: httpx.Headers, secrets: set[str]) -> dict[str, str]:
    result = {}
    for name in _HEADER_NAMES:
        values = headers.get_list(name)
        if len(values) == 1 and _HEADER_VALUE.fullmatch(values[0]):
            clean = _redact(values[0], secrets)
            if clean == values[0]:
                result[name] = clean
    return result


def _kind(value: object) -> str:
    if value is None:
        return "null"
    return {bool: "boolean", int: "integer", float: "number", str: "string",
            list: "array", dict: "object"}.get(type(value), "other")


def _request_shape(raw: bytes | None) -> dict[str, object]:
    if raw is None:
        return {"content_state": "unavailable", "reason": "request_not_read"}
    if len(raw) > _MAX_REQUEST_SHAPE:
        return {"content_state": "withheld", "reason": "request_shape_budget_exceeded"}
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return {"content_state": "unavailable", "reason": "request_not_json"}
    if not isinstance(value, dict):
        return {"content_state": "unavailable", "reason": "request_not_object"}
    result: dict[str, object] = {
        "content_state": "shape_only",
        "field_presence": {name: name in value for name in _FIELDS},
        "unknown_field_count": len(set(value) - set(_FIELDS)),
        "field_types": {name: _kind(value[name]) for name in _FIELDS if name in value},
        "model_class": _MODELS.get(value["model"], "other_or_unavailable")
        if isinstance(value.get("model"), str) else "other_or_unavailable",
    }
    messages = value.get("messages")
    if isinstance(messages, list):
        roles = {role: 0 for role in (*_ROLES, "other")}
        content = {kind: 0 for kind in (*_CONTENT_TYPES, "string", "other")}
        for message in messages:
            if not isinstance(message, dict):
                roles["other"] += 1
                continue
            role = message.get("role")
            roles[role if isinstance(role, str) and role in _ROLES else "other"] += 1
            items = message.get("content")
            if isinstance(items, str):
                content["string"] += 1
            elif isinstance(items, list):
                for item in items:
                    kind = item.get("type") if isinstance(item, dict) else None
                    content[kind if isinstance(kind, str) and kind in _CONTENT_TYPES else "other"] += 1
            else:
                content["other"] += 1
        result.update(message_count=len(messages), roles=roles, content_types=content)
    if isinstance(value.get("tools"), list):
        result["tool_count"] = len(value["tools"])
    return result


def _content(message: httpx.Request | httpx.Response) -> bytes | None:
    try:
        return message.content
    except (httpx.RequestNotRead, httpx.ResponseNotRead):
        return None


def _packet(response: httpx.Response, credential: str, provider_scope: str) -> dict[str, object] | None:
    if provider_scope not in {"llmgw", "aitunnel"} or not 400 <= response.status_code <= 599:
        return None
    try:
        request = response.request
    except RuntimeError:
        request = None
    request_headers = request.headers if request is not None else httpx.Headers()
    secrets = set(provider_secret_forms(credential, request_headers, response.headers))
    sent, received = _content(request) if request is not None else None, _content(response)
    endpoint = {"/v1/chat/completions": "chat_completions", "/v1/messages": "messages"}
    return dict(
        schema_version=1, diagnostic_visibility="operator_only",
        captured_at=datetime.now(UTC).isoformat(),
        provider_scope=provider_scope, upstream_http_status=response.status_code,
        **provider_diagnostic_scope(),
        endpoint_class=endpoint.get(request.url.path, "other") if request is not None else "other",
        request_method=request.method if request is not None and request.method in {"POST", "GET"} else "other",
        request_headers=_headers(request_headers, secrets),
        response_headers=_headers(response.headers, secrets),
        request_body_sha256=hashlib.sha256(sent).hexdigest() if sent is not None else None,
        request_body_bytes=len(sent) if sent is not None else None,
        request_shape=_request_shape(sent), response_body=_response_body(received, secrets),
    )


def _store(packet: dict[str, object]) -> dict[str, str | None]:
    try:
        result = store_provider_failure(packet)
    except Exception:
        return {"state": "withheld", "artifact_id": None, "reason": "store_unavailable"}
    if isinstance(result, StoreResult):
        if result.state == "stored" and isinstance(result.artifact_id, str) and result.reason is None:
            with suppress(ValueError):
                if str(UUID(result.artifact_id)) == result.artifact_id:
                    return {"state": "stored", "artifact_id": result.artifact_id, "reason": None}
        if (result.state == "withheld" and result.artifact_id is None
                and isinstance(result.reason, str) and result.reason in _STORE_REASONS):
            return {"state": "withheld", "artifact_id": None, "reason": result.reason}
    return {"state": "withheld", "artifact_id": None, "reason": "invalid_store_result"}


def _record(response: httpx.Response, credential: str, provider_scope: str) -> None:
    packet = _packet(response, credential, provider_scope)
    if packet is None:
        return
    # Copy the safe summary before calling any sink. Even a failing writer
    # cannot attach private text to nested values subsequently logged.
    body = packet["response_body"]
    summary = deepcopy({name: value for name, value in packet.items() if name != "response_body"})
    summary["response_body"] = {name: body[name] for name in ("sha256", "bytes", "content_state", "reason")
                                if isinstance(body, dict) and name in body}
    summary["private_artifact"] = _store(packet)
    log.warning("provider.http_failure", **summary)


def record_provider_failure(
    response: httpx.Response, *, credential: str, provider_scope: str,
) -> None:
    """Logging failure is deliberately unable to replace the provider failure."""
    # Never retry a provider request, consume a stream, or change accounting
    # to recover private evidence. Body text never enters the logger.
    with suppress(Exception):
        _record(response, credential, provider_scope)
