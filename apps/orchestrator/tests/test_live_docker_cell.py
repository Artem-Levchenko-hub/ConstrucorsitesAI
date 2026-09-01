from __future__ import annotations

import asyncio
import hashlib
import os
from types import SimpleNamespace
from uuid import uuid4

import docker  # type: ignore[import-untyped]
import pytest

from omnia_orchestrator.core.cell_resources import LifecycleMutation
from omnia_orchestrator.core.workspace_provider import ControlAction, WorkspaceSpec
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from omnia_orchestrator.services.workspace_provider_factory import build_workspace_provider

pytestmark = pytest.mark.skipif(
    os.environ.get("OMNIA_CELL_LIVE_DOCKER") != "1",
    reason="set OMNIA_CELL_LIVE_DOCKER=1 to exercise a disposable real Docker cell",
)


def _mutation(epoch: int, kind: str) -> LifecycleMutation:
    return LifecycleMutation(
        operation_id=uuid4(),
        fencing_epoch=epoch,
        request_digest=hashlib.sha256(f"live-docker:{epoch}:{kind}".encode()).hexdigest(),
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"{name} is required for the live Docker cell test")
    return value


def _settings(state_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_provider="docker_owner_canary",
        docker_owner_canary_enabled=True,
        docker_host=_required_env("OMNIA_CELL_DOCKER_HOST"),
        cell_profile_version="docker-owner-cell-live-v1",
        cell_postgres_image=_required_env("OMNIA_CELL_POSTGRES_IMAGE"),
        cell_redis_image=_required_env("OMNIA_CELL_REDIS_IMAGE"),
        cell_backup_image=_required_env("OMNIA_CELL_BACKUP_IMAGE"),
        cell_max_active_bundles=1,
        cell_bundle_cpu_cores=1.0,
        cell_bundle_memory_bytes=1024**3,
        cell_host_cpu_reserve_cores=0.0,
        cell_host_memory_reserve_bytes=0,
        cell_required_free_disk_bytes=1024**2,
        cell_host_disk_reserve_bytes=0,
        cell_required_free_inodes=10,
        cell_host_inode_reserve=0,
        cell_state_path=state_path,
    )


def _assert_exec_ok(container: object, command: list[str], password: str) -> bytes:
    __tracebackhide__ = True
    result = container.exec_run(  # type: ignore[attr-defined]
        command,
        environment={"PGPASSWORD": password},
        demux=False,
    )
    assert result.exit_code == 0, bytes(result.output).decode("utf-8", "ignore")
    return bytes(result.output)


async def _wait_for_postgres(container: object, password: str) -> None:
    __tracebackhide__ = True
    for _ in range(60):
        try:
            output = _assert_exec_ok(
                container,
                ["psql", "-U", "postgres", "-d", "postgres", "-tAc", "select 1"],
                password,
            )
            if output.strip() == b"1":
                return
        except (AssertionError, docker.errors.APIError):
            pass
        await asyncio.sleep(0.25)
        container.reload()  # type: ignore[attr-defined]
    container.reload()  # type: ignore[attr-defined]
    detail = bytes(container.logs(tail=100)).decode("utf-8", "ignore")  # type: ignore[attr-defined]
    pytest.fail(
        "project-cell postgres did not become queryable: "
        f"status={container.status!r} logs={detail}"  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_live_digest_pinned_cell_checkpoint_restore_and_cleanup(tmp_path) -> None:
    workspace_id = uuid4()
    project_id = uuid4()
    owner_id = uuid4()
    generation_run_id = uuid4()
    provider = build_workspace_provider(_settings(str(tmp_path / "project-cells.json")))
    assert isinstance(provider, DockerOwnerCanaryProvider)
    assert provider.resource_manager is not None
    manager = provider.resource_manager
    docker_client = docker.DockerClient(base_url=_required_env("OMNIA_CELL_DOCKER_HOST"))

    try:
        handle = await provider.ensure(
            WorkspaceSpec(
                workspace_id=workspace_id,
                project_id=project_id,
                owner_id=owner_id,
                profile_version="docker-owner-cell-live-v1",
                generation_run_id=generation_run_id,
            ),
            _mutation(1, "ensure"),
        )
        state = manager.state_store.load(workspace_id)
        assert state is not None
        assert state.active_generation_run_id == generation_run_id
        assert state.active_generation_fencing_epoch == 1
        names = state.resource_names
        assert names is not None
        postgres = docker_client.containers.get(names.postgres_container)
        redis = docker_client.containers.get(names.redis_container)
        password = manager.credential_store.load_or_create(workspace_id).postgres_password
        await _wait_for_postgres(postgres, password)
        redis.reload()
        assert redis.status == "running", bytes(redis.logs(tail=50)).decode("utf-8", "ignore")
        assert handle.provider == "docker_owner_canary"
        observed = await provider.inspect_resources(workspace_id)
        raw_observation = await manager._observe_state(state)
        assert observed.state == "resources_ready", raw_observation.detail

        await manager.docker.write_volume_files(
            names.workspace_volume,
            {"repo/.git/HEAD": b"ref: refs/heads/main\n", "repo/proof.txt": b"accepted"},
        )
        _assert_exec_ok(
            postgres,
            [
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "create table live_proof(value text not null); "
                "insert into live_proof(value) values ('accepted');",
            ],
            password,
        )

        checkpoint_ref = "live-before-mutation"
        await provider.execute_control(
            workspace_id,
            ControlAction(kind="pause", checkpoint_ref=checkpoint_ref),
            _mutation(2, "pause"),
        )
        await provider.wake(workspace_id, _mutation(3, "wake"))
        postgres = docker_client.containers.get(names.postgres_container)
        await _wait_for_postgres(postgres, password)
        _assert_exec_ok(
            postgres,
            [
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "update live_proof set value='mutated';",
            ],
            password,
        )

        await provider.execute_control(
            workspace_id,
            ControlAction(kind="restore", checkpoint_ref=checkpoint_ref),
            _mutation(4, "restore"),
        )
        await provider.wake(workspace_id, _mutation(5, "wake-restored"))
        postgres = docker_client.containers.get(names.postgres_container)
        await _wait_for_postgres(postgres, password)
        restored = _assert_exec_ok(
            postgres,
            [
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-tAc",
                "select value from live_proof",
            ],
            password,
        )
        assert restored.strip() == b"accepted"
        workspace_files = await manager.docker.read_volume_files(names.workspace_volume)
        assert workspace_files["repo/proof.txt"] == b"accepted"
        inventory = await manager.inventory_for_workspace(workspace_id)
        assert inventory.helper_container_ids == ()
        assert inventory.secret_staging_volume_ids == ()
        assert inventory.persistent_container_env_secret_matches == ()
        assert len(inventory.retained_volume_names) == 5
    finally:
        label = f"omnia.workspace_id={workspace_id}"
        for container in docker_client.containers.list(all=True, filters={"label": label}):
            container.remove(force=True)
        for network in docker_client.networks.list(filters={"label": label}):
            network.remove()
        for volume in docker_client.volumes.list(filters={"label": label}):
            volume.remove(force=True)
        docker_client.close()
