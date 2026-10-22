"""Exact provider facts survive reload without exposing a raw error or authorizing replay."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_generation_recovery_public import records
from yleum_api.core.errors import ApiError
from yleum_api.schemas.message import GenerationFailureDiagnostics
from yleum_api.services.generation.recovery_public import project_loaded
from yleum_api.services.generation_failure import public_generation_failure


def provider_step(**overrides):
    return {
        "kind": "provider_error", "ok": False, "http_status": 502,
        "upstream_http_status": 502, "error_code": "model_unavailable",
        "provider_charge_ambiguous": True, "will_retry": False,
        "detail": "secret-provider-body", **overrides,
    }


async def test_owner_status_exposes_exact_failure_before_canary_cleanup():
    data = records()
    data["run"].status = "failed"
    data["run"].error = "provider_http_502: private-provider-body"
    data["message"].agent_steps = [provider_step()]
    view = await project_loaded(**data)
    assert view.failure.code == "provider_unavailable"
    assert view.failure.retryable is False
    assert view.failure.diagnostics.model_dump(exclude_none=True) == {
        "error_code": "model_unavailable", "error_origin": "provider", "http_status": 502,
        "upstream_http_status": 502, "provider_charge_ambiguous": True, "retry_safe": False,
    }
    assert not view.acceptance.current_request_complete
    assert "private-provider-body" not in view.model_dump_json()
    assert "secret-provider-body" not in view.model_dump_json()


async def test_failure_projection_preserves_owner_and_message_scope():
    from uuid import uuid4

    data = records()
    data["run"].status = "failed"
    data["run"].error = "provider_http_502: private"
    data["message"].project_id = uuid4()
    with pytest.raises(ApiError):
        await project_loaded(**data)


@pytest.mark.parametrize("terminal", ["MAX_FINALIZATION_FAILED: bad build", "deadline"])
def test_previous_transient_provider_error_is_not_relabelled_as_final_cause(terminal):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error=terminal, agent_state={}),
        agent_steps=[provider_step()],
    )
    assert failure.diagnostics is None


def test_historical_numeric_marker_has_exact_status_but_no_invented_receipt():
    failure = public_generation_failure(SimpleNamespace(
        status="failed", error="provider_http_502: arbitrary-body", agent_state={},
    ))
    assert failure.diagnostics.model_dump(exclude_none=True) == {
        "error_origin": "provider", "upstream_http_status": 502,
    }


def test_gateway_status_is_distinct_from_upstream_and_cannot_grant_retry():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_http_502: arbitrary", agent_state={}),
        agent_steps=[provider_step(http_status=503, retry_safe=True)],
    )
    assert failure.diagnostics.http_status == 503
    assert failure.diagnostics.upstream_http_status == 502
    assert failure.diagnostics.retry_safe is False


def test_stale_or_contradictory_step_does_not_override_terminal_status():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_http_502: arbitrary", agent_state={}),
        agent_steps=[provider_step(upstream_http_status=401)],
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {
        "error_origin": "provider", "upstream_http_status": 502,
    }


@pytest.mark.parametrize("steps", [
    [provider_step(will_retry=True)],
    [provider_step(), {"kind": "step", "tool": "build", "ok": True}],
])
def test_generic_failure_does_not_borrow_an_earlier_provider_attempt(steps):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_request_failed: private", agent_state={}),
        agent_steps=steps,
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {"error_origin": "unknown"}


def test_timeout_never_inherits_previous_http_error_even_if_steps_were_truncated():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_timeout: private", agent_state={}),
        agent_steps=[provider_step()],
    )
    assert failure.diagnostics.upstream_http_status is None


@pytest.mark.parametrize("value", [None, "secret", {}, [None, [], "secret"]])
def test_malformed_historical_steps_do_not_break_status(value):
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_request_failed: secret", agent_state={}),
        agent_steps=value,
    )
    assert "secret" not in failure.model_dump_json()


def test_arbitrary_codes_status_coercion_and_raw_messages_are_discarded():
    failure = public_generation_failure(
        SimpleNamespace(status="failed", error="provider_request_failed: secret", agent_state={}),
        agent_steps=[provider_step(
            error_code="secret", http_status=True, upstream_http_status="502",
            provider_charge_ambiguous="false", retry_safe="true",
        )],
    )
    assert failure.diagnostics.model_dump(exclude_none=True) == {"error_origin": "unknown"}


@pytest.mark.parametrize("payload", [
    {"http_status": True}, {"http_status": "502"}, {"upstream_http_status": 200},
    {"retry_safe": "true"}, {"error_code": "private"}, {"error_origin": "private"},
    {"body": "private"},
])
def test_public_diagnostics_schema_is_closed_and_strict(payload):
    with pytest.raises(ValidationError):
        GenerationFailureDiagnostics.model_validate(payload)


async def test_rest_failed_generation_preserves_committed_provider_cause(client, db_session):
    from tests.test_generation_runs import _owner_and_project
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.message import Message
    from yleum_api.services.generation_runs import reserve_generation_run

    owner, project = await _owner_and_project(db_session)
    run, _ = await reserve_generation_run(
        db_session, project_id=project.id, user_id=owner.id,
        idempotency_key="safe-provider-diagnostics", prompt="synthetic test",
    )
    message = Message(
        project_id=project.id, role="assistant", content="private-assistant-body",
        agent_steps=[provider_step()],
    )
    db_session.add(message)
    await db_session.flush()
    run.assistant_message_id = message.id
    run.status = "failed"
    run.error = "provider_http_502: private-provider-body"
    await db_session.commit()

    async def current_user():
        return owner

    app.dependency_overrides[get_current_user] = current_user
    try:
        response = await client.get(f"/api/projects/{project.id}/generation")
        assert response.status_code == 200
        body = response.json()
        assert body["failure"]["diagnostics"]["upstream_http_status"] == 502
        assert body["failure"]["diagnostics"]["retry_safe"] is False
        assert body["failure"]["code"] == "provider_unavailable"
        assert body["acceptance"]["current_request_complete"] is False
        assert "private-provider-body" not in response.text
        assert "private-assistant-body" not in response.text
        other, _ = await _owner_and_project(db_session)
        project.owner_id = other.id
        await db_session.commit()
        assert (await client.get(f"/api/projects/{project.id}/generation")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
