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
from collections.abc import Coroutine
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

import anyio
import httpx
import structlog
from fastapi import APIRouter, Request, Response

from yleum_gateway.core.errors import BillingReconciliationRequiredError, WalletEmptyError
from yleum_gateway.core.runner_auth import (
    RunnerAuthConfigError,
    RunnerAuthError,
    RunnerClaims,
    RunnerReplayError,
    RunnerReplayUnavailableError,
    consume_runner_jti,
    validate_runner_metadata,
    verify_runner_bearer_header,
)
from yleum_gateway.providers import llmgw
from yleum_gateway.services import billing, file_logger, provider_calls
from yleum_gateway.services.cache_pricing import anthropic_cache_write_factor
from yleum_gateway.services.model_router import is_supported, native_messages_route, slug_to_omnia
from yleum_gateway.services.pricing import (
    ReportedCost,
    calculate_cost_rub,
    read_reported_cost,
    resolve_request_cost,
)

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
        # Resolve every preceding assistant tool call before adding feedback.
        # Native turns append loop/repair guidance after tool_result blocks;
        # putting that user message first breaks the OpenAI tool-call sequence.
        if text:
            out.append({"role": "user", "content": text})
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


class _InvalidUsageReceipt(ValueError):
    """A paid response has no trustworthy token totals for settlement."""


def _native_usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _InvalidUsageReceipt("Missing provider token receipt")
    usage = dict(raw)
    for field in ("prompt_tokens", "completion_tokens"):
        value = usage.get(field)
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            try:
                value = int(value)
            except ValueError as exc:
                raise _InvalidUsageReceipt("Invalid provider token receipt") from exc
        if type(value) is not int or value < 0:
            raise _InvalidUsageReceipt("Invalid provider token receipt")
        usage[field] = value
    normalized, _ = llmgw.normalize_usage(usage, 0, 0)
    return normalized


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
            arguments = function.get("arguments")
            try:
                tool_input = json.loads(arguments) if isinstance(arguments, str) else arguments
            except ValueError:
                tool_input = None
            content.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("id") or f"call_{uuid4().hex}"),
                    "name": str(function.get("name") or ""),
                    "input": tool_input if isinstance(tool_input, dict) else {},
                    **({"input_error": "invalid_tool_arguments"}
                       if not isinstance(tool_input, dict) else {}),
                }
            )

    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        stop_reason = "max_tokens"
    elif tool_calls or finish_reason == "tool_calls":
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"

    usage = _native_usage(data.get("usage"))
    return {
        "id": data.get("id") or f"msg_{uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": omnia_model,
        "content": content,
        "stop_reason": stop_reason,
        "provider_finish_reason": finish_reason if finish_reason in {
            "length", "tool_calls", "stop", "content_filter", "function_call",
        } else None,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"],
            "cache_read_input_tokens": usage["prompt_cache_hit_tokens"],
            "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
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
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


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
    The documented balance-audit header takes precedence over legacy shapes.
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
                _decimal(response.headers.get("x-llmgw-cost-rub")),
                _decimal(response.headers.get("x-cost-rub")),
                _decimal(usage.get("cost_rub")),
                _decimal(metadata.get("cost_rub")),
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


async def _await_provider_receipt[ReceiptResult](
    operation: Coroutine[Any, Any, ReceiptResult],
) -> ReceiptResult:
    # Own one settlement task through repeated client cancellation. Never start
    # the provider or its receipt write again while waiting for this task.
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        if task.cancelled():
            raise
        # ASGI uses level cancellation: shield cleanup from the cancelled
        # AnyIO scope, while still tolerating repeated direct task.cancel().
        with anyio.CancelScope(shield=True):
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise cancelled from None
                except Exception as exc:
                    log.warning("native_messages.provider_receipt_failed", error_type=type(exc).__name__)
                    break
        raise cancelled


async def _finish_provider_call(call_id: UUID, **receipt: Any) -> bool:
    try:
        await _await_provider_receipt(provider_calls.finish_call(call_id, **receipt))
    except Exception as exc:
        log.warning("native_messages.provider_receipt_failed", error_type=type(exc).__name__)
        return False
    return True


def _known_response_evidence(upstream: httpx.Response) -> dict[str, Any]:
    try:
        data = upstream.json()
    except (ValueError, UnicodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    reported = read_reported_cost(data, upstream.headers)
    raw_id = (data.get("id") or upstream.headers.get("x-llmgw-request-id")
              or upstream.headers.get("x-request-id"))
    request_id = raw_id if (
        isinstance(raw_id, str) and raw_id.strip() and len(raw_id) <= 512
        and all(ord(char) >= 32 for char in raw_id)
    ) else None
    return {"provider_request_id": request_id, "provider_cost_rub": reported.cost_rub,
            "provider_cost_usd": reported.cost_usd}


async def _settle_native_cost(
    call_id: UUID, actual_model: str, payload: dict[str, Any],
    receipt: dict[str, Any], reported: ReportedCost,
) -> tuple[Decimal, dict[str, Any]] | None:
    try:
        cost_rub, provenance = await resolve_request_cost(
            actual_model, tokens_in=receipt["tokens_in"], tokens_out=receipt["tokens_out"],
            cache_read_tokens=receipt["cache_read_tokens"],
            cache_write_tokens=receipt["cache_write_tokens"], reported=reported,
            cache_write_factor=anthropic_cache_write_factor(actual_model, payload),
        )
        calculated_cost_rub = Decimal(provenance["calculated_cost_rub"])
    except Exception as exc:
        await _finish_provider_call(call_id, status="ambiguous", **receipt,
                                    error_type=type(exc).__name__)
        return None
    if not await _finish_provider_call(
        call_id, **receipt, calculated_cost_rub=calculated_cost_rub, cost_provenance=provenance,
    ):
        return None
    return cost_rub, provenance


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
    user_id = (
        None if runner_claims is not None else _uuid(body.get("user") or metadata.get("user_id"))
    )
    project_id = (
        runner_claims.project_id if runner_claims is not None else _uuid(metadata.get("project_id"))
    )
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
        provider_call_id = await provider_calls.start_call(
            route="/v1/project-cell/messages" if runner_claims is not None else "/v1/messages",
            model=model, user_id=user_id, project_id=project_id, run_id=run_id,
            message_id=message_id, stage=stage, free=free,
        )
    except Exception as exc:
        log.warning("native_messages.provider_admission_failed", error_type=type(exc).__name__)
        return _err(503, "billing_unavailable", "Provider accounting is temporarily unavailable")

    try:
        upstream = await asyncio.to_thread(
            _post_llmgw,
            f"{api_base.rstrip('/')}/chat/completions",
            payload,
            headers,
        )
    except httpx.HTTPError as exc:
        log.warning(
            "native_messages.transport_error",
            error_type=type(exc).__name__,
            run_id=str(run_id) if run_id else None,
            project_id=str(project_id) if project_id else None,
            message_id=str(message_id) if message_id else None,
            stage=stage if stage in {"build_plan", "native_agent", "verification"} else "unknown",
            retry_count=retry_count,
        )
        safe_to_retry = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
        recorded = await _finish_provider_call(
            provider_call_id, status="failed" if safe_to_retry else "ambiguous",
            error_type=type(exc).__name__,
        )
        if safe_to_retry and recorded:
            # These failures occur before the request reaches the provider.
            return _err(502, "api_error", f"upstream transport: {type(exc).__name__}")
        # Once a request may have been accepted, replay risks a second paid call.
        # Deployed API callers already stop on this terminal billing error.
        return _err(503, "billing_unavailable", "Provider request outcome could not be confirmed")

    known_evidence = _known_response_evidence(upstream)
    if upstream.status_code >= 400:
        ambiguous = upstream.status_code >= 500
        recorded = await _finish_provider_call(
            provider_call_id, status="ambiguous" if ambiguous else "failed",
            error_type="HTTPStatusError", **known_evidence,
        )
        if ambiguous or not recorded:
            return _err(503, "billing_unavailable", "Provider request outcome could not be confirmed")
        try:
            upstream_error = upstream.json().get("error", {})
            message = upstream_error.get("message") or upstream.text[:300]
        except (ValueError, AttributeError):
            message = upstream.text[:300]
        return _err(upstream.status_code, "api_error", str(message))

    try:
        upstream_data = upstream.json()
        if not isinstance(upstream_data, dict):
            raise ValueError("upstream response must be an object")
        reported_model = upstream_data.get("model")
        actual_model = slug_to_omnia(reported_model) if isinstance(reported_model, str) else None
        if reported_model and (actual_model is None or not is_supported(actual_model)):
            if not await _finish_provider_call(
                provider_call_id, status="ambiguous", error_type="ValidationFailedError",
                **known_evidence,
            ):
                return _err(503, "billing_unavailable", "Provider accounting is temporarily unavailable")
            return _err(
                409,
                "model_route_mismatch",
                "upstream model identity cannot be accounted for safely",
            )
        actual_model = actual_model or model
        fallback_used = actual_model != model
        adapted = _anthropic_response(upstream_data, actual_model)
    except _InvalidUsageReceipt:
        await _finish_provider_call(
            provider_call_id, status="ambiguous", error_type="ValidationFailedError", **known_evidence,
        )
        # The API already treats this code as non-replayable: the provider may
        # have charged, so neither a guessed zero debit nor a retry is safe.
        return _err(503, "billing_unavailable", "Provider token usage could not be confirmed")
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        await _finish_provider_call(
            provider_call_id, status="ambiguous", error_type=type(exc).__name__, **known_evidence,
        )
        log.warning("native_messages.malformed_response", error_type=type(exc).__name__)
        # A successful but unreadable provider response may already be charged.
        return _err(503, "billing_unavailable", "Provider response could not be confirmed")
    usage = adapted["usage"]
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    reported = read_reported_cost(upstream_data, upstream.headers)
    provider_cost_usd = reported.cost_usd
    provider_request_id = known_evidence["provider_request_id"]
    settled = await _await_provider_receipt(_settle_native_cost(
        provider_call_id, actual_model, payload, {
            **known_evidence, "actual_model": actual_model,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
        }, reported,
    ))
    if settled is None:
        return _err(503, "billing_unavailable", "Provider accounting is temporarily unavailable")
    cost_rub, provenance = settled

    if user_id is not None:
        try:
            await billing.charge(
                user_id=user_id,
                project_id=project_id,
                message_id=message_id,
                run_id=run_id,
                model_id=actual_model,
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
                cost_provenance=provenance,
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
        except BillingReconciliationRequiredError as exc:
            # Keep the terminal billing type understood by deployed callers,
            # with a specific machine-readable reason for reconciliation.
            return Response(
                status_code=exc.http_status,
                media_type="application/json",
                content=json.dumps({"type": "error", "error": {
                    "type": "billing_unavailable", "code": exc.code,
                    "message": "Completed provider receipt requires explicit billing reconciliation",
                }}),
            )
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
        "requested_model": model,
        "actual_model": actual_model,
        "model_identity_confirmed": bool(reported_model),
        "fallback_used": fallback_used,
        "cost_rub": str(cost_rub),
        "cost_provenance": provenance,
        "provider_cost_usd": str(provider_cost_usd) if provider_cost_usd is not None else None,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "message_id": (
            runner_claims.jti
            if runner_claims is not None
            else str(message_id)
            if message_id is not None
            else None
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
                "model": actual_model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_rub": cost_rub,
                "cache_hit": cache_read > 0,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "retry_count": retry_count,
                "stage": stage,
                "provider_request_id": provider_request_id,
                "fallback_used": fallback_used,
                "stream": False,
                "provider_finish_reason": adapted.get("provider_finish_reason"),
                "invalid_tool_argument_count": sum(
                    bool(block.get("input_error")) for block in adapted["content"]
                    if isinstance(block, dict)
                ),
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
