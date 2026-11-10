"""Validate the native adapter's OpenAI transcript before provider admission.

This checks the adapter's message and tool envelopes, not the complete OpenAI
API or arbitrary JSON Schema. Historical calls need not belong to the current
tool set: the native agent changes its available tools between stages.
"""

from __future__ import annotations

import json
from decimal import Decimal, DecimalException
from typing import Any, NoReturn, TypeGuard

INVALID_NATIVE_REQUEST_MESSAGE = "Native model request has invalid message or tool structure."


def _invalid() -> NoReturn:
    raise ValueError(INVALID_NATIVE_REQUEST_MESSAGE) from None


def _nonempty_string(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _cache_control(envelope: dict[str, Any]) -> None:
    if "cache_control" in envelope and not isinstance(envelope["cache_control"], dict):
        _invalid()


def _content(value: Any) -> None:
    if isinstance(value, str):
        return
    if not isinstance(value, list) or not value:
        _invalid()
    for block in value:
        if not isinstance(block, dict):
            _invalid()
        _cache_control(block)
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                _invalid()
        elif block.get("type") == "image_url":
            image = block.get("image_url")
            if not isinstance(image, dict) or not _nonempty_string(image.get("url")):
                _invalid()
            if "detail" in image and image["detail"] not in {"auto", "low", "high"}:
                _invalid()
        else:
            _invalid()


def _json_constant(_value: str) -> NoReturn:
    _invalid()


def _tool_call_id(call: Any) -> str:
    if not isinstance(call, dict) or call.get("type") != "function":
        _invalid()
    call_id = call.get("id")
    function = call.get("function")
    if not _nonempty_string(call_id) or not isinstance(function, dict):
        _invalid()
    if not _nonempty_string(function.get("name")):
        _invalid()
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        _invalid()
    try:
        decoded = json.loads(arguments, parse_float=Decimal, parse_constant=_json_constant)
    except (ValueError, TypeError, RecursionError, DecimalException):
        _invalid()
    if not isinstance(decoded, dict):
        _invalid()
    # The preceding string check is deliberately not a provider-specific ID regex.
    assert isinstance(call_id, str)
    return call_id


def _tools(value: Any) -> None:
    if not isinstance(value, list):
        _invalid()
    names: set[str] = set()
    for tool in value:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            _invalid()
        _cache_control(tool)
        function = tool.get("function")
        if not isinstance(function, dict):
            _invalid()
        name = function.get("name")
        if not _nonempty_string(name) or name in names:
            _invalid()
        names.add(name)
        if "description" in function and not isinstance(function["description"], str):
            _invalid()
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            _invalid()
        if "type" in parameters and parameters["type"] != "object":
            _invalid()
        if "properties" in parameters and not isinstance(parameters["properties"], dict):
            _invalid()
        if "required" in parameters:
            required = parameters["required"]
            if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
                _invalid()
            if len(set(required)) != len(required):
                _invalid()


def _messages(value: Any) -> None:
    if not isinstance(value, list) or not value:
        _invalid()
    pending: set[str] = set()
    seen: set[str] = set()
    for message in value:
        if not isinstance(message, dict):
            _invalid()
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            _invalid()
        _cache_control(message)
        if pending and role != "tool":
            _invalid()
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not _nonempty_string(call_id) or call_id not in pending:
                _invalid()
            pending.remove(call_id)
        elif "tool_call_id" in message:
            _invalid()
        calls = message.get("tool_calls")
        if "tool_calls" in message:
            if role != "assistant" or not isinstance(calls, list) or not calls:
                _invalid()
            for call in calls:
                call_id = _tool_call_id(call)
                if call_id in seen:
                    _invalid()
                seen.add(call_id)
                pending.add(call_id)
        if message.get("content") is None and role == "assistant" and calls:
            continue
        _content(message.get("content"))
    if pending:
        _invalid()


def validate_native_chat_request(payload: dict[str, Any]) -> None:
    """Reject malformed converted messages without mutating or repairing them.

    The existing wire guards retain responsibility for request size/encoding.
    Every structural rejection has the same safe message; untrusted IDs, text,
    schema and decoder details never appear in the exception.
    """
    try:
        if not isinstance(payload, dict):
            _invalid()
        _messages(payload.get("messages"))
        if "tools" in payload:
            _tools(payload["tools"])
    except (ValueError, TypeError, RecursionError):
        _invalid()
