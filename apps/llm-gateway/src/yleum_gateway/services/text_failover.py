"""Bounded, provider-specific refusal routing; never replay uncertain work."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import GatewayError, ModelUnavailableError
from yleum_gateway.services.generation_identity import GenerationProviderIdentity

PRIMARY_URL = "https://api.llmgw.ru/v1/chat/completions"
MODEL = "claude-sonnet-5.5"
POLICY = "llmgw_overload_v1"
_MONEY_HEADERS = {"x-llmgw-cost-rub", "x-cost-rub", "x-cost-usd", "x-llmgw-cost", "x-cost"}


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
    return httpx.Response(response.status_code, headers=response.headers, content=bytes(body),
                          request=response.request, history=response.history)


def _contains_receipt(value: Any, depth: int = 0) -> bool:
    if depth > 12:
        return True
    if isinstance(value, dict):
        return any(
            str(key).lower() in {"usage", "choices", "cost", "cost_rub", "cost_usd", "content", "tool_calls"}
            or _contains_receipt(item, depth + 1)
            for key, item in value.items()
        )
    return isinstance(value, list) and any(_contains_receipt(item, depth + 1) for item in value)


def overload_candidate(response: httpx.Response, model: str) -> dict[str, Any] | None:
    """LLMGW support confirms explicit 502 refusals are not charged (2026-10-23).

    This permission applies only to the official endpoint and a fully read error
    response. A timeout, partial stream or reported expense never qualifies.
    Routers must commit the failed receipt BEFORE publishing this candidate.
    """
    if not enabled(model):
        return None
    try:
        request, raw = response.request, response.content
    except (RuntimeError, httpx.ResponseNotRead):
        return None
    if (response.status_code != 502 or request.method != "POST"
            or str(request.url) != PRIMARY_URL or response.history
            or len(raw) > 16_384 or any(key in response.headers for key in _MONEY_HEADERS)):
        return None
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate provider error field")
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8").strip()
    except UnicodeError:
        return None
    if text.startswith("{"):
        try:
            body = json.loads(text, object_pairs_hook=unique_pairs)
        except (ValueError, RecursionError):
            return None
        if (not isinstance(body, dict) or not body.get("error")
                or set(body) - {"error", "id", "request_id"} or _contains_receipt(body)):
            return None
    elif not re.fullmatch(r"(?:502\s+)?bad gateway[.]?", text, re.IGNORECASE):
        # A mixed SSE/JSON document containing the words Bad Gateway does not
        # prove a refusal. Accept only the canonical nginx error document.
        if not re.fullmatch(
            r"<html>\s*<head><title>502 Bad Gateway</title></head>\s*<body>\s*"
            r"<center><h1>502 Bad Gateway</h1></center>\s*<hr><center>nginx(?:/[0-9.]+)?</center>\s*"
            r"</body>\s*</html>", text, re.IGNORECASE,
        ):
            return None
    return {
        "provider_failure_kind": "upstream_refused", "provider_charge_ambiguous": False,
        "retry_safe": True, "retry_policy": POLICY, "upstream_http_status": 502,
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
    backup_parent = parent["provider_scope"] == "aitunnel"
    overload = parent["terminal_reason"] == "upstream_refused"
    if not backup_parent and not overload:
        return "llmgw"
    if (parent["phase"] != "closed" or parent["status"] != "failed"
            or not parent["receipt_hash"] or not enabled(model)
            or parent["terminal_reason"] not in {"upstream_refused", "preconnect", "rate_limit_refused"}
            or (overload and parent["provider_scope"] != "llmgw")):
        raise ModelUnavailableError("Backup continuation is unconfirmed")
    return "aitunnel"
