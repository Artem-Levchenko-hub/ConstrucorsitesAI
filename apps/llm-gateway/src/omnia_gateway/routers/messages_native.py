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
import hashlib
import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import WalletEmptyError
from omnia_gateway.providers import llmgw
from omnia_gateway.services import billing, cache, file_logger
from omnia_gateway.services.model_router import native_messages_route
from omnia_gateway.services.pricing import (
    PROVIDER_INPUT_OVERHEAD_TOKENS,
    calculate_cost_rub,
    calculate_provider_cost_usd_upper_bound,
)

log = structlog.get_logger(__name__)
router = APIRouter()

# A complete MAX product composition can legitimately take more than four
# minutes upstream even when the response stays inside the agent's bounded tool
# payload.  A read timeout after request transmission is billing-ambiguous and
# therefore cannot be retried; cutting it short discards the whole generation.
# Keep connection/write/pool failures tight while giving the paid response time
# to finish.  The API caller waits longer than this value.
_UPSTREAM_CONNECT_TIMEOUT_SECONDS = 30.0
_UPSTREAM_WRITE_TIMEOUT_SECONDS = 60.0
_UPSTREAM_POOL_TIMEOUT_SECONDS = 30.0
_TURN_ID_RE = re.compile(r"^[A-Za-z0-9:._-]{1,200}$")


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


_HISTORICAL_TOOL_RESULT_CHARS = 1_600
_HISTORICAL_TOOL_ARGUMENT_CHARS = 600


def _compact_tool_arguments(name: str, raw: Any) -> dict[str, Any]:
    """Bound already-executed file mutations in the provider transcript.

    Native responses carry complete ``write_file`` bodies as tool arguments.
    Replaying a 20 KB page on every later model turn adds no information: the
    authoritative source is in the container and remains available via
    ``read_file``. Keep paths and small patches verbatim, replacing only large
    mutation bodies with an explicit history marker.
    """

    args = dict(raw) if isinstance(raw, dict) else {}
    fields = ("content",) if name == "write_file" else ("search", "replace")
    if name not in {"write_file", "edit_file"}:
        return args
    for field in fields:
        value = args.get(field)
        if isinstance(value, str) and len(value) > _HISTORICAL_TOOL_ARGUMENT_CHARS:
            args[field] = (
                f"[OMITTED FROM HISTORY: {len(value)} characters already applied; "
                "use read_file for current source]"
            )
    return args


def _openai_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system = _openai_text_content(body.get("system"))
    if system:
        out.append({"role": "system", "content": system})

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("messages must be an array")

    for message_index, message in enumerate(raw_messages):
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
                                    _compact_tool_arguments(
                                        str(block.get("name") or ""),
                                        block.get("input") or {},
                                    ),
                                    ensure_ascii=False,
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
            result_content = _text(block.get("content")) or str(block.get("content") or "")
            # The newest observation is the model's working set and stays exact.
            # Older long reads/build logs are recoverable from the live project;
            # carrying every copy forever caused prompt growth from 6K to 40K
            # tokens in a single 14-turn MAX build.
            if (
                message_index < len(raw_messages) - 1
                and len(result_content) > _HISTORICAL_TOOL_RESULT_CHARS
            ):
                result_content = (
                    f"[OLDER TOOL RESULT OMITTED: {len(result_content)} characters; "
                    "rerun the tool if this evidence is still needed]"
                )
            tool_result: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": str(block.get("tool_use_id") or ""),
                "content": result_content,
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


def _safe_upstream_error_type(value: Any) -> str:
    raw = str(value or "")
    return raw if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", raw) else ""


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


def _reserve_native_cost(model: str, payload: dict[str, Any]) -> Decimal:
    """Conservative upper-bound reservation for one provider request.

    One BPE token cannot encode less than one UTF-8 byte, so byte length is a
    safe input-token ceiling. Reserve the full requested output allowance and
    assume no cache discount. The settled usage row replaces this reservation
    with the provider's actual counters after a successful call.
    """
    serialized = json.dumps(
        {"messages": payload.get("messages"), "tools": payload.get("tools")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    input_ceiling = max(1, len(serialized.encode("utf-8"))) + PROVIDER_INPUT_OVERHEAD_TOKENS
    output_ceiling = max(1, _non_negative_int(payload.get("max_tokens")))
    return calculate_cost_rub(model, input_ceiling, output_ceiling)


def _reserve_native_provider_cost(model: str, payload: dict[str, Any]) -> Decimal:
    serialized = json.dumps(
        {"messages": payload.get("messages"), "tools": payload.get("tools")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    input_ceiling = max(1, len(serialized.encode("utf-8")))
    output_ceiling = max(1, _non_negative_int(payload.get("max_tokens")))
    return calculate_provider_cost_usd_upper_bound(
        model,
        input_ceiling,
        output_ceiling,
    )


async def _release_reservation_safely(usage_id: UUID) -> None:
    try:
        await billing.release_native_run_reservation(usage_id)
    except Exception:
        # A leaked reservation is intentionally fail-closed: it may temporarily
        # block this run, but can never permit an unaccounted provider retry.
        log.exception("native_messages.reservation_release_failed", usage_id=str(usage_id))


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


def _upstream_timeout() -> httpx.Timeout:
    """Idle timeout for one upstream chunk, not the whole composition."""

    return httpx.Timeout(
        float(get_settings().native_response_idle_timeout_seconds),
        connect=_UPSTREAM_CONNECT_TIMEOUT_SECONDS,
        write=_UPSTREAM_WRITE_TIMEOUT_SECONDS,
        pool=_UPSTREAM_POOL_TIMEOUT_SECONDS,
    )


def _turn_request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _turn_cache_key(run_id: UUID, turn_id: str) -> str:
    return f"native-turn:{run_id}:{turn_id}"


async def _cached_turn_result(
    run_id: UUID,
    turn_id: str,
    request_hash: str,
) -> dict[str, Any] | None:
    """Return a settled logical turn after a lost API response.

    Redis is an optimisation, not a new availability dependency: if it is
    unavailable the durable request reservation and normal provider path still
    apply. A turn id may never be replayed with a different transcript.
    """

    try:
        record = await cache.get(_turn_cache_key(run_id, turn_id))
    except Exception:
        log.exception("native_messages.turn_cache_read_failed", run_id=str(run_id))
        return None
    if record is None:
        return None
    if record.get("request_hash") != request_hash:
        raise ValueError("provider turn id was reused with a different request")
    response = record.get("response")
    return response if isinstance(response, dict) else None


async def _store_turn_result(
    run_id: UUID,
    turn_id: str,
    request_hash: str,
    response: dict[str, Any],
) -> None:
    try:
        await cache.set(
            _turn_cache_key(run_id, turn_id),
            {"request_hash": request_hash, "response": response},
            ttl_seconds=get_settings().native_turn_cache_ttl_seconds,
        )
    except Exception:
        log.exception("native_messages.turn_cache_write_failed", run_id=str(run_id))


def _post_llmgw(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    """Consume upstream SSE internally and rebuild one OpenAI response.

    Native tool calls are still returned atomically to the API, so no partial
    file operation can escape. Streaming only changes the gateway-to-provider
    transport: every received chunk resets httpx's read-idle timeout and avoids
    treating a long, healthy composition as a stalled response body.
    """

    streamed_payload = {
        **payload,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    streamed_headers = {**headers, "accept": "text/event-stream"}
    started_at = time.monotonic()
    total_timeout_seconds = max(
        1,
        int(get_settings().native_response_total_timeout_seconds),
    )
    with httpx.Client(  # noqa: SIM117 - response context depends on the opened client
        timeout=_upstream_timeout(),
        trust_env=False,
        mounts={"all://": httpx.HTTPTransport()},
    ) as client:
        with client.stream(
            "POST", url, json=streamed_payload, headers=streamed_headers
        ) as response:
            if response.status_code >= 400:
                content = response.read()
                return httpx.Response(
                    response.status_code,
                    content=content,
                    headers=response.headers,
                    request=response.request,
                )

            content_type = response.headers.get("content-type", "").lower()
            if "text/event-stream" not in content_type:
                content = response.read()
                return httpx.Response(
                    response.status_code,
                    content=content,
                    headers=response.headers,
                    request=response.request,
                )

            response_id = ""
            response_model = str(streamed_payload.get("model") or "")
            content_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] = {}
            finish_reason: str | None = None
            saw_done = False

            for raw_line in response.iter_lines():
                if time.monotonic() - started_at >= total_timeout_seconds:
                    raise httpx.ReadTimeout(
                        "upstream response exceeded its total deadline",
                        request=response.request,
                    )
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                raw_data = raw_line[5:].strip()
                if raw_data == "[DONE]":
                    saw_done = True
                    break
                try:
                    chunk = json.loads(raw_data)
                except ValueError as exc:
                    raise httpx.RemoteProtocolError("malformed upstream SSE JSON") from exc
                if not isinstance(chunk, dict) or chunk.get("error"):
                    raise httpx.RemoteProtocolError("upstream SSE returned an error event")
                response_id = str(chunk.get("id") or response_id)
                response_model = str(chunk.get("model") or response_model)
                if isinstance(chunk.get("usage"), dict):
                    usage = dict(chunk["usage"])
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0] if isinstance(choices[0], dict) else {}
                if choice.get("finish_reason") is not None:
                    finish_reason = str(choice["finish_reason"])
                raw_delta = choice.get("delta")
                delta: dict[str, Any] = raw_delta if isinstance(raw_delta, dict) else {}
                content_delta = delta.get("content")
                if isinstance(content_delta, str):
                    content_parts.append(content_delta)
                raw_tool_calls = delta.get("tool_calls")
                if not isinstance(raw_tool_calls, list):
                    continue
                for raw_tool_call in raw_tool_calls:
                    if not isinstance(raw_tool_call, dict):
                        continue
                    index = int(raw_tool_call.get("index") or 0)
                    current = tool_calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if isinstance(raw_tool_call.get("id"), str):
                        current["id"] += raw_tool_call["id"]
                    function_delta = raw_tool_call.get("function")
                    if not isinstance(function_delta, dict):
                        continue
                    if isinstance(function_delta.get("name"), str):
                        current["function"]["name"] += function_delta["name"]
                    if isinstance(function_delta.get("arguments"), str):
                        current["function"]["arguments"] += function_delta["arguments"]

            if not saw_done and finish_reason is None:
                raise httpx.RemoteProtocolError("upstream SSE ended without a terminal marker")
            ordered_tool_calls = [tool_calls[index] for index in sorted(tool_calls)]
            if not content_parts and not ordered_tool_calls:
                raise httpx.RemoteProtocolError("upstream SSE contained no usable completion")
            data = {
                "id": response_id or f"chatcmpl-{uuid4()}",
                "object": "chat.completion",
                "model": response_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "".join(content_parts) or None,
                            "tool_calls": ordered_tool_calls,
                        },
                        "finish_reason": finish_reason
                        or ("tool_calls" if ordered_tool_calls else "stop"),
                    }
                ],
                "usage": usage,
            }
            return httpx.Response(
                200,
                json=data,
                headers=response.headers,
                request=response.request,
            )


@router.post("/v1/messages")
async def native_messages(request: Request) -> Response:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return _err(400, "invalid_request_error", "body is not valid JSON")

    model = str(body.get("model") or "")
    if not model:
        return _err(400, "invalid_request_error", "model is required")

    route = native_messages_route(model)
    if route is None:
        return _err(
            400,
            "invalid_request_error",
            "provider API key is not configured for the native agent model",
        )
    api_key, api_base = route
    raw_metadata = body.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    user_id = _uuid(body.get("user") or metadata.get("user_id"))
    project_id = _uuid(metadata.get("project_id"))
    message_id = _uuid(metadata.get("message_id"))
    run_id = _uuid(metadata.get("run_id"))
    stage = str(metadata.get("stage") or "native_agent")[:80]
    retry_count = _non_negative_int(metadata.get("retry_count"))
    turn_id = str(metadata.get("turn_id") or "")
    free = bool(metadata.get("free", False))
    if user_id is None or run_id is None:
        return _err(
            400,
            "invalid_request_error",
            "native agent calls require valid user and run attribution",
        )
    if turn_id and not _TURN_ID_RE.fullmatch(turn_id):
        return _err(
            400,
            "invalid_request_error",
            "native agent calls require a valid logical turn id",
        )

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

    request_hash = _turn_request_hash(payload)
    # Rolling deploy compatibility: an older API has no explicit turn id. Its
    # canonical request hash is stable and still gives safe replay semantics.
    turn_id = turn_id or f"legacy-{request_hash[:32]}"
    try:
        cached_result = await _cached_turn_result(run_id, turn_id, request_hash)
    except ValueError:
        return _err(
            409,
            "provider_turn_conflict",
            "logical provider turn was reused with a different request",
        )
    if cached_result is not None:
        log.info(
            "native_messages.turn_reconciled",
            run_id=str(run_id),
            turn_id=turn_id,
        )
        return Response(
            content=json.dumps(cached_result),
            status_code=200,
            media_type="application/json",
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        "accept": "text/event-stream",
        # llmgw may forward or honour this standard best-effort key. Omnia's own
        # settled-response cache remains the authoritative replay mechanism.
        "Idempotency-Key": turn_id,
    }

    estimated_cost_rub = _estimate_native_cost(model, payload)
    try:
        reserved_cost_rub = _reserve_native_cost(model, payload)
        reserved_provider_cost_usd = _reserve_native_provider_cost(model, payload)
    except Exception:
        log.exception("native_messages.run_budget_estimate_failed", run_id=str(run_id))
        return _err(503, "billing_unavailable", "run budget could not be calculated")
    settings = get_settings()
    try:
        reservation = await billing.reserve_native_run_request(
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            message_id=message_id,
            model_id=model,
            stage=stage,
            reserved_cost_rub=reserved_cost_rub,
            reserved_provider_cost_usd=reserved_provider_cost_usd,
            max_requests=max(1, int(settings.native_run_max_requests)),
            max_cost_rub=Decimal(str(settings.native_run_max_cost_rub)),
            max_provider_cost_usd=Decimal(str(settings.native_run_max_provider_cost_usd)),
        )
    except billing.RunBudgetExceededError:
        log.warning("native_messages.run_budget_exhausted", run_id=str(run_id), free=free)
        return _err(
            409,
            "run_budget_exhausted",
            "The safe spend limit for this generation has been reached.",
        )
    except (billing.UnknownRunError, billing.InvalidRunAttributionError):
        return _err(400, "invalid_request_error", "generation run does not exist")
    except billing.InactiveRunError:
        return _err(409, "run_not_active", "generation run is no longer active")
    except Exception:
        log.exception("native_messages.run_budget_reservation_failed", run_id=str(run_id))
        return _err(
            503,
            "billing_unavailable",
            "run usage accounting is temporarily unavailable",
        )

    # Fail closed before the provider call. A native build must never keep
    # spending after the user's wallet floor or when accounting is unavailable.
    if not free:
        try:
            await billing.precheck_balance(user_id, estimated_cost_rub)
        except WalletEmptyError as exc:
            await _release_reservation_safely(reservation.usage_id)
            return _err(402, "wallet_empty", exc.message)
        except Exception:
            log.exception("native_messages.precheck_failed", user_id=str(user_id))
            await _release_reservation_safely(reservation.usage_id)
            return _err(503, "billing_unavailable", "usage accounting is temporarily unavailable")

    try:
        upstream = await asyncio.to_thread(
            _post_llmgw,
            f"{api_base.rstrip('/')}/chat/completions",
            payload,
            headers,
        )
    except httpx.ReadTimeout as exc:
        log.warning(
            "native_messages.response_timeout",
            model=model,
            run_id=str(run_id),
            turn_id=turn_id,
            error=type(exc).__name__,
        )
        return _err(
            504,
            "provider_response_timeout",
            "upstream response body stopped making progress",
        )
    except httpx.HTTPError as exc:
        log.warning("native_messages.transport_error", model=model, error=str(exc))
        return _err(
            502,
            "paid_call_ambiguous",
            f"upstream transport: {type(exc).__name__}",
        )

    if upstream.status_code >= 400:
        # Release only when the provider definitively rejected the request
        # before execution. Timeouts, throttling and 5xx are ambiguous: the
        # upstream may already have completed and billed the call, so retaining
        # the reservation is the only fail-closed accounting choice.
        if 400 <= upstream.status_code < 500 and upstream.status_code not in {
            408,
            425,
            429,
        }:
            await _release_reservation_safely(reservation.usage_id)
        try:
            upstream_error = upstream.json().get("error", {})
            message = upstream_error.get("message") or upstream.text[:300]
        except (ValueError, AttributeError):
            upstream_error = {}
            message = upstream.text[:300]
        # Keep provider diagnostics useful without copying an upstream message
        # into logs: it can contain request fragments or credentials. Numeric
        # status + provider error type are enough to distinguish auth, payload,
        # throttling and service failures during a production canary.
        upstream_error_type = _safe_upstream_error_type(
            upstream_error.get("type") or upstream_error.get("code")
            if isinstance(upstream_error, dict)
            else ""
        )
        log.warning(
            "native_messages.upstream_rejected",
            run_id=str(run_id),
            status_code=upstream.status_code,
            upstream_error_type=upstream_error_type,
        )
        error_type = (
            "paid_call_ambiguous"
            if upstream.status_code >= 500 or upstream.status_code in {408, 425, 429}
            else "api_error"
        )
        return _err(upstream.status_code, error_type, str(message))

    try:
        upstream_data = upstream.json()
        adapted = _anthropic_response(upstream_data, model)
    except (ValueError, TypeError) as exc:
        log.warning("native_messages.malformed_response", model=model, error=str(exc))
        return _err(
            502,
            "paid_call_ambiguous",
            "llmgw returned a malformed response",
        )
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
    # Some upstream responses omit an explicit USD charge. Keep the pre-call
    # conservative reservation in that case instead of erasing the provider
    # budget ledger during settlement.
    accounted_provider_cost_usd = (
        provider_cost_usd if provider_cost_usd is not None else reserved_provider_cost_usd
    )
    provider_request_id = str(upstream_data.get("id") or adapted.get("id") or "")[:200] or None

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
            provider_cost_usd=accounted_provider_cost_usd,
            reserved_usage_id=reservation.usage_id,
        )
    except WalletEmptyError:
        log.warning(
            "native_messages.wallet_exhausted_after_call",
            user_id=str(user_id),
            project_id=str(project_id) if project_id else None,
            run_id=str(run_id),
            cost_rub=str(cost_rub),
        )
        return _err(
            503,
            "paid_call_ambiguous",
            "the provider completed but wallet settlement could not be confirmed",
        )
    except Exception:
        # The conservative reservation remains in usage, so no subsequent
        # request can pretend this already-completed provider call was free.
        log.exception(
            "native_messages.charge_failed",
            user_id=str(user_id),
            project_id=str(project_id) if project_id else None,
            run_id=str(run_id),
        )
        return _err(
            503,
            "paid_call_ambiguous",
            "the provider completed but settlement is temporarily unavailable",
        )

    adapted["metadata"] = {
        "cost_rub": str(cost_rub),
        "provider_cost_usd": str(accounted_provider_cost_usd),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "retry_count": retry_count,
        "run_id": str(run_id),
        "stage": stage,
        "turn_id": turn_id,
    }
    await _store_turn_result(run_id, turn_id, request_hash, adapted)
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
