from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellIndeterminateOperation,
    CellResourceError,
)
from omnia_orchestrator.services.machine_adapter import MachineAdapter


async def test_protected_cold_resume_keeps_current_data_and_never_restores_archives(
    tmp_path, monkeypatch
):
    from omnia_orchestrator.services import machine_adapter as module
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, volumes, *_ = retained_preview_fixture(tmp_path)
    current_files = []
    for index, volume in enumerate(volumes.values()):
        marker = Path(volume.attrs["Mountpoint"]) / "current-after-snapshot"
        marker.write_bytes(f"new user data / installed dependency {index}".encode())
        current_files.append((marker, marker.read_bytes()))
    runtime = MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "states")), SimpleNamespace()
    )
    machine = SimpleNamespace(
        path=tmp_path / "machine.json",
        state=lambda: {"manifest": reference.manifest.model_dump(mode="json"), "epoch": 7},
    )
    runtime.parts = lambda _: (machine, backend)
    calls = []
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.load_policy", lambda _: {"epoch": 7}
    )
    monkeypatch.setattr(
        module.MachineEnvironmentStore, "restore", lambda *a, **kw: pytest.fail("restored old data")
    )
    monkeypatch.setattr(
        backend, "consume_retained_preview", lambda *a, **kw: pytest.fail("old checkpoint receipt")
    )
    monkeypatch.setattr(backend, "ensure", lambda *a: calls.append("ensure-current"))
    monkeypatch.setattr(backend, "start_service", lambda *a: calls.append("service"))
    monkeypatch.setattr(backend, "service_status", lambda *a, **kw: {"ready": True})
    runtime._start_boundary = lambda *a: calls.append("boundary")
    await runtime.resume_preview(SimpleNamespace(workspace_id=backend.workspace_id))
    assert calls == ["ensure-current", "service", "service", "boundary"]
    assert all(path.read_bytes() == value for path, value in current_files)


def test_capability_is_per_workspace_without_changing_legacy_defaults(monkeypatch):
    runtime = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda state: (None, SimpleNamespace(protected=state.protected))
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.load_policy",
        lambda backend: {"epoch": 7} if backend.protected else None,
    )
    assert runtime.capabilities()["database_admin"] == "full"
    assert runtime.capabilities(SimpleNamespace(protected=False))["database_admin"] == "full"
    assert runtime.capabilities(SimpleNamespace(protected=True))["database_admin"] == "protected"


def test_active_code_volume_accepts_combined_cell_and_machine_identity(tmp_path):
    from omnia_orchestrator.services.protected_machine_lifecycle import validate_retained_runtime
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, volumes, *_ = retained_preview_fixture(tmp_path)
    original = volumes[backend.workspace_volume]
    active_name = backend.stem + "-code-" + uuid4().hex
    active_labels = {**original.attrs["Labels"], **backend.labels("project-volume")}
    volumes[active_name] = SimpleNamespace(
        attrs={
            **original.attrs,
            "Name": active_name,
            "Labels": active_labels,
        }
    )
    backend.workspace_volume = active_name
    validate_retained_runtime(backend, reference.manifest)
    active_labels["omnia.owner_id"] = str(uuid4())
    with pytest.raises(CellIdentityConflict, match="volume identity"):
        validate_retained_runtime(backend, reference.manifest)


@pytest.mark.parametrize("explicit_metadata", [True, False])
def test_retained_runtime_accepts_only_configured_pinned_base(tmp_path, explicit_metadata):
    from omnia_orchestrator.services.project_machine import write_controller_json
    from omnia_orchestrator.services.protected_machine_lifecycle import validate_retained_runtime
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, volumes, image, _ = retained_preview_fixture(tmp_path)
    old_names = backend.environment_volume_names(reference.manifest)
    backend.base_image = "registry.example/project-machine@sha256:" + "a" * 64
    for old, current in zip(
        old_names, backend.environment_volume_names(reference.manifest), strict=True
    ):
        volume = volumes.pop(old)
        volume.attrs["Name"] = current
        volumes[current] = volume
    metadata = backend._metadata()
    metadata["restored_image"] = backend.base_image if explicit_metadata else None
    write_controller_json(backend.metadata_path, metadata)
    # Manifest digest identifies the configured image reference, not Image.id.
    image.attrs["Config"] = {"Env": ["PATH=/usr/bin"], "Cmd": ["python3"], "Labels": {}}
    lookups = []
    backend.client.images.get = lambda ref: (lookups.append(ref), image)[1]

    validate_retained_runtime(backend, reference.manifest)

    assert lookups == [backend.base_image]
    assert image.id != "sha256:" + "a" * 64


@pytest.mark.parametrize("image_ref", ["project-machine:latest", "other@sha256:" + "b" * 64])
def test_retained_runtime_rejects_untrusted_image_reference(tmp_path, image_ref):
    from omnia_orchestrator.services.project_machine import write_controller_json
    from omnia_orchestrator.services.protected_machine_lifecycle import validate_retained_runtime
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, *_ = retained_preview_fixture(tmp_path)
    metadata = backend._metadata()
    metadata["restored_image"] = image_ref
    write_controller_json(backend.metadata_path, metadata)
    with pytest.raises(CellIdentityConflict, match="not immutable"):
        validate_retained_runtime(backend, reference.manifest)


def test_configured_mutable_base_is_not_trusted(tmp_path):
    from omnia_orchestrator.services.project_machine import write_controller_json
    from omnia_orchestrator.services.protected_machine_lifecycle import validate_retained_runtime
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, *_ = retained_preview_fixture(tmp_path)
    backend.base_image = "project-machine:latest"
    metadata = backend._metadata()
    metadata["restored_image"] = backend.base_image
    write_controller_json(backend.metadata_path, metadata)
    with pytest.raises(CellIdentityConflict, match="not immutable"):
        validate_retained_runtime(backend, reference.manifest)


@pytest.mark.parametrize("fault", ["ownership", "runtime_config", "changed_id"])
def test_resolved_base_id_does_not_bypass_captured_image_validation(tmp_path, fault):
    from omnia_orchestrator.services.protected_machine_lifecycle import validate_retained_runtime
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, _, image, _ = retained_preview_fixture(tmp_path)
    backend.base_image = "project-machine@sha256:" + "a" * 64
    # Both lookups resolve to this image; metadata contains its captured ID only.
    if fault == "ownership":
        image.attrs["Config"]["Labels"]["omnia.owner_id"] = str(uuid4())
    elif fault == "runtime_config":
        image.attrs["Config"]["Env"] = ["UNTRUSTED=value"]
    else:
        image.id = "sha256:" + "e" * 64
    with pytest.raises(CellIdentityConflict):
        validate_retained_runtime(backend, reference.manifest)


@pytest.fixture
def migration_runtime(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services import protected_machine_lifecycle as lifecycle
    from omnia_orchestrator.services.project_machine import write_controller_json
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, *_ = retained_preview_fixture(tmp_path)
    request = SimpleNamespace(
        generation_run_id=uuid4(), operation_id=uuid4(), fencing_epoch=7, expected_revision="a" * 64
    )
    state = SimpleNamespace(
        workspace_id=backend.workspace_id,
        project_id=backend.project_id,
        owner_id=backend.owner_id,
        fencing_epoch=7,
        active_generation_run_id=request.generation_run_id,
        active_generation_fencing_epoch=7,
    )
    calls = []
    contract = {"version": 1, "tables": []}
    policy = {"epoch": 6, "contract": contract, "blocked_deletes": []}
    runtime = SimpleNamespace(
        manager=SimpleNamespace(state_store=SimpleNamespace(load=lambda _: state)),
        checkpoint=AsyncMock(return_value=reference),
        _start_boundary=lambda *a: calls.append("boundary"),
    )
    machine = SimpleNamespace(
        path=tmp_path / "machine.json",
        state=lambda: {"epoch": 7, "manifest": reference.manifest.model_dump(mode="json")},
    )
    runtime.parts = lambda _: (machine, backend)
    monkeypatch.setattr(
        lifecycle, "read_desired_contract", AsyncMock(return_value=contract), raising=False
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.load_policy", lambda _: policy
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_catalog.catalog_contract",
        lambda *a, **kw: (lifecycle.DataContract.model_validate(contract), []),
    )
    product = SimpleNamespace(remove=lambda **kw: calls.append("remove-product"))
    monkeypatch.setattr(backend, "_container", lambda: product)
    monkeypatch.setattr(backend, "ensure", lambda *a: calls.append("ensure"))
    monkeypatch.setattr(backend, "remove", lambda: calls.append("remove-both"))
    monkeypatch.setattr(backend, "start_service", lambda *a: calls.append("service"))
    monkeypatch.setattr(backend, "service_status", lambda *a, **kw: {"ready": True})

    def apply(*a, **kw):
        assert lifecycle.pending_migration(backend)["state"] == "ddl_pending"
        assert calls[-1] == "remove-product"
        calls.append("ddl")
        return {"contract": contract, "applied_actions": [], "schema_digest": "b" * 64}

    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_migrations.apply_additive_contract", apply
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.recover_policy",
        lambda *a, **kw: calls.append("rotate"),
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.install_policy",
        lambda *a: calls.append("roles"),
    )
    write_controller_json(machine.path, machine.state())
    return lifecycle, runtime, state, request, backend, reference.manifest, calls


async def test_declarative_migration_persists_intent_and_installs_roles_before_restart(
    migration_runtime,
):
    lifecycle, runtime, state, request, backend, manifest, calls = migration_runtime
    proof = await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert proof["schema_digest"] == "b" * 64
    assert calls == [
        "ensure",
        "remove-product",
        "ddl",
        "remove-both",
        "rotate",
        "ensure",
        "roles",
        "service",
        "service",
        "boundary",
    ]
    runtime.checkpoint.assert_awaited_once_with(state, volumes=(), persist=False)
    assert lifecycle.pending_migration(backend) is None
    before_replay = list(calls)
    assert await lifecycle.execute_generation_migration(runtime, state, manifest, request) == proof
    assert calls == before_replay  # Lost command acknowledgement cannot rotate/start twice.


async def test_migration_role_failure_replays_without_repeating_ddl(migration_runtime, monkeypatch):
    lifecycle, runtime, state, request, backend, manifest, calls = migration_runtime
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.install_policy",
        lambda *a: (_ for _ in ()).throw(RuntimeError("roles unavailable")),
    )
    with pytest.raises(RuntimeError, match="roles unavailable"):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert lifecycle.pending_migration(backend)["state"] == "policy_staged"
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.install_policy",
        lambda *a: calls.append("roles"),
    )
    await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert calls.count("ddl") == 1
    assert calls.count("rotate") == 1
    assert lifecycle.pending_migration(backend) is None


async def test_migration_rejects_changed_generation_before_any_effect(migration_runtime):
    lifecycle, runtime, state, request, backend, manifest, calls = migration_runtime
    request.generation_run_id = uuid4()
    with pytest.raises(CellIdentityConflict, match="generation lease changed"):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert calls == []
    assert not lifecycle.migration_path(backend).exists()


async def test_unsupported_contract_leaves_running_app_and_no_pending_intent(
    migration_runtime,
    monkeypatch,
):
    lifecycle, runtime, state, request, backend, manifest, calls = migration_runtime
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_migrations.plan_additive_migration",
        lambda *a: (_ for _ in ()).throw(CellResourceError("migration_unsupported:drop_column")),
    )
    with pytest.raises(CellResourceError, match="drop_column"):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert calls == []
    runtime.checkpoint.assert_not_awaited()
    assert not lifecycle.migration_path(backend).exists()


async def test_owner_preview_cannot_skip_pending_role_recovery(migration_runtime):
    from omnia_orchestrator.services.project_machine import write_controller_json

    lifecycle, fixture_runtime, state, _, backend, _, calls = migration_runtime
    write_controller_json(
        lifecycle.migration_path(backend),
        {
            "workspace_id": str(state.workspace_id),
            "project_id": str(state.project_id),
            "owner_id": str(state.owner_id),
            "state": "policy_staged",
        },
    )
    runtime = MachineAdapter(fixture_runtime.manager, SimpleNamespace())
    runtime.parts = fixture_runtime.parts
    with pytest.raises(CellResourceError, match="migration pending"):
        await runtime.resume_preview(state)
    assert calls == []


async def test_migration_uncertain_sql_replays_same_desired_declaration(
    migration_runtime, monkeypatch
):
    lifecycle, runtime, state, request, backend, manifest, calls = migration_runtime
    attempts = []

    def apply(*a, **kw):
        attempts.append(a[1].model_dump(mode="json"))
        if len(attempts) == 1:
            raise RuntimeError("lost SQL acknowledgement")
        return {"contract": attempts[-1], "applied_actions": [], "schema_digest": "b" * 64}

    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_migrations.apply_additive_contract",
        apply,
    )
    with pytest.raises(RuntimeError, match="lost SQL acknowledgement"):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert lifecycle.pending_migration(backend)["state"] == "ddl_pending"
    assert "rotate" not in calls
    await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert attempts == [{"version": 1, "tables": []}] * 2
    runtime.checkpoint.assert_awaited_once()


async def test_pending_migration_rejects_new_source_before_replay(migration_runtime, monkeypatch):
    lifecycle, runtime, state, request, _backend, manifest, calls = migration_runtime
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_migrations.apply_additive_contract",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("lost SQL acknowledgement")),
    )
    with pytest.raises(RuntimeError):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    previous_calls = list(calls)
    request.expected_revision = "c" * 64
    with pytest.raises(CellIdentityConflict, match="envelope changed"):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    assert calls == previous_calls


@pytest.mark.parametrize("times_out", [False, True])
async def test_exact_command_uses_controller_without_guest_exec(
    migration_runtime, monkeypatch, times_out
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
    from omnia_orchestrator.services.project_machine import write_controller_json

    lifecycle, fixture_runtime, state, _, backend, manifest, _calls = migration_runtime
    runtime = MachineAdapter(fixture_runtime.manager, SimpleNamespace())
    machine = SimpleNamespace(
        state=lambda: {"manifest": manifest.model_dump(mode="json")},
        ensure=AsyncMock(),
        request_start=AsyncMock(return_value=None),
        request_finish=AsyncMock(side_effect=lambda _, result: result),
        exec_start=AsyncMock(side_effect=AssertionError("guest executed")),
    )
    runtime.parts = lambda _: (machine, backend)
    request = WorkspaceAgentExecRequest(
        generation_run_id=state.active_generation_run_id,
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd=lifecycle.MIGRATION_COMMAND,
    )

    async def apply(*args):
        if times_out:
            write_controller_json(
                lifecycle.migration_path(backend),
                {
                    "workspace_id": str(state.workspace_id),
                    "project_id": str(state.project_id),
                    "owner_id": str(state.owner_id),
                    "state": "ddl_pending",
                },
            )
            raise TimeoutError
        return {"schema_digest": "b" * 64}

    monkeypatch.setattr(lifecycle, "execute_generation_migration", apply)
    if times_out:
        with pytest.raises(CellIndeterminateOperation, match="same declaration"):
            await runtime.execute(state, manifest, request)
        machine.request_finish.assert_not_awaited()
    else:
        result = await runtime.execute(state, manifest, request)
        assert result.exit_code == 0
        assert '"schema_digest"' in result.output
        machine.request_finish.assert_awaited_once()
    machine.exec_start.assert_not_awaited()


@pytest.mark.parametrize("entry", ["command", "apply", "activate"])
@pytest.mark.parametrize("missing", ["postgres", "home"])
async def test_protected_generated_paths_never_recreate_missing_current_material(
    tmp_path,
    monkeypatch,
    entry,
    missing,
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, volumes, *_ = retained_preview_fixture(tmp_path)
    name = backend.project_postgres_volume if missing == "postgres" else backend.stem + "-home"
    del volumes[name]
    manifest = reference.manifest
    machine = SimpleNamespace(
        state=lambda: {"manifest": manifest.model_dump(mode="json")},
        ensure=AsyncMock(),
        exec_start=AsyncMock(),
    )
    runtime = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda _: (machine, backend)
    runtime.checkpoint = AsyncMock()
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.load_policy", lambda _: {"epoch": 7}
    )
    ddl = AsyncMock()
    monkeypatch.setattr(
        "omnia_orchestrator.services.protected_machine_lifecycle.execute_generation_migration", ddl
    )
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="printf harmless",
    )
    state = SimpleNamespace(workspace_id=backend.workspace_id)
    with pytest.raises(CellResourceError, match=r"retained material.*missing"):
        if entry == "command":
            await runtime.execute(state, manifest, request)
        elif entry == "apply":
            await runtime.apply(state, manifest, request)
        else:
            await runtime._activate_runtime(state, manifest, request)
    machine.ensure.assert_not_awaited()
    machine.exec_start.assert_not_awaited()
    runtime.checkpoint.assert_not_awaited()
    ddl.assert_not_awaited()


async def test_protected_generation_rejects_new_material_mount_admission(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, *_ = retained_preview_fixture(tmp_path)
    current = reference.manifest
    proposed = current.model_dump(mode="json")
    proposed["services"][0]["mounts"] = [{"volume": "uploads", "target": "/uploads"}]
    machine = SimpleNamespace(
        state=lambda: {"manifest": current.model_dump(mode="json")},
        ensure=AsyncMock(),
        exec_start=AsyncMock(),
    )
    runtime = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda _: (machine, backend)
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.load_policy", lambda _: {"epoch": 7}
    )
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="printf harmless",
    )
    with pytest.raises(CellResourceError, match="mount changes require explicit admission"):
        await runtime.execute(SimpleNamespace(), MachineManifest.model_validate(proposed), request)
    machine.ensure.assert_not_awaited()
    machine.exec_start.assert_not_awaited()


@pytest.mark.parametrize("resource", ["volume", "image"])
def test_protected_runtime_rejects_missing_material_without_recreating_it(
    tmp_path, monkeypatch, resource
):
    from omnia_orchestrator.services.protected_machine_lifecycle import validate_retained_runtime
    from tests.test_docker_machine_backend import retained_preview_fixture

    backend, reference, *_ = retained_preview_fixture(tmp_path)
    collection = backend.client.volumes if resource == "volume" else backend.client.images
    monkeypatch.setattr(collection, "get", lambda *a: (_ for _ in ()).throw(LookupError(resource)))
    with pytest.raises(LookupError, match=resource):
        validate_retained_runtime(backend, reference.manifest)


async def test_cancel_reconciles_persisted_intent_without_guest_source_or_service_restart(
    migration_runtime,
    monkeypatch,
):
    from unittest.mock import AsyncMock

    lifecycle, runtime, state, request, backend, manifest, calls = migration_runtime
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.install_policy",
        lambda *a: (_ for _ in ()).throw(RuntimeError("roles unavailable")),
    )
    with pytest.raises(RuntimeError):
        await lifecycle.execute_generation_migration(runtime, state, manifest, request)
    monkeypatch.setattr(
        lifecycle,
        "read_desired_contract",
        AsyncMock(side_effect=AssertionError("new guest input during release")),
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.restoration_database.install_policy",
        lambda *a: calls.append("roles"),
    )
    await lifecycle.reconcile_generation_migration(runtime, state)
    assert lifecycle.pending_migration(backend) is None
    assert calls.count("ddl") == 1
    assert "service" not in calls and "boundary" not in calls


@pytest.mark.parametrize("fails", [False, True])
async def test_canonical_release_reconciles_before_clearing_generation(tmp_path, fails):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from tests.test_docker_cell_resources import _make_manager, _mutation, _spec

    manager, _docker, store, _lock = _make_manager(tmp_path)
    spec = replace(_spec(uuid4()), generation_run_id=uuid4())
    await manager.ensure(spec, _mutation("a", 1))
    calls = []

    async def reconcile(state):
        assert store.load(spec.workspace_id).active_generation_run_id == spec.generation_run_id
        assert state.active_generation_fencing_epoch == 1
        calls.append("reconciled")
        if fails:
            raise RuntimeError("SQL still unavailable")

    async def halt(*a, **kw):
        assert calls == ["reconciled"]
        calls.append("halt")

    manager.machine_runtime = SimpleNamespace(
        reconcile_pending_migration=reconcile, halt=AsyncMock(side_effect=halt)
    )
    if fails:
        with pytest.raises(RuntimeError, match="SQL still unavailable"):
            await manager.release_generation(
                spec.workspace_id, _mutation("b", 2), generation_run_id=spec.generation_run_id
            )
        assert store.load(spec.workspace_id).active_generation_run_id == spec.generation_run_id
        manager.machine_runtime.halt.assert_not_awaited()
    else:
        await manager.release_generation(
            spec.workspace_id, _mutation("b", 2), generation_run_id=spec.generation_run_id
        )
        assert calls == ["reconciled", "halt"]
        assert store.load(spec.workspace_id).active_generation_run_id is None


@pytest.mark.parametrize("admission", ["ensure", "pause", "prepare_pause", "destroy", "reconcile"])
async def test_pending_migration_failure_cannot_advance_other_lifecycle_admission(
    tmp_path, admission
):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from tests.test_docker_cell_resources import _make_manager, _mutation, _spec

    manager, _docker, store, _lock = _make_manager(tmp_path)
    spec = replace(_spec(uuid4()), generation_run_id=uuid4())
    await manager.ensure(spec, _mutation("a", 1))
    before = store.load(spec.workspace_id)
    reservation = manager._capacity_reservation_store().load(spec.workspace_id)

    async def reconcile(state):
        assert store.load(spec.workspace_id) == before
        assert state.active_generation_fencing_epoch == 1
        raise RuntimeError("pending original SQL intent")

    manager.machine_runtime = SimpleNamespace(
        reconcile_pending_migration=reconcile, halt=AsyncMock()
    )
    with pytest.raises(RuntimeError, match="pending original SQL intent"):
        if admission == "ensure":
            await manager.ensure(replace(spec, generation_run_id=uuid4()), _mutation("b", 2))
        elif admission == "pause":
            await manager.pause_services(spec.workspace_id, _mutation("b", 2), checkpoint_ref=None)
        elif admission == "prepare_pause":
            async with manager.operation_lock.hold(spec.workspace_id):
                await manager.prepare_control_operation(
                    spec.workspace_id, _mutation("b", 2), kind="pause"
                )
        elif admission == "destroy":
            await manager.destroy_compute(spec.workspace_id, _mutation("b", 2))
        else:
            await manager.reconcile(spec.workspace_id, _mutation("b", 2))
    assert store.load(spec.workspace_id) == before
    assert manager._capacity_reservation_store().load(spec.workspace_id) == reservation
    manager.machine_runtime.halt.assert_not_awaited()
