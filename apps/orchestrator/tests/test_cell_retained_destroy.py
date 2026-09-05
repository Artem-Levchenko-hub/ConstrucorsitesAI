from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, LifecycleMutation
from omnia_orchestrator.core.workspace_provider import ControlAction
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from tests.test_cell_checkpoint import _make_fixture, _spec


async def paused_cell(tmp_path, *, portable=False):
    manager, checkpoints, docker = _make_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    spec = _spec(uuid4())
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))
    if portable:
        runtime = SimpleNamespace(
            exists=lambda _: True,
            checkpoint_payload=AsyncMock(return_value=b'{"reference":"portable-with-database"}'),
            validate_restore_payload=AsyncMock(),
            halt=AsyncMock(),
        )
        manager.machine_runtime = runtime
        checkpoints.machine_runtime = runtime
    await provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="pause", checkpoint_ref="accepted-final"),
        LifecycleMutation(uuid4(), 2, "b" * 64),
    )
    docker.postgres_dump = AsyncMock(side_effect=AssertionError("must not dump stopped DB"))
    docker.start_container = AsyncMock(side_effect=AssertionError("must not wake"))
    return provider, manager, checkpoints, docker, spec


@pytest.mark.parametrize("invalid_archive", [False, True])
async def test_portable_retained_cleanup_validates_database_archive_before_no_capture_halt(
    tmp_path,
    invalid_archive,
):
    provider, manager, _, docker, spec = await paused_cell(tmp_path, portable=True)
    runtime = manager.machine_runtime
    runtime.halt.reset_mock()
    if invalid_archive:
        runtime.validate_restore_payload.side_effect = RuntimeError(
            "portable database archive missing"
        )
        with pytest.raises(RuntimeError, match="database archive missing"):
            await provider.execute_control(
                spec.workspace_id,
                ControlAction(kind="destroy"),
                LifecycleMutation(uuid4(), 3, "c" * 64),
            )
        runtime.halt.assert_not_awaited()
        assert docker.containers
    else:
        await provider.execute_control(
            spec.workspace_id,
            ControlAction(kind="destroy"),
            LifecycleMutation(uuid4(), 3, "c" * 64),
        )
        assert (
            runtime.validate_restore_payload.await_args.args[1]
            == b'{"reference":"portable-with-database"}'
        )
        assert runtime.halt.await_args.kwargs == {"remove_network": True, "capture": False}
        assert not docker.containers


async def test_paused_destroy_reuses_verified_archive_and_removes_compute_without_wake(tmp_path):
    provider, manager, _, docker, spec = await paused_cell(tmp_path)
    volumes_before = set(docker.volumes)
    mutation = LifecycleMutation(uuid4(), 3, "c" * 64)
    result = await provider.execute_control(
        spec.workspace_id, ControlAction(kind="destroy"), mutation
    )
    assert result.state == "retained"
    assert result.checkpoint_ref == f"final-3-{mutation.operation_id.hex}"
    assert not docker.containers
    assert not docker.networks
    assert set(docker.volumes) == volumes_before
    assert manager._capacity_reservation_store().load(spec.workspace_id) is None
    assert (
        await provider.execute_control(spec.workspace_id, ControlAction(kind="destroy"), mutation)
        == result
    )


@pytest.mark.parametrize("failed_resource", ["container", "network"])
@pytest.mark.parametrize("control_reconcile", [False, True])
async def test_deleted_paused_cell_recovers_transient_teardown_without_wake(
    tmp_path, monkeypatch, failed_resource, control_reconcile
):
    provider, manager, _, docker, spec = await paused_cell(tmp_path)
    volumes_before = set(docker.volumes)
    remove = getattr(docker, f"remove_{failed_resource}")

    async def unavailable(*args, **kwargs):
        raise RuntimeError("temporary Docker transport failure")

    monkeypatch.setattr(docker, f"remove_{failed_resource}", unavailable)
    with pytest.raises(RuntimeError, match="temporary Docker"):
        await provider.execute_control(
            spec.workspace_id,
            ControlAction(kind="destroy"),
            LifecycleMutation(uuid4(), 3, "c" * 64),
        )
    assert manager.state_store.load(spec.workspace_id).phase == "indeterminate"
    monkeypatch.setattr(docker, f"remove_{failed_resource}", remove)
    reconciliation = LifecycleMutation(uuid4(), 4, "d" * 64)
    if control_reconcile:
        await provider.execute_control(
            spec.workspace_id, ControlAction(kind="reconcile"), reconciliation
        )
    else:
        await provider.observe_resources(spec.workspace_id, reconciliation)
    result = await provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="destroy"),
        LifecycleMutation(uuid4(), 5, "e" * 64),
    )
    assert result.state == "retained"
    assert manager.state_store.load(spec.workspace_id).phase == "completed"
    assert manager._capacity_reservation_store().load(spec.workspace_id) is None
    assert not docker.containers
    assert not docker.networks
    assert set(docker.volumes) == volumes_before
    for action in (
        ControlAction(kind="wake"),
        ControlAction(kind="restore", checkpoint_ref="accepted-final"),
    ):
        with pytest.raises(RuntimeError, match="delet"):
            await provider.execute_control(
                spec.workspace_id, action, LifecycleMutation(uuid4(), 6, "f" * 64)
            )


async def test_paused_destroy_rejects_corrupt_checkpoint_before_removing_compute(tmp_path):
    provider, manager, _, docker, spec = await paused_cell(tmp_path)
    state = manager.state_store.load(spec.workspace_id)
    await docker.write_volume_files(
        state.resource_names.checkpoint_volume, {"accepted-final/postgres.dump": b"corrupt"}
    )
    before = set(docker.containers)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        await provider.execute_control(
            spec.workspace_id,
            ControlAction(kind="destroy"),
            LifecycleMutation(uuid4(), 3, "c" * 64),
        )
    assert set(docker.containers) == before


@pytest.mark.parametrize("failure_after_pg", ["redis", "network"])
@pytest.mark.parametrize("corrupt_seal", [False, True])
async def test_ready_partial_delete_resumes_verified_final_seal_without_missing_pg_dump(
    tmp_path, monkeypatch, failure_after_pg, corrupt_seal
):
    manager, checkpoints, docker = _make_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    spec = _spec(uuid4())
    await provider.ensure(spec, LifecycleMutation(uuid4(), 1, "a" * 64))
    state = manager.state_store.load(spec.workspace_id)
    names = state.resource_names
    volumes_before = set(docker.volumes)
    original_dump = docker.postgres_dump

    async def dump_existing(name, password):
        if name not in docker.containers:
            raise RuntimeError("Docker container not found: managed postgres")
        return await original_dump(name, password)

    monkeypatch.setattr(docker, "postgres_dump", dump_existing)
    method = "remove_container" if failure_after_pg == "redis" else "remove_network"
    remove = getattr(docker, method)

    async def interrupted(name):
        if failure_after_pg == "network" or name == names.redis_container:
            raise RuntimeError("temporary Docker transport failure after PG removal")
        return await remove(name)

    monkeypatch.setattr(docker, method, interrupted)
    deletion = LifecycleMutation(uuid4(), 2, "b" * 64)
    with pytest.raises(RuntimeError, match="after PG removal"):
        await provider.execute_control(spec.workspace_id, ControlAction(kind="destroy"), deletion)
    assert names.postgres_container not in docker.containers
    assert manager.state_store.load(spec.workspace_id).phase == "indeterminate"
    monkeypatch.setattr(docker, method, remove)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    await provider.observe_resources(spec.workspace_id, LifecycleMutation(uuid4(), 3, "c" * 64))
    if corrupt_seal:
        await docker.write_volume_files(
            names.checkpoint_volume,
            {f"final-2-{deletion.operation_id.hex}/postgres.dump": b"corrupt"},
        )
    before_retry = set(docker.containers)
    retry = provider.execute_control(
        spec.workspace_id,
        ControlAction(kind="destroy"),
        LifecycleMutation(uuid4(), 4, "d" * 64),
    )
    if corrupt_seal:
        with pytest.raises(RuntimeError, match="hash mismatch"):
            await retry
        assert set(docker.containers) == before_retry
    else:
        assert (await retry).state == "retained"
        assert not docker.containers
        assert not docker.networks
        assert manager._capacity_reservation_store().load(spec.workspace_id) is None
    assert set(docker.volumes) == volumes_before


async def test_paused_destroy_refuses_running_compute_even_when_state_says_paused(tmp_path):
    provider, _, _, docker, spec = await paused_cell(tmp_path)
    name = next(iter(docker.containers))
    docker.containers[name] = replace(docker.containers[name], state="running")
    before = set(docker.containers)
    with pytest.raises(RuntimeError, match="running"):
        await provider.execute_control(
            spec.workspace_id,
            ControlAction(kind="destroy"),
            LifecycleMutation(uuid4(), 3, "c" * 64),
        )
    assert set(docker.containers) == before


async def test_paused_portable_destroy_requires_machine_database_envelope(tmp_path):
    provider, manager, checkpoints, _docker, spec = await paused_cell(tmp_path)
    runtime = SimpleNamespace(
        exists=lambda _: True, validate_restore_payload=AsyncMock(), halt=AsyncMock()
    )
    manager.machine_runtime = runtime
    checkpoints.machine_runtime = runtime
    with pytest.raises(RuntimeError, match="portable"):
        await provider.execute_control(
            spec.workspace_id,
            ControlAction(kind="destroy"),
            LifecycleMutation(uuid4(), 3, "c" * 64),
        )
    runtime.halt.assert_not_awaited()


async def test_absent_cell_destroy_proves_absence_without_creating_state(tmp_path):
    manager, checkpoints, docker = _make_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    workspace_id = uuid4()
    result = await provider.execute_control(
        workspace_id, ControlAction(kind="destroy"), LifecycleMutation(uuid4(), 1, "a" * 64)
    )
    assert result.state == "retained"
    assert result.has_workspace is False
    assert manager.state_store.load(workspace_id) is None
    assert not docker.containers
    with pytest.raises(RuntimeError, match="delet"):
        await provider.ensure(_spec(workspace_id), LifecycleMutation(uuid4(), 2, "b" * 64))


async def test_destroy_tombstone_blocks_restart_and_agent_volume_mutations(tmp_path):
    from omnia_orchestrator.core.errors import OrchestratorError
    from omnia_orchestrator.routers.workspace import _workspace_volume_identity

    provider, manager, _, _, spec = await paused_cell(tmp_path)
    mutation = LifecycleMutation(uuid4(), 3, "c" * 64)
    await provider.execute_control(spec.workspace_id, ControlAction(kind="destroy"), mutation)
    # Even a fresh provider/direct manager call cannot resurrect deleted identity.
    for operation in (
        provider.execute_control(
            spec.workspace_id, ControlAction(kind="wake"), LifecycleMutation(uuid4(), 4, "d" * 64)
        ),
        manager.ensure(spec, LifecycleMutation(uuid4(), 4, "d" * 64)),
        _workspace_volume_identity(manager, spec.workspace_id),
    ):
        with pytest.raises((RuntimeError, OrchestratorError), match="delet"):
            await operation
    assert (await provider.inspect_resources(spec.workspace_id)).state == "retained"


async def test_absent_cell_destroy_refuses_existing_unlabelled_named_volume(tmp_path):
    from omnia_orchestrator.core.cell_resources import CellResourceNames

    manager, checkpoints, docker = _make_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    workspace_id = uuid4()
    names = CellResourceNames.for_workspace(workspace_id)
    await docker.create_volume(names.workspace_volume, {})
    with pytest.raises(CellIdentityConflict):
        await provider.execute_control(
            workspace_id, ControlAction(kind="destroy"), LifecycleMutation(uuid4(), 1, "a" * 64)
        )
    assert names.workspace_volume in docker.volumes
