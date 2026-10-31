from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from yleum_api.services import orchestrator_client
from yleum_api.services.max_finalization import MaxFinalizationCoordinator


def unavailable(reason="stopped"):
    return orchestrator_client.OrchestratorMachineInventoryUnavailable(
        reason,
        "a" * 64,
        "b" * 64,
    )


def coordinator(events):
    wid, run = uuid4(), uuid4()
    identity = SimpleNamespace(workspace_id=wid, generation_run_id=run)
    result = SimpleNamespace(outcome="green")
    instance = object.__new__(MaxFinalizationCoordinator)
    instance.generation_run_id = run
    instance.executor = SimpleNamespace(
        workspace_id=wid,
        prove_restoration_adaptation=None,
        current_identity=AsyncMock(side_effect=[unavailable(), identity]),
        prepare_environment=AsyncMock(),
    )

    async def reconciled():
        events.append("reconciled")

    async def failures_checked():
        events.append("failures_checked")

    async def prepare(revision, manifest):
        assert events == ["reconciled", "failures_checked"]
        assert (revision, manifest) == ("a" * 64, "b" * 64)
        events.append("prepared")

    instance.executor.prepare_environment.side_effect = prepare
    instance._reconcile_tool_commands = reconciled
    instance._raise_persisted_infrastructure_failure = failures_checked
    instance._proof = AsyncMock(return_value=SimpleNamespace())
    instance._find = AsyncMock(return_value=None)
    instance._run_bootstrap = AsyncMock(return_value=(result, identity, SimpleNamespace()))
    instance._run_role_result = AsyncMock(return_value=result)
    return instance, identity


async def test_normal_fast_check_prepares_after_reconciliation_and_refreshes_identity():
    events = []
    instance, _identity = coordinator(events)
    assert (await instance.fast_check()).outcome == "green"
    assert events == ["reconciled", "failures_checked", "prepared"]
    assert instance.executor.current_identity.await_count == 2
    instance._run_bootstrap.assert_awaited_once()
    instance._run_role_result.assert_awaited_once()


async def test_second_unavailable_identity_is_fatal_not_a_prepare_loop():
    instance, _identity = coordinator([])
    instance.executor.current_identity.side_effect = [unavailable(), unavailable()]
    with pytest.raises(orchestrator_client.OrchestratorMachineInventoryUnavailable):
        await instance.fast_check()
    assert instance.executor.prepare_environment.await_count == 1
    instance._proof.assert_not_awaited()
    instance._run_bootstrap.assert_not_awaited()


async def test_pending_reconciliation_failure_prevents_startup():
    instance, _identity = coordinator([])
    instance._reconcile_tool_commands = AsyncMock(side_effect=RuntimeError("pending operation"))
    with pytest.raises(RuntimeError, match="pending"):
        await instance.fast_check()
    instance.executor.current_identity.assert_not_awaited()
    instance.executor.prepare_environment.assert_not_awaited()


async def test_persisted_failure_prevents_prepare_even_with_stopped_inventory():
    instance, _identity = coordinator([])
    instance._raise_persisted_infrastructure_failure = AsyncMock(side_effect=RuntimeError("hold"))
    with pytest.raises(RuntimeError, match="hold"):
        await instance.fast_check()
    instance.executor.current_identity.assert_not_awaited()
    instance.executor.prepare_environment.assert_not_awaited()


@pytest.mark.parametrize("reason", ["unavailable", "paused"])
async def test_unclassified_inventory_cannot_authorize_normal_start(reason):
    instance, _identity = coordinator([])
    instance.executor.current_identity.side_effect = unavailable(reason)
    with pytest.raises(orchestrator_client.OrchestratorMachineInventoryUnavailable):
        await instance.fast_check()
    instance.executor.prepare_environment.assert_not_awaited()


async def test_adaptation_does_not_enter_normal_startup_recovery():
    instance, _identity = coordinator([])
    instance.executor.prove_restoration_adaptation = object()
    instance._adaptation_fast_check = AsyncMock(return_value="adaptation")
    assert await instance.fast_check() == "adaptation"
    instance.executor.current_identity.assert_not_awaited()
    instance.executor.prepare_environment.assert_not_awaited()


async def test_identity_client_only_classifies_exact_safe_stopped_envelope(monkeypatch):
    async def rejected(*_args, **_kwargs):
        raise orchestrator_client.OrchestratorBadRequest(
            "inventory unavailable",
            status_code=409,
            upstream_code="container_not_running",
            details={
                "reason": "stopped",
                "workspace_revision": "a" * 64,
                "manifest_digest": "b" * 64,
            },
        )

    monkeypatch.setattr(orchestrator_client, "_request", rejected)
    with pytest.raises(orchestrator_client.OrchestratorMachineInventoryUnavailable) as caught:
        await orchestrator_client.project_cell_agent_identity(
            uuid4(),
            generation_run_id=uuid4(),
            fencing_epoch=7,
        )
    assert caught.value.reason == "stopped"
    assert caught.value.workspace_revision == "a" * 64


@pytest.mark.parametrize("fault", [None, "operation", "revision", "manifest", "extra"])
async def test_explicit_prepare_client_binds_response_to_owned_intent(monkeypatch, fault):
    wid, project, owner, run = (uuid4() for _ in range(4))
    expected_operation = uuid5(run, "machine-startup-prepare/v1:7")
    identity = {
        "workspace_revision": "a" * 64,
        "dependency_digest": "c" * 64,
        "schema_data_digest": "d" * 64,
        "cell_manifest_digest": "b" * 64,
        "environment_digest": "e" * 64,
        "build_config_digest": "f" * 64,
    }
    response = {
        "operation_id": str(expected_operation),
        "binding_sha256": "1" * 64,
        "startup_incarnation": "2" * 64,
        "identity": identity,
    }
    if fault == "operation":
        response["operation_id"] = str(uuid4())
    elif fault == "revision":
        identity["workspace_revision"] = "9" * 64
    elif fault == "manifest":
        identity["cell_manifest_digest"] = "9" * 64
    elif fault == "extra":
        response["unexpected"] = "private sentinel must never be accepted"

    async def request(method, path, **kwargs):
        assert method == "POST"
        assert path == f"/internal/workspaces/{wid}/agent/environment/prepare"
        assert kwargs["json"] == {
            "project_id": str(project),
            "owner_id": str(owner),
            "generation_run_id": str(run),
            "fencing_epoch": 7,
            "expected_revision": "a" * 64,
            "manifest_digest": "b" * 64,
        }
        return response

    monkeypatch.setattr(orchestrator_client, "_request", request)
    call = orchestrator_client.project_cell_prepare_environment(
        wid,
        project_id=project,
        owner_id=owner,
        generation_run_id=run,
        fencing_epoch=7,
        expected_revision="a" * 64,
        manifest_digest="b" * 64,
    )
    if fault:
        with pytest.raises(orchestrator_client.OrchestratorUnavailable):
            await call
    else:
        assert (await call).environment_digest == "e" * 64
