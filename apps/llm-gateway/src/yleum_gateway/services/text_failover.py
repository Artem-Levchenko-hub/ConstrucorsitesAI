"""Bounded, provider-specific refusal routing; never replay uncertain work."""

from __future__ import annotations

import asyncio
import json
import math
import re
from http import HTTPStatus
from typing import Any, Literal
from uuid import UUID

import httpx

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import (
    DispatchDeadlineError,
    GatewayError,
    ModelUnavailableError,
    UpstreamProviderError,
)
from yleum_gateway.providers.rate_limit import POLICY as KEY_RATE_LIMIT_POLICY
from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.kimi_native import KIMI_MODEL
from yleum_gateway.services.provider_calls import LLMGW_BALANCE_REFUSED

PRIMARY_URL = "https://api.llmgw.ru/v1/chat/completions"
MODEL = "claude-sonnet-5.5"
POLICY = "llmgw_unavailable_v1"
HTTP_REFUSAL_POLICY = "llmgw_http_refused_v1"
_GENERATION_ATTEMPT_LIMITS = {"llmgw": 2, "aitunnel": 3}
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


def next_retry_scope(
    model: str, scope: str, reason: str | None, attempts: dict[str, int], *,
    generation_retries: bool,
    refusal: dict[str, Any] | None = None, defer_primary: bool = False,
) -> str | None:
    """Select a route after the failed attempt's receipt commits."""
    repeat_primary_refusal = (
        reason == "upstream_refused" and refusal is not None
        and refusal.get("retry_policy") == POLICY and not defer_primary
    )
    if (generation_retries and (reason == "preconnect" or repeat_primary_refusal)
            and attempts[scope] < _GENERATION_ATTEMPT_LIMITS[scope]):
        return scope
    return alternate_scope(model, scope, reason, set(attempts))


def preconnect_proof(
    scope: str, model: str, stage: str | None, *, kimi_repair: bool = False,
) -> dict[str, Any]:
    """Publish retry permission for the exact local failure after its receipt commits."""
    failover = enabled(MODEL if kimi_repair else model)
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


def primary_retry_after(candidate: object) -> int | None:
    if (not isinstance(candidate, dict) or candidate.get("retry_policy") != KEY_RATE_LIMIT_POLICY
            or candidate.get("retry_safe") is not True):
        return None
    delay = candidate.get("retry_after_seconds")
    return delay if type(delay) is int and 0 <= delay <= 86_400 else None


def primary_rate_limit_error(delay: int) -> UpstreamProviderError:
    return UpstreamProviderError("Primary provider rate limit exceeded", details={
        "upstream_http_status": 429, "provider_refusal_category": "rate_limit",
        "provider_charge_ambiguous": False, "retry_safe": True,
        "retry_policy": KEY_RATE_LIMIT_POLICY, "retry_after_seconds": delay,
    })


async def primary_retry_after_backup(identity: GenerationProviderIdentity, call_id: UUID) -> int | None:
    """Read the previous primary cooldown only behind an exact uncharged backup failure."""
    async with get_pool().acquire() as conn:
        delay = await conn.fetchval(
            """
            SELECT GREATEST(0, CEIL(EXTRACT(EPOCH FROM
                (pc.finished_at - now())) + pc.retry_after_seconds))::int
            FROM generation_provider_attempts a
            JOIN generation_provider_attempts p ON p.attempt_id=a.parent_attempt_id
            JOIN provider_calls c ON c.id=a.provider_call_id
            JOIN provider_calls pc ON pc.id=p.provider_call_id
            WHERE a.provider_call_id=$1 AND a.run_id=$2 AND a.project_id=$3 AND a.user_id=$4
            AND a.prompt_hash=$5 AND a.request_sha256=$6
            AND p.run_id=a.run_id AND p.project_id=a.project_id AND p.user_id=a.user_id
            AND p.prompt_hash=a.prompt_hash AND p.request_sha256=a.request_sha256
            AND a.phase='closed' AND a.terminal_reason='preconnect'
            AND c.provider_scope='aitunnel' AND c.status='failed' AND c.receipt_hash IS NOT NULL
            AND c.usage_id IS NULL AND c.tokens_in IS NULL AND c.tokens_out IS NULL
            AND c.cache_read_tokens IS NULL AND c.cache_write_tokens IS NULL
            AND c.calculated_cost_rub IS NULL AND c.provider_cost_rub IS NULL
            AND c.provider_cost_usd IS NULL AND c.cost_provenance IS NULL
            AND p.phase='closed' AND p.terminal_reason='upstream_refused'
            AND pc.provider_scope='llmgw' AND pc.status='failed' AND pc.receipt_hash IS NOT NULL
            AND pc.retry_after_seconds IS NOT NULL AND pc.route=c.route
            AND c.requested_model=$7 AND pc.requested_model=$7
            """,
            call_id, identity.run_id, identity.project_id, identity.user_id,
            identity.prompt_hash, identity.request_sha256, MODEL,
        )
    return delay if type(delay) is int and 0 <= delay <= 86_400 else None


async def retry_after_backup_preconnect(
    identity: GenerationProviderIdentity | None, call_id: UUID, primary_retry_at: float | None,
) -> int | None:
    if identity is not None:
        return await primary_retry_after_backup(identity, call_id)
    if primary_retry_at is not None:
        return max(0, math.ceil(primary_retry_at - asyncio.get_running_loop().time()))
    return None


async def _wait_for_primary_cooldown(delay: object, deadline: float | None) -> None:
    if type(delay) is not int or not 0 < delay <= 86_400:
        return
    loop = asyncio.get_running_loop()
    if deadline is not None and loop.time() + delay >= deadline:
        raise DispatchDeadlineError()
    await asyncio.sleep(delay)
    if deadline is not None and loop.time() >= deadline:
        raise DispatchDeadlineError()


async def confirmed_chain_exhausted(
    identity: GenerationProviderIdentity, call_id: UUID, *, route: str, user_id: UUID | None,
    model: str = MODEL,
) -> bool:
    """Confirm both provider refusals in the exact closed chain, starting at this call."""
    async with get_pool().acquire() as conn:
        confirmed = await conn.fetchval(
            """
            WITH RECURSIVE chain AS (
                SELECT attempt_id,parent_attempt_id,provider_call_id,phase,terminal_reason,0 AS depth
                FROM generation_provider_attempts
                WHERE attempt_id=$1 AND run_id=$2 AND project_id=$3 AND user_id=$4
                AND prompt_hash=$5 AND request_sha256=$6 AND provider_call_id=$7
                UNION ALL
                SELECT p.attempt_id,p.parent_attempt_id,p.provider_call_id,p.phase,p.terminal_reason,
                    chain.depth+1
                FROM chain JOIN generation_provider_attempts p ON p.attempt_id=chain.parent_attempt_id
                WHERE chain.depth<8 AND p.run_id=$2 AND p.project_id=$3 AND p.user_id=$4
                AND p.prompt_hash=$5 AND p.request_sha256=$6
            )
            SELECT bool_or(chain.parent_attempt_id IS NULL)
                AND count(DISTINCT c.provider_scope)=2
                AND count(c.retry_after_seconds)=0
                AND bool_and(COALESCE(
                    chain.phase='closed' AND c.status='failed' AND c.receipt_hash IS NOT NULL
                    AND c.provider_scope IN ('llmgw','aitunnel') AND c.requested_model=$8
                    AND c.run_id=$2 AND c.project_id=$3 AND c.route=$9
                    AND c.user_id IS NOT DISTINCT FROM $10::uuid
                    AND c.usage_id IS NULL AND c.tokens_in IS NULL AND c.tokens_out IS NULL
                    AND c.cache_read_tokens IS NULL AND c.cache_write_tokens IS NULL
                    AND c.calculated_cost_rub IS NULL AND c.provider_cost_rub IS NULL
                    AND c.provider_cost_usd IS NULL AND c.cost_provenance IS NULL
                    AND (chain.terminal_reason='preconnect' OR (
                        c.provider_scope='llmgw'
                        AND chain.terminal_reason IN ('upstream_refused','rate_limit_refused')
                    )), FALSE))
            FROM chain LEFT JOIN provider_calls c ON c.id=chain.provider_call_id
            """,
            identity.attempt_id, identity.run_id, identity.project_id, identity.user_id,
            identity.prompt_hash, identity.request_sha256, call_id, model, route, user_id,
        )
    return confirmed is True


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


def primary_refusal_candidate(
    response: httpx.Response, model: str, *, kimi_repair: bool = False,
) -> dict[str, Any] | None:
    """Identify a complete, uncharged HTTP refusal from the primary provider.

    LLMGW support states that non-200 responses are not charged. Require a
    fully read error from the official endpoint with no usage or cost evidence.

    Routers must commit the failed receipt BEFORE publishing this candidate.
    """
    if not enabled(MODEL if kimi_repair else model):
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


async def select_kimi_repair_scope(
    identity: GenerationProviderIdentity,
) -> Literal["llmgw", "aitunnel"]:
    """Keep the settled gateway after a transcript reset, or allow one safe switch."""
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT a.parent_attempt_id, (
                SELECT call.provider_scope FROM provider_calls call
                WHERE call.run_id=a.run_id AND call.project_id=a.project_id AND call.user_id=a.user_id
                    AND call.route='/v1/messages' AND call.stage='source_repair' AND call.requested_model=$7
                    AND call.actual_model=$7 AND call.status='completed'
                    AND call.receipt_hash IS NOT NULL AND call.usage_id IS NOT NULL
                ORDER BY call.finished_at DESC, call.id DESC LIMIT 1
            ) AS settled_scope, (
                p.parent_attempt_id IS NULL AND p.phase='closed'
                AND p.terminal_reason IN ('preconnect', 'upstream_refused', 'rate_limit_refused')
                AND c.status='failed' AND c.receipt_hash IS NOT NULL AND c.provider_scope='llmgw'
                AND c.requested_model=$7 AND c.stage='source_repair' AND c.route='/v1/messages'
                AND c.run_id=a.run_id AND c.project_id=a.project_id AND c.user_id=a.user_id
                AND c.usage_id IS NULL AND c.tokens_in IS NULL AND c.tokens_out IS NULL
                AND c.cache_read_tokens IS NULL AND c.cache_write_tokens IS NULL
                AND c.provider_cost_rub IS NULL AND c.provider_cost_usd IS NULL
                AND c.calculated_cost_rub IS NULL AND c.cost_provenance IS NULL
            ) AS backup_allowed
            FROM generation_provider_attempts a
            LEFT JOIN generation_provider_attempts p ON p.attempt_id=a.parent_attempt_id
                AND p.run_id=a.run_id AND p.project_id=a.project_id AND p.user_id=a.user_id
                AND p.prompt_hash=a.prompt_hash AND p.request_sha256=a.request_sha256
            LEFT JOIN provider_calls c ON c.id=p.provider_call_id
            WHERE a.attempt_id=$1 AND a.run_id=$2 AND a.project_id=$3 AND a.user_id=$4
                AND a.prompt_hash=$5 AND a.request_sha256=$6
            """,
            identity.attempt_id, identity.run_id, identity.project_id, identity.user_id,
            identity.prompt_hash, identity.request_sha256, KIMI_MODEL,
        )
    if row is not None:
        settled = row["settled_scope"]
        if row["parent_attempt_id"] is None:
            if settled in (None, "llmgw"):
                return "llmgw"
            if settled == "aitunnel" and enabled(MODEL):
                return "aitunnel"
        elif settled is None and row["backup_allowed"] is True and enabled(MODEL):
            return "aitunnel"
    raise ModelUnavailableError("Kimi repair backup has no confirmed uncharged request")


async def select_scope(
    identity: GenerationProviderIdentity | None, model: str, *,
    run_id: UUID | None = None, project_id: UUID | None = None, user_id: UUID | None = None,
    cell_route: str | None = None, deadline: float | None = None,
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
    delay = None
    balance_refused = False
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
                    SELECT p.phase,p.terminal_reason,c.id AS provider_call_id,
                        c.status,c.receipt_hash,c.provider_scope,
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
                if run_scoped and not balance_refused:
                    delay = await conn.fetchval(
                        "SELECT CEIL(EXTRACT(EPOCH FROM MAX(finished_at + "
                        "retry_after_seconds * interval '1 second' - now())))::int "
                        "FROM provider_calls WHERE run_id=$1 AND project_id=$2 "
                        "AND user_id IS NOT DISTINCT FROM $3::uuid "
                        "AND requested_model=$4 AND ($3::uuid IS NOT NULL OR route=$5) "
                        "AND provider_scope='llmgw' AND status='failed' AND receipt_hash IS NOT NULL "
                        "AND retry_after_seconds IS NOT NULL "
                        "AND finished_at + retry_after_seconds * interval '1 second' > now()",
                        run_id, project_id, user_id, model, cell_route,
                    )
    except Exception:
        from yleum_gateway.services.generation_attempts import AttemptConflict

        raise AttemptConflict("Provider routing could not be confirmed") from None
    if parent is None:
        if balance_refused:
            return "aitunnel"
        await _wait_for_primary_cooldown(delay, deadline)
        return "llmgw"
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
        if (identity is not None and backup_parent and reason == "preconnect"
                and isinstance(call_id := parent.get("provider_call_id"), UUID)):
            delay = await primary_retry_after_backup(identity, call_id)
            if delay is not None:
                await _wait_for_primary_cooldown(delay, deadline)
                return "llmgw"
    raise ModelUnavailableError("No untried provider is confirmed for this attempt")
