"""Pure transcript validation; gateway conftest forbids unmocked HTTP."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from yleum_gateway.services.native_request_validation import (
    INVALID_NATIVE_REQUEST_MESSAGE,
    validate_native_chat_request,
)


def _call(call_id: str = "one", *, name: str = "read_file", arguments: str = "{}") -> dict[str, Any]:
    return {
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _turn(call: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {"role": "assistant", "content": None, "tool_calls": [_call() if call is None else call]},
        {"role": "tool", "tool_call_id": "one", "content": ""},
    ]


def _payload(messages: list[Any] | None = None) -> dict[str, Any]:
    return {
        "model": "anthropic/claude-sonnet-5.5",
        "stream": False,
        "messages": [{"role": "user", "content": "Build carefully"}] if messages is None else messages,
    }


def _invalid(payload: Any) -> None:
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError) as caught:
        validate_native_chat_request(payload)
    assert str(caught.value) == INVALID_NATIVE_REQUEST_MESSAGE
    assert caught.value.__cause__ is None
    assert payload == before


def test_text_multimodal_cache_and_parallel_tools_are_unchanged() -> None:
    cache = {"type": "ephemeral", "ttl": "5m"}
    payload = _payload([
        {"role": "system", "content": [{"type": "text", "text": "Stable", "cache_control": cache}]},
        {"role": "user", "content": [
            {"type": "text", "text": "Reference"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": "high"},
             "cache_control": cache},
        ]},
        {"role": "assistant", "content": "Inspecting", "tool_calls": [
            _call("one", arguments='{"path":"src/app.tsx"}'), _call("two"),
        ]},
        {"role": "tool", "tool_call_id": "two", "content": "", "cache_control": cache},
        {"role": "tool", "tool_call_id": "one", "content": "result"},
        {"role": "user", "content": [{"type": "text", "text": "Now implement", "cache_control": cache}]},
        {"role": "assistant", "content": "Done"},
    ])
    payload["tools"] = [{
        "type": "function", "cache_control": cache,
        "function": {
            "name": "done", "description": "Finish",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    payload["tool_choice"] = "auto"
    before = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    validate_native_chat_request(payload)
    assert json.dumps(payload, ensure_ascii=False, separators=(",", ":")) == before


def test_historical_names_and_opaque_ids_are_not_limited_to_current_tools() -> None:
    call_id = "provider:工具/opaque id.123"
    payload = _payload([
        {"role": "assistant", "tool_calls": [_call(call_id, name="historical:工具/read")]},
        {"role": "tool", "tool_call_id": call_id, "content": "result"},
    ])
    payload["tools"] = [{"type": "function", "function": {
        "name": "current:工具/write", "parameters": {"$ref": "#/$defs/Args", "$defs": {"Args": {}}},
    }}]
    validate_native_chat_request(payload)


def test_actual_native_converter_preserves_valid_tool_and_guidance_fixture() -> None:
    from yleum_gateway.routers.messages_native import _openai_messages, _openai_tools

    cache = {"type": "ephemeral"}
    converted = _openai_messages({
        "system": [{"type": "text", "text": "Build", "cache_control": cache}],
        "messages": [
            {"role": "user", "content": "Create an app"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "not forwarded"},
                {"type": "tool_use", "id": "one", "name": "read_file", "input": {"path": "page.tsx"}},
                {"type": "tool_use", "id": "two", "name": "read_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "one", "content": "source"},
                {"type": "tool_result", "tool_use_id": "two", "content": "", "cache_control": cache},
                {"type": "text", "text": "Implement now", "cache_control": cache},
            ]},
        ],
    })
    payload = _payload(converted)
    payload["tools"] = _openai_tools([{
        "name": "done", "input_schema": {"type": "object", "properties": {}}, "cache_control": cache,
    }])
    before = copy.deepcopy(payload)
    validate_native_chat_request(payload)
    assert payload == before
    assert [message["role"] for message in converted] == ["system", "user", "assistant", "tool", "tool", "user"]


@pytest.mark.parametrize("body", [
    {"messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "private"}]}]},
    {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "", "content": "result"}]}]},
    {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "orphan", "content": "result"}]}]},
])
def test_previously_unchecked_converter_shapes_are_rejected(body: dict[str, Any]) -> None:
    from yleum_gateway.routers.messages_native import _openai_messages

    _invalid(_payload(_openai_messages(body)))


@pytest.mark.parametrize("content", [
    None, True, 7, {"text": "wrong envelope"}, [], ["text"], [None],
    [{"type": "text", "text": 7}], [{"type": "text", "text": {"untrusted": "value"}}],
    [{"type": "text"}], [{"type": "text", "text": "x", "cache_control": "ephemeral"}],
    [{"type": "image_url", "image_url": "https://example.invalid/x"}],
    [{"type": "image_url", "image_url": {"url": ""}}],
    [{"type": "image_url", "image_url": {"url": True}}],
    [{"type": "image_url", "image_url": {"url": "https://example.invalid/x", "detail": []}}],
    [{"type": "image_url", "image_url": {"url": "https://example.invalid/x", "detail": "wrong"}}],
    [{"type": "thinking", "thinking": "not an OpenAI content block"}],
])
def test_malformed_message_content_is_rejected(content: Any) -> None:
    _invalid(_payload([{"role": "user", "content": content}]))


@pytest.mark.parametrize("messages", [
    [], [None], [{"role": "unknown", "content": "x"}], [{"role": [], "content": "x"}],
    [{"role": "assistant", "content": None}], [{"role": "assistant"}],
    [{"role": "assistant", "content": "x", "tool_calls": []}],
    [{"role": "assistant", "content": "x", "tool_calls": None}],
    [{"role": "user", "content": "x", "tool_calls": [_call()]}],
    [{"role": "user", "content": "x", "tool_call_id": "one"}],
    [{"role": "tool", "content": "x", "tool_call_id": "orphan"}],
    [{"role": "user", "content": "x", "cache_control": []}],
])
def test_malformed_message_envelopes_are_rejected(messages: list[Any]) -> None:
    _invalid(_payload(messages))


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "  "), ("id", 1), ("type", "wrong"), ("function", None),
])
def test_malformed_tool_call_envelope_is_rejected(field: str, value: Any) -> None:
    call = _call()
    call[field] = value
    _invalid(_payload(_turn(call)))


@pytest.mark.parametrize("field,value", [
    ("name", ""), ("name", "  "), ("name", False),
    ("arguments", {}), ("arguments", ""), ("arguments", "not JSON"),
    ("arguments", "[]"), ("arguments", "null"), ("arguments", "true"),
    ("arguments", '{"x":NaN}'), ("arguments", '{"x":Infinity}'),
    ("arguments", '{"x":-Infinity}'), ("arguments", "[" * 1100),
])
def test_tool_arguments_must_be_json_objects(field: str, value: Any) -> None:
    call = _call()
    call["function"][field] = value
    _invalid(_payload(_turn(call)))


def test_json_numbers_do_not_add_an_unrelated_float_range_restriction() -> None:
    validate_native_chat_request(_payload(_turn(_call(arguments='{"x":1e999}'))))


@pytest.mark.parametrize("arguments", [
    '{"x":1e9999999999999999999}',
    '{"x":1e-9999999999999999999}',
])
def test_decimal_parser_boundaries_keep_the_fixed_safe_error(arguments: str) -> None:
    _invalid(_payload(_turn(_call(arguments=arguments))))


@pytest.mark.parametrize("messages", [
    [{"role": "assistant", "tool_calls": [_call()]}],
    [{"role": "assistant", "tool_calls": [_call(), _call()]}],
    _turn() + [{"role": "tool", "tool_call_id": "one", "content": "duplicate"}],
    _turn() + _turn(),
    [{"role": "assistant", "tool_calls": [_call()]}, {"role": "user", "content": "early guidance"}],
    [{"role": "assistant", "tool_calls": [_call(), _call("two")]},
     {"role": "tool", "tool_call_id": "one", "content": "result"},
     {"role": "user", "content": "one call still unresolved"}],
    [{"role": "assistant", "tool_calls": [_call()]},
     {"role": "tool", "tool_call_id": "wrong", "content": "result"}],
    [{"role": "assistant", "tool_calls": [_call()]},
     {"role": "assistant", "content": "unresolved result"}],
])
def test_tool_results_must_resolve_exactly_once_before_next_turn(messages: list[Any]) -> None:
    _invalid(_payload(messages))


@pytest.mark.parametrize("tools", [
    None, {}, [None], [{"type": "wrong", "function": {}}],
    [{"type": "function", "function": {"name": "", "parameters": {}}}],
    [{"type": "function", "function": {"name": "x", "parameters": []}}],
    [{"type": "function", "function": {"name": "x", "description": {}, "parameters": {}}}],
    [{"type": "function", "function": {"name": "x", "parameters": {"type": "array"}}}],
    [{"type": "function", "function": {"name": "x", "parameters": {"properties": []}}}],
    [{"type": "function", "function": {"name": "x", "parameters": {"required": "x"}}}],
    [{"type": "function", "function": {"name": "x", "parameters": {"required": [1]}}}],
    [{"type": "function", "function": {"name": "x", "parameters": {"required": ["x", "x"]}}}],
    [{"type": "function", "cache_control": None, "function": {"name": "x", "parameters": {}}}],
    [{"type": "function", "function": {"name": "x", "parameters": {}}}] * 2,
])
def test_basic_tool_definition_shapes_are_checked(tools: Any) -> None:
    payload = _payload()
    payload["tools"] = tools
    _invalid(payload)


@pytest.mark.parametrize("payload", [None, [], {"messages": None}, {"messages": "not a list"}])
def test_invalid_payload_envelopes_are_safe(payload: Any) -> None:
    _invalid(payload)


def test_failure_message_never_contains_untrusted_values() -> None:
    sentinel = "private-synthetic-untrusted-value"
    call = _call(arguments=sentinel)
    call["id"] = sentinel
    with pytest.raises(ValueError) as caught:
        validate_native_chat_request(_payload(_turn(call)))
    assert str(caught.value) == INVALID_NATIVE_REQUEST_MESSAGE
    assert sentinel not in str(caught.value)


def test_validator_leaves_size_and_nonstructural_wire_policy_to_existing_guards() -> None:
    payload = _payload()
    payload.update({"stream": False, "max_tokens": 123, "temperature": 0.7, "extra": object()})
    before = payload.copy()
    validate_native_chat_request(payload)
    assert payload == before
