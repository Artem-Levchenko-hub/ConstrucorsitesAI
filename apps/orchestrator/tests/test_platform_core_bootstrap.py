"""Only controller-trusted platform SQL can run during package bootstrap."""

import hashlib
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests._machine_adapter_fakes import legacy_machine_controller  # noqa: F401
from tests.test_project_machine_manifest import payload
from tests.test_workspace_router import _internal_settings as _internal_settings
from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.routers import workspace
from yleum_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
from yleum_orchestrator.services import applied_migration_sources, machine_adapter
from yleum_orchestrator.services.cell_draft_support import trusted_template_source
from yleum_orchestrator.services.project_migrations import (
    PLATFORM_BOOTSTRAP_CONTRACT,
    PLATFORM_CORE_MIGRATION,
    select_platform_core_migration,
)


def core_source():
    return (trusted_template_source("max-miniapp-nextjs") / PLATFORM_CORE_MIGRATION).read_text(
        encoding="utf-8",
    )


def request(role="bootstrap"):
    return WorkspaceAgentExecRequest(
        generation_run_id=uuid4(), fencing_epoch=7, expected_revision="a" * 64,
        cmd="omnia:" + role, task_role=role,
    )


def test_bootstrap_inventory_never_selects_other_pending_sql():
    core = core_source()
    assert select_platform_core_migration({
        PLATFORM_CORE_MIGRATION: core, "drizzle/0004_product.sql": "DROP TABLE business;",
    }, core) == {PLATFORM_CORE_MIGRATION: core}
    assert select_platform_core_migration({"drizzle/0004_product.sql": "SELECT 1;"}, core) == {}
    with pytest.raises(CellResourceError, match="rejected before SQL"):
        select_platform_core_migration({PLATFORM_CORE_MIGRATION: core + "-- changed"}, core)


@pytest.mark.parametrize("fault", [None, "applied_changed", "catalog", "core_changed", "absent"])
async def test_platform_bootstrap_preserves_applied_sources_and_ignores_pending_product(
    monkeypatch, fault,
):
    core = core_source()
    protected = {"drizzle/0002_accepted.sql": "CREATE TABLE accepted(id int);"}
    files = {**protected, PLATFORM_CORE_MIGRATION: core,
             "drizzle/0004_pending.sql": "DROP TABLE accepted;"}
    if fault == "applied_changed":
        files["drizzle/0002_accepted.sql"] = "DROP TABLE accepted;"
    elif fault == "core_changed":
        files[PLATFORM_CORE_MIGRATION] += "-- mutation"
    elif fault == "absent":
        files.pop(PLATFORM_CORE_MIGRATION)
    runtime = machine_adapter.MachineAdapter(NS(), NS())
    runtime.parts = lambda _: (NS(state=lambda: {}), NS())
    runtime._migration_inventory = AsyncMock(return_value=files)
    runtime._run_migration_inventory = AsyncMock(return_value={"contract": "project-migrations-v1"})
    stored = []
    runtime._store_migration_receipt = lambda *a: stored.append(a[-1])
    monkeypatch.setattr(applied_migration_sources, "protected_sources",
        lambda *_: (_ for _ in ()).throw(CellResourceError("catalog unavailable"))
        if fault == "catalog" else protected)
    command = request()
    state = NS(workspace_id=uuid4(), active_generation_run_id=command.generation_run_id,
               operations=())
    if fault in {"applied_changed", "catalog", "core_changed"}:
        with pytest.raises(CellResourceError):
            await runtime._bootstrap_platform_migrations(state, command)
        runtime._run_migration_inventory.assert_not_awaited()
    else:
        await runtime._bootstrap_platform_migrations(state, command)
        if fault == "absent":
            runtime._run_migration_inventory.assert_not_awaited()
            assert stored == []
            return
        runtime._run_migration_inventory.assert_awaited_once_with(
            state, command, {**protected, PLATFORM_CORE_MIGRATION: core},
            verify_only=False, verify_applied=False,
        )
        marker = stored[0]["platform_core_bootstrap"]
        assert marker == {
            "contract": PLATFORM_BOOTSTRAP_CONTRACT, "migration_path": PLATFORM_CORE_MIGRATION,
            "migration_sha256": hashlib.sha256(core.encode()).hexdigest(),
            "operation_id": str(command.operation_id),
        }


async def test_adaptation_bootstrap_never_classifies_or_applies_historical_sql():
    command = request()
    runtime = machine_adapter.MachineAdapter(NS(), NS())
    runtime.parts = lambda _: (NS(state=lambda: {
        "restoration_adaptation_run_id": str(command.generation_run_id),
    }), NS())
    runtime._migration_inventory = AsyncMock(side_effect=AssertionError("no historical SQL read"))
    runtime._run_migration_inventory = AsyncMock(
        side_effect=AssertionError("no historical SQL apply"),
    )
    await runtime._bootstrap_platform_migrations(NS(
        active_generation_run_id=command.generation_run_id, operations=(),
    ), command)
    runtime._migration_inventory.assert_not_awaited()
    runtime._run_migration_inventory.assert_not_awaited()


def test_new_bootstrap_command_digest_does_not_alias_install_only_contract():
    data = payload()
    data["app_config_protocol"] = 1
    manifest, command = MachineManifest.model_validate(data), request()
    old = {
        "cmd": command.cmd, "role": "bootstrap", "revision": command.expected_revision,
        "manifest": manifest.digest(), "timeout_seconds": command.timeout_seconds,
    }
    old_digest = hashlib.sha256(json.dumps(
        old, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert machine_adapter.MachineAdapter._request_digest(manifest, command) != old_digest
    for role in ("full_build", "restore_runtime"):
        command = request(role)
        previous = {**old, "cmd": command.cmd, "role": role,
                    "migration_contract": "project-migrations-v1"}
        assert machine_adapter.MachineAdapter._request_digest(manifest, command) == hashlib.sha256(
            json.dumps(previous, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()


@pytest.mark.usefixtures("_internal_settings")
@pytest.mark.parametrize("managed", [False, True])
async def test_bootstrap_wire_binds_current_schema_and_keeps_legacy_response(
    tmp_path, monkeypatch, managed,
):
    from tests.test_machine_workspace_dispatch import PortableRuntime
    from tests.test_workspace_router import _client, _ready_provider

    workspace_id = uuid4()
    provider, manager, docker, run_id = await _ready_provider(tmp_path, workspace_id)
    state = manager.state_store.load(workspace_id)
    data = payload()
    if managed:
        data["app_config_protocol"] = 1
    files = {".omnia/cell.json": json.dumps(data), PLATFORM_CORE_MIGRATION: core_source()}
    await docker.write_volume_files(state.resource_names.workspace_volume, {
        path: source.encode() for path, source in files.items()
    })

    class CoreRuntime(PortableRuntime):
        def __init__(self):
            super().__init__()
            self.receipt = {"platform_core_bootstrap": {"contract": PLATFORM_BOOTSTRAP_CONTRACT}}

        def migration_receipt(self, *_):
            assert managed, "legacy bootstrap must not acquire a new migration receipt"
            return self.receipt

    runtime = CoreRuntime()
    manager.machine_runtime = runtime
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _: provider)
    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/agent/exec",
            headers={"X-Internal-Token": "test-internal-token-not-a-real-secret"},
            json={"generation_run_id": str(run_id), "fencing_epoch": 4,
                  "expected_revision": workspace._workspace_revision(files),
                  "operation_id": str(uuid4()), "cmd": "omnia:bootstrap",
                  "task_role": "bootstrap"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    if managed:
        assert body["project_migration_receipt"]["platform_core_bootstrap"][
            "schema_data_digest"
        ] == body["after_identity"]["schema_data_digest"]
        assert "schema_data_digest" not in runtime.receipt["platform_core_bootstrap"]
    else:
        assert body.get("project_migration_receipt") is None
