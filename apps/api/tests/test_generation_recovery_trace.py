"""Causal diagnostics keep protocol facts and browser origins, never request bodies."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.services.agent_progress import sanitize_agent_step
from yleum_api.services.generation.progress import GenerationProgress
from yleum_api.services.generation.recovery_trace import safe_recovery_trace as normalize
from yleum_api.services.generation_events import (
    append_generation_event,
    generation_event_envelope,
    redact_event_payload,
)


def test_trace_links_call_and_source_without_secrets():
    safe = normalize({
        "run_id": "run-1", "attempt_id": "attempt-2", "operation_id": "operation-3",
        "provider_call_id": "call-4", "provider_request_id": "request-5",
        "requirement_id": "requirement-6", "snapshot_id": "snapshot-7", "fence": 8,
        "snapshot_hash": "d" * 64, "release_revision": "e" * 40,
        "prompt_hash": "a" * 64, "source_hash": "b" * 64, "proof_hash": "c" * 64,
        "stage": "repair", "error_origin": "provider", "finish_reason": "length",
        "max_tokens_mode": "omitted", "completion_tokens": 128000,
        "transport_stage": "body", "retry_decision": "unknown",
        "error_code": "provider_output_truncated", "upstream_http_status": 429,
        "authorization": "private-auth", "cookies": "private-cookie",
        "raw_response": "private-body", "message": "private-message",
        "messages": [{"content": "private-prompt"}], "api_key": "private-key",
    })
    assert safe["run_id"] == "run-1" and safe["stage"] == "repair"
    assert safe["attempt_id"] == "attempt-2" and safe["provider_call_id"] == "call-4"
    assert safe["source_hash"] == "b" * 64 and safe["proof_hash"] == "c" * 64
    assert safe["error_origin"] == "provider" and safe["max_tokens_mode"] == "omitted"
    assert safe["completion_tokens"] == 128000
    assert safe["requirement_id"] == "requirement-6" and safe["fence"] == 8
    assert safe["snapshot_hash"] == "d" * 64 and safe["release_revision"] == "e" * 40
    assert "provider_refusal_category" not in safe  # HTTP 429 alone proves no category.
    assert "private-" not in str(safe)


def test_console_error_keeps_source_stack_and_url_safely():
    safe = normalize({
        "run_id": "run-1", "stage": "verify", "error_origin": "browser",
        "source_hash": "b" * 64,
        "console_error": {
            "message": "TypeError: request failed token=private-token",
            "source": "https://app.test/page.tsx?token=private-source",
            "stack": "TypeError\n at load (https://app.test/page.tsx?key=private-key:12:4)",
            "url": "https://user:private-pass@app.test/page?token=private-query&view=public#private",
            "authorization": "private-auth", "cookies": "private-cookie",
            "response_body": "private-response",
        },
    })
    diagnostic = safe["console_error"]
    assert safe["error_origin"] == "browser" and safe["source_hash"] == "b" * 64
    assert diagnostic["message"].startswith("TypeError: request failed")
    assert "at load" in diagnostic["stack"] and "page.tsx" in diagnostic["stack"]
    assert diagnostic["source"] == "https://app.test/page.tsx"
    assert diagnostic["url"].startswith("https://app.test/page")
    assert "private-" not in json.dumps(safe)
    assert "response_body" not in diagnostic and "authorization" not in diagnostic


@pytest.mark.parametrize("value", [True, -1, "128000", 10**100, {}, []])
def test_trace_rejects_malformed_protocol_fields(value):
    safe = normalize({"completion_tokens": value, "requested_max_tokens": value,
                      "stage": value, "source_hash": value, "run_id": value})
    assert safe == {}


def test_trace_and_event_preserve_token_counters_with_existing_byte_bounds():
    safe = normalize({"error_origin": "browser", "completion_tokens": 5,
                      "console_error": {"message": "я" * 20000, "stack": "x" * 20000,
                                        "url": "https://app.test/" + "a" * 20000}})
    event = redact_event_payload({"recovery_trace": safe, "detail": "z" * 20000})
    assert event["recovery_trace"]["completion_tokens"] == 5
    assert len(json.dumps(event, ensure_ascii=False).encode()) <= 16_384
    assert all(len(value.encode()) <= 4096 for value in safe["console_error"].values())


def test_public_transcript_sanitizes_previously_stored_trace():
    safe = sanitize_agent_step({"action": "Check", "recovery_trace": {
        "run_id": "run-1", "raw_response": "private-response",
        "console_error": {"url": "https://app.test/?token=private-token"},
        "error_origin": "browser",
    }})
    assert safe["recovery_trace"]["run_id"] == "run-1"
    assert "private-" not in str(safe)


async def test_provider_progress_attaches_correlation_and_discards_response_detail(monkeypatch):
    recorder = GenerationProgress(None, uuid4(), uuid4(), uuid4())
    record = AsyncMock()
    monkeypatch.setattr(recorder, "record_agent_step", record)
    await recorder.emit_agent_event("agent.step", {
        "action": "provider_response", "detail": "private-provider-body",
        "run_id": "foreign-run", "attempt_id": "attempt-2", "source_hash": "b" * 64,
        "provider_call_id": "call-4", "finish_reason": "length",
        "max_tokens_mode": "omitted", "completion_tokens": 128000,
    })
    row = record.await_args.args[0]
    assert row["recovery_trace"]["run_id"] == str(recorder.run_id)
    assert row["recovery_trace"]["provider_call_id"] == "call-4"
    assert row["recovery_trace"]["source_hash"] == "b" * 64
    assert "private-provider-body" not in str(row)


async def test_generation_trace_boundary_uses_run_identity_and_allowlist(monkeypatch):
    recorder = GenerationProgress(None, uuid4(), uuid4(), uuid4())
    record = AsyncMock()
    monkeypatch.setattr(recorder, "_record", record)
    await recorder.record_generation_event("generation.trace", {
        "run_id": "foreign-run", "attempt_id": "attempt-2", "stage": "repair",
        "raw_response": "private-body", "message": "private-message",
    })
    payload = record.await_args.args[1]
    assert payload["run_id"] == str(recorder.run_id)
    assert payload["attempt_id"] == "attempt-2"
    assert "private-" not in str(payload)


def test_event_replay_sanitizes_historical_trace_and_binds_run():
    run_id = uuid4()
    event = SimpleNamespace(
        id=uuid4(), generation_run_id=run_id, seq=1, event_type="generation.trace",
        payload={"run_id": "foreign-run", "stage": "repair", "raw_response": "private-body",
                 "error_origin": "browser", "console_error": {
                     "message": "TypeError", "url": "https://app.test/?secret=private-query"}},
    )
    envelope = generation_event_envelope(event)
    assert envelope["data"]["run_id"] == str(run_id)
    assert envelope["data"]["console_error"]["message"] == "TypeError"
    assert "private-" not in str(envelope)


def test_event_replay_filters_incomplete_historical_provider_row():
    event = SimpleNamespace(
        id=uuid4(), generation_run_id=uuid4(), seq=1, event_type="agent.step",
        payload={"kind": "provider_error", "raw_response": "private-body"},
    )
    assert "private-body" not in str(generation_event_envelope(event))


async def test_event_append_filters_trace_and_preserves_scalar_token_counts():
    run_id, project_id = uuid4(), uuid4()
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [SimpleNamespace(id=run_id, project_id=project_id), 1]
    event = await append_generation_event(
        session, run_id=run_id, project_id=project_id, message_id=None,
        event_type="generation.trace",
        payload={"run_id": "foreign-run", "raw_response": "private-body",
                 "prompt_tokens": 12, "completion_tokens": 3, "max_tokens_mode": "omitted"},
    )
    assert event.payload == {"run_id": str(run_id), "prompt_tokens": 12,
                             "completion_tokens": 3, "max_tokens_mode": "omitted"}


def test_provider_transcript_replay_never_exposes_provider_content():
    row = sanitize_agent_step({
        "kind": "step", "action": "private-action", "tool": "provider_response",
        "detail": "private-detail", "human": "private-human", "raw_response": "private-body",
        "finish_reason": "length", "completion_tokens": 10,
    })
    assert row["finish_reason"] == "length" and row["completion_tokens"] == 10
    assert "private-" not in str(row)


def test_provider_event_cannot_use_browser_exception_to_store_response_body():
    row = redact_event_payload({
        "kind": "provider_error", "step": 1, "action": "Failure",
        "raw_response": "private-body", "error_origin": "browser",
        "recovery_trace": {"error_origin": "browser", "console_error": {
            "message": "private-provider-body"}},
    })
    assert "private-" not in str(row)
    assert row["error_origin"] == "provider"


@pytest.mark.parametrize("message", [
    'request failed token="private-token"',
    'request failed cookies=private-cookie',
    'request failed "cookie": "private-cookie"',
    'request failed Authorization: Bearer private-auth',
])
def test_browser_diagnostic_redacts_inline_credentials(message):
    safe = normalize({"error_origin": "browser", "console_error": {"message": message}})
    assert "request failed" in safe["console_error"]["message"]
    assert "private-" not in str(safe)


def test_browser_redaction_catches_quoted_secret_crossing_storage_boundary():
    safe = normalize({"error_origin": "browser", "console_error": {
        "message": "x" * 4070 + ' token="private-' + "s" * 6000 + '"',
    }})
    message = safe["console_error"]["message"]
    assert "private-" not in message
    assert "[REDACTED]" in message and len(message.encode()) <= 4096
