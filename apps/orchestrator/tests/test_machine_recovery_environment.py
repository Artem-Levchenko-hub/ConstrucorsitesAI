from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceNames, LifecycleMutation
from yleum_orchestrator.core.workspace_provider import ReleaseEnvironmentExpectation
from yleum_orchestrator.services.cell_state import CellOperationRecord, CellWorkspaceState
from yleum_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    VolumeEnvironmentRef,
)
from yleum_orchestrator.services.machine_recovery_environment import (
    RecoveryEnvironmentUnproved,
    RecoveryReleaseRefused,
    RunningEnvironment,
    finish_proof,
    record_release,
    require_recorded_release_capture,
    verify_release,
)
from yleum_orchestrator.services.machine_startup import (
    EnvironmentIdentity,
    PrepareBinding,
    StartupJournal,
)


@pytest.mark.parametrize("changed", ["source", "manifest", "environment"])
async def test_strict_release_refuses_drift_before_stopping(tmp_path, monkeypatch, changed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from yleum_orchestrator.routers import workspace as routes
    from yleum_orchestrator.routers.runtime import _workspace_revision
    from yleum_orchestrator.services import active_source_volume, machine_adapter
    from yleum_orchestrator.services.machine_defaults import next_machine_manifest

    workspace, project, owner, run, operation = (uuid4() for _ in range(5))
    names = CellResourceNames.for_workspace(workspace)
    state = CellWorkspaceState(
        workspace, project, owner, "portable", "completed", "resources_ready",
        3, run, 3, uuid4(), "docker-test", names, (),
    )
    release = LifecycleMutation(operation, 4, "a" * 64)
    files = {"src/app/page.tsx": "settled source"}
    manifest = next_machine_manifest()
    before = RunningEnvironment(
        "sha256:" + "b" * 64, EnvironmentIdentity("c" * 64, "d" * 64),
        "e" * 64, "2026-11-15T00:00:00Z",
    )
    expected = ReleaseEnvironmentExpectation(
        source_sha256="f" * 64 if changed == "source" else _workspace_revision(files),
        environment_sha256="f" * 64 if changed == "environment" else before.identity.digest,
    )
    persisted = manifest.model_dump(mode="json")
    if changed == "manifest":
        persisted["tasks"] = list(reversed(persisted["tasks"]))
    machine = SimpleNamespace(state=lambda: {"epoch": 3, "manifest": persisted})
    backend = SimpleNamespace(
        workspace_volume=names.workspace_volume, _container=lambda: object(),
        _metadata=lambda: {"manifest": manifest.model_dump(mode="json")},
        invalidate_retained_preview=Mock(), _reconcile_recovery_helpers=Mock(),
        stop=Mock(), remove=Mock(),
    )
    runtime = machine_adapter.MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state")), SimpleNamespace(),
    )
    monkeypatch.setattr(machine_adapter.MachineAdapter, "root", property(lambda _: tmp_path))
    runtime.exists = lambda _: True
    runtime.recovery_required = lambda _: False
    runtime.parts = lambda _: (machine, backend)
    runtime.checkpoint = AsyncMock(return_value=None)
    monkeypatch.setattr(routes, "_read_agent_workspace_files", AsyncMock(return_value=files))
    monkeypatch.setattr(routes, "_machine_manifest", lambda *_args, **_kw: manifest)
    monkeypatch.setattr(
        active_source_volume, "source_volume_call", AsyncMock(return_value="a" * 64),
    )
    monkeypatch.setattr(machine_adapter, "running_environment", Mock(return_value=before))
    monkeypatch.setattr(machine_adapter, "replay_release_capture", Mock(return_value=None))

    with pytest.raises(RecoveryReleaseRefused) as error:
        await runtime.halt(state, generation_release=release, require_environment_proof=expected)

    assert error.value.reason == f"recovery_release_{changed}_changed"
    assert error.value.pre_effect is (changed != "environment")
    runtime.checkpoint.assert_not_called()
    backend.stop.assert_not_called()
    backend.remove.assert_not_called()


@pytest.mark.parametrize(
    "damage", [None, "owner", "source", "volume", "image", "inventory", "release", "archive"],
)
def test_owned_release_can_resume_exact_rootfs_under_next_fence(tmp_path, damage):
    workspace, project, owner, run = (uuid4() for _ in range(4))
    original, release, reacquire = (uuid4() for _ in range(3))
    namespace = CellResourceNames.for_workspace(workspace)
    volume = namespace.workspace_volume
    controller_operations = (
        CellOperationRecord(
            original, "ensure", "completed", "completed", "a" * 64, 3,
            generation_run_id=run, bundle_state="resources_ready", startup_contract_version=1,
        ),
        CellOperationRecord(
            release, "release", "completed", "completed", "b" * 64, 4,
            generation_run_id=run, bundle_state="resources_ready",
            detail="retained_source_fencing_epoch=3",
        ),
        CellOperationRecord(
            reacquire, "ensure", "completed", "completed", "c" * 64, 5,
            generation_run_id=run, bundle_state="resources_ready",
        ),
    )
    prior = CellWorkspaceState(
        workspace, project, owner, "docker-owner-cell-resources-v1",
        "completed", "resources_ready", 3, run, 3, original, "cell", namespace,
        controller_operations[:1],
    )
    after = replace(
        prior, fencing_epoch=5, active_generation_fencing_epoch=5,
        last_operation_id=reacquire, operations=controller_operations,
    )
    source, manifest, volume_identity = "d" * 64, "e" * 64, "f" * 64
    old_root, new_root = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    started_at = "2026-11-15T00:00:00Z"
    before = RunningEnvironment(
        old_root, EnvironmentIdentity("3" * 64, "4" * 64), "5" * 64, started_at,
    )
    current = RunningEnvironment(
        new_root, EnvironmentIdentity("6" * 64, "7" * 64), "8" * 64, started_at,
        previous_image_inventory_sha256=before.identity.content_digest,
    )
    artifact = MachineEnvironmentRef(
        workspace_id=workspace, image_id=new_root, artifact_ref="1" * 32 + ".tar",
        sha256="9" * 64, size=4096, base_image="synthetic", manifest_digest=manifest,
        volumes=(VolumeEnvironmentRef(
            name=volume, artifact_ref="2" * 32 + ".tar", sha256="0" * 64, size=4096,
        ),),
    )
    record_release(
        tmp_path, prior, LifecycleMutation(release, 4, "b" * 64),
        before=before, source=source, volume=volume, volume_identity=volume_identity,
        reference=artifact,
    )
    binding = PrepareBinding(
        workspace_id=workspace, project_id=project, owner_id=owner,
        generation_run_id=run, fencing_epoch=5, admission_operation_id=original,
        admission_request_sha256="a" * 64, physical_volume=volume,
        physical_volume_identity=volume_identity, workspace_revision=source,
        manifest_digest=manifest,
    )
    journal = StartupJournal(tmp_path, workspace)
    journal.begin(binding)
    journal.dispatch(binding)
    incarnation = current.identity.incarnation
    journal.reconcile(binding, {
        "operation_id": str(binding.operation_id), "binding_sha256": binding.digest(),
        "incarnation": incarnation,
    }, incarnation)

    if damage == "owner":
        after = replace(after, owner_id=uuid4())
    if damage == "source":
        source = "0" * 64
    if damage == "volume":
        volume_identity = "0" * 64
    if damage == "image":
        current = replace(current, image_id=old_root)
    if damage == "inventory":
        current = replace(current, previous_image_inventory_sha256="0" * 64)
    if damage == "release":
        release = uuid4()
    if damage == "archive":
        artifact = artifact.model_copy(update={"sha256": "0" * 64})

    def proof():
        record = verify_release(
            tmp_path, after, run, 5, release, source=source, manifest=manifest,
            volume=volume, volume_identity=volume_identity,
        )
        return finish_proof(tmp_path, after, record, artifact, current, 5)

    if damage is not None:
        with pytest.raises(RecoveryEnvironmentUnproved):
            proof()
        return
    assert proof().model_dump(mode="json") == {
        "version": 1, "workspace_id": str(workspace), "project_id": str(project),
        "owner_id": str(owner), "generation_run_id": str(run),
        "release_operation_id": str(release), "previous_fencing_epoch": 3,
        "release_fencing_epoch": 4, "fencing_epoch": 5, "source_sha256": source,
        "manifest_sha256": manifest, "previous_environment_sha256": before.identity.digest,
        "environment_sha256": current.identity.digest, "artifact_sha256": artifact.sha256,
    }


@pytest.mark.parametrize(
    "strict, damage",
    [(False, change) for change in (None, "container", "archive", "mount", "started_at", "request")]
    + [
        (True, change)
        for change in (
            None, "archive", "request", "expectation", "removed", "renamed",
            "fresh", "stop_unproved", "interrupted", "archive_sync",
            "intent_missing", "intent_volume", "database_running", "database_replaced",
        )
    ],
)
async def test_release_capture_precedes_removal_and_replays(tmp_path, monkeypatch, strict, damage):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import docker

    from yleum_orchestrator.routers import workspace as routes
    from yleum_orchestrator.routers.runtime import _workspace_revision
    from yleum_orchestrator.services import (
        active_source_volume,
        machine_adapter,
        machine_recovery_environment,
    )
    from yleum_orchestrator.services.machine_defaults import next_machine_manifest
    from yleum_orchestrator.services.machine_recovery_environment import _intent_path, _record_path
    from yleum_orchestrator.services.machine_startup import MachineInventoryUnavailable
    from yleum_orchestrator.services.published_machine_backend import PublishedMachineBackend

    workspace, project, owner, run, operation = (uuid4() for _ in range(5))
    names = CellResourceNames.for_workspace(workspace)
    volume = names.workspace_volume
    release = LifecycleMutation(operation, 4, "a" * 64)
    files = {"src/app/page.tsx": "exact before crash"}
    blocked_replay = damage in {"intent_missing", "intent_volume", "database_replaced"}
    interrupted = blocked_replay or damage in {"interrupted", "archive_sync", "database_running"}
    fresh = interrupted or damage in {"fresh", "stop_unproved"}
    effective_manifest = next_machine_manifest()
    source = _workspace_revision(files)
    manifest = effective_manifest.digest() if fresh else "b" * 64
    volume_identity = "c" * 64
    database_volume = "owned-project-postgres"
    container_id, image = "d" * 64, "sha256:" + "e" * 64
    started = "2026-11-15T00:00:00Z"
    rootfs, workspace_data = b"captured rootfs", b"captured source"
    volume_refs = [VolumeEnvironmentRef(
        name=volume, artifact_ref="2" * 32 + ".tar",
        sha256=hashlib.sha256(workspace_data).hexdigest(), size=len(workspace_data),
    )]
    if fresh:
        volume_refs.append(VolumeEnvironmentRef(
            name=database_volume, artifact_ref="3" * 32 + ".tar",
            sha256=hashlib.sha256(workspace_data).hexdigest(), size=len(workspace_data),
        ))
    archived = MachineEnvironmentRef(
        workspace_id=workspace, image_id="sha256:" + "f" * 64, artifact_ref="1" * 32 + ".tar",
        sha256=hashlib.sha256(rootfs).hexdigest(), size=len(rootfs),
        base_image="synthetic", manifest_digest=manifest,
        volumes=tuple(volume_refs),
    )
    archive_root = tmp_path / "artifacts"
    archive_root.mkdir(mode=0o700)
    archives = archive_root / str(workspace)
    archives.mkdir(mode=0o700)
    (archives / archived.artifact_ref).write_bytes(rootfs[::-1] if damage == "archive" else rootfs)
    for volume_ref in volume_refs:
        (archives / volume_ref.artifact_ref).write_bytes(workspace_data)
    state = CellWorkspaceState(
        workspace, project, owner, "docker-owner-cell-resources-v1",
        "completed", "resources_ready", 3, run, 3, None, "cell", names, (),
    )
    before = RunningEnvironment(
        image, EnvironmentIdentity("3" * 64, "4" * 64), container_id, started,
    )
    journal_path = _record_path(tmp_path, workspace, operation)
    original_receipt = None
    if not fresh:
        record_release(
            tmp_path, state, release, before=before, source=source, volume=volume,
            volume_identity=volume_identity, reference=archived,
        )
        original_receipt = journal_path.read_bytes()
    if damage == "request":
        release = LifecycleMutation(operation, 4, "8" * 64)
    expectation = ReleaseEnvironmentExpectation(
        source_sha256=source,
        environment_sha256="4" * 64 if damage == "expectation" else before.identity.digest,
    ) if strict else None
    if damage == "started_at":
        started = "2026-11-15T00:00:01Z"
    labels = {"omnia.workspace_id": str(workspace), "omnia.fencing_epoch": "3"}
    volumes = [{"Type": "volume", "Name": volume, "Destination": "/workspace"}]
    actual_id = "8" * 64 if damage == "container" else container_id
    if damage == "mount":
        volumes[0]["Name"] = "foreign"
    stopped = SimpleNamespace(
        id=actual_id, status="exited", reload=Mock(), attrs={
            "Id": actual_id, "Image": image, "Config": {"Labels": labels},
            "State": {"Status": "exited", "Running": False, "Paused": False, "StartedAt": started},
            "Mounts": volumes,
        },
    )
    metadata = {
        "environment_ref": archived.model_dump(mode="json"),
        "environment_revision": source, "restored_image": archived.image_id,
        "manifest": effective_manifest.model_dump(mode="json"),
    }
    database = SimpleNamespace(id="a" * 64, reload=Mock(), stop=Mock(), attrs={
        "Id": "a" * 64, "Image": "sha256:" + "1" * 64,
        "Config": {"Labels": labels},
        "Mounts": [{
            "Name": database_volume, "Type": "volume", "RW": True,
            "Destination": "/var/lib/postgresql/data",
        }],
        "State": {
            "Status": "running", "Running": True, "Paused": False, "StartedAt": started,
        },
    })
    database.stop.side_effect = lambda **_: database.attrs["State"].update(
        Status="exited", Running=False,
    )
    backend = SimpleNamespace(
        _container=lambda: None if damage in {"removed", "renamed"} else stopped,
        _project_postgres=lambda: database, project_postgres_volume=database_volume,
        _metadata=lambda: metadata, stem="synthetic",
        workspace_volume=volume, base_image="synthetic", disk_bytes=16384,
        client=SimpleNamespace(containers=SimpleNamespace(get=Mock(
            return_value=stopped if damage == "renamed" else None,
            side_effect=docker.errors.NotFound("removed") if damage == "removed" else None,
        ))),
        _lookup=lambda *_: None,
        labels=lambda _: labels, invalidate_retained_preview=Mock(),
        prepare_capture=Mock(), _require_recovery_access=Mock(),
        _reconcile_recovery_helpers=Mock(), remove=Mock(), record_retained_preview=Mock(
            return_value=True,
        ),
    )
    runtime = machine_adapter.MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state")), SimpleNamespace(),
    )
    monkeypatch.setattr(machine_adapter.MachineAdapter, "root", property(lambda _: tmp_path))
    runtime.exists = lambda _: True
    runtime.recovery_required = lambda _: False
    runtime.parts = lambda _: (SimpleNamespace(state=lambda: {
        "epoch": 3, "manifest": effective_manifest.model_dump(mode="json"),
    }), backend)
    runtime.checkpoint = AsyncMock(return_value=None)
    monkeypatch.setattr(routes, "_read_agent_workspace_files", AsyncMock(return_value=files))
    monkeypatch.setattr(routes, "_machine_manifest", lambda *_args, **_kw: SimpleNamespace(
        digest=lambda: manifest,
    ))
    monkeypatch.setattr(active_source_volume, "source_volume_call", AsyncMock(
        return_value=volume_identity,
    ))
    if fresh:
        docker_state = stopped.attrs["State"]
        docker_state.update(Status="running", Running=True)
        runtime._seal_identity = AsyncMock(return_value={
            "revision": source, "schema_digest": "1" * 64,
        })
        monkeypatch.setattr(PublishedMachineBackend, "schema_digest", lambda _: "1" * 64)

        def observe_running(*_):
            if docker_state["Status"] != "running":
                raise MachineInventoryUnavailable("unavailable")
            return before

        monkeypatch.setattr(machine_adapter, "running_environment", observe_running)

        async def checkpoint(*_, release_intent):
            assert release_intent.source_sha256 == source
            assert _intent_path(tmp_path, workspace, operation).exists()
            assert not journal_path.exists()
            docker_state.update(Status="exited", Running=False)
            if damage not in {"database_running", "database_replaced"}:
                database.attrs["State"].update(Status="exited", Running=False)
            if interrupted and damage != "archive_sync" and runtime.checkpoint.await_count == 1:
                raise OSError("capture interrupted after stopping")
            if damage == "stop_unproved":
                docker_state["StartedAt"] = "2026-11-15T00:01:00Z"
            return archived

        def remove():
            assert journal_path.exists()

        runtime.checkpoint = AsyncMock(side_effect=checkpoint)
        backend.remove.side_effect = remove
        if damage == "archive_sync":
            sync = machine_recovery_environment.sync_controller_parent_chain
            reference_path = archives / archived.artifact_ref

            def fail_first_sync(path):
                if path == reference_path and runtime.checkpoint.await_count == 1:
                    raise OSError("archive directory durability unavailable")
                sync(path)

            monkeypatch.setattr(
                machine_recovery_environment, "sync_controller_parent_chain", fail_first_sync,
            )

    if interrupted or damage == "stop_unproved":
        with pytest.raises(RecoveryReleaseRefused, match="generation release environment"):
            await runtime.halt(
                state, generation_release=release, require_environment_proof=expectation,
            )
        assert not journal_path.exists()
        backend.remove.assert_not_called()
        if damage == "stop_unproved":
            return
        if blocked_replay:
            if damage == "intent_missing":
                _intent_path(tmp_path, workspace, operation).unlink()
            elif damage == "intent_volume":
                monkeypatch.setattr(
                    active_source_volume, "source_volume_call", AsyncMock(return_value="9" * 64),
                )
            else:
                database.id = database.attrs["Id"] = "b" * 64
            with pytest.raises(RecoveryReleaseRefused):
                await runtime.halt(
                    state, generation_release=release, require_environment_proof=expectation,
                )
            assert not journal_path.exists()
            backend.remove.assert_not_called()
            database.stop.assert_not_called()
            assert runtime.checkpoint.await_count == 1
            return

    if strict and not fresh and damage not in {None, "removed"}:
        with pytest.raises(RecoveryReleaseRefused) as error:
            await runtime.halt(
                state, generation_release=release, retain_trusted=True,
                require_environment_proof=expectation,
            )
        assert error.value.reason == "recovery_release_capture_unproved"
        assert error.value.pre_effect is False
        runtime.checkpoint.assert_not_awaited()
        backend.remove.assert_not_called()
        assert journal_path.read_bytes() == original_receipt
        return

    await runtime.halt(
        state, generation_release=release, retain_trusted=True,
        require_environment_proof=expectation,
    )

    backend.remove.assert_called_once_with()
    if fresh:
        assert expectation is not None
        require_recorded_release_capture(tmp_path, state, release, run, expectation)
        assert runtime.checkpoint.await_count == (2 if interrupted else 1)
        if damage == "database_running":
            database.stop.assert_called_once_with(timeout=10)
        return
    assert journal_path.read_bytes() == original_receipt
    if damage in {None, "removed"}:
        runtime.checkpoint.assert_not_awaited()
        if damage is None:
            backend.record_retained_preview.assert_called_once_with(
                archived, epoch=3, retain_trusted=True,
            )
        else:
            assert backend.client.containers.get.call_count == 2
    else:
        runtime.checkpoint.assert_awaited_once_with(state)
        backend.record_retained_preview.assert_not_called()
