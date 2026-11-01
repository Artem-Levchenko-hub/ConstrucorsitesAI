import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from yleum_api.schemas.message import MessagePublic
from yleum_api.services.agent_progress import (
    REDACTED,
    bounded_redacted_text,
    redact_sensitive_text,
    sanitize_agent_step,
    sanitize_agent_steps,
)


@pytest.mark.parametrize("placement", ["flat", "trace", "both"])
def test_progress_preserves_known_upstream_422_instead_of_generic_gateway_failure(placement):
    facts = {
        "error_code": "model_unavailable", "error_origin": "provider",
        "http_status": 502, "upstream_http_status": 422,
        "provider_charge_ambiguous": True, "retry_safe": False, "will_retry": False,
    }
    step = {"kind": "provider_error", "ok": False, "step": 26,
            "action": "PRIVATE_PROVIDER_BODY", "detail": "PRIVATE_PROVIDER_BODY"}
    if placement in {"flat", "both"}:
        step.update(facts)
    if placement in {"trace", "both"}:
        step["recovery_trace"] = dict(facts)
    safe = sanitize_agent_step(step)
    assert safe["action"] == "Провайдер отклонил запрос"
    assert "HTTP 422" in safe["detail"]
    assert "502" not in safe["detail"]
    evidence = safe if placement == "flat" else safe["recovery_trace"]
    assert all(evidence[key] == value for key, value in facts.items())
    assert "PRIVATE_PROVIDER_BODY" not in json.dumps(safe)


@pytest.mark.parametrize("change", [
    {"error_origin": "gateway"}, {"upstream_http_status": 502},
    {"upstream_http_status": "422"}, {"upstream_http_status": True},
    {"upstream_http_status": None},
])
def test_progress_does_not_infer_provider_422_from_gateway_or_malformed_facts(change):
    safe = sanitize_agent_step({
        "kind": "provider_error", "ok": False, "error_origin": "provider",
        "http_status": 422, "upstream_http_status": 422, **change,
        "detail": "PRIVATE_PROVIDER_BODY HTTP422",
    })
    assert "HTTP 422" not in safe["detail"]
    assert "PRIVATE_PROVIDER_BODY" not in json.dumps(safe)


def test_progress_contradictory_or_unattributed_422_does_not_gain_specific_summary():
    for step in [
        {"error_origin": "provider", "upstream_http_status": 422,
         "recovery_trace": {"error_origin": "provider", "upstream_http_status": 503}},
        {"upstream_http_status": 422},
        {"error_origin": "provider", "http_status": 422},
    ]:
        safe = sanitize_agent_step({"kind": "provider_error", "ok": False, **step})
        assert "HTTP 422" not in safe["detail"]


def test_progress_existing_refusal_category_remains_specific():
    safe = sanitize_agent_step({
        "kind": "provider_error", "ok": False, "error_origin": "provider",
        "upstream_http_status": 422, "provider_refusal_category": "funds",
    })
    assert safe["detail"] == "Провайдер сообщил о недостатке средств."


@pytest.mark.parametrize("facts", [
    {"upstream_http_status": 422},
    {"upstream_http_status": 422, "error_origin": None},
    {"upstream_http_status": 422, "error_origin": "browser"},
    {"upstream_http_status": 422, "error_origin": "provider",
     "recovery_trace": {"upstream_http_status": 422, "error_origin": "gateway"}},
    {"upstream_http_status": 422, "error_origin": "provider",
     "recovery_trace": {"upstream_http_status": 503, "error_origin": "provider"}},
    {"upstream_http_status": 422, "error_origin": "provider",
     "recovery_trace": {"upstream_http_status": "422", "error_origin": "provider"}},
])
def test_unattributed_or_conflicting_422_cannot_gain_attribution_at_public_schema(facts):
    step = {"kind": "provider_error", "ok": False, **facts,
            "provider_charge_ambiguous": True, "retry_safe": False,
            "detail": "PRIVATE_PROVIDER_BODY"}
    first = sanitize_agent_step(step)
    second = sanitize_agent_step(first)
    public = MessagePublic.model_validate({
        "id": UUID("11111111-1111-4111-8111-111111111111"),
        "project_id": UUID("22222222-2222-4222-8222-222222222222"),
        "role": "assistant", "content": "", "created_at": datetime.now(UTC),
        "agent_steps": [first],
    })
    assert public.agent_steps is not None
    assert first == second == public.agent_steps[0]
    assert "HTTP 422" not in first["detail"]
    assert first["error_origin"] == "unknown"
    assert first["upstream_http_status"] == 422
    assert first["provider_charge_ambiguous"] is True
    assert first["retry_safe"] is False
    assert "PRIVATE_PROVIDER_BODY" not in json.dumps(public.model_dump(), default=str)


def test_explicit_422_public_schema_round_trip_preserves_known_status_and_evidence():
    facts = {"upstream_http_status": 422, "error_origin": "provider",
             "http_status": 502, "provider_charge_ambiguous": True,
             "retry_safe": False, "will_retry": False}
    first = sanitize_agent_step({"kind": "provider_error", "ok": False,
                                 **facts, "recovery_trace": dict(facts)})
    public = MessagePublic.model_validate({
        "id": UUID("11111111-1111-4111-8111-111111111111"),
        "project_id": UUID("22222222-2222-4222-8222-222222222222"),
        "role": "assistant", "content": "", "created_at": datetime.now(UTC),
        "agent_steps": [first],
    })
    assert public.agent_steps == [first] == [sanitize_agent_step(first)]
    assert "HTTP 422" in first["detail"]
    assert all(first[key] == value for key, value in facts.items())
    assert first["recovery_trace"] == facts


def test_legacy_unattributed_error_without_upstream_status_keeps_default_origin():
    first = sanitize_agent_step({"kind": "provider_error", "ok": False})
    assert first == sanitize_agent_step(first)
    assert first["error_origin"] == "provider"
    assert "HTTP 422" not in first["detail"]


def test_redacts_shell_assignments_dsn_and_tokens() -> None:
    source = "\n".join(
        [
            "export AUTH_SECRET='auth-value'",
            "DATABASE_URL=postgresql://app:db-password@postgres:5432/app",
            "MINIO_ACCESS_KEY=access-value",
            "Authorization: Bearer bearer-value",
            "token=sk-exampleexampleexampleexample",
        ]
    )

    result = redact_sensitive_text(source)

    assert "auth-value" not in result
    assert "db-password" not in result
    assert "access-value" not in result
    assert "bearer-value" not in result
    assert "sk-example" not in result
    assert result.count(REDACTED) >= 5


def test_public_transcript_sanitizer_preserves_safe_shape() -> None:
    rows = sanitize_agent_steps(
        [
            {
                "step": 1,
                "kind": "step",
                "action": "Выполняю команду",
                "tool": "bash",
                "path": "",
                "detail": "AUTH_SECRET=do-not-show\nbuild clean",
                "ok": True,
            }
        ]
    )

    assert rows is not None
    assert rows[0]["step"] == 1
    assert rows[0]["ok"] is True
    assert rows[0]["detail"] == f"AUTH_SECRET={REDACTED}\nbuild clean"


def test_bounded_redaction_catches_token_crossing_storage_boundary() -> None:
    result = bounded_redacted_text("A" * 4090 + "ghp_" + "B" * 24, max_bytes=4096)

    assert "ghp_" not in result
    assert REDACTED in result
