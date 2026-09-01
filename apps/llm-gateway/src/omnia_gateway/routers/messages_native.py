"""Anthropic-shaped native agent endpoint backed by llmgw OpenAI tools.

The API agent speaks Anthropic Messages because its transcript uses
``tool_use`` / ``tool_result`` blocks. llmgw documents an OpenAI-compatible
``/chat/completions`` surface, including function calling. This adapter converts
requests and responses at the gateway boundary so the agent loop remains stable
while all LLM traffic uses llmgw.

Thinking blocks from historical transcripts are intentionally omitted when
building the OpenAI request: llmgw does not document Anthropic thinking
signatures. Tool ids and results are preserved, which is the state required for
the build loop to continue correctly.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from omnia_gateway.core.errors import WalletEmptyError
from omnia_gateway.core.runner_auth import (
    RunnerAuthConfigError,
    RunnerAuthError,
    RunnerClaims,
    RunnerReplayError,
    RunnerReplayUnavailableError,
    consume_runner_jti,
    validate_runner_metadata,
    verify_runner_bearer_header,
)
from omnia_gateway.providers import llmgw
from omnia_gateway.services import billing, file_logger
from omnia_gateway.services.model_router import native_messages_route
from omnia_gateway.services.pricing import calculate_cost_rub

log = structlog.get_logger(__name__)
router = APIRouter()

_TIMEOUT_S = 240.0


def _err(status: int, err_type: str, message: str) -> Response:
    return Response(
        content=json.dumps({"type": "error", "error": {"type": err_type, "message": message}}),
        status_code=status,
        media_type="application/json",
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    )


def _openai_text_content(content: Any) -> str | list[dict[str, Any]]:
    """Keep Anthropic cache breakpoints while adapting text blocks.

    llmgw accepts OpenAI content arrays. LiteLLM-compatible Anthropic routes read
    ``cache_control`` from those text blocks, so flattening them to a string (the
    old behaviour) silently disabled provider prefix caching on every agent turn.
    Non-text blocks are intentionally omitted; tool turns are adapted separately.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "")
        if not text:
            continue
        adapted: dict[str, Any] = {"type": "text", "text": text}
        if isinstance(block.get("cache_control"), dict):
            adapted["cache_control"] = dict(block["cache_control"])
        blocks.append(adapted)
    if not blocks:
        return ""
    if any("cache_control" in block for block in blocks):
        return blocks
    return "\n".join(str(block["text"]) for block in blocks)


def _openai_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system = _openai_text_content(body.get("system"))
    if system:
        out.append({"role": "system", "content": system})

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("messages must be an array")

    for message in raw_messages:
        if not isinstance(message, dict):
            raise ValueError("each message must be an object")
        role = message.get("role")
        content = message.get("content", "")

        if role == "assistant":
            text = _openai_text_content(content)
            tool_calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_calls.append(
                        {
                            "id": str(block.get("id") or f"call_{uuid4().hex}"),
                            "type": "function",
                            "function": {
                                "name": str(block.get("name") or ""),
                                "arguments": json.dumps(
                                    block.get("input") or {}, ensure_ascii=False
                                ),
                            },
                        }
                    )
            item: dict[str, Any] = {
                "role": "assistant",
                "content": text or None,
            }
            if tool_calls:
                item["tool_calls"] = tool_calls
            out.append(item)
            continue

        if role != "user":
            raise ValueError(f"unsupported role: {role}")

        if isinstance(content, str):
            out.append({"role": "user", "content": content})
            continue
        if not isinstance(content, list):
            raise ValueError("message content must be text or an array")

        text = _openai_text_content(content)
        if text:
            out.append({"role": "user", "content": text})
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_result: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": str(block.get("tool_use_id") or ""),
                "content": _text(block.get("content")) or str(block.get("content") or ""),
            }
            # The native loop puts its moving prefix breakpoint on the last
            # tool_result. LiteLLM understands cache_control at message level,
            # so carry it across instead of silently dropping the main cache.
            if isinstance(block.get("cache_control"), dict):
                tool_result["cache_control"] = dict(block["cache_control"])
            out.append(tool_result)
    return out


def _openai_tools(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tools: list[dict[str, Any]] = []
    for tool in raw:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        function: dict[str, Any] = {
            "name": str(tool["name"]),
            "description": str(tool.get("description") or ""),
            "parameters": tool.get("input_schema")
            if isinstance(tool.get("input_schema"), dict)
            else {"type": "object", "properties": {}},
        }
        item: dict[str, Any] = {"type": "function", "function": function}
        # LiteLLM reads tool cache_control beside `type`/`function`, matching
        # the OpenAI extension it converts to Anthropic's cached tool block.
        if isinstance(tool.get("cache_control"), dict):
            item["cache_control"] = dict(tool["cache_control"])
        tools.append(item)
    return tools


def _openai_tool_choice(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return "auto"
    choice_type = raw.get("type")
    if choice_type == "any":
        return "required"
    if choice_type == "tool" and raw.get("name"):
        return {
            "type": "function",
            "function": {"name": str(raw["name"])},
        }
    return "auto"


def _anthropic_response(data: dict[str, Any], omnia_model: str) -> dict[str, Any]:
    try:
        choice = (data.get("choices") or [])[0]
        message = choice.get("message") or {}
    except (IndexError, AttributeError) as exc:
        raise ValueError("llmgw returned no completion choice") from exc

    content: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})

    tool_calls = message.get("tool_calls") or []
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments") or "{}"
            try:
                tool_input = json.loads(arguments) if isinstance(arguments, str) else arguments
            except ValueError:
                tool_input = {"raw": str(arguments)}
            content.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("id") or f"call_{uuid4().hex}"),
                    "name": str(function.get("name") or ""),
                    "input": tool_input if isinstance(tool_input, dict) else {},
                }
            )

    finish_reason = choice.get("finish_reason")
    if tool_calls or finish_reason == "tool_calls":
        stop_reason = "tool_use"
    elif finish_reason == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    usage = data.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return {
        "id": data.get("id") or f"msg_{uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": omnia_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "cache_read_input_tokens": int(
                prompt_details.get("cached_tokens")
                or usage.get("prompt_cache_hit_tokens")
                or usage.get("cache_read_input_tokens")
                or 0
            ),
            "cache_creation_input_tokens": int(
                prompt_details.get("cache_creation_tokens")
                or usage.get("cache_creation_input_tokens")
                or 0
            ),
        },
    }


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result >= 0 else None


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _estimate_native_cost(model: str, payload: dict[str, Any]) -> Decimal:
    """Conservative enough to stop before the wallet floor, without reserving
    the entire output ceiling on every tool turn."""
    serialized = json.dumps(
        {"messages": payload.get("messages"), "tools": payload.get("tools")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    estimated_input = max(1, len(serialized) // 4)
    requested_output = _non_negative_int(payload.get("max_tokens"))
    estimated_output = max(256, min(requested_output, max(256, estimated_input // 4)))
    try:
        return calculate_cost_rub(model, estimated_input, estimated_output)
    except Exception:
        return Decimal("0")


def _reported_cost(
    data: dict[str, Any], response: httpx.Response
) -> tuple[Decimal | None, Decimal | None]:
    """Read a provider-reported charge when the upstream exposes one.

    llmgw deployments have used both response metadata and headers over time.
    We accept the known shapes and otherwise fall back to token pricing with
    cache-read/cache-write counters. No message content is inspected or logged.
    """
    raw_usage = data.get("usage")
    raw_metadata = data.get("metadata")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    rub = next(
        (
            item
            for item in (
                _decimal(usage.get("cost_rub")),
                _decimal(metadata.get("cost_rub")),
                _decimal(response.headers.get("x-cost-rub")),
                _decimal(response.headers.get("x-llmgw-cost-rub")),
            )
            if item is not None
        ),
        None,
    )
    usd = next(
        (
            item
            for item in (
                _decimal(usage.get("cost_usd")),
                _decimal(usage.get("cost")),
                _decimal(metadata.get("cost_usd")),
                _decimal(response.headers.get("x-cost-usd")),
            )
            if item is not None
        ),
        None,
    )
    return rub, usd


def _post_llmgw(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    with httpx.Client(
        timeout=httpx.Timeout(_TIMEOUT_S, connect=30.0),
        trust_env=False,
        mounts={"all://": httpx.HTTPTransport()},
    ) as client:
        return client.post(url, json=payload, headers=headers)


async def _native_messages_impl(
    request: Request,
    *,
    runner_claims: RunnerClaims | None = None,
) -> Response:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return _err(400, "invalid_request_error", "body is not valid JSON")

    raw_metadata = body.get("metadata")
    try:
        metadata = (
            validate_runner_metadata(raw_metadata, runner_claims)
            if runner_claims is not None
            else raw_metadata
            if isinstance(raw_metadata, dict)
            else {}
        )
    except RunnerAuthError as exc:
        return _err(401, "authentication_error", str(exc))
    if runner_claims is not None:
        try:
            await consume_runner_jti(runner_claims)
        except RunnerReplayError as exc:
            return _err(401, "authentication_error", str(exc))
        except RunnerReplayUnavailableError as exc:
            return _err(503, "api_error", str(exc))

    model = str(body.get("model") or "")
    if not model:
        return _err(400, "invalid_request_error", "model is required")

    route = native_messages_route()
    if route is None:
        return _err(
            400,
            "invalid_request_error",
            "LLMGW_API_KEY is not configured for the native agent",
        )
    api_key, api_base = route
    user_id = None if runner_claims is not None else _uuid(body.get("user") or metadata.get("user_id"))
    project_id = runner_claims.project_id if runner_claims is not None else _uuid(metadata.get("project_id"))
    message_id = _uuid(metadata.get("message_id"))
    run_id = runner_claims.run_id if runner_claims is not None else _uuid(metadata.get("run_id"))
    stage = str(metadata.get("stage") or "native_agent")[:80]
    retry_count = _non_negative_int(metadata.get("retry_count"))
    free = True if runner_claims is not None else bool(metadata.get("free", False))

    try:
        messages = _openai_messages(body)
    except ValueError as exc:
        return _err(400, "invalid_request_error", str(exc))

    payload: dict[str, Any] = {
        "model": llmgw.native_slug(model),
        "messages": messages,
        "max_tokens": int(body.get("max_tokens") or 8192),
        "stream": False,
    }
    tools = _openai_tools(body.get("tools"))
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = _openai_tool_choice(body.get("tool_choice"))
    if isinstance(body.get("temperature"), (int, float)):
        payload["temperature"] = body["temperature"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        "accept": "application/json",
    }

    # Fail closed before the provider call. A native build must never keep
    # spending after the user's wallet floor or when accounting is unavailable.
    if user_id is not None and not free:
        try:
            await billing.precheck_balance(user_id, _estimate_native_cost(model, payload))
        except WalletEmptyError as exc:
            return _err(402, "wallet_empty", exc.message)
        except Exception:
            log.exception("native_messages.precheck_failed", user_id=str(user_id))
            return _err(503, "billing_unavailable", "usage accounting is temporarily unavailable")

    try:
        upstream = await asyncio.to_thread(
            _post_llmgw,
            f"{api_base.rstrip('/')}/chat/completions",
            payload,
            headers,
        )
    except httpx.HTTPError as exc:
        log.warning("native_messages.transport_error", model=model, error=str(exc))
        return _err(502, "api_error", f"upstream transport: {type(exc).__name__}")

    if upstream.status_code >= 400:
        try:
            upstream_error = upstream.json().get("error", {})
            message = upstream_error.get("message") or upstream.text[:300]
        except (ValueError, AttributeError):
            message = upstream.text[:300]
        return _err(upstream.status_code, "api_error", str(message))

    try:
        upstream_data = upstream.json()
        adapted = _anthropic_response(upstream_data, model)
    except (ValueError, TypeError) as exc:
        log.warning("native_messages.malformed_response", model=model, error=str(exc))
        return _err(502, "api_error", "llmgw returned a malformed response")
    usage = adapted["usage"]
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    reported_rub, provider_cost_usd = _reported_cost(upstream_data, upstream)
    try:
        calculated_rub = calculate_cost_rub(
            model,
            tokens_in,
            tokens_out,
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
    except Exception:
        calculated_rub = Decimal("0")
    cost_rub = reported_rub if reported_rub is not None else calculated_rub
    provider_request_id = str(upstream_data.get("id") or adapted.get("id") or "")[:200] or None

    if user_id is not None:
        try:
            await billing.charge(
                user_id=user_id,
                project_id=project_id,
                message_id=message_id,
                run_id=run_id,
                model_id=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_rub=cost_rub,
                description=f"Native agent · {stage}",
                free=free,
                stage=stage,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                retry_count=retry_count,
                provider_request_id=provider_request_id,
                provider_cost_usd=provider_cost_usd,
            )
        except WalletEmptyError as exc:
            log.warning(
                "native_messages.wallet_exhausted_after_call",
                user_id=str(user_id),
                project_id=str(project_id) if project_id else None,
                run_id=str(run_id) if run_id else None,
                cost_rub=str(cost_rub),
            )
            return _err(402, "wallet_empty", exc.message)
        except Exception:
            # The provider already completed this single call, but no subsequent
            # call may run while its accounting is unknown.
            log.exception(
                "native_messages.charge_failed",
                user_id=str(user_id),
                project_id=str(project_id) if project_id else None,
                run_id=str(run_id) if run_id else None,
            )
            return _err(503, "billing_unavailable", "usage accounting is temporarily unavailable")

    adapted["metadata"] = {
        "cost_rub": str(cost_rub),
        "provider_cost_usd": str(provider_cost_usd) if provider_cost_usd is not None else None,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "message_id": (
            runner_claims.jti
            if runner_claims is not None
            else str(message_id) if message_id is not None else None
        ),
        "retry_count": retry_count,
        "run_id": str(run_id) if run_id else None,
        "session_id": str(runner_claims.session_id) if runner_claims is not None else None,
        "stage": stage,
        "workspace_id": str(runner_claims.workspace_id) if runner_claims is not None else None,
        "fencing_epoch": runner_claims.fencing_epoch if runner_claims is not None else None,
        "cancel_epoch": runner_claims.cancel_epoch if runner_claims is not None else None,
        "jti": runner_claims.jti if runner_claims is not None else None,
    }
    try:
        file_logger.log_request(
            {
                "user_id": user_id,
                "project_id": project_id,
                "message_id": message_id,
                "run_id": run_id,
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_rub": cost_rub,
                "cache_hit": cache_read > 0,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "retry_count": retry_count,
                "stage": stage,
                "provider_request_id": provider_request_id,
                "fallback_used": False,
                "stream": False,
            }
        )
    except Exception:
        log.exception("native_messages.file_log_failed")

    return Response(
        content=json.dumps(adapted, ensure_ascii=False),
        status_code=200,
        media_type="application/json",
    )


@router.post("/v1/messages")
async def native_messages(request: Request) -> Response:
    return await _native_messages_impl(request)


@router.post("/v1/project-cell/messages")
async def project_cell_native_messages(request: Request) -> Response:
    try:
        runner_claims = verify_runner_bearer_header(request.headers.get("authorization"))
    except RunnerAuthConfigError as exc:
        return _err(503, "api_error", str(exc))
    except RunnerAuthError as exc:
        return _err(401, "authentication_error", str(exc))
    return await _native_messages_impl(request, runner_claims=runner_claims)
