from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellResourceNames,
    CellResourceProfile,
    CellRestoreFailed,
    LifecycleMutation,
)
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.services.cell_admission import CellAdmissionGate, DockerHostCapacityReader
from omnia_orchestrator.services.cell_checkpoint import CellCheckpointManager
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.cell_state import CellCredentialStore, CellStateStore
from omnia_orchestrator.services.docker_cell_resources import (
    DockerCellResourceManager,
    DockerContainerRecord,
    DockerContainerSpec,
)
from tests._cell_fakes import FakeDockerBackend


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


class FailingPreRestoreSnapshotBackend(FakeDockerBackend):
    def __init__(self) -> None:
        super().__init__()
        self._dump_calls = 0

    async def postgres_dump(self, container_name: str, password: str) -> bytes:
        self._dump_calls += 1
        if self._dump_calls == 2:
            raise RuntimeError("pre-restore snapshot failed")
        return await super().postgres_dump(container_name, password)


class ReplayGuardBackend(FakeDockerBackend):
    def __init__(self) -> None:
        super().__init__()
        self.fail_on_postgres_dump = False
        self.fail_on_create_container = False
        self.postgres_dump_calls = 0

    async def postgres_dump(self, container_name: str, password: str) -> bytes:
        self.postgres_dump_calls += 1
        if self.fail_on_postgres_dump:
            raise AssertionError("unexpected postgres_dump")
        return await super().postgres_dump(container_name, password)

    async def create_container(self, spec: DockerContainerSpec) -> DockerContainerRecord:
        if self.fail_on_create_container:
            raise AssertionError("unexpected create_container")
        return await super().create_container(spec)


def _make_fixture(
    tmp_path: Path,
    docker: FakeDockerBackend | None = None,
) -> tuple[DockerCellResourceManager, CellCheckpointManager, FakeDockerBackend]:
    docker = docker or FakeDockerBackend()
    state_path = tmp_path / "runtime-state" / "project-cells.json"
    daemon_root = tmp_path / "daemon-root"
    daemon_root.mkdir(parents=True, exist_ok=True)
    docker.docker_root_dir = str(daemon_root)
    profile = _profile(state_path)
    credential_store = CellCredentialStore(tmp_path / "runtime-state" / "credentials")
    state_store = CellStateStore(state_path)
    manager = DockerCellResourceManager(
        profile=profile,
        docker=docker,
        admission_gate=CellAdmissionGate(profile),
        capacity_reader=DockerHostCapacityReader(
            docker=docker,
            state_path=str(state_path),
            statvfs=lambda _path: SimpleNamespace(f_bavail=200, f_frsize=1024**3, f_favail=10**7),
            meminfo_reader=lambda: 64 * 1024**3,
            loadavg_reader=lambda: (1.0, 0.0, 0.0),
            cpu_count_reader=lambda: 8,
            active_bundle_counter=lambda: 0,
        ),
        credential_store=credential_store,
        state_store=state_store,
        operation_lock=WorkspaceOperationLock(
            tmp_path / "runtime-state", acquire_timeout_seconds=1, retry_interval_seconds=0.01
        ),
        namespace="test",
    )
    checkpoints = CellCheckpointManager(
        profile_version=profile.profile_version,
        postgres_image=profile.postgres_image,
        docker=docker,
        credential_store=credential_store,
        state_store=state_store,
    )
    return manager, checkpoints, docker


def _names(manager: DockerCellResourceManager, workspace_id: UUID) -> CellResourceNames:
    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.resource_names is not None
    return state.resource_names


def test_archive_round_trip_preserves_leading_dot_paths() -> None:
    payload = {
        ".env": b"secretless",
        ".git/HEAD": b"ref: refs/heads/main\n",
        "./nested/.keep": b"",
    }

    archived = CellCheckpointManager.__module__
    _ = archived
    from omnia_orchestrator.services.cell_checkpoint import _archive_bytes, _extract_archive

    restored = _extract_archive(_archive_bytes(payload))

    assert restored == {
        ".env": b"secretless",
        ".git/HEAD": b"ref: refs/heads/main\n",
        "nested/.keep": b"",
    }


@pytest.mark.asyncio
async def test_checkpoint_is_private_atomic_and_secret_free(tmp_path: Path) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )

    manifest = await checkpoints.create(workspace_id, "before-migration-1", _mutation("b", 2))

    assert set(manifest.artifacts) == {"workspace.tar", "agent-home.tar", "postgres.dump"}
    assert manifest.redis_policy == "clear_on_restore"
    assert helper.finalized_paths[-1] == "before-migration-1"
    assert helper.remaining_tmp_paths == []
    assert "password" not in manifest.model_dump_json().casefold()


@pytest.mark.asyncio
async def test_checkpoint_rejects_same_operation_with_different_ref_before_capture(
    tmp_path: Path,
) -> None:
    backend = ReplayGuardBackend()
    manager, checkpoints, helper = _make_fixture(tmp_path, docker=backend)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    checkpoint_mutation = _mutation("b", 2)
    await checkpoints.create(workspace_id, "accepted-1", checkpoint_mutation)
    backend.fail_on_postgres_dump = True

    with pytest.raises(CellFenceRejected, match="replay envelope mismatch"):
        await checkpoints.create(workspace_id, "accepted-2", checkpoint_mutation)

    checkpoint_files = await helper.read_volume_files(names.checkpoint_volume)
    assert "accepted-2/manifest.json" not in checkpoint_files
    assert helper.finalized_paths == ["accepted-1"]


@pytest.mark.asyncio
async def test_restore_rejects_same_operation_with_different_digest_before_side_effects(
    tmp_path: Path,
) -> None:
    backend = ReplayGuardBackend()
    manager, checkpoints, helper = _make_fixture(tmp_path, docker=backend)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    await manager.pause_services(workspace_id, _mutation("c", 3))
    restore_mutation = _mutation("d", 4)
    await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)
    finalized_before = list(helper.finalized_paths)
    backend.fail_on_create_container = True

    with pytest.raises(CellFenceRejected, match="replay envelope mismatch"):
        await checkpoints.restore(
            workspace_id,
            "accepted-1",
            LifecycleMutation(
                restore_mutation.operation_id,
                restore_mutation.fencing_epoch,
                "e" * 64,
            ),
        )

    assert helper.finalized_paths == finalized_before


@pytest.mark.asyncio
async def test_restore_requires_paused_bundle_before_any_mutation(tmp_path: Path) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(names.redis_volume, {"cache.txt": b"warm"})

    with pytest.raises(CellRestoreFailed, match="paused"):
        await checkpoints.restore(workspace_id, "accepted-1", _mutation("c", 3))

    assert helper.finalized_paths == ["accepted-1"]
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"draft"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"draft-home"
    assert (await helper.read_volume_files(names.redis_volume))["cache.txt"] == b"warm"


@pytest.mark.asyncio
async def test_restore_rejects_manifest_identity_mismatch_before_mutation(
    tmp_path: Path,
) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    await manager.pause_services(workspace_id, _mutation("c", 3))
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(names.redis_volume, {"cache.txt": b"warm"})
    checkpoint_files = await helper.read_volume_files(names.checkpoint_volume)
    manifest = json.loads(checkpoint_files["accepted-1/manifest.json"].decode("utf-8"))
    manifest["project_id"] = str(UUID("00000000-0000-0000-0000-000000000099"))
    await helper.write_volume_files(
        names.checkpoint_volume,
        {"accepted-1/manifest.json": json.dumps(manifest).encode("utf-8")},
    )

    with pytest.raises(CellRestoreFailed, match="project mismatch"):
        await checkpoints.restore(workspace_id, "accepted-1", _mutation("d", 4))

    assert helper.finalized_paths == ["accepted-1"]
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"draft"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"draft-home"
    assert (await helper.read_volume_files(names.redis_volume))["cache.txt"] == b"warm"


@pytest.mark.asyncio
async def test_restore_rejects_artifact_hash_mismatch_before_mutation(
    tmp_path: Path,
) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    pause_mutation = _mutation("c", 3)
    await manager.pause_services(workspace_id, pause_mutation)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(names.redis_volume, {"cache.txt": b"warm"})
    await helper.write_volume_files(
        names.checkpoint_volume,
        {"accepted-1/workspace.tar": b"corrupted-archive"},
    )
    restore_mutation = _mutation("d", 4)

    with pytest.raises(CellRestoreFailed, match="hash mismatch"):
        await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    with pytest.raises(CellRestoreFailed, match="hash mismatch"):
        await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.bundle_state == "resources_paused"
    assert state.phase == "completed"
    assert state.last_operation_id == pause_mutation.operation_id
    assert state.operation(restore_mutation.operation_id) is None
    assert helper.finalized_paths == ["accepted-1"]
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"draft"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"draft-home"
    assert (await helper.read_volume_files(names.redis_volume))["cache.txt"] == b"warm"


@pytest.mark.asyncio
async def test_restore_rejects_missing_artifact_before_mutation(
    tmp_path: Path,
) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    pause_mutation = _mutation("c", 3)
    await manager.pause_services(workspace_id, pause_mutation)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(names.redis_volume, {"cache.txt": b"warm"})
    await helper.delete_volume_paths(names.checkpoint_volume, ("accepted-1/workspace.tar",))
    restore_mutation = _mutation("d", 4)

    with pytest.raises(CellRestoreFailed, match=r"checkpoint artifact missing: workspace\.tar"):
        await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.bundle_state == "resources_paused"
    assert state.phase == "completed"
    assert state.last_operation_id == pause_mutation.operation_id
    assert state.operation(restore_mutation.operation_id) is None
    assert helper.finalized_paths == ["accepted-1"]
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"draft"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"draft-home"
    assert (await helper.read_volume_files(names.redis_volume))["cache.txt"] == b"warm"


@pytest.mark.asyncio
async def test_restore_pre_snapshot_failure_keeps_paused_state_and_journal_intact(
    tmp_path: Path,
) -> None:
    backend = FailingPreRestoreSnapshotBackend()
    manager, checkpoints, helper = _make_fixture(tmp_path, docker=backend)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    pause_mutation = _mutation("c", 3)
    await manager.pause_services(workspace_id, pause_mutation)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["draft"]).encode("utf-8")}
    )
    await helper.write_volume_files(names.redis_volume, {"cache.txt": b"warm"})
    restore_mutation = _mutation("d", 4)

    with pytest.raises(CellRestoreFailed, match="pre-restore snapshot failed"):
        await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.bundle_state == "resources_paused"
    assert state.phase == "failed"
    assert state.last_operation_id == restore_mutation.operation_id
    operation = state.operation(state.last_operation_id)
    assert operation is not None
    assert operation.kind == "restore"
    assert operation.status == "failed"
    assert operation.detail == "pre-restore snapshot failed"
    helper_name = names.helper_container_name("postgres-maintenance", restore_mutation.operation_id)
    assert helper_name not in helper.containers
    assert helper.finalized_paths == ["accepted-1"]
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"draft"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"draft-home"
    assert (await helper.read_volume_files(names.redis_volume))["cache.txt"] == b"warm"
    postgres_files = await helper.read_volume_files(names.postgres_volume)
    assert json.loads(postgres_files["db.json"].decode("utf-8")) == ["draft"]


@pytest.mark.asyncio
async def test_failed_restore_rolls_back_pre_restore_state(tmp_path: Path) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))

    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["draft"]).encode("utf-8")}
    )
    await manager.pause_services(workspace_id, _mutation("c", 3))
    helper.fail_after_workspace_extract = True
    restore_mutation = _mutation("d", 4)

    with pytest.raises(CellRestoreFailed, match="restore failure injected"):
        await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    assert "pre-restore-" in helper.finalized_paths[-1]
    assert helper.rollback_completed is True
    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.bundle_state == "resources_paused"
    assert state.phase == "failed"
    assert state.last_operation_id == restore_mutation.operation_id
    operation = state.operation(restore_mutation.operation_id)
    assert operation is not None
    assert operation.status == "failed"
    assert operation.detail == "restore failure injected"
    workspace_files = await helper.read_volume_files(names.workspace_volume)
    agent_home_files = await helper.read_volume_files(names.agent_home_volume)
    postgres_files = await helper.read_volume_files(names.postgres_volume)
    assert workspace_files["proof.txt"] == b"draft"
    assert agent_home_files["state.txt"] == b"draft-home"
    assert json.loads(postgres_files["db.json"].decode("utf-8")) == ["draft"]
    finalized_before = list(helper.finalized_paths)

    with pytest.raises(CellRestoreFailed, match="restore failure injected"):
        await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    assert helper.finalized_paths == finalized_before
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"draft"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"draft-home"
    replay_postgres_files = await helper.read_volume_files(names.postgres_volume)
    assert json.loads(replay_postgres_files["db.json"].decode("utf-8")) == ["draft"]


@pytest.mark.asyncio
async def test_restore_uses_single_maintenance_postgres_helper_and_cleans_it_up(
    tmp_path: Path,
) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"draft"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"draft-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["draft"]).encode("utf-8")}
    )
    await manager.pause_services(workspace_id, _mutation("c", 3))
    restore_mutation = _mutation("d", 4)

    await checkpoints.restore(workspace_id, "accepted-1", restore_mutation)

    helper_name = names.helper_container_name("postgres-maintenance", restore_mutation.operation_id)
    assert helper_name not in helper.containers
    assert helper.containers[names.postgres_container].state == "exited"
    assert any(
        record.labels.get("omnia.resource_kind") == "postgres-maintenance"
        and record.state == "running"
        for record in helper.container_history
    )
    assert (await helper.read_volume_files(names.workspace_volume))["proof.txt"] == b"accepted"
    assert (
        await helper.read_volume_files(names.agent_home_volume)
    )["state.txt"] == b"accepted-home"


@pytest.mark.asyncio
async def test_restore_rollback_failure_marks_bundle_degraded(tmp_path: Path) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(names.workspace_volume, {"proof.txt": b"accepted"})
    await helper.write_volume_files(names.agent_home_volume, {"state.txt": b"accepted-home"})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    await manager.pause_services(workspace_id, _mutation("c", 3))
    helper.fail_after_workspace_extract = True
    helper.postgres_smoke_fail_on_calls = {1}

    with pytest.raises(CellRestoreFailed, match="postgres smoke query failed"):
        await checkpoints.restore(workspace_id, "accepted-1", _mutation("d", 4))

    state = manager.state_store.load(workspace_id)
    assert state is not None
    assert state.bundle_state == "degraded"
    assert state.phase == "completed"


@pytest.mark.asyncio
async def test_checkpoint_restore_preserves_zero_byte_files(tmp_path: Path) -> None:
    manager, checkpoints, helper = _make_fixture(tmp_path)
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    await manager.ensure(spec, _mutation("a", 1))
    names = _names(manager, workspace_id)
    await helper.write_volume_files(
        names.workspace_volume,
        {"empty.txt": b"", "full.txt": b"accepted"},
    )
    await helper.write_volume_files(names.agent_home_volume, {"blank.txt": b""})
    await helper.write_volume_files(
        names.postgres_volume, {"db.json": json.dumps(["accepted"]).encode("utf-8")}
    )
    await checkpoints.create(workspace_id, "accepted-1", _mutation("b", 2))
    await manager.pause_services(workspace_id, _mutation("c", 3))
    await helper.delete_volume_paths(names.workspace_volume, ("empty.txt", "full.txt"))
    await helper.delete_volume_paths(names.agent_home_volume, ("blank.txt",))
    await helper.write_volume_files(
        names.workspace_volume,
        {"full.txt": b"draft", "extra.txt": b"stale"},
    )
    await helper.write_volume_files(names.agent_home_volume, {"other.txt": b"stale"})

    await checkpoints.restore(workspace_id, "accepted-1", _mutation("d", 4))

    workspace_files = await helper.read_volume_files(names.workspace_volume)
    agent_home_files = await helper.read_volume_files(names.agent_home_volume)
    assert workspace_files["empty.txt"] == b""
    assert workspace_files["full.txt"] == b"accepted"
    assert "extra.txt" not in workspace_files
    assert agent_home_files["blank.txt"] == b""
    assert "other.txt" not in agent_home_files
