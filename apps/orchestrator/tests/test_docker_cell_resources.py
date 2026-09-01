from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellIdentityConflict,
    CellIndeterminateOperation,
    CellResourceError,
    CellResourceNames,
    CellResourceProfile,
    LifecycleMutation,
    identity_labels,
)
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.services.cell_admission import CellAdmissionGate, DockerHostCapacityReader
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.cell_state import CellCredentialStore, CellStateStore
from omnia_orchestrator.services.docker_cell_resources import (
    DockerCellResourceManager,
    DockerContainerRecord,
    DockerContainerSpec,
    DockerNetworkRecord,
)
from tests._cell_fakes import FakeDockerBackend, SimulatedProcessCrash


def _profile(state_path: Path) -> CellResourceProfile:
    return CellResourceProfile(
        profile_version="docker-owner-cell-resources-v1",
        postgres_image="postgres@sha256:" + "1" * 64,
        redis_image="redis@sha256:" + "2" * 64,
        backup_image="backup@sha256:" + "3" * 64,
        max_active_bundles=1,
        bundle_cpu_cores=2.0,
        bundle_memory_bytes=4 * 1024**3,
        host_cpu_reserve_cores=2.0,
        host_memory_reserve_bytes=4 * 1024**3,
        required_free_disk_bytes=20 * 1024**3,
        host_disk_reserve_bytes=10 * 1024**3,
        required_free_inodes=100_000,
        host_inode_reserve=50_000,
        state_path=str(state_path),
    )


def _spec(workspace_id: UUID) -> WorkspaceSpec:
    return WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
    )


def _mutation(seed: str, fence: int) -> LifecycleMutation:
    return LifecycleMutation(uuid4(), fence, seed * 64)


def _statvfs_factory(docker_root: str, state_root: str) -> Callable[[str], SimpleNamespace]:
    values = {
        Path(docker_root): SimpleNamespace(f_bavail=200, f_frsize=1024**3, f_favail=10**7),
        Path(state_root): SimpleNamespace(f_bavail=150, f_frsize=1024**3, f_favail=10**6),
    }

    def _reader(path: str) -> SimpleNamespace:
        return values[Path(path)]

    return _reader


def _make_manager(
    tmp_path: Path, docker: FakeDockerBackend | None = None
) -> tuple[DockerCellResourceManager, FakeDockerBackend, CellStateStore, WorkspaceOperationLock]:
    docker_backend = docker or FakeDockerBackend()
    state_path = tmp_path / "runtime-state" / "project-cells.json"
    daemon_root = tmp_path / "daemon-root"
    daemon_root.mkdir(parents=True, exist_ok=True)
    docker_backend.docker_root_dir = str(daemon_root)
    profile = _profile(state_path)
    state_store = CellStateStore(state_path)
    lock = WorkspaceOperationLock(
        tmp_path / "runtime-state", acquire_timeout_seconds=1, retry_interval_seconds=0.01
    )
    manager = DockerCellResourceManager(
        profile=profile,
        docker=docker_backend,
        admission_gate=CellAdmissionGate(profile),
        capacity_reader=DockerHostCapacityReader(
            docker=docker_backend,
            state_path=str(state_path),
            statvfs=_statvfs_factory(str(daemon_root), str(state_path.parent)),
            meminfo_reader=lambda: 64 * 1024**3,
            loadavg_reader=lambda: (1.0, 0.0, 0.0),
            cpu_count_reader=lambda: 8,
            active_bundle_counter=lambda: 0,
        ),
        credential_store=CellCredentialStore(tmp_path / "runtime-state" / "credentials"),
        state_store=state_store,
        operation_lock=lock,
        namespace="test",
        draft_port_registry_path=str(tmp_path / "runtime-state" / ".cell-port-registry.json"),
    )
    return manager, docker_backend, state_store, lock


@pytest.mark.asyncio
async def test_ensure_creates_exact_private_bundle(tmp_path: Path) -> None:
    manager, docker, _, _ = _make_manager(tmp_path)
    spec = _spec(UUID("00000000-0000-0000-0000-000000000001"))
    mutation = _mutation("a", 1)

    handle = await manager.ensure(spec, mutation)
    inventory = await manager.inventory_for_workspace(spec.workspace_id)

    assert set(inventory.retained_volume_names) == set(handle.resource_names.retained_volumes)
    assert inventory.helper_container_ids == ()
    assert inventory.secret_staging_volume_ids == ()
    assert docker.networks[handle.resource_names.internal_network].internal is True
    assert docker.networks[handle.resource_names.egress_network].internal is True
    assert docker.containers[handle.resource_names.postgres_container].ports == {}
    assert docker.containers[handle.resource_names.redis_container].ports == {}


@pytest.mark.asyncio
async def test_draft_runtime_follows_pause_wake_destroy_lifecycle(tmp_path: Path) -> None:
    manager, docker, _state_store, _lock = _make_manager(tmp_path)
    spec = _spec(UUID("00000000-0000-0000-0000-000000000101"))
    await manager.ensure(spec, _mutation("a", 1))

    created = await manager.ensure_draft_runtime(spec.workspace_id)
    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    registry_path = tmp_path / "runtime-state" / ".cell-port-registry.json"

    assert created.state == "running"
    assert docker.containers[names.draft_container_name()].ports == {"3000/tcp": "127.0.0.1:3200"}

    await manager.pause_services(
        spec.workspace_id,
        _mutation("b", 2),
        checkpoint_ref="accepted-1",
    )
    assert docker.containers[names.draft_container_name()].state == "exited"

    await manager.wake(spec.workspace_id, _mutation("c", 3))
    assert docker.containers[names.draft_container_name()].state == "running"

    await manager.destroy_compute(spec.workspace_id, _mutation("d", 4))
    assert names.draft_container_name() not in docker.containers
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {}


@pytest.mark.asyncio
async def test_corrupt_draft_port_registry_fails_closed(tmp_path: Path) -> None:
    manager, _docker, _state_store, _lock = _make_manager(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000102")
    registry_path = tmp_path / "runtime-state" / ".cell-port-registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(CellResourceError, match="draft port registry is invalid"):
        await manager.acquire_draft_preview_port(workspace_id)

    assert registry_path.read_text(encoding="utf-8") == "{broken"


@pytest.mark.asyncio
async def test_draft_port_registry_save_is_atomic(tmp_path: Path) -> None:
    manager, _docker, _state_store, _lock = _make_manager(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000103")
    registry_path = tmp_path / "runtime-state" / ".cell-port-registry.json"

    assert await manager.acquire_draft_preview_port(workspace_id) == 3200
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        str(workspace_id): 3200
    }
    assert list(registry_path.parent.glob(f".{registry_path.name}.*.tmp")) == []


@pytest.mark.asyncio
async def test_same_name_wrong_labels_is_never_adopted_or_removed(tmp_path: Path) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    mutation = _mutation("a", 1)
    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    docker.seed_volume(names.workspace_volume, {"omnia.workspace_id": "different"})

    with pytest.raises(CellIdentityConflict):
        await manager.ensure(spec, mutation)

    assert docker.removed_resources == []
    assert state_store.load(spec.workspace_id) is None
    assert docker.begin_operation_calls == 0


@pytest.mark.asyncio
async def test_profile_version_mismatch_rejected_before_mutation(tmp_path: Path) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = WorkspaceSpec(
        workspace_id=uuid4(),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="other-profile-v1",
    )

    with pytest.raises(CellIdentityConflict, match="profile version mismatch"):
        await manager.ensure(spec, _mutation("a", 1))

    assert state_store.load(spec.workspace_id) is None
    assert docker.begin_operation_calls == 0


@pytest.mark.asyncio
async def test_existing_named_network_internal_mismatch_rejected_before_journal(
    tmp_path: Path,
) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    docker.networks[names.internal_network] = DockerNetworkRecord(
        resource_id="seed-network-1",
        name=names.internal_network,
        labels=identity_labels(spec, "internal"),
        internal=False,
    )

    with pytest.raises(CellIdentityConflict, match="resource identity mismatch"):
        await manager.ensure(spec, _mutation("a", 1))

    assert state_store.load(spec.workspace_id) is None
    assert docker.begin_operation_calls == 0


@pytest.mark.asyncio
async def test_existing_named_container_spec_mismatch_rejected_before_journal(
    tmp_path: Path,
) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    docker.seed_volume(names.postgres_volume, identity_labels(spec, "postgres"))
    docker.containers[names.postgres_container] = DockerContainerRecord(
        resource_id="seed-container-1",
        name=names.postgres_container,
        image="postgres:16",
        labels=identity_labels(spec, "postgres"),
        user="postgres",
        cap_add=[],
        cap_drop=["ALL"],
        read_only=True,
        privileged=False,
        security_opt=["no-new-privileges:true"],
        ports={},
        env={},
        volumes=(names.postgres_volume,),
        mounts=(),
        network_names=(names.internal_network,),
        state="running",
        helper=False,
        memory_limit_bytes=manager.profile.bundle_memory_bytes // 2,
        cpu_quota=max(manager.profile.bundle_cpu_cores / 2.0, 0.5),
    )

    with pytest.raises(CellIdentityConflict, match="resource identity mismatch"):
        await manager.ensure(spec, _mutation("a", 1))

    assert state_store.load(spec.workspace_id) is None
    assert docker.begin_operation_calls == 0


@pytest.mark.asyncio
async def test_existing_workspace_identity_mismatch_does_not_mutate_durable_state(
    tmp_path: Path,
) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    original_state = state_store.load(workspace_id)
    assert original_state is not None
    removed_before = list(docker.removed_resources)
    mismatched_spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000099"),
        owner_id=spec.owner_id,
        profile_version=spec.profile_version,
    )

    with pytest.raises(CellIdentityConflict, match="immutable identity mismatch"):
        await manager.ensure(mismatched_spec, _mutation("b", 2))

    restored = state_store.load(workspace_id)
    assert restored == original_state
    assert docker.removed_resources == removed_before


@pytest.mark.asyncio
async def test_empty_postgres_volume_bootstraps_with_removed_one_shot_helper(
    tmp_path: Path,
) -> None:
    manager, docker, _, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    mutation = _mutation("a", 1)

    await manager.ensure(spec, mutation)

    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    helper = docker.last_container("postgres-init")
    assert helper.labels["omnia.resource_kind"] == "postgres-init"
    assert helper.ports == {}
    assert helper.user == "postgres"
    ownership = docker.last_container("postgres-ownership")
    assert ownership.user == "0:0"
    assert ownership.cap_add == ["CHOWN"]
    steady = docker.containers[names.postgres_container]
    assert steady.user == "postgres"
    assert steady.cap_drop == ["ALL"]
    assert steady.read_only is True
    postgres_files = await docker.read_volume_files(names.postgres_volume)
    assert postgres_files["PGDATA/PG_VERSION"] == b"16\n"
    assert "PGDATA/postgres-password.txt" not in postgres_files


@pytest.mark.asyncio
async def test_bootstrap_cancellation_cleans_secret_staging_and_keeps_pgdata_secret_free(
    tmp_path: Path,
) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    mutation = _mutation("a", 1)
    docker.block_on_phase = "container_create"

    task = asyncio.create_task(manager.ensure(spec, mutation))
    await docker.wait_until_blocked()

    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    blocked_inventory = await manager.inventory_for_workspace(spec.workspace_id)
    blocked_postgres_files = await docker.read_volume_files(names.postgres_volume)

    assert len(blocked_inventory.secret_staging_volume_ids) == 1
    assert blocked_postgres_files == {}
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    inventory = await manager.inventory_for_workspace(spec.workspace_id)
    record = state_store.load(spec.workspace_id)

    assert inventory.secret_staging_volume_ids == ()
    assert inventory.helper_container_ids == ()
    assert "PGDATA/postgres-password.txt" not in await docker.read_volume_files(
        names.postgres_volume
    )
    assert record is not None
    assert record.phase == "indeterminate"


@pytest.mark.asyncio
async def test_crash_after_network_create_is_indeterminate_and_not_replayed(
    tmp_path: Path,
) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    mutation = _mutation("a", 1)
    docker.crash_after = "network_create"

    with pytest.raises(SimulatedProcessCrash):
        await manager.ensure(spec, mutation)

    record = state_store.load(spec.workspace_id)
    assert record is not None
    assert record.phase == "indeterminate"
    with pytest.raises(CellIndeterminateOperation):
        await manager.ensure(spec, mutation)


@pytest.mark.asyncio
async def test_two_managers_serialize_before_docker(tmp_path: Path) -> None:
    spec = _spec(uuid4())
    first, first_docker, _, _ = _make_manager(tmp_path / "first")
    second, second_docker, _, _ = _make_manager(tmp_path / "first")

    results = await asyncio.gather(
        first.ensure(spec, _mutation("a", 8)),
        second.ensure(spec, _mutation("b", 8)),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, CellFenceRejected) for item in results) == 1
    assert first_docker.begin_operation_calls + second_docker.begin_operation_calls == 1


@pytest.mark.asyncio
async def test_cancellation_releases_lock_but_keeps_journal(tmp_path: Path) -> None:
    manager, docker, state_store, lock = _make_manager(tmp_path)
    spec = _spec(uuid4())
    mutation = _mutation("a", 1)
    docker.block_next_side_effect = True

    task = asyncio.create_task(manager.ensure(spec, mutation))
    await docker.wait_until_blocked()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with lock.hold(spec.workspace_id):
        pass
    record = state_store.load(spec.workspace_id)
    assert record is not None
    assert record.phase == "indeterminate"


@pytest.mark.asyncio
async def test_reconcile_records_completed_operation_on_healthy_bundle(tmp_path: Path) -> None:
    manager, _, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    mutation = _mutation("b", 2)
    await manager.ensure(spec, _mutation("a", 1))

    observation = await manager.reconcile(spec.workspace_id, mutation)

    state = state_store.load(spec.workspace_id)
    assert observation.state == "resources_ready"
    assert state is not None
    assert state.last_operation_id == mutation.operation_id
    operation = state.operation(mutation.operation_id)
    assert operation is not None
    assert operation.kind == "reconcile"
    assert operation.status == "completed"
    assert operation.phase == "completed"


@pytest.mark.asyncio
async def test_reconcile_cleanup_removes_only_expected_ephemera(tmp_path: Path) -> None:
    manager, docker, _, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    handle = await manager.ensure(spec, _mutation("a", 1))
    valid_secret = handle.resource_names.secret_staging_volume_name(uuid4(), "postgres-init")
    valid_helper = handle.resource_names.helper_container_name("postgres-init", uuid4())
    docker.seed_volume(
        valid_secret,
        identity_labels(spec, "secret-staging"),
        files={"postgres-password.txt": b"staged"},
    )
    docker.seed_volume(
        "foreign-secret-volume",
        {
            **identity_labels(spec, "secret-staging"),
            "omnia.project_id": str(UUID("00000000-0000-0000-0000-000000000099")),
        },
        files={"postgres-password.txt": b"keep"},
    )
    await docker.create_container(
        DockerContainerSpec(
            name=valid_helper,
            image=manager.profile.postgres_image,
            labels={**identity_labels(spec, "postgres-init"), "omnia.helper": "true"},
            user="postgres",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=(),
            mounts=(),
            network_names=(),
            helper=True,
        )
    )
    await docker.create_container(
        DockerContainerSpec(
            name="foreign-helper",
            image=manager.profile.postgres_image,
            labels={**identity_labels(spec, "postgres-init"), "omnia.helper": "true"},
            user="postgres",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=(),
            mounts=(),
            network_names=(),
            helper=True,
        )
    )

    observation = await manager.reconcile(spec.workspace_id, _mutation("b", 2))

    assert observation.state == "resources_ready"
    assert valid_secret not in docker.volumes
    assert valid_helper not in docker.containers
    assert "foreign-secret-volume" in docker.volumes
    assert "foreign-helper" in docker.containers


@pytest.mark.asyncio
async def test_reconcile_marks_missing_ready_compute_degraded_and_removes_leaks(
    tmp_path: Path,
) -> None:
    manager, docker, state_store, _ = _make_manager(tmp_path)
    spec = _spec(uuid4())
    handle = await manager.ensure(spec, _mutation("a", 1))
    docker.seed_volume(
        handle.resource_names.secret_staging_volume_name(uuid4(), "postgres-init"),
        identity_labels(spec, "secret-staging"),
        files={"postgres-password.txt": b"staged"},
    )
    await docker.create_container(
        DockerContainerSpec(
            name=handle.resource_names.helper_container_name("postgres-init", uuid4()),
            image=manager.profile.postgres_image,
            labels={
                "omnia.managed": "true",
                "omnia.project_cell": "true",
                "omnia.workspace_id": str(spec.workspace_id),
                "omnia.project_id": str(spec.project_id),
                "omnia.owner_id": str(spec.owner_id),
                "omnia.provider": "docker_owner_canary",
                "omnia.profile_version": spec.profile_version,
                "omnia.resource_kind": "postgres-init",
            },
            user="postgres",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=(),
            mounts=(),
            network_names=(),
            helper=True,
        )
    )
    await docker.remove_network(handle.resource_names.egress_network)

    observation = await manager.reconcile(spec.workspace_id, _mutation("b", 2))
    state = state_store.load(spec.workspace_id)
    inventory = await manager.inventory_for_workspace(spec.workspace_id)

    assert observation.state == "degraded"
    assert state is not None
    assert state.bundle_state == "degraded"
    assert state.last_operation_id is not None
    operation = state.operation(state.last_operation_id)
    assert operation is not None
    assert operation.kind == "reconcile"
    assert operation.bundle_state == "degraded"
    assert inventory.secret_staging_volume_ids == ()
    assert docker.removed_resources
