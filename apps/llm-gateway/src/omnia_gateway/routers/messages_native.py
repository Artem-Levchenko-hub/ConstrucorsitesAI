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
from typing import Any
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from omnia_gateway.providers import llmgw
from omnia_gateway.services.model_router import native_messages_route

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


def _openai_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system = _text(body.get("system"))
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
            text = _text(content)
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

        text = _text(content)
        if text:
            out.append({"role": "user", "content": text})
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": _text(block.get("content")) or str(block.get("content") or ""),
                }
            )
    return out


def _openai_tools(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tools: list[dict[str, Any]] = []
    for tool in raw:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": str(tool["name"]),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("input_schema")
                    if isinstance(tool.get("input_schema"), dict)
                    else {"type": "object", "properties": {}},
                },
            }
        )
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
            "cache_read_input_tokens": int(prompt_details.get("cached_tokens") or 0),
        },
    }


def _post_llmgw(url: str, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    with httpx.Client(
        timeout=httpx.Timeout(_TIMEOUT_S, connect=30.0),
        trust_env=False,
        mounts={"all://": httpx.HTTPTransport()},
    ) as client:
        return client.post(url, json=payload, headers=headers)


@router.post("/v1/messages")
async def native_messages(request: Request) -> Response:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return _err(400, "invalid_request_error", "body is not valid JSON")

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
        adapted = _anthropic_response(upstream.json(), model)
    except (ValueError, TypeError) as exc:
        log.warning("native_messages.malformed_response", model=model, error=str(exc))
        return _err(502, "api_error", "llmgw returned a malformed response")
    return Response(
        content=json.dumps(adapted, ensure_ascii=False),
        status_code=200,
        media_type="application/json",
    )
