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


class NativeRequestValidationError(ValueError):
    """A diagnostic reason and field location independent of the response text."""

    def __init__(self, reason: str, field: str, *, message: str = INVALID_NATIVE_REQUEST_MESSAGE) -> None:
        super().__init__(message)
        self.reason = reason
        self.field = field


def _invalid(reason: str, field: str) -> NoReturn:
    raise NativeRequestValidationError(reason, field) from None


def _nonempty_string(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _cache_control(envelope: dict[str, Any], field: str) -> None:
    if "cache_control" in envelope and not isinstance(envelope["cache_control"], dict):
        _invalid("invalid_cache_control", f"{field}.cache_control")


def _content(value: Any, field: str) -> None:
    if isinstance(value, str):
        return
    if not isinstance(value, list) or not value:
        _invalid("invalid_message_content", field)
    for index, block in enumerate(value):
        block_field = f"{field}[{index}]"
        if not isinstance(block, dict):
            _invalid("invalid_content_block", block_field)
        _cache_control(block, block_field)
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                _invalid("invalid_text_content", f"{block_field}.text")
        elif block.get("type") == "image_url":
            image = block.get("image_url")
            if not isinstance(image, dict) or not _nonempty_string(image.get("url")):
                _invalid("invalid_image_url", f"{block_field}.image_url")
            if "detail" in image and image["detail"] not in ("auto", "low", "high"):
                _invalid("invalid_image_detail", f"{block_field}.image_url.detail")
        else:
            _invalid("unsupported_content_type", f"{block_field}.type")


def _json_constant(_value: str) -> NoReturn:
    raise ValueError("Non-finite JSON constant")


def _tool_call_id(call: Any, field: str) -> str:
    if not isinstance(call, dict) or call.get("type") != "function":
        _invalid("invalid_tool_call", field)
    call_id = call.get("id")
    function = call.get("function")
    if not _nonempty_string(call_id):
        _invalid("missing_tool_call_id", f"{field}.id")
    if not isinstance(function, dict):
        _invalid("invalid_tool_call_function", f"{field}.function")
    if not _nonempty_string(function.get("name")):
        _invalid("missing_tool_call_name", f"{field}.function.name")
    arguments = function.get("arguments")
    argument_field = f"{field}.function.arguments"
    if not isinstance(arguments, str):
        _invalid("invalid_tool_arguments_type", argument_field)
    try:
        decoded = json.loads(arguments, parse_float=Decimal, parse_constant=_json_constant)
    except (ValueError, TypeError, RecursionError, DecimalException):
        _invalid("invalid_tool_arguments_json", argument_field)
    if not isinstance(decoded, dict):
        _invalid("non_object_tool_arguments", argument_field)
    # The preceding string check is deliberately not a provider-specific ID regex.
    assert isinstance(call_id, str)
    return call_id


def _tools(value: Any) -> None:
    if not isinstance(value, list):
        _invalid("invalid_tool_list", "tools")
    names: set[str] = set()
    for index, tool in enumerate(value):
        field = f"tools[{index}]"
        if not isinstance(tool, dict) or tool.get("type") != "function":
            _invalid("invalid_tool_definition", field)
        _cache_control(tool, field)
        function = tool.get("function")
        if not isinstance(function, dict):
            _invalid("invalid_tool_definition", f"{field}.function")
        name = function.get("name")
        if not _nonempty_string(name):
            _invalid("missing_tool_name", f"{field}.function.name")
        if name in names:
            _invalid("duplicate_tool_name", f"{field}.function.name")
        names.add(name)
        if "description" in function and not isinstance(function["description"], str):
            _invalid("invalid_tool_description", f"{field}.function.description")
        parameters = function.get("parameters")
        parameter_field = f"{field}.function.parameters"
        if not isinstance(parameters, dict):
            _invalid("invalid_tool_parameters", parameter_field)
        if "type" in parameters and parameters["type"] != "object":
            _invalid("invalid_tool_parameters_type", f"{parameter_field}.type")
        if "properties" in parameters and not isinstance(parameters["properties"], dict):
            _invalid("invalid_tool_properties", f"{parameter_field}.properties")
        if "required" in parameters:
            required = parameters["required"]
            if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
                _invalid("invalid_tool_required_fields", f"{parameter_field}.required")
            if len(set(required)) != len(required):
                _invalid("duplicate_tool_required_fields", f"{parameter_field}.required")


def _messages(value: Any) -> None:
    if not isinstance(value, list) or not value:
        _invalid("invalid_message_list", "messages")
    pending: set[str] = set()
    seen: set[str] = set()
    for index, message in enumerate(value):
        field = f"messages[{index}]"
        if not isinstance(message, dict):
            _invalid("invalid_message", field)
        role = message.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            _invalid("unsupported_message_role", f"{field}.role")
        _cache_control(message, field)
        if pending and role != "tool":
            _invalid("unresolved_tool_calls", f"{field}.role")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not _nonempty_string(call_id):
                _invalid("missing_tool_result_id", f"{field}.tool_call_id")
            if call_id not in pending:
                _invalid("unexpected_tool_result", f"{field}.tool_call_id")
            pending.remove(call_id)
        elif "tool_call_id" in message:
            _invalid("tool_result_id_on_non_tool_message", f"{field}.tool_call_id")
        calls = message.get("tool_calls")
        if "tool_calls" in message:
            if role != "assistant" or not isinstance(calls, list) or not calls:
                _invalid("invalid_tool_calls", f"{field}.tool_calls")
            for call_index, call in enumerate(calls):
                call_field = f"{field}.tool_calls[{call_index}]"
                call_id = _tool_call_id(call, call_field)
                if call_id in seen:
                    _invalid("duplicate_tool_call_id", f"{call_field}.id")
                seen.add(call_id)
                pending.add(call_id)
        if message.get("content") is None and role == "assistant" and calls:
            continue
        _content(message.get("content"), f"{field}.content")
    if pending:
        _invalid("unresolved_tool_calls", "messages")


def validate_native_chat_request(payload: dict[str, Any]) -> None:
    """Reject malformed converted messages without mutating or repairing them.

    The existing wire guards retain responsibility for request size/encoding.
    Structural rejections share one public message and carry a safe reason and
    field for diagnostics. IDs, text, schema values and decoder details stay out.
    """
    try:
        if not isinstance(payload, dict):
            _invalid("invalid_payload", "payload")
        _messages(payload.get("messages"))
        if "tools" in payload:
            _tools(payload["tools"])
    except NativeRequestValidationError:
        raise
    except (ValueError, TypeError, RecursionError):
        _invalid("invalid_transcript_structure", "payload")
