"""An unverified rollback retains its actual cause without leaking credentials."""

from types import SimpleNamespace

import pytest

from yleum_api.services.generation import agent_recovery

from .test_generation_stopped_recovery import stopped_recovery as stopped_recovery


def first_or_existing(kwargs, baseline, first):
    if first:
        baseline.pop("src/app/page.tsx")
        kwargs["_max_has_generated_snapshot"] = False
    kwargs["_agent_res"].stop_reason = "source_deadline"


@pytest.mark.parametrize("first", [False, True])
async def test_red_restore_keeps_migration_cause_without_claiming_success(stopped_recovery, first):
    kwargs, _, baseline, trace, _ = stopped_recovery
    first_or_existing(kwargs, baseline, first)
    detail = "project migration verification failed: application receipt missing"

    async def restore(role, operation_id):
        return SimpleNamespace(ok=False, redacted_detail=detail)

    kwargs["runtime"].handle.run_role = restore
    with pytest.raises(RuntimeError, match=r"source_deadline.*rollback_unverified") as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert detail in str(error.value)
    assert str(error.value.__cause__) == detail
    assert "rollback_event" not in trace


@pytest.mark.parametrize("first", [False, True])
async def test_restore_exception_is_redacted_in_error_chain_and_log(
    stopped_recovery, capsys, first,
):
    kwargs, _, baseline, _, _ = stopped_recovery
    first_or_existing(kwargs, baseline, first)

    async def restore(role, operation_id):
        raise RuntimeError("migration controller unavailable\nLLMGW_API_KEY=private-test-value")

    kwargs["runtime"].handle.run_role = restore
    with pytest.raises(RuntimeError) as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    output = capsys.readouterr()
    surfaces = [str(error.value), str(error.value.__cause__), output.out, output.err]
    assert all("private-test-value" not in surface for surface in surfaces)
    assert "migration controller unavailable" in str(error.value)
    assert "[REDACTED]" in str(error.value)
    assert "rollback_unverified" in str(error.value)


@pytest.mark.parametrize("first", [False, True])
async def test_red_restore_diagnostic_is_bounded_but_preserves_final_cause(stopped_recovery, first):
    kwargs, _, baseline, _, _ = stopped_recovery
    first_or_existing(kwargs, baseline, first)
    detail = "migration verification failed\n" + "x" * 8000 + "\nSQLSTATE 42P01"

    async def restore(role, operation_id):
        return SimpleNamespace(ok=False, redacted_detail=detail)

    kwargs["runtime"].handle.run_role = restore
    with pytest.raises(RuntimeError) as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert "migration verification failed" in str(error.value)
    assert str(error.value).endswith("SQLSTATE 42P01")
    assert len(str(error.value).encode()) < 4000


@pytest.mark.parametrize("unverified", [1, "ready"])
async def test_truthy_non_boolean_receipt_cannot_verify_rollback(stopped_recovery, unverified):
    kwargs, _, _, trace, _ = stopped_recovery

    async def restore(role, operation_id):
        return SimpleNamespace(ok=unverified, redacted_detail="missing boolean verification")

    kwargs["runtime"].handle.run_role = restore
    with pytest.raises(RuntimeError, match="rollback_unverified") as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert "missing boolean verification" in str(error.value)
    assert "rollback_event" not in trace


async def test_provider_cause_keeps_precedence_when_restore_is_red(stopped_recovery):
    kwargs, _, _, _, _ = stopped_recovery
    kwargs["_provider_failure"] = "provider_http_401: access rejected"

    async def restore(role, operation_id):
        return SimpleNamespace(ok=False, redacted_detail="restoration was not verified")

    kwargs["runtime"].handle.run_role = restore
    with pytest.raises(RuntimeError) as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert str(error.value) == "provider_http_401: access rejected"


@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("raises", [False, True])
async def test_stopped_and_rollback_causes_share_credential_sanitization(
    stopped_recovery, capsys, first, raises,
):
    kwargs, _, baseline, trace, _ = stopped_recovery
    first_or_existing(kwargs, baseline, first)
    kwargs["_agent_res"].summary = (
        "stopped prefix API_KEY=private-stopped-secret "
        "https://owner:private-stopped-uri@primary.example/source?x=private-stopped-query"
    )
    detail = (
        "rollback prefix SECRET_KEY=private-rollback-secret "
        "https://owner:private-rollback-uri@proxy.example/rollback"
        "?password=private-rollback-query#private-fragment"
    )
    async def restore(role, operation_id):
        if raises:
            raise RuntimeError(detail)
        return SimpleNamespace(ok=False, redacted_detail=detail)
    kwargs["runtime"].handle.run_role = restore
    with pytest.raises(RuntimeError, match="rollback_unverified") as raised:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    output = capsys.readouterr()
    surfaces = [str(raised.value), str(raised.value.__cause__), output.out, output.err]
    assert all("private-" not in value for value in surfaces)
    assert "stopped prefix" in str(raised.value) and "rollback prefix" in str(raised.value)
    assert "https://primary.example/source" in str(raised.value)
    assert "https://proxy.example/rollback" in str(raised.value)
    assert "rollback_event" not in trace
