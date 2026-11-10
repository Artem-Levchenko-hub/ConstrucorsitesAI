"""Closed evidence for LLMGW's temporary OpenRouter spending reservation.

This classifies a complete refusal only. Routers must commit the failed
financial receipt before publishing permission to wait and retry the step.
Ordinary payment failures and uncertain paid outcomes never acquire permission.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from yleum_gateway.providers.rate_limit import _delay

URL = "https://api.llmgw.ru/v1/chat/completions"
POLICY = "llmgw_inflight_budget_v1"
_LIMIT = 16_384
_MONEY_HEADERS = {"x-llmgw-cost-rub", "x-cost-rub", "x-cost-usd", "x-llmgw-cost", "x-cost"}
_PREFIX = "litellm.APIError: APIError: OpenrouterException - "
_SUFFIX = (
    r"\n\nLiteLLM: model group '[a-zA-Z0-9~/._-]{1,160}' failed with the error above\. "
    r"No fallback was attempted\.\n?"
)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate refusal field")
        value[key] = item
    return value


def _finite(_: str) -> None:
    raise ValueError("nonfinite refusal field")


def _document(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) - {"error", "id", "request_id", "user_id"}:
        return None
    if any(not isinstance(item, str) or not item or len(item) > 512
           for key, item in value.items() if key != "error"):
        return None
    error = value.get("error")
    return error if isinstance(error, dict) else None


def _is_402(code: Any) -> bool:
    return (type(code) is int and code == 402) or (isinstance(code, str) and code == "402")


def _unwrapped_error(value: Any, decoder: json.JSONDecoder) -> dict[str, Any] | None:
    error = _document(value)
    if error is None:
        return None
    if "metadata" in error:
        return error
    if (
        set(error) - {"message", "type", "param", "code"}
        or error.get("type") != "invalid_request_error"
        or not _is_402(error.get("code"))
        or error.get("param") is not None
    ):
        return None
    message = error.get("message")
    if not isinstance(message, str) or not message.startswith(_PREFIX):
        return None
    # One known wrapper is permitted. Never search recursively for a suggestive
    # JSON fragment in arbitrary provider prose or strip unexplained suffixes.
    nested, end = decoder.raw_decode(message, len(_PREFIX))
    if not re.fullmatch(_SUFFIX, message[end:]):
        return None
    return _document(nested)


def _nested_delay(error: dict[str, Any], now: datetime) -> tuple[bool, int | None]:
    if (
        set(error) - {"message", "type", "param", "code", "metadata"}
        or not _is_402(error.get("code"))
        or not isinstance(error.get("message"), str)
        or not error["message"].strip()
        or error.get("type", "invalid_request_error") not in {"invalid_request_error", "api_error"}
        or error.get("param") is not None
    ):
        return False, None
    metadata = error.get("metadata")
    if (
        not isinstance(metadata, dict)
        or set(metadata) - {"reason", "limit_source", "remedy_hint", "headers", "provider_name"}
        or metadata.get("reason") != "in_flight_budget_exhausted"
        or metadata.get("limit_source") != "openrouter_in_flight_budget"
        or ("remedy_hint" in metadata and not isinstance(metadata["remedy_hint"], str))
        or (metadata.get("provider_name") is not None
            and not isinstance(metadata["provider_name"], str))
    ):
        return False, None
    if "headers" not in metadata:
        return True, None
    headers = metadata["headers"]
    if not isinstance(headers, dict) or len(headers) != 1:
        return False, None
    name, value = next(iter(headers.items()))
    if name.casefold() != "retry-after" or not isinstance(value, str):
        return False, None
    delay = _delay(value, now)
    return delay is not None, delay


def retry_candidate(
    response: httpx.Response, *, provider_scope: str, now: datetime | None = None,
) -> dict[str, Any] | None:
    """Recognize only the trusted pre-execution reservation refusal, not all 402s."""
    try:
        request, raw = response.request, response.content
    except (RuntimeError, httpx.ResponseNotRead):
        return None
    if (
        provider_scope != "llmgw" or response.status_code != 402
        or request.method != "POST" or str(request.url) != URL or response.history
        or len(raw) > _LIMIT
        or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        == "text/event-stream"
        or any(name in response.headers for name in _MONEY_HEADERS)
    ):
        return None
    current = now or datetime.now(UTC)
    decoder = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_finite)
    try:
        error = _unwrapped_error(decoder.decode(raw.decode("utf-8")), decoder)
        if error is None:
            return None
        valid, nested_delay = _nested_delay(error, current)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not valid:
        return None
    values = response.headers.get_list("retry-after")
    if len(values) > 1:
        return None
    header_delay = _delay(values[0], current) if values else None
    if values and header_delay is None:
        return None
    if header_delay is not None and nested_delay is not None and header_delay != nested_delay:
        return None
    delay = header_delay if header_delay is not None else nested_delay
    if delay is None or delay <= 0:
        return None
    return {
        "upstream_http_status": 402, "provider_scope": "llmgw",
        "provider_refusal_category": "rate_limit", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": POLICY, "retry_after_seconds": delay,
    }
