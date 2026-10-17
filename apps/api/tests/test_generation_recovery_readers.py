"""Complete process inventories, not a new aggregate heartbeat, admit readers."""

import asyncio
import copy
import json
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.services.generation import recovery_execution
from yleum_api.services.generation import recovery_readers as readers
from yleum_api.services.generation.recovery_contract import RecoveryContractError


def inventory():
    now = time.time_ns() // 1_000_000
    expected, observed = [], []
    for slot in sorted(readers.SLOTS):
        host, role = slot.split("/")
        expected.append(dict(slot=slot, host=host, role=role))
        observed.append(
            dict(
                host=host,
                role=role,
                host_boot_id=str(uuid4()),
                pid_namespace_inode=10,
                pid=os.getpid(),
                start_ticks=20,
                startup_nonce=str(uuid4()),
                slot=slot,
                host_pid=100 + len(observed),
                container_id=None if role == "orchestrator" else "c" * 64,
                supervisor_id=f"supervisor/{slot}",
                release_sha="a" * 40,
                capabilities=list(
                    readers.CONTROLLER_CAPABILITIES
                    if role == "orchestrator"
                    else readers.API_CAPABILITIES
                ),
            )
        )
    value = dict(
        version=1,
        epoch=str(uuid4()),
        state="ready",
        release_sha="a" * 40,
        observed_at_ms=now - 1000,
        expires_at_ms=now + 20000,
        redis_run_id="b" * 40,
        expected=expected,
        observed=observed,
        registered=sorted(
            readers.digest({key: item[key] for key in readers.ReaderIdentity.model_fields})
            for item in observed
        ),
    )
    value["digest"] = readers.digest(value)
    return value


def encode(value):
    value["digest"] = readers.digest({key: item for key, item in value.items() if key != "digest"})
    return readers.canonical(value).encode()


class Redis:
    def __init__(self, value):
        self.epoch, self.run_id = value["epoch"], value["redis_run_id"]
        self.values = {}
        for item in value["observed"]:
            registration = readers.ObservedReader.model_validate_json(
                json.dumps(item)
            ).registration()
            self.values[readers.PREFIX + readers.incarnation(registration.identity)] = (
                registration.model_dump_json()
            )
        self.lifetime = 30

    async def get(self, key):
        return self.epoch if key == readers.EPOCH_KEY else self.values.get(key)

    async def pttl(self, key):
        return self.lifetime * 1000

    async def info(self, section):
        return {"run_id": self.run_id}


def install_healthy_reader_inventory(monkeypatch, *, refresh=False):
    """Explicit I/O collaborator for other recovery tests, not a gate bypass.

    Model/SQL/session/owner and the complete real inventory parser remain live.
    ``refresh`` simulates the observer and heartbeat while a long cold-loop
    fixture exercises its separate transport and budget boundaries.
    """
    value = inventory()
    redis = Redis(value)
    settings = SimpleNamespace(
        generation_recovery_inventory_dir="/trusted",
        omnia_release_sha="a" * 40,
        generation_recovery_execution_enabled=False,
    )
    monkeypatch.setattr(readers, "get_settings", lambda: settings)

    def read(directory):
        if refresh:
            now = time.time_ns() // 1_000_000
            value.update(observed_at_ms=now, expires_at_ms=now + 30000)
        return encode(value)

    monkeypatch.setattr(readers, "read_snapshot", read)
    monkeypatch.setattr(readers, "require_no_rollout_inhibit", lambda directory: None)

    @asynccontextmanager
    async def connection():
        yield redis

    monkeypatch.setattr(readers, "reader_connection", connection)
    own = next(item for item in value["observed"] if item["slot"] == "core/api")
    monkeypatch.setattr(
        readers, "_OWN", readers.ObservedReader.model_validate_json(json.dumps(own)).registration()
    )
    return value, redis, settings


@pytest.fixture
def ready(monkeypatch):
    return install_healthy_reader_inventory(monkeypatch)


async def test_full_set_with_new_writers_off_still_supports_saved_reader(ready):
    value, _, _ = ready
    assert await readers.require_recovery_readers() == value["digest"]


@pytest.mark.parametrize("fault", ["expiry", "inhibit"])
async def test_connection_cleanup_cannot_outlive_admission(ready, monkeypatch, fault):
    value, redis, _ = ready
    closed = False

    @asynccontextmanager
    async def connection():
        nonlocal closed
        try:
            yield redis
        finally:
            await asyncio.sleep(0)
            closed = True
            if fault == "expiry":
                monkeypatch.setattr(
                    readers.time, "time_ns", lambda: value["expires_at_ms"] * 1_000_000
                )

    def marker(directory):
        if closed and fault == "inhibit":
            raise RecoveryContractError("rollout started during connection cleanup")

    monkeypatch.setattr(readers, "reader_connection", connection)
    monkeypatch.setattr(readers, "require_no_rollout_inhibit", marker)
    with pytest.raises(RecoveryContractError):
        await readers.require_recovery_readers()
    assert closed


@pytest.mark.parametrize(
    "fault",
    [
        "hidden_old",
        "missing",
        "extra",
        "expected_missing",
        "unknown_capability",
        "missing_capability",
        "duplicate_slot",
        "stale",
        "future",
        "long_ttl",
        "deny",
        "wrong_release",
        "unknown_version",
        "bool_version",
        "bool_pid",
        "registration_missing",
        "registration_changed",
        "expired_registration",
        "redis_restart",
        "new_startup_epoch",
        "own_restart",
        "unregistered_own",
        "wrong_target",
    ],
)
async def test_complete_inventory_fails_closed(ready, monkeypatch, fault):
    value, redis, _ = ready
    if fault == "hidden_old":
        value["observed"][0]["release_sha"] = "0" * 40
    elif fault == "missing":
        value["observed"].pop()
    elif fault == "extra":
        value["observed"].append(copy.deepcopy(value["observed"][0]))
    elif fault == "expected_missing":
        value["expected"].pop()
    elif fault == "unknown_capability":
        value["observed"][0]["capabilities"].append("future:2")
    elif fault == "missing_capability":
        value["observed"][0]["capabilities"].pop()
    elif fault == "duplicate_slot":
        value["expected"][0] = value["expected"][1]
    elif fault == "stale":
        value["expires_at_ms"] = value["observed_at_ms"] + 1
    elif fault == "future":
        value["observed_at_ms"] += 100000
    elif fault == "long_ttl":
        value["expires_at_ms"] += 100000
    elif fault == "deny":
        value["state"] = "deny"
    elif fault == "wrong_release":
        value["release_sha"] = "0" * 40
    elif fault == "unknown_version":
        value["version"] = 2
    elif fault == "bool_version":
        value["version"] = True
    elif fault == "bool_pid":
        value["observed"][0]["pid"] = True
    elif fault == "registration_missing":
        redis.values.pop(next(iter(redis.values)))
    elif fault == "registration_changed":
        key = next(iter(redis.values))
        payload = json.loads(redis.values[key])
        payload["release_sha"] = "0" * 40
        redis.values[key] = json.dumps(payload)
    elif fault == "expired_registration":
        redis.lifetime = -2
    elif fault == "redis_restart":
        redis.run_id = "0" * 40
    elif fault == "new_startup_epoch":
        redis.epoch = str(uuid4())
    elif fault == "own_restart":
        monkeypatch.setattr(
            readers,
            "_OWN",
            readers._OWN.model_copy(
                update={
                    "identity": readers._OWN.identity.model_copy(update={"startup_nonce": uuid4()}),
                }
            ),
        )
    elif fault == "unregistered_own":
        monkeypatch.setattr(readers, "_OWN", None)
    with pytest.raises(RecoveryContractError):
        await readers.require_recovery_readers(
            release_sha="0" * 40 if fault == "wrong_target" else None
        )


def test_duplicate_keys_cannot_hide_identity_or_unknown_contract():
    value = inventory()
    raw = encode(value).decode().replace('"version":1', '"version":999,"version":1')
    with pytest.raises(RecoveryContractError):
        readers.validate_snapshot(
            raw.encode(), release_sha="a" * 40, now_ms=time.time_ns() // 1_000_000
        )


async def test_awaited_validation_cannot_extend_snapshot_lifetime(ready, monkeypatch):
    value, redis, _ = ready
    original = redis.info

    async def delayed(section):
        result = await original(section)
        monkeypatch.setattr(readers.time, "time_ns", lambda: value["expires_at_ms"] * 1_000_000)
        return result

    monkeypatch.setattr(redis, "info", delayed)
    with pytest.raises(RecoveryContractError):
        await readers.require_recovery_readers()


async def test_physical_owner_check_requires_inventory_before_any_dispatch(monkeypatch):
    """An actual Task9 guard must enforce compatibility, even with a live PG owner."""
    run_id = uuid4()
    connection = SimpleNamespace(scalar=AsyncMock(return_value=True), commit=AsyncMock())
    token = recovery_execution._SESSION_OWNER.set(
        recovery_execution.SessionOwnership(run_id, connection)
    )
    gate = AsyncMock(side_effect=RecoveryContractError("hidden old reader"))
    monkeypatch.setattr(readers, "require_recovery_readers", gate)
    try:
        with pytest.raises(RecoveryContractError, match="hidden old reader"):
            await recovery_execution._require_session_owner(run_id)
        gate.assert_awaited_once()
    finally:
        recovery_execution._SESSION_OWNER.reset(token)


async def test_wait_owner_check_can_preserve_state_when_inventory_is_unavailable(monkeypatch):
    run_id = uuid4()
    connection = SimpleNamespace(scalar=AsyncMock(return_value=True), commit=AsyncMock())
    token = recovery_execution._SESSION_OWNER.set(
        recovery_execution.SessionOwnership(run_id, connection)
    )
    gate = AsyncMock(side_effect=RecoveryContractError("reader expired"))
    monkeypatch.setattr(readers, "require_recovery_readers", gate)
    try:
        await recovery_execution._require_session_owner(run_id, compatibility=False)
        gate.assert_not_awaited()
        connection.scalar.return_value = False
        with pytest.raises(RecoveryContractError, match="session lock"):
            await recovery_execution._require_session_owner(run_id, compatibility=False)
    finally:
        recovery_execution._SESSION_OWNER.reset(token)


@pytest.mark.parametrize("configured", [False, True])
async def test_api_health_does_not_run_recovery_callbacks_while_standby(monkeypatch, configured):
    from yleum_api import main

    entered, allow = asyncio.Event(), asyncio.Event()
    events = []
    signals = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            generation_recovery_inventory_dir="/trusted" if configured else "",
        ),
    )

    @asynccontextmanager
    async def lifecycle(role, *, wait):
        events.append("registration-before-health")
        yield

    async def standby():
        entered.set()
        await allow.wait()

    @asynccontextmanager
    async def callbacks(*, on_failure=None):
        events.append("recovery-callbacks")
        try:
            yield
        finally:
            events.append("cleanup")

    monkeypatch.setattr(readers, "reader_lifecycle", lifecycle)
    monkeypatch.setattr(readers, "wait_for_readers", standby)
    monkeypatch.setattr(main, "_background_services", callbacks)
    async with main.lifespan(None):
        if configured:
            await asyncio.wait_for(entered.wait(), 1)
            assert events == ["registration-before-health"]
            allow.set()
            await asyncio.sleep(0)
        assert events == ["registration-before-health", "recovery-callbacks"]
    assert events[-1] == "cleanup"
    assert signals == []


@pytest.mark.parametrize("field", ["epoch", "host_boot_id", "startup_nonce"])
def test_noncanonical_uuid_is_not_another_incarnation_encoding(field):
    value = inventory()
    target = value if field == "epoch" else value["observed"][0]
    target[field] = target[field].upper()
    with pytest.raises(RecoveryContractError):
        readers.validate_snapshot(
            encode(value), release_sha="a" * 40, now_ms=time.time_ns() // 1_000_000
        )


@pytest.mark.parametrize("failure", ["policy", "resume", "cleanup_error", "cleanup_hang"])
async def test_admitted_startup_failure_requests_process_stop(monkeypatch, failure):
    import signal

    from yleum_api import main

    app = main.create_app()
    stopped = asyncio.Event()
    cleanup_finished = asyncio.Event()
    events = []

    @asynccontextmanager
    async def lifecycle(role, *, wait):
        yield

    def policy():
        if failure == "policy":
            raise RuntimeError("synthetic startup failure")

    async def resume():
        raise RuntimeError("synthetic startup failure")

    async def record(event):
        events.append(event)
        if event == "stop-listener":
            if failure == "cleanup_error":
                cleanup_finished.set()
                raise RuntimeError("synthetic cleanup failure")
            if failure == "cleanup_hang":
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_finished.set()
        if event == "dispose-engine":
            cleanup_finished.set()

    def stop(pid, sig):
        assert pid == os.getpid() and sig == signal.SIGTERM
        events.append("stop-process")
        stopped.set()

    monkeypatch.setattr(os, "kill", stop)
    monkeypatch.setattr(main, "_BACKGROUND_CLEANUP_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(readers, "reader_lifecycle", lifecycle)
    monkeypatch.setattr(readers, "wait_for_readers", AsyncMock())
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(
        generation_recovery_inventory_dir="/trusted",
        omnia_release_sha="a" * 40,
    ))
    monkeypatch.setattr(main, "reconcile_public_bucket_policies", policy)
    monkeypatch.setattr(main, "get_engine", lambda: events.append("engine"))
    monkeypatch.setattr(main, "recover_interrupted_generation_runs", AsyncMock(return_value=0))
    monkeypatch.setattr(main, "recover_interrupted_cell_operations", AsyncMock(return_value=0))
    monkeypatch.setattr(main.hub, "start_listener", lambda: record("listener"))
    monkeypatch.setattr(main.hub, "stop_listener", lambda: record("stop-listener"))
    monkeypatch.setattr(main, "dispose_redis", lambda: record("redis"))
    monkeypatch.setattr(main, "dispose_engine", lambda: record("dispose-engine"))
    monkeypatch.setattr(main, "resume_capacity_queued_generations", resume)
    # Observe the stop request while the actual lifespan still serves; a stale
    # exception first surfaced at shutdown is not a successful fail-stop.
    async with main.lifespan(app):
        await asyncio.wait_for(stopped.wait(), 2)
        assert app.state.background_services_failed is True
        if failure != "policy":
            await asyncio.wait_for(cleanup_finished.wait(), 2)
        monkeypatch.setattr(main.readiness, "probe_readiness", AsyncMock(
            return_value=main.readiness.ReadinessReport({"database": "ok"}, {}),
        ))
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/health")
        assert (await endpoint()).status_code == 503
    expected = ["stop-process"] if failure == "policy" else [
        "engine", "listener", "stop-process", "stop-listener",
    ]
    if failure == "resume":
        expected += ["redis", "dispose-engine"]
    assert events == expected



def test_owner_mode_without_readonly_mount_cannot_authorize_inventory(tmp_path):
    if not hasattr(os, "O_NOFOLLOW"):
        # Windows cannot establish the Linux mount guarantee either.
        with pytest.raises(RecoveryContractError, match="unavailable"):
            readers.read_snapshot(str(tmp_path))
        return
    os.chmod(tmp_path, 0o755)
    (tmp_path / "current.json").write_bytes(encode(inventory()))
    with pytest.raises(RecoveryContractError, match=r"writable|ownership"):
        readers.read_snapshot(str(tmp_path))


async def test_final_redis_read_cannot_outlive_snapshot(ready, monkeypatch):
    value, redis, _ = ready
    original = redis.info
    calls = 0

    async def final_delay(section):
        nonlocal calls
        calls += 1
        result = await original(section)
        if calls == 2:
            monkeypatch.setattr(readers.time, "time_ns", lambda: value["expires_at_ms"] * 1_000_000)
        return result

    monkeypatch.setattr(redis, "info", final_delay)
    with pytest.raises(RecoveryContractError, match="expired"):
        await readers.require_recovery_readers()


@pytest.mark.parametrize("introduced_during_read", [False, True])
async def test_rollout_inhibit_denies_runtime_but_not_privileged_end_validation(
    ready,
    monkeypatch,
    introduced_during_read,
):
    _, redis, _ = ready
    blocked = not introduced_during_read
    checks = []

    def filesystem(directory):
        checks.append(directory)
        if blocked:
            raise RecoveryContractError("reader rollout is inhibited")

    original = redis.info

    async def apply_marker(section):
        nonlocal blocked
        result = await original(section)
        blocked = True
        return result

    monkeypatch.setattr(readers, "require_no_rollout_inhibit", filesystem, raising=False)
    monkeypatch.setattr(redis, "info", apply_marker)
    with pytest.raises(RecoveryContractError, match="inhibited"):
        await readers.require_recovery_readers()
    assert checks
    checks.clear()
    assert await readers.require_recovery_readers(own=False)
    assert checks == []


@pytest.mark.parametrize(
    "monitor,callback",
    [
        ("_monitor_capacity_queued_generations", "resume_capacity_queued_generations"),
        ("_monitor_owner_wake_operations", "advance_owner_wake_operations"),
        ("_monitor_terminal_generation_cleanup", "advance_terminal_generation_cleanup"),
    ],
)
async def test_existing_api_background_tick_rechecks_inventory(monkeypatch, monitor, callback):
    from yleum_api import main

    async def once(_delay):
        return None

    monkeypatch.setattr(main.asyncio, "sleep", once)
    effect = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(main, callback, effect)
    gate = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(readers, "wait_for_readers", gate)
    with pytest.raises(asyncio.CancelledError):
        await getattr(main, monitor)()
    effect.assert_not_awaited()
    gate.assert_awaited_once()


async def test_restoration_tick_checks_inventory_after_semaphore_wait(monkeypatch):
    from yleum_api.services import restoration_reconciliation as reconciliation

    @asynccontextmanager
    async def factory():
        yield object()

    entered = asyncio.Event()
    calls = []

    async def gate():
        calls.append("guard")
        if len(calls) == 3:
            raise RecoveryContractError("rollout began while waiting")

    async def observe(*args, **kwargs):
        entered.set()

    monkeypatch.setattr(
        reconciliation,
        "claim_due_restorations",
        AsyncMock(return_value=[(uuid4(), uuid4(), uuid4()), (uuid4(), uuid4(), uuid4())]),
    )
    observed = AsyncMock(side_effect=observe)
    monkeypatch.setattr(reconciliation, "_observe", observed)
    with pytest.raises(RecoveryContractError, match="rollout"):
        await reconciliation.reconcile_due_restorations(
            factory, object(), concurrency=1, before_tick=gate
        )
    assert observed.await_count == 1 and entered.is_set()


async def test_actual_reader_start_invalidates_before_callback_and_only_registers_itself(
    ready, monkeypatch
):
    value, redis, _ = ready
    writes = []

    async def write(key, value, *, ex=None):
        writes.append((key, ex))
        if key == readers.EPOCH_KEY:
            redis.epoch = value
        else:
            redis.values[key] = value
        return True

    monkeypatch.setattr(redis, "set", write, raising=False)
    async with readers.reader_lifecycle("api", wait=False):
        assert redis.epoch != value["epoch"]
        assert writes == [(readers.EPOCH_KEY, None)]
        await asyncio.sleep(0)
        assert writes[1] == (readers.PREFIX + readers.incarnation(readers._OWN.identity), 30)
        with pytest.raises(RecoveryContractError, match="epoch changed"):
            await readers.require_recovery_readers()
    assert len(writes) == 2


async def test_generation_worker_heartbeat_does_not_start_dispatch_in_standby(monkeypatch):
    from yleum_api.workers import generation

    @asynccontextmanager
    async def registered(role, *, wait):
        assert role == "generation-worker" and wait is False
        yield

    heartbeat = AsyncMock()

    async def standby(callback):
        await callback()
        raise asyncio.CancelledError()

    monkeypatch.setattr(readers, "reader_lifecycle", registered)
    monkeypatch.setattr(readers, "wait_for_readers", standby)
    monkeypatch.setattr(generation, "get_redis", lambda: SimpleNamespace(set=heartbeat))
    dispatch = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(generation, "_run_dispatch_forever", dispatch)
    monkeypatch.setattr(generation, "_run_terminal_notification_forever", notify)
    with pytest.raises(asyncio.CancelledError):
        await generation.run_forever()
    heartbeat.assert_awaited_once()
    dispatch.assert_not_awaited()
    notify.assert_not_awaited()
