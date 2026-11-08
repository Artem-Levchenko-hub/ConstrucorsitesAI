"""Bounded, provider-specific refusal routing; never replay uncertain work."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import Any

import httpx

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import GatewayError, ModelUnavailableError
from yleum_gateway.services.generation_identity import GenerationProviderIdentity

PRIMARY_URL = "https://api.llmgw.ru/v1/chat/completions"
MODEL = "claude-sonnet-5.5"
POLICY = "llmgw_unavailable_v1"
HTTP_REFUSAL_POLICY = "llmgw_http_refused_v1"
_REFUSAL_TITLES = {
    500: ("Internal Server Error",), 502: ("Bad Gateway",),
    503: ("Service Unavailable", "Service Temporarily Unavailable"),
    504: ("Gateway Timeout", "Gateway Time-out"),
}
_MONEY_HEADERS = {"x-llmgw-cost-rub", "x-cost-rub", "x-cost-usd", "x-llmgw-cost", "x-cost"}
_RECEIPT_FIELDS = {
    "usage", "choices", "cost", "cost_rub", "cost_usd", "provider_cost_rub", "provider_cost_usd",
    "calculated_cost_rub", "cost_provenance", "content", "delta", "output", "output_text",
    "function_call", "tool_calls", "tool_use", "tokens", "tokens_in", "tokens_out", "total_cost",
    "charge", "charged", "billing", "billed", "provider_charge_ambiguous", "partial",
}
_ERROR_FIELDS = {"message", "type", "code", "param", "status", "status_code", "id", "request_id"}
_VALIDATION_FIELDS = {"loc", "msg", "type", "url", "input", "ctx"}


class ProviderRefusedError(GatewayError):
    code = "api_error"
    http_status = 503


def enabled(model: str) -> bool:
    settings = get_settings()
    return bool(model == MODEL and settings.aitunnel_text_fallback_enabled
                and settings.aitunnel_text_api_key)


def credentials(scope: str) -> tuple[str, str]:
    settings = get_settings()
    key = settings.aitunnel_text_api_key
    if scope != "aitunnel" or key is None or not enabled(MODEL):
        raise ModelUnavailableError("Backup provider is unavailable")
    return key.get_secret_value(), settings.aitunnel_base_url


def scope_options(scope: str) -> dict[str, Any]:
    return {"provider_scope": scope} if scope != "llmgw" else {}


def read_refusal_body(response: httpx.Response) -> httpx.Response | None:
    """Do not materialize an unbounded error stream to decide on a backup."""
    body = bytearray()
    for chunk in response.iter_bytes(chunk_size=4096):
        body.extend(chunk)
        if len(body) > 16_384:
            return None
    return httpx.Response(response.status_code, headers=decoded_body_headers(response), content=bytes(body),
                          request=response.request, history=response.history)


def decoded_body_headers(response: httpx.Response) -> httpx.Headers:
    headers = httpx.Headers(response.headers)
    headers.pop("content-encoding", None)
    headers.pop("content-length", None)
    return headers


def _contains_receipt(value: Any, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        for key, item in value.items():
            field = key.lower()
            if (field in _RECEIPT_FIELDS or field.endswith(("_tokens", "_token_count"))
                    or _contains_receipt(item, depth + 1)):
                return True
        return False
    return isinstance(value, list) and any(_contains_receipt(item, depth + 1) for item in value)


def _structured_error(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return (
        isinstance(value, dict)
        and not (value.keys() - _ERROR_FIELDS)
        and all(item is None or type(item) in {str, int} for item in value.values())
        and any(type(item) is int or (isinstance(item, str) and item.strip())
                for item in (value.get("message"), value.get("type"), value.get("code")))
    )


def _validation_error_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(error, dict) and not (error.keys() - _VALIDATION_FIELDS)
        and isinstance(error.get("type"), str) and bool(error["type"])
        and isinstance(error.get("loc"), list)
        and all(type(part) in {str, int} for part in error["loc"])
        for error in value
    )


def _canonical_refusal_document(text: str, status: int) -> bool:
    # Explicit status aliases include the standard nginx 503/504 documents.
    titles = _REFUSAL_TITLES.get(status)
    if titles is None:
        try:
            titles = (HTTPStatus(status).phrase,)
        except ValueError:
            return False
    for raw_title in titles:
        title = re.escape(raw_title)
        status_title = str(status) + " " + title
        plain = r"(?:" + str(status) + r"\s+)?" + title + r"[.]?"
        nginx = (
            r"<html>\s*<head><title>" + status_title + r"</title></head>\s*<body>\s*"
            r"<center><h1>" + status_title + r"</h1></center>\s*<hr><center>nginx(?:/[0-9.]+)?</center>\s*"
            r"</body>\s*</html>"
        )
        if any(re.fullmatch(pattern, text, re.IGNORECASE) for pattern in (plain, nginx)):
            return True
    return False


def primary_refusal_candidate(response: httpx.Response, model: str) -> dict[str, Any] | None:
    """Identify a complete, uncharged HTTP refusal from the primary provider.

    LLMGW support states that non-200 responses are not charged. Require a
    fully read error from the official endpoint with no usage or cost evidence.

    Routers must commit the failed receipt BEFORE publishing this candidate.
    """
    if not enabled(model):
        return None
    try:
        request, raw = response.request, response.content
    except (RuntimeError, httpx.ResponseNotRead):
        return None
    status = response.status_code
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (not 400 <= status <= 599 or request.method != "POST"
            or str(request.url) != PRIMARY_URL or response.history
            or content_type == "text/event-stream"
            or len(raw) > 16_384 or any(key in response.headers for key in _MONEY_HEADERS)):
        return None
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate provider error field")
            result[key] = value
        return result

    def reject_nonfinite(_: str) -> None:
        raise ValueError("nonfinite provider error field")

    try:
        text = raw.decode("utf-8").strip()
    except UnicodeError:
        return None
    if text.startswith("{"):
        try:
            body = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=reject_nonfinite)
        except (ValueError, RecursionError):
            return None
        if not isinstance(body, dict):
            return None
        error_fields = body.keys() & {"error", "detail"}
        if (len(error_fields) != 1
                or set(body) - error_fields - {"id", "request_id", "type"}
                or body.get("type", "error") != "error" or _contains_receipt(body)):
            return None
        field = next(iter(error_fields))
        if not (_structured_error(body[field]) or (field == "detail" and _validation_error_list(body[field]))):
            return None
    elif text and not _canonical_refusal_document(text, status):
        return None
    return {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": POLICY if status in _REFUSAL_TITLES else HTTP_REFUSAL_POLICY,
        "upstream_http_status": status,
    }


async def select_scope(identity: GenerationProviderIdentity | None, model: str) -> str:
    """Only a matching, committed SQL parent can route a retained child."""
    if identity is None or model != MODEL:
        return "llmgw"
    try:
        async with get_pool().acquire() as conn:
            parent = await conn.fetchrow(
                "SELECT p.phase,p.terminal_reason,c.status,c.receipt_hash,c.provider_scope "
                "FROM generation_provider_attempts a "
                "JOIN generation_provider_attempts p ON p.attempt_id=a.parent_attempt_id "
                "JOIN provider_calls c ON c.id=p.provider_call_id "
                "WHERE a.attempt_id=$1 AND a.run_id=$2 AND a.project_id=$3 AND a.user_id=$4 "
                "AND a.prompt_hash=$5 AND a.request_sha256=$6 "
                "AND p.run_id=a.run_id AND p.project_id=a.project_id AND p.user_id=a.user_id "
                "AND p.prompt_hash=a.prompt_hash AND p.request_sha256=a.request_sha256",
                identity.attempt_id, identity.run_id, identity.project_id, identity.user_id,
                identity.prompt_hash, identity.request_sha256,
            )
    except Exception:
        from yleum_gateway.services.generation_attempts import AttemptConflict

        raise AttemptConflict("Provider routing could not be confirmed") from None
    if parent is None:
        return "llmgw"
    reason = parent["terminal_reason"]
    backup_parent = parent["provider_scope"] == "aitunnel"
    backup_enabled = enabled(model)
    if not backup_parent and reason != "upstream_refused" and not (
        reason == "preconnect" and backup_enabled
    ):
        return "llmgw"
    if (parent["phase"] != "closed" or parent["status"] != "failed"
            or not parent["receipt_hash"] or not backup_enabled
            or reason not in {"upstream_refused", "preconnect", "rate_limit_refused"}
            or (reason == "upstream_refused" and parent["provider_scope"] != "llmgw")):
        raise ModelUnavailableError("Backup continuation is unconfirmed")
    return "aitunnel"
