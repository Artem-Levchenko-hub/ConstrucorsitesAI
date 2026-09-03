import json
from uuid import uuid4

import pytest

from omnia_orchestrator.routers import workspace
from omnia_orchestrator.services.docker_cell_resources import DockerCommandResult
from tests.test_project_machine_manifest import payload
from tests.test_workspace_router import _client, _ready_provider
from tests.test_workspace_router import _internal_settings as _internal_settings


class PortableRuntime:
    def __init__(self):
        self.commands = []

    def capabilities(self):
        return {
            "portable_machine": True,
            "manifest_path": ".omnia/cell.json",
            "public_package_egress": True,
            "persistent_environment": True,
        }

    async def execute(self, state, manifest, request):
        self.commands.append((state.workspace_id, manifest.services[0].argv, request.task_role))
        return DockerCommandResult(exit_code=0, output="python tests passed", timed_out=False)

    async def apply(self, state, manifest, request):
        return await self.execute(state, manifest, type("Request", (), {"task_role": "build"})())


@pytest.mark.usefixtures("_internal_settings")
async def test_machine_lifecycle_removes_dead_gateway_ingress_and_reads_service_logs(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from omnia_orchestrator.services.project_machine import write_controller_json

    workspace_id = uuid4()
    _provider, manager, _docker, _run_id = await _ready_provider(tmp_path, workspace_id)
    state = manager.state_store.load(workspace_id)
    marker = (
        manager.state_store.root.parent / "project-machines" / str(workspace_id) / "portable.json"
    )
    write_controller_json(marker, {"workspace_id": str(workspace_id)})
    manager.machine_runtime = SimpleNamespace(
        preview=lambda state: None, logs=AsyncMock(return_value="web: useful runtime error")
    )
    unpublish = AsyncMock()
    monkeypatch.setattr(workspace.nginx_writer, "unpublish", unpublish)
    await workspace._sync_lifecycle_draft_preview(
        manager,
        workspace_id,
        LifecycleMutation(state.last_operation_id, state.fencing_epoch, "a" * 64),
    )
    assert (
        await workspace._draft_runtime_log_tail(manager, workspace_id)
        == "web: useful runtime error"
    )
    unpublish.assert_awaited_once()


@pytest.mark.usefixtures("_internal_settings")
@pytest.mark.parametrize("copied_template", [False, True])
async def test_new_pristine_seed_gets_main_stack_machine_before_writes(
    tmp_path, monkeypatch, copied_template
):
    from pathlib import Path

    workspace_id = uuid4()
    _provider, manager, _docker, _run_id = await _ready_provider(tmp_path, workspace_id)
    manager.machine_runtime = PortableRuntime()
    state = manager.state_store.load(workspace_id)
    template = Path(__file__).parents[1] / "templates" / "max-miniapp-nextjs"
    monkeypatch.setattr(
        workspace,
        "_project_workspace_dir",
        lambda _: template if copied_template else tmp_path / "absent",
    )
    monkeypatch.setattr(
        workspace,
        "trusted_template_source",
        lambda _: Path(__file__).parents[1] / "templates" / "max-miniapp-nextjs",
    )
    files, seeded = await workspace._ensure_seed_workspace_files(
        manager, state, state.resource_names.workspace_volume
    )
    assert seeded is copied_template
    assert json.loads(files[".omnia/cell.json"])["services"][0]["argv"] == ["pnpm", "start"]
    assert "src/app/page.tsx" not in files
    assert "AUTH_SECRET" not in "\n".join(files.values())


@pytest.mark.usefixtures("_internal_settings")
async def test_existing_customized_project_source_is_not_automatically_migrated(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    workspace_id = uuid4()
    _provider, manager, _docker, _run_id = await _ready_provider(tmp_path, workspace_id)
    manager.machine_runtime = PortableRuntime()
    state = manager.state_store.load(workspace_id)
    monkeypatch.setattr(workspace, "_project_workspace_dir", lambda _: tmp_path)
    original = {"package.json": '{"private":true}', "src/app/page.tsx": "my existing UI"}
    monkeypatch.setattr(
        workspace, "_collect_project_workspace_files", AsyncMock(return_value=(original, []))
    )
    files, seeded = await workspace._ensure_seed_workspace_files(
        manager, state, state.resource_names.workspace_volume
    )
    assert seeded is True
    assert files == original


@pytest.mark.usefixtures("_internal_settings")
async def test_manifest_build_dispatches_to_machine_after_lease_and_revision_checks(
    tmp_path, monkeypatch
):
    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    runtime = PortableRuntime()
    manager.machine_runtime = runtime
    files = {".omnia/cell.json": json.dumps(payload()), "server.py": "print('python')"}
    state = manager.state_store.load(workspace_id)
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {path: content.encode() for path, content in files.items()},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    request = {
        "generation_run_id": str(run_id),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision(files),
        "cmd": "omnia:build",
        "task_role": "build",
        "operation_id": str(uuid4()),
    }
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            json=request,
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["detail"] == "python tests passed"
        assert runtime.commands == [(workspace_id, ["python", "server.py"], "build")]
        request["fencing_epoch"] = 3
        denied = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            json=request,
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
        assert denied.status_code == 409
        assert len(runtime.commands) == 1


@pytest.mark.usefixtures("_internal_settings")
async def test_manifest_is_not_silently_executed_in_legacy_when_provider_unavailable(
    tmp_path, monkeypatch
):
    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    files = {".omnia/cell.json": json.dumps(payload())}
    state = manager.state_store.load(workspace_id)
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {path: content.encode() for path, content in files.items()},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            json={
                "generation_run_id": str(run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision(files),
                "cmd": "python test.py",
            },
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
        assert response.status_code == 503, response.text
        assert "portable machine" in response.text.lower()


@pytest.mark.usefixtures("_internal_settings")
async def test_portable_draft_without_provider_never_runs_legacy_migrations(tmp_path, monkeypatch):
    async def publish(*_args):
        return "https://test.invalid"

    monkeypatch.setattr(workspace, "_publish_draft_preview", publish)
    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    files = {".omnia/cell.json": json.dumps(payload())}
    state = manager.state_store.load(workspace_id)
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {path: content.encode() for path, content in files.items()},
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/apply",
            json={
                "generation_run_id": str(run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision(files),
                "files": {},
            },
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
        assert response.status_code == 503, response.text
        assert "portable machine" in response.text.lower()


@pytest.mark.usefixtures("_internal_settings")
@pytest.mark.parametrize("endpoint", ["agent/exec", "draft/apply"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_guest_manifest_deletion_cannot_downgrade_sticky_machine_to_credentialed_legacy(
    tmp_path,
    monkeypatch,
    endpoint,
    enabled,
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services.project_machine import write_controller_json

    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    manager.machine_runtime = PortableRuntime() if enabled else None
    marker = (
        manager.state_store.root.parent / "project-machines" / str(workspace_id) / "machine.json"
    )
    write_controller_json(marker, {"workspace_id": str(workspace_id), "epoch": 4})
    files = {"scripts/apply-migrations.mjs": "console.log(process.env.PGPASSWORD)"}
    state = manager.state_store.load(workspace_id)
    await docker.write_volume_files(
        state.resource_names.workspace_volume,
        {path: content.encode() for path, content in files.items()},
    )
    legacy = AsyncMock(
        return_value=DockerCommandResult(exit_code=0, output="legacy", timed_out=False)
    )
    monkeypatch.setattr(docker, "run_workspace_command", legacy)
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    monkeypatch.setattr(
        workspace, "_publish_draft_preview", AsyncMock(return_value="https://test.invalid")
    )
    payload = {
        "generation_run_id": str(run_id),
        "fencing_epoch": 4,
        "expected_revision": workspace._workspace_revision(files),
    }
    payload.update(
        {"cmd": "node scripts/apply-migrations.mjs"} if endpoint == "agent/exec" else {"files": {}}
    )
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/{endpoint}",
            json=payload,
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
    assert response.status_code == 409, response.text
    assert "manifest" in response.text.lower()
    legacy.assert_not_awaited()


@pytest.mark.usefixtures("_internal_settings")
@pytest.mark.parametrize("endpoint", ["agent/write-files", "draft/apply"])
async def test_first_manifest_patch_retires_credentialed_legacy_before_shared_source_write(
    tmp_path,
    monkeypatch,
    endpoint,
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services.machine_identity import is_portable_workspace

    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    manager.machine_runtime = PortableRuntime()
    await manager.ensure_draft_runtime(workspace_id)
    state = manager.state_store.load(workspace_id)
    draft_name = state.resource_names.draft_container_name()
    assert draft_name in docker.containers
    await docker.write_volume_files(
        state.resource_names.workspace_volume, {"original.txt": b"legacy source"}
    )
    original_write = docker.write_volume_files
    events = []

    async def write(volume, files):
        if ".omnia/cell.json" in files:
            events.append(
                (
                    "write",
                    draft_name in docker.containers,
                    is_portable_workspace(manager.state_store.root, workspace_id),
                )
            )
        return await original_write(volume, files)

    async def unpublish(host):
        events.append("unpublish")

    monkeypatch.setattr(docker, "write_volume_files", write)
    monkeypatch.setattr(workspace.nginx_writer, "unpublish", unpublish)
    monkeypatch.setattr(
        workspace, "_publish_draft_preview", AsyncMock(return_value="https://test.invalid")
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    files = await workspace._read_agent_workspace_files(
        manager, state.resource_names.workspace_volume
    )
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/{endpoint}",
            json={
                "generation_run_id": str(run_id),
                "fencing_epoch": 4,
                "expected_revision": workspace._workspace_revision(files),
                "files": {
                    ".omnia/cell.json": json.dumps(payload()),
                    "scripts/apply-migrations.mjs": "console.log(process.env.PGPASSWORD)",
                },
            },
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
        )
    assert response.status_code == 200, response.text
    assert events == ["unpublish", ("write", False, True)]
