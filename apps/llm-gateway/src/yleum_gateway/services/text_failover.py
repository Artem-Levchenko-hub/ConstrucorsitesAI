"""Bounded, provider-specific refusal routing; never replay uncertain work."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import GatewayError, ModelUnavailableError
from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.provider_calls import LLMGW_BALANCE_REFUSED

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


def alternate_scope(model: str, scope: str, reason: str | None, attempted: set[str]) -> str | None:
    """Choose the other provider only after a committed, uncharged refusal."""
    if scope not in {"llmgw", "aitunnel"} or not enabled(model):
        return None
    if reason != "preconnect" and (scope, reason) != ("llmgw", "upstream_refused"):
        return None
    other = "aitunnel" if scope == "llmgw" else "llmgw"
    return None if other in attempted else other


def preconnect_proof(scope: str, model: str, stage: str | None) -> dict[str, Any]:
    """Publish retry permission for the exact local failure after its receipt commits."""
    failover = enabled(model)
    if (scope == "aitunnel" or failover) and stage != "connect":
        return {}
    proof: dict[str, Any] = {
        "provider_failure_kind": "preconnect", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": "llmgw_preconnect_v1",
    }
    if scope == "aitunnel" or failover:
        proof.update(
            retry_policy="aitunnel_preconnect_v1" if scope == "aitunnel" else "llmgw_failover_preconnect_v1",
            provider_scope=scope, transport_stage="connect",
        )
    return proof


def exhausted_error() -> ModelUnavailableError:
    return ModelUnavailableError("Both configured providers refused this request", details={
        "provider_failure_kind": "providers_exhausted",
        "provider_charge_ambiguous": False,
        "retry_safe": False,
    })


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


async def read_async_refusal_body(response: httpx.Response) -> httpx.Response | None:
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=4096):
        body.extend(chunk)
        if len(body) > 16_384:
            return None
    return httpx.Response(
        response.status_code, headers=decoded_body_headers(response), content=bytes(body),
        request=response.request, history=response.history,
    )


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


def primary_balance_refused(response: httpx.Response, category: str | None) -> bool:
    """Recognize funds evidence in an already verified, uncharged refusal."""
    if category == "funds":
        return True
    if response.status_code != HTTPStatus.PAYMENT_REQUIRED or "retry-after" in response.headers:
        return False
    # An unclassified structured 402 can describe a temporary reservation or
    # contradict itself. Only a bare HTTP Payment Required is sufficient.
    text = response.content.decode("utf-8").strip()
    return not text or _canonical_refusal_document(text, response.status_code)


async def select_scope(
    identity: GenerationProviderIdentity | None, model: str, *,
    run_id: UUID | None = None, project_id: UUID | None = None, user_id: UUID | None = None,
    cell_route: str | None = None,
) -> str:
    """Route new steps using a committed balance refusal, and retries using their parent."""
    if model != MODEL:
        return "llmgw"
    backup_enabled = enabled(model)
    if identity is not None and cell_route is None:
        run_id, project_id, user_id = identity.run_id, identity.project_id, identity.user_id
    # Only the authenticated cell route may look up platform-owned receipts.
    run_scoped = backup_enabled and run_id is not None and project_id is not None and (
        user_id is not None or cell_route == "/v1/project-cell/messages"
    )
    if identity is None and not run_scoped:
        return "llmgw"
    try:
        async with get_pool().acquire() as conn:
            parent = None
            if identity is not None:
                parent = await conn.fetchrow(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT p.attempt_id,p.parent_attempt_id,p.provider_call_id,0 AS depth
                        FROM generation_provider_attempts a
                        JOIN generation_provider_attempts p ON p.attempt_id=a.parent_attempt_id
                        WHERE a.attempt_id=$1 AND a.run_id=$2 AND a.project_id=$3 AND a.user_id=$4
                        AND a.prompt_hash=$5 AND a.request_sha256=$6
                        AND p.run_id=$2 AND p.project_id=$3 AND p.user_id=$4
                        AND p.prompt_hash=$5 AND p.request_sha256=$6
                        UNION ALL
                        SELECT p.attempt_id,p.parent_attempt_id,p.provider_call_id,chain.depth+1
                        FROM chain
                        JOIN generation_provider_attempts p ON p.attempt_id=chain.parent_attempt_id
                        WHERE chain.depth<8 AND p.run_id=$2 AND p.project_id=$3 AND p.user_id=$4
                        AND p.prompt_hash=$5 AND p.request_sha256=$6
                    )
                    SELECT p.phase,p.terminal_reason,c.status,c.receipt_hash,c.provider_scope,
                        (SELECT array_agg(DISTINCT pc.provider_scope) FROM chain AS history
                         JOIN provider_calls pc ON pc.id=history.provider_call_id) AS tried_scopes,
                        EXISTS (SELECT 1 FROM chain WHERE parent_attempt_id IS NULL)
                        AND NOT EXISTS (SELECT 1 FROM chain WHERE provider_call_id IS NULL) AS chain_complete
                    FROM chain
                    JOIN generation_provider_attempts p ON p.attempt_id=chain.attempt_id
                    JOIN provider_calls c ON c.id=chain.provider_call_id
                    WHERE chain.depth=0
                    """,
                    identity.attempt_id, identity.run_id, identity.project_id, identity.user_id,
                    identity.prompt_hash, identity.request_sha256,
                )
            if parent is None:
                balance_refused = run_scoped and await conn.fetchval(
                    "SELECT status='failed' FROM provider_calls "
                    "WHERE run_id=$1 AND project_id=$2 AND user_id IS NOT DISTINCT FROM $3::uuid "
                    "AND requested_model=$4 AND ($3::uuid IS NOT NULL OR route=$6) "
                    "AND provider_scope='llmgw' AND receipt_hash IS NOT NULL "
                    "AND (status='completed' OR (status='failed' AND error_type=$5)) "
                    "ORDER BY finished_at DESC,created_at DESC,id DESC LIMIT 1",
                    run_id, project_id, user_id, model, LLMGW_BALANCE_REFUSED, cell_route,
                )
                return "aitunnel" if balance_refused else "llmgw"
    except Exception:
        from yleum_gateway.services.generation_attempts import AttemptConflict

        raise AttemptConflict("Provider routing could not be confirmed") from None
    reason = parent["terminal_reason"]
    scope = parent["provider_scope"]
    backup_parent = scope == "aitunnel"
    if not backup_parent and reason != "upstream_refused" and not (
        reason == "preconnect" and backup_enabled
    ):
        return "llmgw"
    if (parent["phase"] != "closed" or parent["status"] != "failed"
            or not parent["receipt_hash"] or not backup_enabled
            or reason not in {"upstream_refused", "preconnect", "rate_limit_refused"}
            or (reason == "upstream_refused" and scope != "llmgw")):
        raise ModelUnavailableError("Backup continuation is unconfirmed")
    if reason == "rate_limit_refused":
        return "aitunnel"
    if parent["chain_complete"]:
        alternate = alternate_scope(model, scope, reason, set(parent["tried_scopes"]))
        if alternate is not None:
            return alternate
    raise ModelUnavailableError("No untried provider is confirmed for this attempt")
