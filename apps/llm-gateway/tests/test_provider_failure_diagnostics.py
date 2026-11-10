"""Synthetic operator-log evidence only; no provider, filesystem or DB calls."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from structlog.testing import capture_logs

from yleum_gateway.services import provider_failure_diagnostics as diagnostics
from yleum_gateway.services import provider_validation_diagnostics as wire
from yleum_gateway.services.provider_failure_store import StoreResult

_KEY = "synthetic-provider-key+/=NeverARealCredential"
_URL = "https://owner:private-url-password@api.llmgw.ru/v1/chat/completions?private=query"
_PRIVATE_PACKETS = []


@pytest.fixture(autouse=True)
def fake_private_writer(monkeypatch):
    """Packet assertions observe an in-memory private sink, never stdout/files."""
    _PRIVATE_PACKETS.clear()

    def store(packet):
        _PRIVATE_PACKETS.append(deepcopy(packet))
        return StoreResult("stored", str(uuid4()), None)

    monkeypatch.setattr(diagnostics, "store_provider_failure", store)


def response(body=b'{"error":{"message":"unsupported request option"}}', *, status=422,
             request_body=None, headers=None):
    payload = request_body if request_body is not None else json.dumps({
        "model": "claude-sonnet-5.5", "stream": False, "max_tokens": 100,
        "messages": [{"role": "user", "content": "private request prompt"}],
        "unknown-private-field-name": "private unknown value",
    }).encode()
    request = httpx.Request("POST", _URL, content=payload,
                            headers={"authorization": "Bearer " + _KEY,
                                     "x-request-id": "safe-request-123"})
    return httpx.Response(status, request=request, content=body,
                          headers=headers or {"x-request-id": "safe-response-456"})


def record(value, *, scope="llmgw", credential=_KEY):
    before = len(_PRIVATE_PACKETS)
    with capture_logs() as logs:
        diagnostics.record_provider_failure(value, credential=credential, provider_scope=scope)
    assert len(logs) == 1 and len(_PRIVATE_PACKETS) == before + 1
    packet = _PRIVATE_PACKETS[-1]
    assert "utf8" not in logs[0]["response_body"]
    body = packet["response_body"].get("utf8")
    if body:
        assert body not in json.dumps(logs[0], ensure_ascii=False)
    assert logs[0]["private_artifact"]["state"] == "stored"
    return {**packet, "event": logs[0]["event"], "private_artifact": logs[0]["private_artifact"]}


@pytest.mark.parametrize("status", [400, 401, 402, 403, 422, 429, 500, 599])
def test_exact_small_error_retained_with_private_safe_shape_and_no_response_changes(status):
    value = response(status=status)
    request_bytes, body = value.request.content, value.content
    headers, extensions = list(value.headers.multi_items()), dict(value.extensions)
    event = record(value)
    assert event["event"] == "provider.http_failure"
    assert event["diagnostic_visibility"] == "operator_only"
    assert event["provider_scope"] == "llmgw" and event["upstream_http_status"] == status
    assert event["response_body"] == {"sha256": hashlib.sha256(body).hexdigest(),
                                      "bytes": len(body), "content_state": "exact_utf8",
                                      "utf8": body.decode()}
    assert event["request_body_sha256"] == hashlib.sha256(request_bytes).hexdigest()
    assert event["request_body_bytes"] == len(request_bytes)
    assert event["request_headers"] == {"x-request-id": "safe-request-123"}
    assert event["response_headers"] == {"x-request-id": "safe-response-456"}
    assert event["endpoint_class"] == "chat_completions"
    shape = event["request_shape"]
    assert shape["unknown_field_count"] == 1 and shape["message_count"] == 1
    assert shape["roles"]["user"] == 1 and shape["content_types"]["string"] == 1
    assert shape["field_types"]["stream"] == "boolean"
    assert shape["field_types"]["max_tokens"] == "integer"
    for secret in ("private request prompt", "unknown-private-field-name", "private unknown value",
                   "private-url-password", "private=query", _URL, _KEY):
        assert secret not in json.dumps(event)
    assert value.request.content == request_bytes and value.content == body
    assert list(value.headers.multi_items()) == headers and value.extensions == extensions


def test_response_echoed_credentials_all_existing_spellings_and_named_secrets_are_redacted():
    header_secret = "synthetic-header-secret-not-current-key"
    forms = wire._secret_forms(_KEY) | wire._secret_forms(header_secret)
    body = json.dumps({"error": {"message": "secret_key=other-named-secret; reason is invalid field",
                                  "client_secret": {"nested": "nested-private-secret"}},
                       "echo": sorted(forms), "link": "https://owner:private-pass@host/path?token=private-query#fragment"}).encode()
    value = response(body)
    value.request.headers["x-api-key"] = header_secret
    value.headers["set-cookie"] = "private-cookie-value"
    value.headers["x-request-id"] = _KEY
    value.headers["x-unapproved-debug"] = "private-debug-header"
    event = record(value)
    serialized = json.dumps(event)
    for secret in (*forms, "other-named-secret", "nested-private-secret", "private-pass",
                   "private-query", "private-debug-header", "private-cookie-value"):
        assert secret not in serialized
    assert event["response_headers"] == {}
    assert event["response_body"]["content_state"] == "redacted"
    assert "reason is invalid field" in event["response_body"]["utf8"]
    assert event["response_body"]["sha256"] == hashlib.sha256(body).hexdigest()


def test_escaped_JSON_credentials_and_URLs_and_duplicate_error_fields_preserve_safe_reason():
    body = (b'{"error":"first safe reason","api\\u005fkey":"encoded-private-secret",'
            b'"error":"second safe reason","url":"\\u0068ttps:\\/\\/owner:pass@host/path?x=y"}')
    event = record(response(body))
    text = event["response_body"]["utf8"]
    assert "first safe reason" in text and "second safe reason" in text
    assert "encoded-private-secret" not in text and "owner" not in text and "host/path" not in text
    assert event["response_body"]["content_state"] == "redacted"


@pytest.mark.parametrize("body,reason", [(b"x" * (16 * 1024 + 1), "body_budget_exceeded"),
                                        (b"\xffprivate-non-utf8", "not_utf8")],
                         ids=["oversized-response", "invalid-UTF8"])
def test_oversize_or_non_UTF8_is_withheld_without_truncation(body, reason):
    event = record(response(body))
    assert event["response_body"] == {"sha256": hashlib.sha256(body).hexdigest(),
                                      "bytes": len(body), "content_state": "withheld",
                                      "reason": reason}


def test_exact_UTF8_limit_and_redaction_expansion_are_honest():
    body = ("ы" * (8 * 1024)).encode()
    assert record(response(body))["response_body"]["utf8"].encode() == body
    event = record(response(b"x" * 2000), credential="x")
    assert event["response_body"]["reason"] == "sanitized_body_budget_exceeded"
    assert "utf8" not in event["response_body"]


def test_unread_request_and_response_streams_are_never_consumed():
    class ForbiddenStream(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("Diagnostics must not consume streams")
            yield b""  # pragma: no cover

    request = httpx.Request("POST", "https://api.llmgw.ru/v1/messages", stream=ForbiddenStream())
    value = httpx.Response(422, request=request, stream=ForbiddenStream())
    event = record(value)
    assert event["response_body"] == {"sha256": None, "bytes": None,
                                      "content_state": "unavailable", "reason": "response_not_read"}
    assert event["request_body_sha256"] is event["request_body_bytes"] is None
    assert event["request_shape"]["reason"] == "request_not_read"
    assert not value.is_stream_consumed


def test_missing_request_retains_status_body_and_committed_scope():
    ids = {name: uuid4() for name in ("project_id", "run_id", "call_id")}
    with wire.provider_validation_scope(**ids):
        event = record(httpx.Response(422, content=b"safe reason"), scope="aitunnel")
    assert event["upstream_http_status"] == 422 and event["provider_scope"] == "aitunnel"
    assert event["response_body"]["utf8"] == "safe reason"
    assert event["request_body_sha256"] is None and event["request_headers"] == {}
    assert {key: event[key] for key in ids} == {key: str(value) for key, value in ids.items()}


async def test_nested_and_concurrent_scope_copies_cannot_leak_or_be_mutated():
    groups = [{name: uuid4() for name in ("project_id", "run_id", "call_id")} for _ in range(2)]

    async def one(ids):
        with wire.provider_validation_scope(**ids):
            copy = wire.provider_diagnostic_scope()
            with pytest.raises(TypeError):
                copy["run_id"] = "not canonical"
            await asyncio.sleep(0)
            event = record(response())
            with wire.provider_validation_scope(project_id="invalid", run_id=ids["run_id"], call_id=ids["call_id"]):
                assert not wire.provider_diagnostic_scope()
            assert dict(copy) == dict(wire.provider_diagnostic_scope())
            return event

    events = await asyncio.gather(*(one(ids) for ids in groups))
    for ids, event in zip(groups, events, strict=True):
        assert {key: event[key] for key in ids} == {key: str(value) for key, value in ids.items()}
    assert not wire.provider_diagnostic_scope()


def test_sink_failure_does_not_replace_original_exception_or_modify_response(monkeypatch):
    value = response()

    def broken(*_args, **_kwargs):
        raise OSError("synthetic private sink failure")

    monkeypatch.setattr(diagnostics, "log", SimpleNamespace(warning=broken))
    original = None
    with pytest.raises(httpx.HTTPStatusError) as raised:
        try:
            value.raise_for_status()
        except httpx.HTTPStatusError as error:
            original = error
            diagnostics.record_provider_failure(value, credential=_KEY, provider_scope="llmgw")
            raise
    assert raised.value is original and raised.value.response is value
    assert value.content == b'{"error":{"message":"unsupported request option"}}'


@pytest.mark.parametrize("mutate", [False, True])
def test_private_store_failure_never_places_body_or_exception_text_in_summary(monkeypatch, mutate):
    marker = "PRIVATE-RESPONSE-BODY-MUST-NOT-ENTER-STDOUT"
    value = response(json.dumps({"error": marker}).encode())

    def broken(packet):
        if mutate:
            packet["response_body"]["utf8"] = marker
            packet["request_shape"]["injected"] = marker
        raise OSError(marker)

    monkeypatch.setattr(diagnostics, "store_provider_failure", broken)
    with capture_logs() as logs:
        diagnostics.record_provider_failure(value, credential=_KEY, provider_scope="llmgw")
    assert len(logs) == 1
    assert marker not in json.dumps(logs)
    assert "utf8" not in logs[0]["response_body"]
    assert logs[0]["private_artifact"] == {"state": "withheld", "artifact_id": None,
                                            "reason": "store_unavailable"}
    assert logs[0]["response_body"]["sha256"] == hashlib.sha256(value.content).hexdigest()


def test_untrusted_sink_result_cannot_leak_its_fields_or_claim_storage(monkeypatch):
    monkeypatch.setattr(diagnostics, "store_provider_failure", lambda packet: StoreResult(
        "stored", "PRIVATE-INVALID-ARTIFACT", "PRIVATE-INVALID-REASON"))
    with capture_logs() as logs:
        diagnostics.record_provider_failure(response(), credential=_KEY, provider_scope="llmgw")
    assert logs[0]["private_artifact"] == {"state": "withheld", "artifact_id": None,
                                            "reason": "invalid_store_result"}
    assert "PRIVATE-INVALID" not in json.dumps(logs)


def test_sanitizer_failure_retains_known_status_and_original_hash(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("do not export this exception text")

    monkeypatch.setattr(diagnostics, "_json_text", broken)
    value = response()
    event = record(value)
    assert event["upstream_http_status"] == 422
    assert event["response_body"] == {"sha256": hashlib.sha256(value.content).hexdigest(),
                                      "bytes": len(value.content), "content_state": "withheld",
                                      "reason": "sanitization_failed"}


def test_redacted_JSON_with_invalid_escaped_unicode_keeps_status_without_unsafe_text():
    body = b'{"url":"https://host/path?private=value","error":"\\ud800"}'
    event = record(response(body))
    assert event["upstream_http_status"] == 422
    assert event["response_body"] == {"sha256": hashlib.sha256(body).hexdigest(),
                                      "bytes": len(body), "content_state": "withheld",
                                      "reason": "sanitization_failed"}


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"x" * (2 * 1024 * 1024 + 1)],
                         ids=["not-JSON", "not-object", "oversized-request"])
def test_unavailable_request_shape_still_preserves_actual_sent_digest(body):
    event = record(response(request_body=body))
    assert event["request_shape"]["content_state"] in {"unavailable", "withheld"}
    assert event["request_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert event["request_body_bytes"] == len(body)


def test_request_unknown_types_and_header_metadata_never_echo_unapproved_names_or_values():
    payload = {"model": "private-model-name", "stream": "private-wrong-value", "max_tokens": [],
               "messages": [{"role": "private-role", "content": [{"type": "private-type", "text": "private-text"}]}],
               "private-field": "private-field-value"}
    value = response(request_body=json.dumps(payload).encode(), headers={
        "request-id": "https://owner:secret@host/path?token=secret", "x-trace-id": "z" * 201,
        "unapproved-private-name": "unapproved-private-value"})
    event = record(value)
    serialized = json.dumps(event)
    assert not any(secret in serialized for secret in ("private-model-name", "private-wrong-value",
        "private-role", "private-type", "private-text", "private-field", "owner", "unapproved-private"))
    assert event["response_headers"] == {}
    assert event["request_shape"]["field_types"]["max_tokens"] == "array"


@pytest.mark.parametrize("model,bucket", [
    ("anthropic/claude-sonnet-5.5", "claude-sonnet-5.5"),
    ("anthropic/claude-sonnet-5", "claude-sonnet-5"),
    ("~anthropic/claude-sonnet-latest", "claude-sonnet-latest"),
    ("google/gemini-3.1-pro-preview-customtools", "gemini-3.1-pro-preview-customtools"),
])
def test_exact_configured_model_aliases_use_fixed_safe_buckets(model, bucket):
    event = record(response(request_body=json.dumps({"model": model}).encode()))
    assert event["request_shape"]["model_class"] == bucket


@pytest.mark.parametrize("status,scope", [(200, "llmgw"), (302, "aitunnel"), (600, "llmgw"), (422, "private-scope")])
def test_non_failure_or_unknown_scope_emits_nothing(status, scope):
    with capture_logs() as logs:
        diagnostics.record_provider_failure(response(status=status), credential=_KEY, provider_scope=scope)
    assert logs == []
