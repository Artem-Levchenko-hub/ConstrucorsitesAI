"""Controller standby/physical-dispatch authority, independent of aggregate health."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.parametrize("dotenv_only", [False, True])
@pytest.mark.parametrize("registered", [False, True])
def test_valid_process_settings_never_use_local_legacy_config_to_bypass_readers(
    tmp_path, monkeypatch, dotenv_only, registered,
):
    from yleum_orchestrator.core import config
    from yleum_orchestrator.services import generation_reader_inventory as readers

    missing = tmp_path / "missing-inventory"
    for name in ("DATABASE_URL", "INTERNAL_TOKEN", "GENERATION_RECOVERY_INVENTORY_DIR"):
        monkeypatch.delenv(name, raising=False)
    if dotenv_only:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "DATABASE_URL=postgresql://synthetic:synthetic@127.0.0.1/synthetic\n"
            "INTERNAL_TOKEN=synthetic-controller-unit-token\n"
            f"GENERATION_RECOVERY_INVENTORY_DIR={missing}\n"
        )
        # Actual process Settings loader, bypassing only its inter-test LRU cache.
        getter = config.get_settings.__wrapped__
    else:
        settings = config.Settings(
            _env_file=None,
            database_url="postgresql://synthetic:synthetic@127.0.0.1/synthetic",
            internal_token="synthetic-controller-unit-token",
            generation_recovery_inventory_dir=str(missing),
        )
        def getter():
            return settings
    monkeypatch.setattr(readers, "get_settings", getter)
    monkeypatch.setattr(readers, "_REVOKED", False)
    fixture = ControllerReaderTests()
    fixture.module = readers
    _, _, own = fixture.snapshot()
    monkeypatch.setattr(readers, "_OWN", own if registered else None)
    calls = []
    local_legacy = SimpleNamespace(generation_recovery_inventory_dir="")
    with pytest.raises(readers.ReaderInventoryUnavailable):
        readers.reader_dispatch(lambda local: calls.append(local), local_legacy)
    assert calls == []
    assert getter().generation_recovery_inventory_dir == str(missing)


@pytest.mark.parametrize("kind", ["volume", "network", "container"])
async def test_destructive_dispatch_rechecks_after_lookup(kind, monkeypatch):
    from yleum_orchestrator.services import docker_py_cell_backend as module

    backend = object.__new__(module.DockerPyCellBackend)
    ready = [True]
    calls = []
    resource = SimpleNamespace(remove=lambda **kw: calls.append(kw))

    def guard():
        if not ready[0]:
            raise RuntimeError("inhibited")

    async def lookup(self, name):
        ready[0] = False
        return resource

    monkeypatch.setattr(module, "require_if_configured", guard)
    monkeypatch.setattr(
        module, "reader_dispatch", lambda call, *a, **kw: (guard(), call(*a, **kw))[1]
    )
    monkeypatch.setattr(module.DockerPyCellBackend, "_get_" + kind + "_obj", lookup)
    with pytest.raises(RuntimeError, match="inhibited"):
        await getattr(backend, "remove_" + kind)("owned")
    assert calls == []


async def test_paused_start_cannot_bypass_dispatch_guard(monkeypatch):
    from yleum_orchestrator.services import docker_py_cell_backend as module

    backend = object.__new__(module.DockerPyCellBackend)
    calls = []
    container = SimpleNamespace(status="paused", unpause=lambda: calls.append("unpaused"))
    monkeypatch.setattr(
        module.DockerPyCellBackend, "_require_container_obj", AsyncMock(return_value=container)
    )
    monkeypatch.setattr(module.DockerPyCellBackend, "_container_record", lambda self, value: value)
    monkeypatch.setattr(
        module, "reader_dispatch", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("inhibited"))
    )
    with pytest.raises(RuntimeError, match="inhibited"):
        await backend.start_container("owned")
    assert calls == []


async def test_owned_command_can_quiesce_and_persist_status_under_inhibit(tmp_path, monkeypatch):
    from uuid import uuid4

    from tests.test_docker_machine_backend import backend as create_backend
    from yleum_orchestrator.core.cell_resources import CellFenceRejected, LifecycleMutation
    from yleum_orchestrator.services import docker_machine_backend as module
    from yleum_orchestrator.services.project_machine import ProjectMachine, write_controller_json

    runtime = create_backend(tmp_path)
    operation = str(uuid4())
    calls = []
    running = [True]
    foreign = [False]

    def execute(argv, **kwargs):
        calls.append(argv)
        if "SIGTERM" in argv[4]:
            running[0] = False
        return SimpleNamespace(exit_code=0, output=b"completed")

    def get(name):
        kind = "development" if name == runtime.machine_name else "namespace-guard"
        labels = runtime.labels(kind)
        if foreign[0]:
            labels["omnia.owner_id"] = str(uuid4())
        return SimpleNamespace(id="machine", attrs={"Config": {"Labels": labels}}, exec_run=execute)

    runtime.client = SimpleNamespace(
        containers=SimpleNamespace(get=get),
        api=SimpleNamespace(
            exec_inspect=lambda _: {
                "ContainerID": "machine",
                "Running": running[0],
                "ExitCode": None if running[0] else 143,
            }
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_metadata",
        lambda: {
            "exec_logs": {"owned-exec": "/run/omnia-logs/command-" + operation + ".log"},
            "exec_pids": {"owned-exec": "/run/omnia-logs/command-" + operation + ".pid"},
        },
    )
    monkeypatch.setattr(
        module, "reader_dispatch", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("inhibited"))
    )
    lease = [7]
    machine = ProjectMachine(tmp_path, runtime.workspace_id, runtime, lease_epoch=lambda: lease[0])
    state = machine.state()
    state.update(epoch=7, ready_epoch=7)
    state["operations"][operation] = {"epoch": 7, "exec_id": "owned-exec", "state": "running"}
    write_controller_json(machine.path, state)
    mutation = LifecycleMutation(uuid4(), 7, "a" * 64)
    await machine.exec_terminate(operation, mutation, grace_seconds=1)
    result = await machine.exec_status(operation, mutation)
    assert result.state == "completed" and result.exit_code == 143
    assert machine.state()["operations"][operation]["result"]["exit_code"] == 143
    assert len(calls) == 2
    # Same fixed read/termination API never accepts foreign container identity.
    foreign[0] = True
    with pytest.raises(module.CellIdentityConflict):
        runtime.terminate_exec("owned-exec", 1)
    foreign[0] = False
    with pytest.raises(module.CellIdentityConflict):
        runtime.terminate_exec("unknown-exec", 1)
    with pytest.raises(module.CellIdentityConflict):
        runtime._read_log("/etc/passwd")
    with pytest.raises(CellFenceRejected):
        await machine.exec_terminate(
            operation, LifecycleMutation(uuid4(), 6, "a" * 64), grace_seconds=1
        )
    assert len(calls) == 2


class ControllerReaderTests(unittest.TestCase):
    def setUp(self):
        name = "yleum_orchestrator.services.generation_reader_inventory"
        self.assertIsNotNone(importlib.util.find_spec(name), "controller reader standby is absent")
        self.module = importlib.import_module(name)

    def snapshot(self):
        module = self.module
        rows = []
        for index, slot in enumerate(sorted(module.SLOTS), 1):
            host, role = slot.split("/")
            rows.append(
                {
                    "slot": slot,
                    "host": host,
                    "role": role,
                    "host_boot_id": "11111111-1111-4111-8111-111111111111",
                    "startup_nonce": f"00000000-0000-4000-8000-{index:012d}",
                    "pid_namespace_inode": index,
                    "pid": os.getpid(),
                    "host_pid": index,
                    "start_ticks": index,
                    "container_id": None if role == "orchestrator" else str(index) * 64,
                    "supervisor_id": slot,
                    "release_sha": "a" * 40,
                    "capabilities": list(
                        module.CONTROLLER_CAPABILITIES
                        if role == "orchestrator"
                        else module.API_CAPABILITIES
                    ),
                }
            )
        registrations = {}
        for row in rows:
            identity = {key: row[key] for key in module.ReaderIdentity.model_fields}
            registrations[module.digest(identity)] = {
                "identity": identity,
                "release_sha": row["release_sha"],
                "capabilities": row["capabilities"],
            }
        value = {
            "version": 1,
            "epoch": "22222222-2222-4222-8222-222222222222",
            "state": "ready",
            "release_sha": "a" * 40,
            "redis_run_id": "b" * 40,
            "observed_at_ms": 1000,
            "expires_at_ms": 2000,
            "observed": rows,
            "expected": [{key: row[key] for key in ("slot", "host", "role")} for row in rows],
            "registered": sorted(registrations),
        }
        value["digest"] = module.digest(value)
        own = module.validate_registration(
            json.dumps(
                next(
                    item
                    for item in registrations.values()
                    if item["identity"]["role"] == "orchestrator"
                )
            )
        )
        return value, registrations, own

    def test_full_current_set_and_final_blocking_read_deadline(self):
        module = self.module
        value, registrations, own = self.snapshot()
        for delay in (False, True):
            with self.subTest(delay=delay):
                clock = [1500]
                calls = [0]

                def info(_, calls=calls, clock=clock, delay=delay):
                    calls[0] += 1
                    if delay and calls[0] == 2:
                        clock[0] = 2001
                    return {"run_id": "b" * 40}

                redis = SimpleNamespace(
                    info=info,
                    pttl=lambda key: 1000,
                    get=lambda key: (
                        value["epoch"]
                        if key == module.EPOCH_KEY
                        else json.dumps(registrations[key[len(module.PREFIX) :]])
                    ),
                )
                with (
                    patch.object(module, "_OWN", own),
                    patch.object(module, "_REVOKED", False),
                    patch.object(
                        module,
                        "get_settings",
                        return_value=SimpleNamespace(
                            generation_recovery_inventory_dir="/readonly",
                            omnia_release_sha="a" * 40,
                        ),
                    ),
                    patch.object(module, "require_no_inhibit"),
                    patch.object(module, "read_snapshot", return_value=json.dumps(value).encode()),
                    patch.object(module, "redis_client", return_value=redis),
                    patch.object(
                        module.time, "time_ns", side_effect=lambda clock=clock: clock[0] * 1_000_000
                    ),
                    patch.object(module.time, "monotonic", return_value=1),
                ):
                    if delay:
                        with self.assertRaises(module.ReaderInventoryUnavailable):
                            module.require_readers()
                    else:
                        self.assertEqual(module.require_readers(), value["digest"])

    def test_legacy_unconfigured_health_does_not_claim_readiness(self):
        with patch.object(
            self.module,
            "get_settings",
            return_value=SimpleNamespace(generation_recovery_inventory_dir=""),
        ):
            self.module.require_if_configured()
            with self.assertRaises(self.module.ReaderInventoryUnavailable):
                self.module.require_readers()

    def test_each_physical_dispatch_rechecks_after_readiness_is_lost(self):
        calls = []
        self.assertTrue(
            hasattr(self.module, "reader_dispatch"), "physical call has no fresh readiness boundary"
        )
        with patch.object(
            self.module,
            "require_if_configured",
            side_effect=[None, self.module.ReaderInventoryUnavailable("changed")],
        ):
            self.assertEqual(self.module.reader_dispatch(lambda: calls.append("first") or 17), 17)
            with self.assertRaises(self.module.ReaderInventoryUnavailable):
                self.module.reader_dispatch(lambda: calls.append("second"))
        self.assertEqual(calls, ["first"])

    def test_new_controller_cannot_dispatch_from_previous_incarnation(self):
        settings = SimpleNamespace(
            generation_recovery_inventory_dir="/readonly", omnia_release_sha="a" * 40
        )
        with (
            patch.object(self.module, "get_settings", return_value=settings),
            patch.object(self.module, "_OWN", None),
        ):
            with self.assertRaises(self.module.ReaderInventoryUnavailable):
                self.module.require_if_configured()


class ControllerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_deferred_start_revokes_reader_without_replaying_effects(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://fixture:fixture@localhost/fixture",
                "INTERNAL_TOKEN": "fixture-only-token",
            },
        ):
            from yleum_orchestrator import main
        readers = importlib.import_module("yleum_orchestrator.services.generation_reader_inventory")
        failed = asyncio.Event()
        calls = []

        @asynccontextmanager
        async def registered():
            yield

        @asynccontextmanager
        async def operations(app):
            calls.append("once")
            raise RuntimeError("private exception must not be logged")
            yield  # pragma: no cover

        async def revoke():
            failed.set()

        with (
            patch.object(readers, "reader_lifecycle", registered),
            patch.object(readers, "wait_ready", AsyncMock()),
            patch.object(readers, "revoke_reader", revoke, create=True),
            patch.object(main, "_operational_lifespan", operations),
            patch.object(
                main,
                "get_settings",
                return_value=SimpleNamespace(generation_recovery_inventory_dir="/readonly"),
            ),
        ):
            async with main.lifespan(None):
                await asyncio.wait_for(failed.wait(), 0.2)
            self.assertEqual(calls, ["once"])

    async def test_periodic_recovery_rechecks_before_second_callback(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://fixture:fixture@localhost/fixture",
                "INTERNAL_TOKEN": "fixture-only-token",
            },
        ):
            from yleum_orchestrator.services import code_restorations as service
        readers = importlib.import_module("yleum_orchestrator.services.generation_reader_inventory")
        calls = []

        async def recover():
            calls.append("recovered")

        sleeps = [0]

        async def sleep(_):
            sleeps[0] += 1
            if sleeps[0] == 2:
                raise asyncio.CancelledError

        with (
            patch.object(
                service,
                "get_code_restoration_service",
                return_value=SimpleNamespace(recover=recover),
            ),
            patch.object(service.asyncio, "sleep", sleep),
            patch.object(
                readers,
                "require_if_configured",
                side_effect=[None, readers.ReaderInventoryUnavailable("inhibited")],
            ),
        ):
            task = service.start_restoration_recovery()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(calls, ["recovered"])

    async def test_health_starts_while_recovery_callbacks_wait_for_inventory(self):
        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://fixture:fixture@localhost/fixture",
                "INTERNAL_TOKEN": "fixture-only-token",
            },
        ):
            from yleum_orchestrator import main
        readers = importlib.import_module("yleum_orchestrator.services.generation_reader_inventory")
        ready = asyncio.Event()
        entered = asyncio.Event()
        closed = asyncio.Event()

        @asynccontextmanager
        async def registered():
            yield

        @asynccontextmanager
        async def operations(app):
            entered.set()
            try:
                yield
            finally:
                closed.set()

        self.assertTrue(
            hasattr(main, "_operational_lifespan"), "standby must precede all recovery callbacks"
        )
        with (
            patch.object(readers, "reader_lifecycle", registered),
            patch.object(readers, "wait_ready", ready.wait),
            patch.object(main, "_operational_lifespan", operations),
            patch.object(
                main,
                "get_settings",
                return_value=SimpleNamespace(generation_recovery_inventory_dir="/readonly"),
            ),
        ):
            async with main.lifespan(None):
                await asyncio.sleep(0)
                self.assertFalse(entered.is_set())
                ready.set()
                await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(closed.is_set())


if __name__ == "__main__":
    unittest.main()
