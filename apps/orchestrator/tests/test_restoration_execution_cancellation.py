"""Task5 Phase B: cancellation owns an exact immutable candidate attempt."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID, uuid5

import pytest

from tests.test_code_restorations import Engine, cancel_request, request, service, status


def candidate_container(value, *, container_id="a" * 64, created="2026-09-20T00:00:00Z"):
    candidate_id = uuid5(value.operation_id, "candidate")
    labels = {
        "omnia.workspace_id": str(candidate_id),
        "omnia.project_id": str(value.project_id),
        "omnia.owner_id": str(value.owner_id),
        "omnia.resource_kind": "development",
        "omnia.fencing_epoch": "1",
    }
    return SimpleNamespace(
        id=container_id,
        attrs={"Id": container_id, "Created": created, "Config": {"Labels": labels}},
    )


def test_attempt_journal_is_durable_identity_only_and_tombstone_blocks_late_exec(tmp_path):
    from yleum_orchestrator.services.restoration_execution import (
        RestorationExecutionCancelled,
        RestorationExecutionJournal,
    )

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    container = candidate_container(value)
    receipt = journal.begin_attempt(
        value,
        container,
        stage="install",
        argv=["pnpm", "install", "--token=private-secret"],
        cwd=".",
    )
    raw = (tmp_path / str(value.operation_id) / "attempt.json").read_text()
    saved = json.loads(raw)
    assert saved == receipt
    assert saved["operation_id"] == str(value.operation_id)
    assert saved["candidate_workspace_id"] == str(uuid5(value.operation_id, "candidate"))
    assert saved["container_id"] == "a" * 64
    assert saved["container_created_at"] == "2026-09-20T00:00:00Z"
    assert saved["stage"] == "install" and len(saved["argv_digest"]) == 64
    assert "private-secret" not in raw and "--token" not in raw

    journal.request_cancel(cancel_request(value))
    with pytest.raises(RestorationExecutionCancelled):
        journal.begin_attempt(
            value,
            candidate_container(value, container_id="b" * 64),
            stage="build:0",
            argv=["pnpm", "build"],
            cwd=".",
        )


class ExactApi:
    def __init__(self, attrs):
        self.attrs = attrs
        self.killed = []

    def inspect_container(self, container_id):
        assert container_id == self.attrs["Id"]
        return self.attrs

    def kill(self, container_id, signal="KILL"):
        self.killed.append((container_id, signal))
        self.attrs = {
            **self.attrs,
            "State": {"Running": False, "Status": "exited"},
        }


async def test_restart_kills_only_exact_candidate_id_and_rejects_reused_identity(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    first = RestorationExecutionJournal(tmp_path)
    container = candidate_container(value)
    first.begin_attempt(value, container, stage="install", argv=["pnpm", "install"], cwd=".")
    first.request_cancel(cancel_request(value))
    attrs = {
        **container.attrs,
        "State": {"Running": True, "Status": "running"},
    }
    api = ExactApi(attrs)

    restarted = RestorationExecutionJournal(tmp_path)
    assert await restarted.stop_active(
        cancel_request(value), api_factory=lambda: api, timeout_seconds=0.5
    )
    assert api.killed == [("a" * 64, "KILL")]
    assert json.loads(
        (tmp_path / str(value.operation_id) / "attempt.json").read_text()
    )["state"] == "stopped"
    assert await restarted.stop_active(
        cancel_request(value), api_factory=lambda: api, timeout_seconds=0.5
    )
    assert api.killed == [("a" * 64, "KILL")]

    other_root = tmp_path / "reused"
    reused = RestorationExecutionJournal(other_root)
    reused.begin_attempt(value, container, stage="install", argv=["pnpm", "install"], cwd=".")
    reused.request_cancel(cancel_request(value))
    wrong = ExactApi({**attrs, "Created": "2026-09-21T00:00:00Z"})
    with pytest.raises(RuntimeError, match="identity"):
        await reused.stop_active(
            cancel_request(value), api_factory=lambda: wrong, timeout_seconds=0.5
        )
    assert wrong.killed == []


async def test_empty_database_migration_attempt_is_exactly_cancellable(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    container = candidate_container(value)
    journal = RestorationExecutionJournal(tmp_path)
    journal.begin_attempt(
        value,
        container,
        stage="empty-database-migrations",
        argv=[
            "pnpm",
            "exec",
            "drizzle-kit",
            "push",
            "--config=drizzle.config.ts",
            "--force",
        ],
        cwd=".",
    )
    journal.request_cancel(cancel_request(value))
    api = ExactApi({**container.attrs, "State": {"Running": True}})

    assert await journal.stop_active(
        cancel_request(value), api_factory=lambda: api, timeout_seconds=0.5
    )
    assert api.killed == [("a" * 64, "KILL")]


async def test_tampered_receipt_cannot_authorize_killing_live_container(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    container = candidate_container(value)
    journal.begin_attempt(value, container, stage="install", argv=["pnpm", "install"], cwd=".")
    journal.request_cancel(cancel_request(value))
    path = tmp_path / str(value.operation_id) / "attempt.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["expected_labels"]["omnia.workspace_id"] = str(value.workspace_id)
    path.write_text(json.dumps(saved), encoding="utf-8")
    live_attrs = {
        **container.attrs,
        "Config": {"Labels": saved["expected_labels"]},
        "State": {"Running": True},
    }
    api = ExactApi(live_attrs)

    with pytest.raises(RuntimeError, match="identity"):
        await journal.stop_active(
            cancel_request(value), api_factory=lambda: api, timeout_seconds=0.5
        )
    assert api.killed == []


async def test_invalid_attempt_state_fails_closed(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    journal.begin_attempt(
        value,
        candidate_container(value),
        stage="install",
        argv=["pnpm", "install"],
        cwd=".",
    )
    journal.request_cancel(cancel_request(value))
    path = tmp_path / str(value.operation_id) / "attempt.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["state"] = "garbage"
    path.write_text(json.dumps(saved), encoding="utf-8")

    with pytest.raises(RuntimeError, match="identity"):
        await journal.stop_active(
            cancel_request(value),
            api_factory=lambda: pytest.fail("invalid receipt must not reach Docker"),
            timeout_seconds=0.5,
        )


async def test_producer_receipt_blocks_terminal_cancel_until_producer_stops(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def provision():
        async with journal.producer(value, stage="provisioning"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(provision())
    await asyncio.wait_for(entered.wait(), 0.5)
    journal.request_cancel(cancel_request(value))
    with pytest.raises(TimeoutError):
        async with journal.no_producers(cancel_request(value), timeout_seconds=0.05):
            pytest.fail("active producer lease must block cancellation completion")
    receipt_path = tmp_path / str(value.operation_id) / "producer.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["state"] == "running"

    release.set()
    await task
    async with journal.no_producers(cancel_request(value), timeout_seconds=0.5):
        pass
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["state"] == "finished"


async def test_cancel_racing_ensure_waits_for_provisioning_producer(tmp_path, monkeypatch):
    from yleum_orchestrator.services import code_restoration_engine as module
    from yleum_orchestrator.services.code_restoration_engine import CodeRestorationEngine
    from yleum_orchestrator.services.restoration_execution import (
        RestorationExecutionCancelled,
    )

    value = request()
    candidate_id = uuid5(value.operation_id, "candidate")
    ensure_started = asyncio.Event()
    ensure_release = asyncio.Event()
    backend_ensure_calls = []

    async def ensure(_spec, _mutation):
        ensure_started.set()
        await ensure_release.wait()

    backend = SimpleNamespace(
        ensure=lambda *_args: backend_ensure_calls.append("ensure"),
    )
    machine = SimpleNamespace(path=tmp_path / "candidate-machine.json")
    state = SimpleNamespace(workspace_id=candidate_id)
    candidate_manager = SimpleNamespace(
        profile=SimpleNamespace(profile_version=1),
        ensure=ensure,
        state_store=SimpleNamespace(load=lambda _workspace: state),
        machine_runtime=SimpleNamespace(parts=lambda _state: (machine, backend)),
    )
    monkeypatch.setattr(
        "yleum_orchestrator.services.cell_publication_capacity.production_manager",
        lambda *_args: candidate_manager,
    )
    monkeypatch.setattr(module, "replace", lambda value, **_changes: value)
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(
        cell_verification_cpu_cores=1.0,
        cell_verification_disk_bytes=1024,
    )
    manifest = SimpleNamespace(model_dump=lambda **_kwargs: {"version": 1})

    task = asyncio.create_task(
        engine._candidate(SimpleNamespace(), value, candidate_id, manifest)
    )
    await asyncio.wait_for(ensure_started.wait(), 0.5)
    journal = engine._execution_journal()
    journal.request_cancel(cancel_request(value))
    with pytest.raises(TimeoutError):
        async with journal.no_producers(cancel_request(value), timeout_seconds=0.05):
            pytest.fail("cancel must not pass an active ensure producer")

    ensure_release.set()
    with pytest.raises(RestorationExecutionCancelled):
        await task
    assert backend_ensure_calls == []
    async with journal.no_producers(cancel_request(value), timeout_seconds=0.5):
        pass


async def test_exec_uses_the_exact_container_bound_in_attempt_receipt(tmp_path):
    from yleum_orchestrator.services.code_restoration_engine import CodeRestorationEngine

    value = request()

    class Container:
        def __init__(self, container_id):
            bound = candidate_container(value, container_id=container_id)
            self.id, self.attrs = bound.id, bound.attrs
            self.calls = []

        def exec_run(self, argv, **kwargs):
            self.calls.append((argv, kwargs))
            return SimpleNamespace(exit_code=0, output=b"")

    first, replacement = Container("a" * 64), Container("b" * 64)
    lookups = iter((first, replacement))
    backend = SimpleNamespace(
        _container=lambda: next(lookups),
        root=tmp_path,
        workspace_id=uuid5(value.operation_id, "candidate"),
    )
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path

    await engine._run_stage(
        value,
        backend,
        ["pnpm", "install"],
        10,
        stage="install",
    )

    assert len(first.calls) == 1
    assert replacement.calls == []
    assert next(lookups) is replacement
    with pytest.raises(StopIteration):
        next(lookups)


async def test_transport_error_after_exec_admission_keeps_attempt_unknown(tmp_path):
    from yleum_orchestrator.services.code_restoration_engine import CodeRestorationEngine

    value = request()
    container = candidate_container(value)

    def failed_exec(*_args, **_kwargs):
        raise OSError("transport lost after start")

    container.exec_run = failed_exec
    backend = SimpleNamespace(
        _container=lambda: container,
        root=tmp_path,
        workspace_id=uuid5(value.operation_id, "candidate"),
    )
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path

    with pytest.raises(OSError, match="transport lost"):
        await engine._run_stage(
            value,
            backend,
            ["pnpm", "install"],
            10,
            stage="install",
        )
    receipt = json.loads(
        (tmp_path / str(value.operation_id) / "attempt.json").read_text(encoding="utf-8")
    )
    assert receipt["state"] == "unknown"


async def test_daemon_timeout_stays_pending_and_does_not_use_default_pool(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    container = candidate_container(value)
    journal.begin_attempt(value, container, stage="install", argv=["pnpm", "install"], cwd=".")
    journal.request_cancel(cancel_request(value))

    class HungApi:
        def inspect_container(self, _container_id):
            time.sleep(1)
            return container.attrs

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await journal.stop_active(
            cancel_request(value), api_factory=HungApi, timeout_seconds=0.05
        )
    assert time.monotonic() - started < 0.5
    assert json.loads(
        (tmp_path / str(value.operation_id) / "attempt.json").read_text()
    )["state"] == "running"


async def test_repeated_timeouts_are_deduped_so_fifth_kill_can_run(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    journal = RestorationExecutionJournal(tmp_path)
    first = request()
    first_container = candidate_container(first)
    journal.begin_attempt(
        first, first_container, stage="install", argv=["pnpm", "install"], cwd="."
    )
    journal.request_cancel(cancel_request(first))
    release = threading.Event()
    calls = 0

    class HungApi:
        def inspect_container(self, _container_id):
            nonlocal calls
            calls += 1
            release.wait(1)
            return {**first_container.attrs, "State": {"Running": False}}

    try:
        for _ in range(4):
            with pytest.raises(TimeoutError):
                await journal.stop_active(
                    cancel_request(first), api_factory=HungApi, timeout_seconds=0.02
                )
        assert calls == 1

        second = request(operation_id=UUID(int=11), workspace_id=UUID(int=12))
        second_container = candidate_container(second, container_id="b" * 64)
        journal.begin_attempt(
            second,
            second_container,
            stage="install",
            argv=["pnpm", "install"],
            cwd=".",
        )
        journal.request_cancel(cancel_request(second))
        quick = ExactApi({**second_container.attrs, "State": {"Running": True}})
        assert await journal.stop_active(
            cancel_request(second), api_factory=lambda: quick, timeout_seconds=0.3
        )
        assert quick.killed == [("b" * 64, "KILL")]
    finally:
        release.set()
        await asyncio.sleep(0.05)


def test_dedicated_docker_client_has_short_transport_timeout(monkeypatch):
    from yleum_orchestrator.services import restoration_execution as module

    calls = []
    api = object()
    monkeypatch.setattr(
        module.docker,
        "APIClient",
        lambda **kwargs: calls.append(kwargs) or api,
    )
    factory = module.dedicated_docker_api_factory(
        "unix:///var/run/docker.sock",
        transport_timeout_seconds=0.75,
    )

    assert factory() is api
    assert calls == [
        {
            "base_url": "unix:///var/run/docker.sock",
            "timeout": 0.75,
        }
    ]


async def test_full_engine_cancel_kills_before_saturated_default_pool_recovers(
    tmp_path, monkeypatch
):
    from yleum_orchestrator.services import code_restoration_engine as module
    from yleum_orchestrator.services.code_restoration_engine import CodeRestorationEngine

    value = request()
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace()
    manager = SimpleNamespace(docker=SimpleNamespace(docker_host="unix:///docker.sock"))
    engine._manager = lambda _workspace: manager

    async def cleanup(*_args):
        return None

    engine._cleanup_candidate = cleanup
    journal = engine._execution_journal()
    container = candidate_container(value)
    journal.begin_attempt(
        value,
        container,
        stage="install",
        argv=["pnpm", "install"],
        cwd=".",
    )
    loop = asyncio.get_running_loop()
    killed = asyncio.Event()

    class SignallingApi(ExactApi):
        def kill(self, container_id, signal="KILL"):
            super().kill(container_id, signal=signal)
            loop.call_soon_threadsafe(killed.set)

    api = SignallingApi({**container.attrs, "State": {"Running": True}})
    monkeypatch.setattr(
        module,
        "dedicated_docker_api_factory",
        lambda *_args, **_kwargs: lambda: api,
    )

    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    occupied = loop.run_in_executor(None, release.wait)
    cancel_task = asyncio.create_task(engine.cancel(cancel_request(value), None))

    killed_within_sla = True
    try:
        await asyncio.wait_for(killed.wait(), 0.3)
    except TimeoutError:
        killed_within_sla = False
    finally:
        release.set()
        await occupied
        await cancel_task
        executor.shutdown(wait=True)
    assert killed_within_sla
    assert api.killed == [("a" * 64, "KILL")]


async def test_cancel_uses_dedicated_pool_when_default_pool_is_saturated(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    container = candidate_container(value)
    journal.begin_attempt(value, container, stage="install", argv=["pnpm", "install"], cwd=".")
    journal.request_cancel(cancel_request(value))
    api = ExactApi({**container.attrs, "State": {"Running": True}})
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(executor)
    occupied = loop.run_in_executor(None, release.wait)
    try:
        started = time.monotonic()
        assert await journal.stop_active(
            cancel_request(value), api_factory=lambda: api, timeout_seconds=0.5
        )
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        await occupied
        executor.shutdown(wait=True)
    assert api.killed == [("a" * 64, "KILL")]


async def test_docker_unavailable_keeps_attempt_nonterminal(tmp_path):
    from yleum_orchestrator.services.restoration_execution import RestorationExecutionJournal

    value = request()
    journal = RestorationExecutionJournal(tmp_path)
    journal.begin_attempt(
        value,
        candidate_container(value),
        stage="install",
        argv=["pnpm", "install"],
        cwd=".",
    )
    journal.request_cancel(cancel_request(value))

    def unavailable():
        raise OSError("daemon unavailable")

    with pytest.raises(OSError, match="daemon unavailable"):
        await journal.stop_active(
            cancel_request(value), api_factory=unavailable, timeout_seconds=0.5
        )
    assert json.loads(
        (tmp_path / str(value.operation_id) / "attempt.json").read_text()
    )["state"] == "running"


async def test_cancel_worker_bypasses_running_execution_drive(tmp_path):
    class InterruptibleEngine(Engine):
        def __init__(self):
            super().__init__()
            self.gate = asyncio.Event()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def prepare(self, value):
            self.started.set()
            return await super().prepare(value)

        def begin_cancel(self, value):
            self.calls.append("tombstone")

        async def cancel(self, value, prepared):
            self.calls.append("cancel")
            self.cancelled.set()
            self.gate.set()

    engine = InterruptibleEngine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await asyncio.wait_for(engine.started.wait(), 0.5)

    accepted = await asyncio.wait_for(svc.cancel(cancel_request(value)), 0.5)
    assert accepted["state"] == "reconciling"
    assert accepted["phase"] == "reconciling"
    await asyncio.wait_for(engine.cancelled.wait(), 0.5)
    await svc.drain()
    assert (await status(svc, value))["state"] == "cancelled"
    assert engine.calls.index("tombstone") < engine.calls.index("cancel")


async def test_service_does_not_terminalize_while_provisioning_producer_runs(tmp_path):
    from yleum_orchestrator.services.restoration_execution import (
        RestorationExecutionCancelled,
        RestorationExecutionJournal,
    )

    class ProducerEngine(Engine):
        def __init__(self):
            super().__init__()
            self.journal = RestorationExecutionJournal(tmp_path / "execution")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def prepare(self, value):
            async with self.journal.producer(value, stage="provisioning"):
                self.started.set()
                await self.release.wait()
                if self.journal.cancel_requested(value.operation_id):
                    raise RestorationExecutionCancelled("cancelled")
                return await super().prepare(value)

        def begin_cancel(self, value):
            self.journal.request_cancel(value)

        async def cancel(self, value, prepared):
            async with self.journal.no_producers(value, timeout_seconds=0.05):
                self.journal.complete_cancel(value)

    engine = ProducerEngine()
    svc = service(tmp_path / "service", engine)
    value = request()
    await svc.prepare(value)
    await engine.started.wait()
    assert (await svc.cancel(cancel_request(value)))["state"] == "reconciling"
    await asyncio.sleep(0.1)
    assert (await status(svc, value))["state"] == "reconciling"

    engine.release.set()
    await svc.drain()
    assert (await status(svc, value))["state"] == "cancelled"


async def test_late_prepare_result_cannot_overwrite_cancelled_terminal_state(tmp_path):
    class LateEngine(Engine):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def prepare(self, value):
            self.started.set()
            await self.release.wait()
            return await super().prepare(value)

        def begin_cancel(self, _value):
            return None

        async def cancel(self, value, prepared):
            await super().cancel(value, prepared)
            self.cancelled.set()

    engine = LateEngine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await engine.started.wait()
    assert (await svc.cancel(cancel_request(value)))["state"] == "reconciling"
    await engine.cancelled.wait()
    await asyncio.sleep(0)
    assert (await status(svc, value))["state"] == "cancelled"

    engine.release.set()
    await svc.drain()
    saved = svc._read(value.workspace_id, value.operation_id)
    assert saved is not None
    assert saved["state"] == "cancelled"
    assert saved["prepared"] is None
    assert saved["candidate_id"] is None


async def test_late_prepare_exception_cannot_overwrite_cancelled_terminal_state(tmp_path):
    class FailingEngine(Engine):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def prepare(self, _value):
            self.started.set()
            await self.release.wait()
            raise OSError("late preparation transport failure")

    engine = FailingEngine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await engine.started.wait()

    async with svc._lock.hold(value.workspace_id):
        engine.release.set()
        # Old code reads the nonterminal journal outside this lock and then
        # waits to write its failure. The CAS implementation waits before read.
        await asyncio.sleep(0.05)
        saved = svc._read(value.workspace_id, value.operation_id)
        assert saved is not None
        saved.update(
            cancel_requested=True,
            state="cancelled",
            phase="cancelled",
            error=None,
        )
        saved["revision"] += 1
        svc._write(saved)

    await svc.drain()
    saved = svc._read(value.workspace_id, value.operation_id)
    assert saved is not None
    assert saved["state"] == "cancelled"
    assert saved["error"] is None


async def test_cancel_is_rejected_after_apply_admission(tmp_path):
    from tests.test_code_restorations import apply_request

    engine = Engine()
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    svc._schedule = lambda *_args: None
    await svc.apply(apply_request(value))

    with pytest.raises(RuntimeError, match="cannot be cancelled"):
        await svc.cancel(cancel_request(value))
    assert (await status(svc, value))["state"] == "applying"
    assert engine.calls == ["prepare"]
