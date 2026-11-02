"""Owned full-build restarts retain content and bind distinct startup processes."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from yleum_orchestrator.services.machine_startup import EnvironmentIdentity


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def owned_binding():
    return {
        "workspace_id": str(uuid4()),
        "project_id": str(uuid4()),
        "owner_id": str(uuid4()),
        "generation_run_id": str(uuid4()),
        "fencing_epoch": 3,
        "operation_id": str(uuid4()),
        "source_revision": "1" * 64,
        "manifest_digest": "2" * 64,
    }


@pytest.mark.parametrize("count", [0, 1])
def test_owned_build_transition_binds_receipts_without_normalizing_process_identity(count):
    before = EnvironmentIdentity("a" * 64, "b" * 64)
    after = EnvironmentIdentity("a" * 64, "c" * 64)
    compilation = {"collector_version": "next-fixed-data-v1", "assets": ["fixed fixture"]}
    migration = {"contract": "project-migrations-v1", "mode": "apply", "migration_count": count}
    binding = owned_binding()
    receipt = before.full_build_transition(
        after,
        binding=binding,
        compilation=compilation,
        migration=migration,
    )
    assert before.digest != after.digest
    assert receipt == dict(
        protocol="full-build-startup-transition/v1",
        version=1,
        role="full_build",
        content_digest="a" * 64,
        before_incarnation="b" * 64,
        after_incarnation="c" * 64,
        compilation_sha256=digest(compilation),
        migration_sha256=digest(migration),
        **binding,
    )
    assert "full_build_startup_transition" not in compilation
    assert migration["migration_count"] == count


def test_changed_content_or_unchanged_process_does_not_mint_a_build_transition():
    before = EnvironmentIdentity("a" * 64, "b" * 64)
    for after in (before, EnvironmentIdentity("c" * 64, "d" * 64)):
        assert before.full_build_transition(after, binding={}, compilation={}, migration={}) is None


def test_full_build_and_restore_protocols_are_distinct():
    before, after = EnvironmentIdentity("a" * 64, "b" * 64), EnvironmentIdentity("a" * 64, "c" * 64)
    assert before.restoration_transition(after)["protocol"] == "restored-environment-transition/v1"
    assert (
        before.full_build_transition(after, binding=owned_binding(), compilation={}, migration={})[
            "protocol"
        ]
        == "full-build-startup-transition/v1"
    )


@pytest.mark.parametrize("mutation", ["foreign", "bool_epoch", "extra", "nan"])
def test_unbounded_or_malformed_producer_input_withholds_transition(mutation):
    before, after = EnvironmentIdentity("a" * 64, "b" * 64), EnvironmentIdentity("a" * 64, "c" * 64)
    binding, compilation = owned_binding(), {}
    if mutation == "foreign":
        binding["owner_id"] = "not-a-uuid"
    elif mutation == "bool_epoch":
        binding["fencing_epoch"] = True
    elif mutation == "extra":
        binding["protocol"] = "forged"
    else:
        compilation["unbounded"] = float("nan")
    assert (
        before.full_build_transition(after, binding=binding, compilation=compilation, migration={})
        is None
    )


def test_real_migration_prepare_controlled_docker_restart_changes_only_process(
    tmp_path, monkeypatch
):
    pytest.importorskip(
        "fcntl", reason="Real backend imports POSIX controller locks; Linux CI required"
    )
    from types import SimpleNamespace

    from yleum_orchestrator.services import docker_machine_backend as backend_module

    machine = SimpleNamespace(
        status="running",
        labels={"omnia.fencing_epoch": "3"},
        incarnation="b" * 64,
        attrs={"Image": "sha256:" + "d" * 64},
    )
    calls = []
    machine.reload = lambda: None

    def stop(**_kwargs):
        calls.append("stop")
        machine.status = "exited"

    def start():
        calls.append("start")
        machine.status = "running"
        machine.incarnation = "c" * 64

    machine.stop, machine.start = stop, start
    machine.exec_run = lambda *_args, **_kwargs: (0, b'[{"package":"unchanged"}]')
    backend = object.__new__(backend_module.DockerMachineBackend)
    backend.root, backend.workspace_id, backend.base_image = tmp_path, uuid4(), "fixed-base"
    folder = tmp_path / str(backend.workspace_id)
    folder.mkdir()
    metadata = {"epoch": 3, "services": {}, "exec_logs": {}, "exec_pids": {}}
    backend._metadata = lambda: dict(metadata)
    backend._container = lambda: machine
    backend._project_postgres = lambda: SimpleNamespace(labels={"omnia.fencing_epoch": "3"})
    backend._require_recovery_access = lambda *_: None
    backend.prepare_capture = lambda: None
    monkeypatch.setattr(backend_module, "startup_incarnation", lambda current: current.incarnation)
    monkeypatch.setattr(
        backend_module, "reader_dispatch", lambda callback, *args, **kw: callback(*args, **kw)
    )
    before = backend.environment_identity()
    backend.prepare_project_database_migrations(3)
    after = backend.environment_identity()
    assert calls == ["stop", "start"]
    assert before.content_digest == after.content_digest and before.incarnation != after.incarnation
    assert before.digest != after.digest


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1])
@pytest.mark.parametrize(
    "mode", ["success", "content_changed", "failed", "timeout", "foreign_role"]
)
async def test_real_router_emits_only_exact_successful_full_build_restart(
    tmp_path,
    monkeypatch,
    count,
    mode,
):
    pytest.importorskip(
        "fcntl", reason="Real router requires POSIX controller locks; Linux CI required"
    )
    from tests.test_machine_workspace_dispatch import PortableRuntime
    from tests.test_project_machine_manifest import payload
    from tests.test_workspace_router import _client, _ready_provider
    from yleum_orchestrator.core.config import get_settings
    from yleum_orchestrator.routers import workspace
    from yleum_orchestrator.services.docker_cell_resources import DockerCommandResult

    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token-not-a-real-secret")
    monkeypatch.delenv("WORKSPACE_PROVIDER", raising=False)
    monkeypatch.delenv("DOCKER_OWNER_CANARY_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        workspace_id = uuid4()
        provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)

        class RestartedRuntime(PortableRuntime):
            def __init__(self):
                super().__init__()
                self.environment = EnvironmentIdentity("a" * 64, "b" * 64)
                self.backend.environment_identity = lambda: self.environment
                self.backend.environment_digest = lambda: self.environment.digest
                self.compilation = self.migration = None

            async def execute(self, state, manifest, request):
                self.environment = EnvironmentIdentity(
                    "d" * 64 if mode == "content_changed" else "a" * 64,
                    "c" * 64,
                )
                self.compilation = dict(
                    collector_version="next-fixed-data-v1",
                    assets=[],
                    workspace_id=str(state.workspace_id),
                    project_id=str(state.project_id),
                    generation_run_id=str(run_id),
                    fencing_epoch=request.fencing_epoch,
                    operation_id=str(request.operation_id),
                    source_revision=request.expected_revision,
                    manifest_digest=manifest.digest(),
                )
                self.migration = dict(
                    contract="project-migrations-v1",
                    mode="apply",
                    migration_count=count,
                    workspace_id=str(state.workspace_id),
                    generation_run_id=str(run_id),
                    fencing_epoch=request.fencing_epoch,
                    source_revision=request.expected_revision,
                    source_digest="6" * 64,
                    database_identity="7" * 64,
                    catalog_digest="8" * 64,
                )
                return DockerCommandResult(
                    exit_code=1 if mode == "failed" else 0,
                    output="controlled build",
                    timed_out=mode == "timeout",
                )

            def compilation_receipt(self, *_args):
                return self.compilation

            def migration_receipt(self, *_args):
                return self.migration

        runtime = RestartedRuntime()
        manager.machine_runtime = runtime
        state = manager.state_store.load(workspace_id)
        files = {".omnia/cell.json": json.dumps(payload()), "server.py": "print('unchanged')"}
        await docker.write_volume_files(
            state.resource_names.workspace_volume,
            {path: source.encode() for path, source in files.items()},
        )
        monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
        operation_id = uuid4()
        async with _client() as client:
            response = await client.post(
                f"/internal/workspaces/{workspace_id}/agent/exec",
                headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
                json={
                    "generation_run_id": str(run_id),
                    "fencing_epoch": 4,
                    "expected_revision": workspace._workspace_revision(files),
                    "operation_id": str(operation_id),
                    "cmd": "omnia:full_build",
                    "task_role": "fast_check" if mode == "foreign_role" else "full_build",
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (
            body["before_identity"]["workspace_revision"]
            == body["after_identity"]["workspace_revision"]
        )
        compilation = body["compiled_asset_receipt"]
        transition = compilation.get("full_build_startup_transition") if compilation else None
        assert (transition is not None) is (mode == "success")
        if transition is not None:
            assert transition["owner_id"] == str(state.owner_id)
            assert transition["operation_id"] == str(operation_id)
            assert transition["migration_sha256"] == digest(body["project_migration_receipt"])
            assert transition["compilation_sha256"] == digest(
                {
                    key: value
                    for key, value in compilation.items()
                    if key != "full_build_startup_transition"
                }
            )
            assert (
                body["before_identity"]["environment_digest"]
                != body["after_identity"]["environment_digest"]
            )
        assert "full_build_startup_transition" not in runtime.compilation
    finally:
        get_settings.cache_clear()
