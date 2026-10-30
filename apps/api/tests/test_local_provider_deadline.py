"""A gateway local deadline stops the agent without disguising real upstream failures."""

from types import SimpleNamespace

import httpx
import pytest

from yleum_api.services import agent_builder, llm_client
from yleum_api.services.agent_progress import sanitize_agent_step
from yleum_api.services.generation_failure import public_generation_failure

from .test_agent_builder import _ok_executor
from .test_legacy_provider_error_policy import install_transport


def deadline_response(**overrides):
    return httpx.Response(504, json={"detail": {"error": {
        "code": "deadline_before_dispatch", "message": "PRIVATE raw gateway diagnostic",
        "details": {"provider_failure_kind": "deadline_before_dispatch",
                    "provider_charge_ambiguous": False, "retry_safe": False, **overrides},
    }}})


async def test_local_deadline_ends_agent_without_retry_and_reports_safe_cause(monkeypatch):
    requests, events, saved, actions = [], [], [], []
    def reply(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": ('<omnia:action name="write_file">'
                            '{"path":"a.ts","content":"draft"}</omnia:action>'),
            }}]})
        return deadline_response()
    install_transport(monkeypatch, reply)
    async def checkpoint(candidate):
        saved.append(dict(candidate.files))
    async def emit(kind, payload):
        events.append((kind, payload))
    result = await agent_builder.run_agent_build(
        system_prompt="s", user_prompt="t", model="claude-sonnet-5.5",
        execute=_ok_executor(actions), emit=emit, max_steps=4, max_segments=3,
        checkpoint=checkpoint,
    )
    assert len(requests) == 2 and not result.done and not result.needs_finalization
    assert actions == [("write_file", "a.ts")] and saved == [{"a.ts": "draft"}]
    assert result.files == {"a.ts": "draft"} and result.stop_reason == "provider_error"
    assert result.summary.startswith("generation deadline exceeded before provider dispatch:")
    diagnostic = next(data for kind, data in events if kind == "agent.provider_error")
    safe = sanitize_agent_step({"kind": "provider_error", "ok": False, **diagnostic})
    assert safe["error_origin"] == "gateway"
    assert safe["error_code"] == "deadline_before_dispatch"
    assert safe["provider_charge_ambiguous"] is False and safe["will_retry"] is False
    assert "PRIVATE" not in str(safe) + result.summary
    run = SimpleNamespace(status="failed", error=result.summary, agent_state={})
    failure = public_generation_failure(run, agent_steps=[safe])
    assert failure.code == "deadline"
    assert failure.diagnostics.error_origin == "gateway"
    assert failure.diagnostics.provider_charge_ambiguous is False
    assert "не отправлен" in failure.message
    assert "Черновик сохранён" not in failure.message
    run.agent_state = {"source_checkpoint": {"digest": "a" * 64, "baseline_sha": "b" * 40}}
    saved = public_generation_failure(run, agent_steps=[safe])
    assert "Черновик сохранён" in saved.message
    assert "Продолжи сборку" in saved.message


@pytest.mark.parametrize("details", [
    {"upstream_http_status": 502}, {"provider_charge_ambiguous": True},
    {"retry_safe": True}, {"provider_failure_kind": "preconnect"},
])
def test_conflicting_deadline_wire_is_not_local_deadline(details):
    failure = llm_client._gateway_failure(deadline_response(**details))
    assert failure.code != "deadline_before_dispatch"
    assert failure.retry_safe is False


def test_external_502_retains_provider_classification():
    failure = llm_client._gateway_failure(httpx.Response(502, json={"detail": {"error": {
        "code": "model_unavailable", "details": {"upstream_http_status": 502,
        "provider_charge_ambiguous": True},
    }}}))
    assert failure.code == "model_unavailable" and failure.upstream_http_status == 502
    assert failure.provider_charge_ambiguous is True and failure.retry_safe is False


@pytest.mark.parametrize("field", [
    "provider_failure_kind", "provider_charge_ambiguous", "retry_safe",
])
def test_incomplete_deadline_contract_does_not_claim_no_dispatch(field):
    body = deadline_response().json()
    del body["detail"]["error"]["details"][field]
    error = llm_client._gateway_failure(httpx.Response(504, json=body))
    assert error.code != "deadline_before_dispatch" and error.retry_safe is False


@pytest.mark.parametrize("checkpoint", [None, {}, {"digest": "a" * 64},
    {"digest": "bad", "baseline_sha": None},
    {"digest": "a" * 64, "baseline_sha": "bad"},
])
def test_deadline_does_not_invent_saved_draft(checkpoint):
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="generation deadline exceeded before provider dispatch: internal",
        agent_state={"source_checkpoint": checkpoint},
    ))
    assert failure.code == "deadline" and "Черновик сохранён" not in failure.message


def test_restoration_deadline_keeps_explicit_history_recovery_path():
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="generation deadline exceeded before provider dispatch: internal",
        agent_state={"restoration_adaptation": {},
                     "source_checkpoint": {"digest": "a" * 64, "baseline_sha": "b" * 40}},
    ))
    assert failure.retryable is False
    assert "историю версий" in failure.message and "Продолжи сборку" not in failure.message
