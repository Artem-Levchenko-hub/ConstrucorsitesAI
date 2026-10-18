from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_docker_cell_resources import _make_manager, _mutation, _spec
from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.services.machine_adapter import MachineAdapter
from yleum_orchestrator.services.machine_environment import MachineEnvironmentRef


@pytest.mark.parametrize("active", [False, True])
async def test_reconcile_observes_lease_under_current_fence_without_acquiring(tmp_path, active):
    from dataclasses import replace

    manager, _docker, store, _lock = _make_manager(tmp_path)
    run = uuid4()
    spec = replace(_spec(uuid4()), generation_run_id=run)
    await manager.ensure(spec, _mutation("a", 1))
    if not active:
        await manager.release_generation(
            spec.workspace_id, _mutation("b", 2), generation_run_id=run
        )
    probe = AsyncMock(return_value=True)
    manager.machine_runtime = SimpleNamespace(lease_quiescent=probe)
    mutation = _mutation("c", 3)
    result = await manager.reconcile(spec.workspace_id, mutation)
    proof = result.lease_observation
    assert proof is not None and proof.operation_id == mutation.operation_id
    assert proof.owner_id == spec.owner_id and proof.project_id == spec.project_id
    assert proof.fencing_epoch == 3 and proof.request_digest == mutation.request_digest
    assert proof.active_generation_run_id == (run if active else None)
    assert proof.quiescent is (not active)
    assert store.load(spec.workspace_id).active_generation_run_id == (run if active else None)
    assert probe.await_count == (0 if active else 1)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "active",
        "pending",
        "missing_reference",
        "retained_unknown",
        "helper",
        "running_exec",
        "unknown_exec",
        "new_exec",
        "missing_execs",
        "malformed_execs",
        "foreign_base",
    ],
)
async def test_idle_physical_proof_denies_unknown_writers(tmp_path, fault):
    runtime = MachineAdapter(
        SimpleNamespace(_state_labels=lambda *_: {"owner": "fixture"}), SimpleNamespace()
    )
    workspace = uuid4()
    state = SimpleNamespace(
        workspace_id=workspace,
        active_generation_run_id=(uuid4() if fault == "active" else None),
        resource_names=SimpleNamespace(postgres_container="pg", redis_container="redis"),
    )
    manifest = MachineManifest.model_validate(payload())
    reference = MachineEnvironmentRef(
        workspace_id=workspace,
        image_id="sha256:" + "a" * 64,
        artifact_ref="b" * 32 + ".tar",
        sha256="c" * 64,
        size=1,
        base_image="base",
        manifest_digest=manifest.digest(),
        volumes=(),
        manifest=manifest,
    )
    saved = {"operations": {}}
    if fault == "pending":
        saved["operations"][str(uuid4())] = {"kind": "command", "state": "starting"}
    metadata = {"epoch": 1, "environment_ref": reference.model_dump(mode="json")}
    if fault == "missing_reference":
        metadata.pop("environment_ref")
    container = SimpleNamespace(
        id="p",
        name="helper" if fault == "helper" else "pg",
        attrs={
            "Config": {"Labels": {"owner": "foreign" if fault == "foreign_base" else "fixture"}},
            "ExecIDs": ["e"] if fault in {"running_exec", "unknown_exec"} else [],
        },
        reload=lambda: None,
    )
    if fault == "missing_execs":
        container.attrs.pop("ExecIDs")
    elif fault == "malformed_execs":
        container.attrs["ExecIDs"] = False
    calls = []

    def containers(**_):
        calls.append(True)
        if fault == "new_exec" and len(calls) == 2:
            container.attrs["ExecIDs"] = ["new"]
        return [container]

    def execution(_):
        return {
            "ContainerID": "p",
            "Running": fault == "running_exec",
            "ExitCode": None if fault in {"unknown_exec", "new_exec"} else 0,
        }

    backend = SimpleNamespace(
        _metadata=lambda: metadata,
        _retained_preview_proof=lambda *_, **__: (
            None if fault == "retained_unknown" else {"trusted": {}}
        ),
        client=SimpleNamespace(
            containers=SimpleNamespace(list=containers), api=SimpleNamespace(exec_inspect=execution)
        ),
    )
    runtime.exists = lambda _: True
    runtime.parts = lambda _, **__: (SimpleNamespace(state=lambda: saved), backend)
    if fault in {
        "helper",
        "running_exec",
        "unknown_exec",
        "new_exec",
        "missing_execs",
        "malformed_execs",
        "foreign_base",
    }:
        with pytest.raises(CellIdentityConflict):
            await runtime.lease_quiescent(state)
    else:
        assert await runtime.lease_quiescent(state) is (fault is None)
