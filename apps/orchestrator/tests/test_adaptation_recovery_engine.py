"""Actual recovery coordinator against isolated filesystem state; no Docker/DB."""

from types import SimpleNamespace
from uuid import uuid4, uuid5

import pytest

from tests.test_cell_checkpoint import _make_fixture
from tests.test_project_machine_manifest import payload
from tests.test_restoration_adaptation_workspace import _request
from yleum_orchestrator.core.cell_resources import CellIdentityConflict, LifecycleMutation
from yleum_orchestrator.core.project_machine import MachineManifest
from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
from yleum_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationRecoveryHold,
    RestorationAdaptationRecoveryReceipt,
)
from yleum_orchestrator.services.machine_environment import MachineEnvironmentRef
from yleum_orchestrator.services.project_machine import ProjectMachine, write_controller_json


async def _fixture(tmp_path):
    from yleum_orchestrator.services.adaptation_recovery_engine import DockerAdaptationRecovery

    original = _request()
    candidate = uuid5(original.operation_id, f"restoration-adaptation:{original.generation_run_id}")
    managers = []
    for label, wid, epoch in [("source", original.workspace_id, 7), ("candidate", candidate, 1)]:
        manager, *_ = _make_fixture(tmp_path / label)
        spec = WorkspaceSpec(
            workspace_id=wid,
            project_id=original.project_id,
            owner_id=original.owner_id,
            generation_run_id=original.generation_run_id,
            profile_version=manager.profile.profile_version,
        )
        await manager.ensure(spec, LifecycleMutation(uuid4(), epoch, "a" * 64))
        managers.append(manager)
    source, candidate_manager = managers
    manifest = MachineManifest.model_validate(payload())
    reference = MachineEnvironmentRef(
        workspace_id=candidate,
        image_id="sha256:" + "a" * 64,
        artifact_ref="a" * 32 + ".tar",
        sha256="b" * 64,
        size=1,
        base_image="sha256:" + "c" * 64,
        manifest_digest=manifest.digest(),
        manifest=manifest,
        volumes=(),
    )
    physical = {
        "halts": 0,
        "resumes": 0,
        "identity": "1" * 64,
        "proof": {"exact": "quiescent"},
        "running": True,
        "unknown": False,
    }
    metadata = {
        "environment_ref": reference.model_dump(mode="json"),
        "retained_preview_receipt": physical["proof"],
    }
    backend = SimpleNamespace(
        _metadata=lambda: metadata,
        _retained_preview_proof=lambda *args, **kwargs: (
            physical["proof"] if not physical["running"] else None
        ),
        recovery_volume_identity=lambda _: physical["identity"],
        metadata_path=tmp_path / "candidate-metadata.json",
    )
    machine = ProjectMachine(
        candidate_manager.state_store.root.parent / "project-machines",
        candidate,
        backend,
        lease_epoch=lambda: (
            candidate_manager.state_store.load(candidate).active_generation_fencing_epoch
        ),
    )
    write_controller_json(
        machine.path,
        {
            "workspace_id": str(candidate),
            "epoch": 1,
            "ready_epoch": 1,
            "manifest": manifest.model_dump(mode="json"),
            "restoration_adaptation_run_id": str(original.generation_run_id),
            "operations": {},
        },
    )

    async def halt(state):
        physical["halts"] += 1
        assert "adaptation_recovery_barrier" in machine.state()
        physical["running"] = False
        if physical["unknown"]:
            raise RuntimeError("lost halt acknowledgement")

    def resume(manifest, epoch):
        physical["resumes"] += 1
        assert "adaptation_recovery_barrier" in machine.state()
        physical["running"] = True

    backend.ensure = resume
    runtime = SimpleNamespace(parts=lambda _: (machine, backend), halt=halt)
    candidate_manager.machine_runtime = runtime
    recovery = DockerAdaptationRecovery(lambda _: source)
    recovery._candidate_manager = lambda _: candidate_manager
    request = RestorationAdaptationRecoveryHold(
        version=1,
        transition_id=uuid4(),
        workspace_id=original.workspace_id,
        generation_run_id=original.generation_run_id,
        operation_id=original.operation_id,
        project_id=original.project_id,
        owner_id=original.owner_id,
        candidate_workspace_id=candidate,
        request_digest=original.digest(),
        expected_cycle=0,
        control_fencing_epoch=7,
        candidate_fencing_epoch=1,
    )
    pending = RestorationAdaptationRecoveryReceipt(
        **{
            name: getattr(request, name)
            for name in (
                "version",
                "transition_id",
                "workspace_id",
                "generation_run_id",
                "operation_id",
                "project_id",
                "owner_id",
                "candidate_workspace_id",
                "request_digest",
            )
        },
        state="hold_pending",
        cycle=0,
        original_control_fencing_epoch=7,
        current_control_fencing_epoch=7,
        original_candidate_fencing_epoch=1,
        current_candidate_fencing_epoch=1,
        barrier_operation_id=uuid4(),
    )
    return recovery, original, request, pending, machine, physical, candidate_manager


async def test_hold_persists_barrier_before_quiesce_and_replays_durable_terminal(tmp_path):
    recovery, original, request, pending, machine, physical, _manager = await _fixture(tmp_path)
    held = await recovery.hold(original, request, pending)
    assert held.state == "held" and physical["halts"] == 1
    assert await recovery.inspect(original, pending) == held
    assert await recovery.hold(original, request, pending) == held
    assert physical["halts"] == 1
    with pytest.raises(CellIdentityConflict):
        await machine.assert_fence(LifecycleMutation(uuid4(), 1, "d" * 64))
    physical["identity"] = "f" * 64
    with pytest.raises(CellIdentityConflict):
        await recovery.inspect(original, held)


async def test_unknown_halt_is_preserved_never_reexecuted_even_after_restart(tmp_path):
    recovery, original, request, pending, machine, physical, _manager = await _fixture(tmp_path)
    physical["unknown"] = True
    with pytest.raises(RuntimeError):
        await recovery.hold(original, request, pending)
    assert "adaptation_recovery_barrier" in machine.state()
    assert await recovery.inspect(original, pending) == pending
    assert await recovery.hold(original, request, pending) == pending
    assert physical["halts"] == 1


async def test_rebind_preserves_candidate_and_barrier_until_terminal_publish(tmp_path):
    from yleum_orchestrator.schemas.restoration_adaptation import (
        RestorationAdaptationRecoveryRebind,
    )

    recovery, original, request, pending, machine, physical, manager = await _fixture(tmp_path)
    held = await recovery.hold(original, request, pending)
    rebind = RestorationAdaptationRecoveryRebind(
        **{
            key: getattr(request, key)
            for key in (
                "version",
                "workspace_id",
                "generation_run_id",
                "operation_id",
                "project_id",
                "owner_id",
                "candidate_workspace_id",
                "request_digest",
            )
        },
        transition_id=uuid4(),
        expected_cycle=0,
        expected_hold_receipt_digest=held.terminal_receipt_digest,
        new_control_fencing_epoch=9,
    )
    next_pending = pending.model_copy(
        update={
            "transition_id": rebind.transition_id,
            "state": "rebinding",
            "cycle": 1,
            "current_control_fencing_epoch": 9,
            "current_candidate_fencing_epoch": 2,
            "restore_operation_id": uuid5(rebind.transition_id, "candidate-resume"),
        }
    )
    # Source lease change uses the existing lifecycle in this isolated fixture.
    source = recovery._manager(original.workspace_id)
    source_state = source.state_store.load(original.workspace_id)
    await source.ensure(
        source._spec_from_state(source_state, generation_run_id=original.generation_run_id),
        LifecycleMutation(uuid4(), 9, "e" * 64),
    )
    backend = manager.machine_runtime.parts(
        manager.state_store.load(request.candidate_workspace_id)
    )[1]
    backend.consume_retained_preview = lambda *a, **k: True
    backend.recovery_runtime_proof = lambda *a, **k: {"confirmed": "runtime", "epoch": 2}
    resumed = await recovery.rebind(original, rebind, next_pending)
    assert resumed.state == "resumable" and physical["resumes"] == 1
    assert "adaptation_recovery_barrier" in machine.state()
    await recovery.publish(original, resumed)
    assert "adaptation_recovery_barrier" not in machine.state()
    assert await recovery.inspect(original, resumed) == resumed
    assert await recovery.rebind(original, rebind, next_pending) == resumed
    assert physical["resumes"] == 1 and physical["halts"] == 1


@pytest.mark.parametrize("unknown", [False, True])
async def test_terminal_physical_cleanup_barrier_and_unknown_no_repeat(tmp_path, unknown):
    recovery, original, request, pending, machine, physical, _manager = await _fixture(tmp_path)
    held = await recovery.hold(original, request, pending)
    calls = []

    async def remove_candidate(original, candidate, epoch):
        from yleum_orchestrator.services.adaptation_recovery_barrier import (
            require_machine_writers_allowed,
        )

        assert not physical["running"]
        require_machine_writers_allowed(machine.path, action="release")
        with pytest.raises(CellIdentityConflict):
            require_machine_writers_allowed(machine.path, action="ensure")
        calls.append((candidate, epoch))
        if unknown:
            raise RuntimeError("unknown deletion")

    if unknown:
        with pytest.raises(RuntimeError):
            await recovery.cleanup(original, held, remove_candidate)
        with pytest.raises(CellIdentityConflict):
            await recovery.cleanup(original, held, remove_candidate)
    else:
        await recovery.cleanup(original, held, remove_candidate)
        await recovery.cleanup(original, held, remove_candidate)
    assert calls == [(request.candidate_workspace_id, 1)]
    assert machine.state()["adaptation_recovery_barrier"]["phase"] == "cleanup"


@pytest.mark.parametrize("field", ["project_id", "terminal_receipt_digest"])
async def test_cold_status_rejects_tampered_physical_terminal(tmp_path, field):
    recovery, original, request, pending, machine, physical, _manager = await _fixture(tmp_path)
    await recovery.hold(original, request, pending)
    saved = machine.state()
    saved["operations"][str(request.transition_id)]["receipt"][field] = (
        str(uuid4()) if field == "project_id" else "f" * 64
    )
    write_controller_json(machine.path, saved)
    with pytest.raises(CellIdentityConflict):
        await recovery.inspect(original, pending)
    assert physical["halts"] == 1


async def _ready_rebind(tmp_path):
    from yleum_orchestrator.schemas.restoration_adaptation import (
        RestorationAdaptationRecoveryIdentity,
        RestorationAdaptationRecoveryRebind,
    )

    recovery, original, request, pending, machine, physical, manager = await _fixture(tmp_path)
    held = await recovery.hold(original, request, pending)
    source = recovery._manager(original.workspace_id)
    source_state = source.state_store.load(original.workspace_id)
    await source.ensure(
        source._spec_from_state(source_state, generation_run_id=original.generation_run_id),
        LifecycleMutation(uuid4(), 9, "e" * 64),
    )
    rebind = RestorationAdaptationRecoveryRebind(
        **{
            key: getattr(request, key)
            for key in RestorationAdaptationRecoveryIdentity.model_fields
            if key != "transition_id"
        },
        transition_id=uuid4(),
        expected_cycle=0,
        expected_hold_receipt_digest=held.terminal_receipt_digest,
        new_control_fencing_epoch=9,
    )
    next_pending = pending.model_copy(
        update={
            "transition_id": rebind.transition_id,
            "state": "rebinding",
            "cycle": 1,
            "current_control_fencing_epoch": 9,
            "current_candidate_fencing_epoch": 2,
            "restore_operation_id": uuid5(rebind.transition_id, "candidate-resume"),
        }
    )
    backend = manager.machine_runtime.parts(
        manager.state_store.load(request.candidate_workspace_id)
    )[1]
    backend.consume_retained_preview = lambda *a, **k: True
    backend.recovery_runtime_proof = lambda *a, **k: {"runtime": "confirmed", "epoch": 2}
    return recovery, original, rebind, next_pending, machine, physical, manager, backend


async def test_unknown_restore_remains_barred_across_new_coordinator_without_repeat(tmp_path):
    from yleum_orchestrator.services.adaptation_recovery_engine import DockerAdaptationRecovery

    recovery, original, request, pending, machine, physical, manager, backend = await _ready_rebind(
        tmp_path
    )
    real_ensure = backend.ensure

    def lost_ack(*args):
        real_ensure(*args)
        raise RuntimeError("lost restore acknowledgement")

    backend.ensure = lost_ack
    with pytest.raises(RuntimeError, match="lost restore acknowledgement"):
        await recovery.rebind(original, request, pending)
    cold = DockerAdaptationRecovery(recovery._manager)
    cold._candidate_manager = lambda _: manager
    assert await cold.inspect(original, pending) == pending
    assert await cold.rebind(original, request, pending) == pending
    assert physical["resumes"] == 1
    assert machine.state()["adaptation_recovery_barrier"]["phase"] == "rebind"


async def test_source_lease_drift_after_restore_keeps_barrier_and_no_terminal(tmp_path):
    recovery, original, request, pending, machine, physical, _mgr, backend = await _ready_rebind(
        tmp_path
    )
    real_ensure = backend.ensure

    def drift_after_effect(*args):
        real_ensure(*args)
        source = recovery._manager(original.workspace_id)
        current = source.state_store.load(original.workspace_id)
        # Test-only atomic persisted state drift during a physical await, not a fake result flag.
        mutation = LifecycleMutation(uuid4(), 10, "f" * 64)
        source.state_store.begin(
            source._spec_from_state(current, generation_run_id=original.generation_run_id),
            mutation,
            kind="ensure",
            phase="running",
            resource_names=current.resource_names,
        )
        source.state_store.complete(
            original.workspace_id,
            mutation,
            bundle_state=current.bundle_state,
            provider_ref=current.provider_ref,
        )

    backend.ensure = drift_after_effect
    with pytest.raises(CellIdentityConflict, match="owner/run/lease changed"):
        await recovery.rebind(original, request, pending)
    assert physical["resumes"] == 1
    assert machine.state()["operations"][str(request.transition_id)]["receipt"] is None
    assert machine.state()["adaptation_recovery_barrier"]["phase"] == "rebind"


async def test_terminal_postrename_fsync_failure_requires_cold_reseal_without_second_halt(
    tmp_path, monkeypatch
):
    from yleum_orchestrator.services import adaptation_recovery_engine as engine_module
    from yleum_orchestrator.services import source_write_journal

    recovery, original, request, pending, machine, physical, manager = await _fixture(tmp_path)
    real_sync = source_write_journal._sync_directory
    failing = True

    def strict_sync(path):
        record = machine.state().get("operations", {}).get(str(request.transition_id), {})
        if failing and record.get("receipt") is not None:
            raise OSError("injected terminal directory fsync")
        real_sync(path)

    monkeypatch.setattr(source_write_journal, "_sync_directory", strict_sync)
    monkeypatch.setattr(engine_module, "_sync_directory", strict_sync)
    with pytest.raises(OSError, match="terminal directory fsync"):
        await recovery.hold(original, request, pending)
    cold = engine_module.DockerAdaptationRecovery(recovery._manager)
    cold._candidate_manager = lambda _: manager
    with pytest.raises(OSError, match="terminal directory fsync"):
        await cold.inspect(original, pending)
    failing = False
    assert (await cold.inspect(original, pending)).state == "held"
    assert (await cold.hold(original, request, pending)).state == "held"
    assert physical["halts"] == 1


async def test_intent_postrename_failure_never_dispatches_on_cold_retry(tmp_path, monkeypatch):
    from yleum_orchestrator.services import source_write_journal
    from yleum_orchestrator.services.adaptation_recovery_engine import DockerAdaptationRecovery

    recovery, original, request, pending, machine, physical, manager = await _fixture(tmp_path)
    real_sync = source_write_journal._sync_directory

    def fail_intent(path):
        if str(request.transition_id) in machine.state().get("operations", {}):
            raise OSError("injected intent directory fsync")
        real_sync(path)

    monkeypatch.setattr(source_write_journal, "_sync_directory", fail_intent)
    with pytest.raises(OSError, match="intent directory fsync"):
        await recovery.hold(original, request, pending)
    monkeypatch.setattr(source_write_journal, "_sync_directory", real_sync)
    cold = DockerAdaptationRecovery(recovery._manager)
    cold._candidate_manager = lambda _: manager
    assert await cold.inspect(original, pending) == pending
    assert await cold.hold(original, request, pending) == pending
    assert physical["halts"] == 0
    assert "adaptation_recovery_barrier" in machine.state()
