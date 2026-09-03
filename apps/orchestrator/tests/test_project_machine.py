import importlib
import importlib.util
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellFenceRejected, LifecycleMutation
from omnia_orchestrator.core.project_machine import MachineManifest
from tests.test_project_machine_manifest import payload


def module():
    name = "omnia_orchestrator.services.project_machine"
    assert importlib.util.find_spec(name) is not None, "persistent machine controller is missing"
    return importlib.import_module(name)


class MachineBackend:
    def __init__(self):
        self.ready = True
        self.running = False
        self.commands = []
        self.epoch = None
        self.result = {"running": False, "exit_code": 0, "output": "installed"}

    def ensure(self, manifest, epoch):
        if not self.ready:
            raise RuntimeError("namespace fence unavailable")
        self.running = True
        self.epoch = epoch

    def exec_start(self, argv, cwd, operation_id):
        self.commands.append((argv, cwd))
        return "docker-exec-" + operation_id

    def exec_status(self, exec_id):
        return self.result

    def remove(self, expected_epoch=None):
        if expected_epoch is not None and self.epoch != expected_epoch:
            return
        self.running = False

    def is_running(self):
        return self.running


def fixture(tmp_path):
    backend = MachineBackend()
    lease = {"epoch": 7}
    machine = module().ProjectMachine(
        tmp_path, uuid4(), backend, lease_epoch=lambda: lease["epoch"]
    )
    mutation = LifecycleMutation(uuid4(), 7, "a" * 64)
    return machine, backend, lease, mutation


async def test_command_replay_returns_result_without_repeating_side_effect(tmp_path):
    machine, backend, _lease, mutation = fixture(tmp_path)
    await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    operation = await machine.exec_start(["pip", "install", "flask"], ".", mutation)
    result = await machine.exec_status(operation, mutation)
    assert result.exit_code == 0
    assert result.output == "installed"
    assert await machine.exec_start(["pip", "install", "flask"], ".", mutation) == operation
    assert len(backend.commands) == 1


async def test_failed_new_epoch_ensure_never_admits_commands_to_previous_physical_machine(tmp_path):
    machine, backend, lease, mutation = fixture(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    await machine.ensure(manifest, mutation)
    backend.ready = False
    lease["epoch"] = 8
    next_mutation = LifecycleMutation(uuid4(), 8, "b" * 64)
    with pytest.raises(RuntimeError, match="namespace fence"):
        await machine.ensure(manifest, next_mutation)
    with pytest.raises(CellFenceRejected, match="not ready"):
        await machine.exec_start(["touch", "wrong-epoch"], ".", next_mutation)
    assert backend.commands == []
    assert backend.epoch == 7


async def test_reused_operation_with_different_command_is_rejected(tmp_path):
    machine, backend, _lease, mutation = fixture(tmp_path)
    await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    await machine.exec_start(["one"], ".", mutation)
    with pytest.raises(CellFenceRejected):
        await machine.exec_start(["two"], ".", mutation)
    assert len(backend.commands) == 1


async def test_old_lease_cannot_continue_or_leave_old_machine_alive(tmp_path):
    machine, backend, lease, mutation = fixture(tmp_path)
    await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    lease["epoch"] = 8
    with pytest.raises(CellFenceRejected):
        await machine.exec_start(["touch", "stale"], ".", mutation)
    assert not backend.running
    assert not backend.commands


async def test_cancel_removes_container_and_survives_controller_recreation(tmp_path):
    machine, backend, lease, mutation = fixture(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    await machine.ensure(manifest, mutation)
    await machine.cancel(mutation)
    assert not backend.running
    restored = module().ProjectMachine(
        tmp_path, machine.workspace_id, backend, lease_epoch=lambda: lease["epoch"]
    )
    with pytest.raises(CellFenceRejected):
        await restored.ensure(manifest, mutation)


async def test_transport_loss_preserves_command_and_recovery_does_not_retry_unknown(tmp_path):
    machine, backend, lease, mutation = fixture(tmp_path)
    await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    backend.result = {"running": True, "exit_code": None, "output": "in progress"}
    operation = await machine.exec_start(["long", "install"], ".", mutation)
    restored = module().ProjectMachine(
        tmp_path, machine.workspace_id, backend, lease_epoch=lambda: lease["epoch"]
    )
    result = await restored.exec_status(operation, mutation)
    assert result.state == "running"
    assert await restored.exec_start(["long", "install"], ".", mutation) == operation
    assert len(backend.commands) == 1


async def test_machine_does_not_start_without_network_boundary(tmp_path):
    machine, backend, _lease, mutation = fixture(tmp_path)
    backend.ready = False
    with pytest.raises(RuntimeError, match="namespace fence"):
        await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    assert not backend.running


async def test_failed_cancel_teardown_can_be_retried_without_reopening_fence(tmp_path):
    machine, backend, _lease, mutation = fixture(tmp_path)
    await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    original = backend.remove
    attempts = []

    def fails_once(**kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("Docker temporarily unavailable")
        original(**kwargs)

    backend.remove = fails_once
    with pytest.raises(RuntimeError, match="Docker temporarily"):
        await machine.cancel(mutation)
    await machine.cancel(mutation)
    assert not backend.running
    with pytest.raises(CellFenceRejected):
        await machine.exec_start(["touch", "after-cancel"], ".", mutation)


async def test_stale_journal_cannot_remove_newer_physical_machine(tmp_path):
    machine, backend, lease, mutation = fixture(tmp_path)
    await machine.ensure(MachineManifest.model_validate(payload()), mutation)
    lease["epoch"] = 8
    backend.epoch = 8  # Docker create succeeded before controller final journal write.
    with pytest.raises(CellFenceRejected):
        await machine.exec_start(["stale"], ".", mutation)
    assert backend.running
    assert backend.epoch == 8


async def test_cancelled_transport_drains_docker_create_before_releasing_lock(tmp_path):
    import asyncio
    import threading

    machine, backend, _lease, mutation = fixture(tmp_path)
    entered = threading.Event()
    finish = threading.Event()
    lock = asyncio.Lock()
    original = backend.ensure

    def blocked_ensure(manifest, epoch):
        entered.set()
        finish.wait(5)
        original(manifest, epoch)

    backend.ensure = blocked_ensure

    async def request():
        async with lock:
            await machine.ensure(MachineManifest.model_validate(payload()), mutation)

    task = asyncio.create_task(request())
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.05)
    try:
        assert lock.locked(), "lock released while Docker create is still running"
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_effect_rejects_late_success_and_no_new_mutation_after_budget(monkeypatch):
    from types import SimpleNamespace

    api = module()
    clock = [0]
    events = []
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def slow_effect():
        clock[0] = 2
        events.append("finished")
        return "too late"

    with api.machine_budget(1):
        with pytest.raises(TimeoutError, match="budget"):
            await api.machine_effect(slow_effect)
        with pytest.raises(TimeoutError, match="budget"):
            await api.machine_effect(lambda: events.append("must not start"))
    assert events == ["finished"]
