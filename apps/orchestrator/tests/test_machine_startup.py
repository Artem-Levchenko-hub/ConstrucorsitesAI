"""Immutable startup intent: no second physical effect after an uncertain ACK."""

from dataclasses import replace
from uuid import uuid4

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceError


def setup(tmp_path):
    from yleum_orchestrator.services.machine_startup import PrepareBinding, StartupJournal

    binding = PrepareBinding(
        workspace_id=uuid4(),
        project_id=uuid4(),
        owner_id=uuid4(),
        generation_run_id=uuid4(),
        fencing_epoch=7,
        admission_operation_id=uuid4(),
        admission_request_sha256="a" * 64,
        physical_volume="owned-code",
        physical_volume_identity="b" * 64,
        workspace_revision="c" * 64,
        manifest_digest="d" * 64,
    )
    return StartupJournal(tmp_path, binding.workspace_id), binding


def ack(binding, incarnation="e" * 64):
    return {
        "operation_id": str(binding.operation_id),
        "binding_sha256": binding.digest(),
        "incarnation": incarnation,
    }


def test_pending_prepare_has_one_immutable_envelope_and_dispatch(tmp_path):
    journal, binding = setup(tmp_path)
    assert journal.begin(binding).state == "intent"
    assert journal.begin(binding).state == "intent"
    journal.dispatch(binding)
    assert journal.load(binding.operation_id).state == "dispatched"
    with pytest.raises(CellResourceError, match="uncertain"):
        journal.dispatch(binding)
    changed = binding.model_copy(update={"workspace_revision": "f" * 64})
    with pytest.raises(CellResourceError, match="immutable"):
        journal.begin(changed)


def test_crash_after_dispatch_without_exact_ack_never_authorizes_second_ensure(tmp_path):
    journal, binding = setup(tmp_path)
    journal.begin(binding)
    journal.dispatch(binding)
    with pytest.raises(CellResourceError, match="uncertain"):
        journal.reconcile(binding, {}, "e" * 64)
    assert journal.load(binding.operation_id).state == "dispatched"


@pytest.mark.parametrize("field", ["operation_id", "binding_sha256", "incarnation"])
def test_old_or_foreign_ensure_ack_cannot_complete_prepare(tmp_path, field):
    journal, binding = setup(tmp_path)
    journal.begin(binding)
    journal.dispatch(binding)
    witness = ack(binding)
    witness[field] = str(uuid4()) if field == "operation_id" else "f" * 64
    with pytest.raises(CellResourceError, match="uncertain"):
        journal.reconcile(binding, witness, "e" * 64)


def test_exact_durable_ack_recovers_lost_reply_and_cold_resume_without_effect(tmp_path):
    from yleum_orchestrator.services.machine_startup import StartupJournal

    journal, binding = setup(tmp_path)
    journal.begin(binding)
    journal.dispatch(binding)
    cold = StartupJournal(tmp_path, binding.workspace_id)
    assert cold.reconcile(binding, ack(binding), "e" * 64).state == "completed"
    assert journal.begin(binding).state == "completed"
    assert journal.reconcile(binding, ack(binding), "e" * 64).state == "completed"
    # Normal later roles replace the controller's last ensure ACK; the completed
    # immutable journal still proves this same physical incarnation on cold read.
    assert cold.reconcile(binding, None, "e" * 64).state == "completed"
    with pytest.raises(CellResourceError, match="incarnation"):
        journal.reconcile(binding, ack(binding), "f" * 64)


def test_prepare_operation_budget_is_run_and_epoch_not_candidate_source(tmp_path):
    _journal, binding = setup(tmp_path)
    edited = binding.model_copy(update={"workspace_revision": "f" * 64})
    assert edited.operation_id == binding.operation_id
    assert edited.digest() != binding.digest()


def test_only_explicit_restoration_can_resolve_unknown_prepare_without_green_ack(tmp_path):
    journal, binding = setup(tmp_path)
    journal.begin(binding)
    journal.dispatch(binding)
    journal.resolve_by_restoration("f" * 64)
    record = journal.load(binding.operation_id)
    assert record.state == "recovered"
    assert record.binding == binding and record.incarnation is None
    assert record.recovery_reference == "f" * 64
    journal.require_clear()
    with pytest.raises(CellResourceError):
        journal.dispatch(binding)
    with pytest.raises(CellResourceError):
        journal.reconcile(binding, ack(binding), "e" * 64)


@pytest.mark.parametrize("state", ["running", "indeterminate", "starting"])
def test_pending_or_unknown_machine_effect_denies_new_startup(state):
    from yleum_orchestrator.services.machine_startup import require_machine_effects_clear

    with pytest.raises(CellResourceError):
        require_machine_effects_clear(
            {
                "operations": {
                    str(uuid4()): {"kind": "request", "state": state, "result": None},
                }
            }
        )


@pytest.mark.parametrize(
    "state,exit_code,timed_out",
    [
        ("failed", 1, False),
        ("timed_out", -1, True),
        ("failed", 0, False),
    ],
)
def test_legacy_request_missing_role_cannot_hide_failed_restoration(state, exit_code, timed_out):
    from yleum_orchestrator.services.machine_startup import require_machine_effects_clear

    operation = str(uuid4())
    with pytest.raises(CellResourceError, match="legacy request"):
        require_machine_effects_clear(
            {
                "operations": {
                    operation: {
                        "kind": "request",
                        "state": state,
                        "result": {
                            "state": "completed",
                            "operation_id": operation,
                            "exit_code": exit_code,
                            "timed_out": timed_out,
                        },
                    }
                }
            }
        )


def test_proven_successful_legacy_request_does_not_block_new_admission():
    from yleum_orchestrator.services.machine_startup import require_machine_effects_clear

    operation = str(uuid4())
    require_machine_effects_clear(
        {
            "operations": {
                operation: {
                    "kind": "request",
                    "state": "completed",
                    "result": {
                        "state": "completed",
                        "operation_id": operation,
                        "exit_code": 0,
                        "timed_out": False,
                    },
                }
            }
        }
    )


def test_admission_is_original_ensure_not_mutable_source_or_current_epoch(tmp_path):
    from tests.test_cell_state import _mutation, _spec
    from yleum_orchestrator.core.cell_resources import CellResourceNames
    from yleum_orchestrator.services.cell_state import CellStateStore
    from yleum_orchestrator.services.machine_startup import require_startup_admission

    store = CellStateStore(tmp_path / "cells")
    spec = _spec(uuid4())
    first = _mutation("a", 1)
    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    store.begin(
        spec, first, kind="ensure", phase="planned", resource_names=names, startup_contract=True
    )
    state = store.complete(spec.workspace_id, first, bundle_state="resources_ready")
    assert (
        require_startup_admission(state, spec.generation_run_id, 1).operation_id
        == first.operation_id
    )
    store._persist_state(
        replace(state, active_generation_run_id=None, active_generation_fencing_epoch=None)
    )
    later = _mutation("b", 2)
    store.begin(spec, later, kind="ensure", phase="planned", resource_names=names)
    state = store.complete(spec.workspace_id, later, bundle_state="resources_ready")
    assert (
        require_startup_admission(state, spec.generation_run_id, 2).operation_id
        == first.operation_id
    )
    assert state.operations[1].startup_contract_version is None


def adapter_setup(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.test_cell_state import _mutation, _spec
    from tests.test_project_machine_manifest import payload
    from yleum_orchestrator.core.cell_resources import CellResourceNames
    from yleum_orchestrator.core.project_machine import MachineManifest
    from yleum_orchestrator.services.cell_state import CellStateStore
    from yleum_orchestrator.services.machine_adapter import MachineAdapter
    from yleum_orchestrator.services.machine_startup import (
        MachineInventoryUnavailable,
        PrepareBinding,
    )
    from yleum_orchestrator.services.project_machine import ProjectMachine

    store = CellStateStore(tmp_path / "cells")
    spec, original = _spec(uuid4()), _mutation("a", 7)
    names = CellResourceNames.for_workspace(spec.workspace_id, namespace="test")
    store.begin(
        spec, original, kind="ensure", phase="planned", resource_names=names, startup_contract=True
    )
    state = store.complete(spec.workspace_id, original, bundle_state="resources_ready")
    manifest = MachineManifest.model_validate(payload())
    binding = PrepareBinding(
        workspace_id=spec.workspace_id,
        project_id=spec.project_id,
        owner_id=spec.owner_id,
        generation_run_id=spec.generation_run_id,
        fencing_epoch=7,
        admission_operation_id=original.operation_id,
        admission_request_sha256=original.request_digest,
        physical_volume=names.workspace_volume,
        physical_volume_identity="b" * 64,
        workspace_revision="c" * 64,
        manifest_digest=manifest.digest(),
    )

    class Backend:
        def __init__(self):
            self.effects, self.running = 0, False

        def ensure(self, *_args):
            self.effects += 1
            self.running = True

        def configure_cache_identity(self, **kwargs):
            assert kwargs["manifest_digest"] == manifest.digest()

        def startup_incarnation(self):
            if not self.running:
                raise MachineInventoryUnavailable("stopped")
            return "e" * 64

        def environment_digest(self):
            self.startup_incarnation()
            return "f" * 64

    backend = Backend()
    adapter = object.__new__(MachineAdapter)
    adapter.manager = SimpleNamespace(state_store=store)
    machine = ProjectMachine(
        adapter.root,
        spec.workspace_id,
        backend,
        lease_epoch=lambda: store.load(spec.workspace_id).active_generation_fencing_epoch,
    )
    adapter.parts = lambda _state: (machine, backend)
    adapter.protect_migration_sources = AsyncMock(return_value={})
    verify = AsyncMock()
    files = {".omnia/cell.json": "local verified source"}
    return SimpleNamespace(
        adapter=adapter,
        state=state,
        manifest=manifest,
        binding=binding,
        verify=verify,
        files=files,
        backend=backend,
        machine=machine,
    )


async def prepare(context):
    return await context.adapter.prepare_environment(
        context.state,
        context.manifest,
        context.binding,
        context.verify,
        files=context.files,
    )


def directory_syncs(monkeypatch, *, after_exists=None):
    import os
    import stat
    from pathlib import Path

    observed = []
    original = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and (after_exists is None or after_exists.exists()):
            observed.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
        return original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    return observed


async def test_new_startup_parent_entries_are_durable_before_ensure(tmp_path, monkeypatch):
    context = adapter_setup(tmp_path)
    journal_root = context.adapter.root / str(context.state.workspace_id) / "startup-prepares"
    observed = directory_syncs(monkeypatch, after_exists=journal_root)
    original = context.backend.ensure
    required = {context.adapter.root, context.adapter.root.parent, context.machine.path.parent}

    def ensure(*args):
        assert required <= set(observed), "new parent entries must precede the physical effect"
        original(*args)

    context.backend.ensure = ensure
    await prepare(context)


@pytest.mark.parametrize("which", ["controller_root", "namespace_parent"])
async def test_parent_sync_failure_blocks_ensure_and_retry_resyncs_existing_intent(
    tmp_path,
    monkeypatch,
    which,
):
    from yleum_orchestrator.services import source_write_journal

    context = adapter_setup(tmp_path)
    failed = context.adapter.root if which == "controller_root" else context.adapter.root.parent
    original = source_write_journal._sync_directory

    def fail_parent(path):
        if path == failed:
            raise OSError("synthetic parent fsync failure")
        original(path)

    monkeypatch.setattr(source_write_journal, "_sync_directory", fail_parent)
    with pytest.raises(OSError, match="parent fsync"):
        await prepare(context)
    assert context.backend.effects == 0
    monkeypatch.setattr(source_write_journal, "_sync_directory", original)
    observed = directory_syncs(monkeypatch)
    await prepare(context)
    assert failed in observed
    assert context.backend.effects == 1


async def test_adapter_prepare_has_one_effect_and_exact_cold_reconciliation(tmp_path):
    from yleum_orchestrator.core.cell_resources import LifecycleMutation

    context = adapter_setup(tmp_path)
    result = await prepare(context)
    assert result == ("e" * 64, "f" * 64)
    assert context.backend.effects == 1
    # A later normal role ensure replaces the last ACK, not the completed journal.
    await context.machine.ensure(context.manifest, LifecycleMutation(uuid4(), 7, "1" * 64))
    assert context.machine.state().get("startup_prepare_ack") is None
    assert await prepare(context) == result
    assert context.backend.effects == 2
    context.backend.running = False
    with pytest.raises(CellResourceError):
        await prepare(context)
    assert context.backend.effects == 2


@pytest.mark.parametrize("phase", ["intent", "dispatched", "ack"])
async def test_crash_boundary_never_repeats_unknown_prepare_effect(tmp_path, phase):
    from yleum_orchestrator.services.machine_startup import StartupJournal
    from yleum_orchestrator.services.project_machine import write_controller_json

    context = adapter_setup(tmp_path)
    journal = StartupJournal(context.adapter.root, context.state.workspace_id)
    journal.begin(context.binding)
    if phase != "intent":
        journal.dispatch(context.binding)
    if phase == "ack":
        context.backend.running = True
        context.backend.effects = 1
        write_controller_json(
            context.machine.path,
            {
                "workspace_id": str(context.state.workspace_id),
                "epoch": 7,
                "ready_epoch": 7,
                "operations": {},
                "startup_prepare_ack": ack(context.binding),
            },
        )
    if phase == "dispatched":
        with pytest.raises(CellResourceError):
            await prepare(context)
        assert context.backend.effects == 0
        assert journal.load(context.binding.operation_id).state == "dispatched"
    else:
        assert await prepare(context) == ("e" * 64, "f" * 64)
        assert context.backend.effects == 1


async def test_cancelled_prepare_drains_effect_before_successor_lock_and_has_no_ack(tmp_path):
    import asyncio
    import threading

    from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

    context = adapter_setup(tmp_path)
    locks = WorkspaceOperationLock(tmp_path / "locks")
    started, release = threading.Event(), threading.Event()
    entered = asyncio.Event()
    original = context.backend.ensure

    def blocked(*args):
        started.set()
        assert release.wait(3), "test must release its owned effect"
        original(*args)

    context.backend.ensure = blocked

    async def locked_prepare():
        async with locks.hold(context.state.workspace_id):
            return await prepare(context)

    async def successor():
        async with locks.hold(context.state.workspace_id):
            entered.set()

    task = asyncio.create_task(locked_prepare())
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    next_task = asyncio.create_task(successor())
    try:
        await asyncio.sleep(0.03)
        assert not task.done() and not entered.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await next_task
    assert entered.is_set() and context.backend.effects == 1
    assert context.machine.state().get("startup_prepare_ack") is None
    with pytest.raises(CellResourceError):
        await prepare(context)
    assert context.backend.effects == 1
