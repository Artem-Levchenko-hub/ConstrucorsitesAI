"""Preserve only bounded gateway attribution, never provider content or replay permission."""

from uuid import uuid4

import httpx
import pytest

from yleum_api.services import agent_builder
from yleum_api.services.agent_progress import sanitize_agent_step
from yleum_api.services.generation.recovery_trace import provider_error_context, safe_recovery_trace
from yleum_api.services.llm_client import _gateway_failure

from .test_legacy_provider_error_policy import install_transport


def failure(**overrides):
    return _gateway_failure(httpx.Response(502, json={"detail": {"error": {
        "code": "model_unavailable", "message": "private provider response",
        "details": {"upstream_http_status": 402, "provider_charge_ambiguous": True,
                    "provider_scope": "aitunnel", "transport_stage": "body", **overrides},
    }}}))


def test_terminal_chat_error_retains_safe_scope_and_call_identity():
    call = str(uuid4())
    error = failure(provider_call_id=call)
    assert error.provider_scope == "aitunnel" and error.transport_stage == "body"
    assert error.provider_call_id == call and error.upstream_http_status == 402
    assert error.provider_charge_ambiguous is True and error.retry_safe is False
    assert "private" not in str(error)
    projected = provider_error_context(error)
    assert projected == {"provider_scope": "aitunnel", "transport_stage": "body",
                         "provider_call_id": call}
    assert safe_recovery_trace(projected) == projected


@pytest.mark.parametrize("field,value", [
    ("provider_scope", "private untrusted scope"), ("provider_scope", []),
    ("transport_stage", "private provider body"), ("transport_stage", True),
    ("provider_call_id", "private external request-id"), ("provider_call_id", str(uuid4()).upper()),
])
def test_malformed_attribution_cannot_cross_the_error_boundary(field, value):
    error = failure(**{field: value})
    assert getattr(error, field) is None and error.retry_safe is False
    assert "private" not in str(error)


@pytest.mark.parametrize("kind,ambiguous", [
    ("response_deadline_exceeded", True), ("deadline_before_dispatch", False),
])
def test_local_deadline_keeps_context_without_becoming_a_provider_refusal(kind, ambiguous):
    call = str(uuid4())
    failure_kind = "response_deadline" if ambiguous else "deadline_before_dispatch"
    error = _gateway_failure(httpx.Response(504, json={"detail": {"error": {
        "code": kind, "details": {
            "provider_failure_kind": failure_kind, "provider_charge_ambiguous": ambiguous,
            "retry_safe": False, "provider_scope": "aitunnel", "transport_stage": "body",
            "provider_call_id": call,
        },
    }}}))
    assert error.code == kind and error.upstream_http_status is None
    assert error.provider_scope == "aitunnel" and error.provider_call_id == call
    assert error.provider_charge_ambiguous is ambiguous and error.retry_safe is False


async def test_one_text_failure_emits_the_scoped_context_through_public_sanitizer(monkeypatch):
    call, posts, events = str(uuid4()), [], []
    def reply(request):
        posts.append(request)
        return httpx.Response(502, json={"detail": {"error": {
            "code": "model_unavailable", "message": "private provider body",
            "details": {"upstream_http_status": 402, "provider_charge_ambiguous": True,
                        "provider_scope": "aitunnel", "transport_stage": "body",
                        "provider_call_id": call},
        }}})
    install_transport(monkeypatch, reply)
    async def execute(action):
        pytest.fail("a rejected request must not execute a source action")
    async def emit(event, payload):
        events.append((event, payload))
    result = await agent_builder.run_agent_build(
        system_prompt="synthetic", user_prompt="synthetic", model="claude-sonnet-5.5",
        execute=execute, emit=emit, max_steps=2, max_segments=1,
    )
    assert len(posts) == 1 and result.stop_reason == "provider_error" and not result.done
    diagnostic = next(payload for event, payload in events if event == "agent.provider_error")
    safe = sanitize_agent_step({"kind": "provider_error", "ok": False, **diagnostic})
    assert safe["provider_scope"] == "aitunnel" and safe["transport_stage"] == "body"
    assert safe["provider_call_id"] == call and safe["upstream_http_status"] == 402
    assert safe["will_retry"] is False and "private" not in str(safe) + result.summary
