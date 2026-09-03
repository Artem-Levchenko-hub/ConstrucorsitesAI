import importlib
import importlib.util
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.schemas.workspace import WorkspaceAgentExecRequest
from tests.test_project_machine_manifest import payload


def module():
    name = "omnia_orchestrator.services.machine_adapter"
    assert importlib.util.find_spec(name) is not None, "portable provider integration is missing"
    return importlib.import_module(name)


async def test_build_executes_manifest_bootstrap_build_and_test_argv_in_order(tmp_path):
    api = module()
    commands = []

    class Machine:
        async def ensure(self, manifest, mutation):
            pass

        async def exec_start(self, argv, cwd, mutation):
            commands.append((argv, cwd))
            return str(mutation.operation_id)

        async def exec_status(self, operation, mutation):
            return SimpleNamespace(state="completed", exit_code=0, output="passed")

    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda state: (Machine(), object())
    value = payload()
    value["tasks"].insert(
        1, {"name": "compile", "role": "build", "argv": ["go", "build", "./..."], "cwd": "backend"}
    )
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:build",
        task_role="build",
    )
    result = await runtime.execute(
        SimpleNamespace(), MachineManifest.model_validate(value), request
    )
    assert result.exit_code == 0
    assert commands == [
        (["sh", "install.sh"], "."),
        (["go", "build", "./..."], "backend"),
        (["python", "-m", "unittest"], "."),
    ]


def test_capabilities_advertise_dedicated_project_postgres():
    capabilities = module().MachineAdapter(SimpleNamespace(), SimpleNamespace()).capabilities()
    assert capabilities["portable_machine"] is True
    assert capabilities["dedicated_postgres"] is True
    assert capabilities["database_url_env"] == "DATABASE_URL"
    assert capabilities["database_admin"] == "full"


async def test_missing_manifest_tests_is_not_a_successful_build(tmp_path):
    api = module()
    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    value = payload()
    value["tasks"] = []
    request = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        fencing_epoch=7,
        expected_revision="a" * 64,
        cmd="omnia:build",
        task_role="build",
    )
    with pytest.raises(ValueError, match="test task"):
        await runtime.execute(SimpleNamespace(), MachineManifest.model_validate(value), request)


async def test_explicit_legacy_restore_clears_marker_only_selection(tmp_path):
    from omnia_orchestrator.services.machine_identity import is_portable_workspace
    from omnia_orchestrator.services.project_machine import write_controller_json

    api = module()
    workspace = uuid4()
    manager = SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "states"))
    runtime = api.MachineAdapter(manager, SimpleNamespace())
    path = runtime.root / str(workspace) / "machine.json"
    write_controller_json(path.parent / "portable.json", {"workspace_id": str(workspace)})
    runtime.parts = lambda state: (
        SimpleNamespace(path=path),
        SimpleNamespace(metadata_path=path.parent / "docker.json"),
    )
    assert is_portable_workspace(manager.state_store.root, workspace)
    await runtime.restore_payload(SimpleNamespace(workspace_id=workspace), None)
    assert not is_portable_workspace(manager.state_store.root, workspace)


async def test_halt_failed_quiesce_preserves_rootfs_without_recapturing(tmp_path):
    from unittest.mock import AsyncMock

    api = module()
    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.exists = lambda workspace: True
    runtime.checkpoint = AsyncMock(side_effect=AssertionError("failed state must not be captured"))
    events = []
    backend = SimpleNamespace(
        _metadata=lambda: {"quiesce_state": "failed"},
        _reconcile_recovery_helpers=lambda: events.append("helpers-confirmed-dead"),
        stop=lambda: events.append("stop-preserve-rootfs"),
        remove=lambda: events.append("remove"),
        _lookup=lambda *args: None,
        stem="owned",
        client=SimpleNamespace(containers=None),
    )
    runtime.parts = lambda state: (None, backend)
    await runtime.halt(SimpleNamespace(workspace_id=uuid4()))
    assert events == ["helpers-confirmed-dead", "stop-preserve-rootfs"]


@pytest.mark.parametrize("budget", [600, 900])
async def test_sequential_install_build_share_one_request_budget(monkeypatch, budget):
    api = module()
    clock = [0]
    started = []
    cancelled = []

    class Machine:
        async def ensure(self, manifest, mutation):
            pass

        async def exec_start(self, argv, cwd, mutation):
            started.append(clock[0])
            return len(started)

        async def exec_status(self, operation, mutation):
            duration = 500 if operation == 1 else 200 if operation == 2 else 0
            complete = clock[0] - started[operation - 1] >= duration
            return SimpleNamespace(
                state="completed" if complete else "running", exit_code=0, output="real work"
            )

        async def cancel(self, mutation):
            cancelled.append(clock[0])

    async def tick(_seconds):
        clock[0] += 100

    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.asyncio, "sleep", tick)
    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())
    runtime.parts = lambda state: (Machine(), object())
    value = payload()
    value["tasks"] = [
        {"name": role, "role": role, "argv": ["sh", role], "timeout_seconds": 900}
        for role in ("bootstrap", "build", "test")
    ]
    result = await runtime.execute(
        SimpleNamespace(),
        MachineManifest.model_validate(value),
        WorkspaceAgentExecRequest(
            generation_run_id=uuid4(),
            fencing_epoch=7,
            expected_revision="a" * 64,
            cmd="omnia:build",
            task_role="build",
            timeout_seconds=budget,
        ),
    )
    if budget == 600:
        assert result.timed_out and result.exit_code == 124
        assert started == [0, 500]
        assert cancelled == [600]
    else:
        assert not result.timed_out and result.exit_code == 0
        assert started == [0, 500, 700]
        assert cancelled == []


async def test_apply_total_timeout_drains_then_cleans_up(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    api = module()
    monkeypatch.setattr(api, "MACHINE_APPLY_TIMEOUT_SECONDS", 0.01, raising=False)
    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())

    async def slow(*args):
        await asyncio.sleep(1)

    runtime._apply = slow
    runtime.halt = AsyncMock()
    result = await runtime.apply(
        SimpleNamespace(), MachineManifest.model_validate(payload()), SimpleNamespace()
    )
    assert result.timed_out and result.exit_code == 124
    runtime.halt.assert_awaited_once()
    assert runtime.halt.await_args.kwargs == {"capture": False}


async def test_apply_shielded_readiness_uses_remaining_budget_and_cleanup_reserve(monkeypatch):
    import http.client

    from omnia_orchestrator.services import project_machine

    api = module()
    clock = [0.0]
    events = []
    fake_time = SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(api, "time", fake_time)
    monkeypatch.setattr(project_machine, "time", fake_time, raising=False)

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            pass

        def getresponse(self):
            return SimpleNamespace(status=503)

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())

    async def late_boundary(*args):
        clock[0] = 860  # Only10s work left;30s is reserved for stopping resources.
        try:
            await project_machine.machine_effect(
                runtime._wait_http,
                SimpleNamespace(status="running", reload=lambda: None),
                "127.0.0.1",
                "/api/health",
                expected=200,
                timeout=120,
            )
        finally:
            events.append("helper-finished")

    async def cleanup(state, *, capture):
        assert not capture
        events.append("cleanup")

    runtime._apply = late_boundary
    runtime.halt = cleanup
    result = await runtime.apply(
        SimpleNamespace(), MachineManifest.model_validate(payload()), SimpleNamespace()
    )
    assert result.exit_code == 124 and result.timed_out
    assert clock[0] == pytest.approx(870)
    assert events == ["helper-finished", "cleanup"]


async def test_apply_timeout_cleans_up_after_shielded_helper_raises(monkeypatch):
    import asyncio
    import threading

    from omnia_orchestrator.core.cell_resources import CellResourceError
    from omnia_orchestrator.services.project_machine import machine_effect

    api = module()
    monkeypatch.setattr(api, "MACHINE_APPLY_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(api, "MACHINE_APPLY_CLEANUP_RESERVE_SECONDS", 0, raising=False)
    release = threading.Event()
    events = []
    runtime = api.MachineAdapter(SimpleNamespace(), SimpleNamespace())

    def failing_helper():
        assert release.wait(2), "test did not release the shielded helper"
        events.append("helper-failed")
        raise CellResourceError("late boundary readiness failure")

    async def work(*args):
        await machine_effect(failing_helper)

    async def cleanup(state, *, capture):
        events.append("cleanup")

    runtime._apply = work
    runtime.halt = cleanup
    task = asyncio.create_task(
        runtime.apply(
            SimpleNamespace(), MachineManifest.model_validate(payload()), SimpleNamespace()
        )
    )
    cancellation_seen = asyncio.Event()
    loop = asyncio.get_running_loop()

    def observe_cancellation():
        if task.cancelling() or task.done():
            cancellation_seen.set()
        else:
            loop.call_later(0.001, observe_cancellation)

    loop.call_soon(observe_cancellation)
    try:
        await asyncio.wait_for(cancellation_seen.wait(), 1)
        assert task.cancelling()
        assert not task.done(), "shielded mutation was detached before cleanup"
    finally:
        release.set()
    result = await task
    assert result.timed_out and result.exit_code == 124
    assert events == ["helper-failed", "cleanup"]


async def test_apply_cleans_all_resources_when_nested_execute_reports_timeout():
    from omnia_orchestrator.services.docker_cell_resources import DockerCommandResult

    runtime = module().MachineAdapter(SimpleNamespace(), SimpleNamespace())
    events = []

    async def timed_out(*args):
        return DockerCommandResult(exit_code=124, output="machine fenced", timed_out=True)

    async def cleanup(state, *, capture):
        events.append(("remove-machine-core-gateway", capture))

    runtime._apply = timed_out
    runtime.halt = cleanup
    result = await runtime.apply(
        SimpleNamespace(), MachineManifest.model_validate(payload()), SimpleNamespace()
    )
    assert result.exit_code == 124 and result.timed_out
    assert events == [("remove-machine-core-gateway", False)]
