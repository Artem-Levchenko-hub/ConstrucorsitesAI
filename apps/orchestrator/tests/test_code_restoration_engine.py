"""Crash outcomes must reflect the running code, without another activation."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.schemas.code_restoration import CodeRestorationApply
from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
from omnia_orchestrator.services.project_machine import write_controller_json


@pytest.fixture
def cleanup_engine(tmp_path, monkeypatch):
    """Persist the real destroy receipt, including its consumed fence."""
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid5

    from omnia_orchestrator.core.cell_resources import CellFenceRejected
    from omnia_orchestrator.schemas.code_restoration import CodeRestorationCancel
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    req = CodeRestorationCancel(
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        owner_id=UUID(int=4),
    )
    candidate_id = uuid5(req.operation_id, "candidate")
    receipt = None
    state = SimpleNamespace(
        workspace_id=candidate_id,
        project_id=req.project_id,
        owner_id=req.owner_id,
        fencing_epoch=1,
        active_generation_run_id=None,
        active_generation_fencing_epoch=None,
        last_operation_id=None,
        phase="completed",
        bundle_state="running",
        resource_names=object(),
        operation=lambda _: receipt,
    )
    volumes = [
        SimpleNamespace(
            name=name,
            labels={
                "omnia.workspace_id": str(candidate_id),
                "omnia.project_id": str(req.project_id),
                "omnia.owner_id": str(req.owner_id),
            },
        )
        for name in ("owned-code", "owned-data")
    ]

    async def destroy(_workspace, mutation, **_kwargs):
        nonlocal receipt
        if receipt is not None and mutation.fencing_epoch != receipt.fencing_epoch:
            raise CellFenceRejected("replay envelope mismatch")
        assert receipt is None, "completed destroy must not halt the removed runtime again"
        receipt = CellOperationRecord(
            operation_id=mutation.operation_id,
            kind="destroy",
            status="completed",
            phase="completed",
            request_digest=mutation.request_digest,
            fencing_epoch=mutation.fencing_epoch,
            bundle_state="retained",
        )
        state.fencing_epoch = mutation.fencing_epoch
        state.last_operation_id = mutation.operation_id
        state.bundle_state = "retained"

    async def remove(name):
        volumes[:] = [volume for volume in volumes if volume.name != name]

    manager = SimpleNamespace(
        state_store=SimpleNamespace(load=lambda _: state),
        operation_lock=Lock(),
        capacity_lock=SimpleNamespace(hold_named=lambda _: Lock().hold(candidate_id)),
        destroy_compute_without_lock=AsyncMock(side_effect=destroy),
        _release_capacity=Mock(),
        _spec_from_state=lambda _: object(),
        _preflight_named_resources=AsyncMock(),
        docker=SimpleNamespace(
            list_workspace_volumes=AsyncMock(side_effect=lambda _: list(volumes)),
            list_workspace_containers=AsyncMock(return_value=[]),
            list_workspace_networks=AsyncMock(return_value=[]),
            remove_volume=AsyncMock(side_effect=remove),
        ),
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication_capacity.production_manager",
        lambda *_: manager,
    )
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace()
    engine._manager = lambda _: manager
    return engine, req, candidate_id, manager, state, volumes


async def test_needs_changes_cleanup_then_repeated_cancel(cleanup_engine):
    engine, req, candidate_id, manager, _, volumes = cleanup_engine
    # prepare's needs_changes finally cleans the candidate before the client cancels.
    await engine._cleanup_candidate(manager, req, candidate_id)
    await engine.cancel(req, None)
    await engine.cancel(req, None)
    assert not volumes
    assert manager.destroy_compute_without_lock.await_count == 1


async def test_completed_candidate_cleanup_retries_failed_volume_removal(cleanup_engine):
    engine, req, candidate_id, manager, _, volumes = cleanup_engine
    remove = manager.docker.remove_volume.side_effect

    async def fail_second(name):
        if name == "owned-data":
            raise OSError("volume busy")
        await remove(name)

    manager.docker.remove_volume.side_effect = fail_second
    with pytest.raises(OSError, match="volume busy"):
        await engine._cleanup_candidate(manager, req, candidate_id)
    assert [volume.name for volume in volumes] == ["owned-data"]
    manager.docker.remove_volume.side_effect = remove
    await engine.cancel(req, None)
    assert not volumes
    assert manager.destroy_compute_without_lock.await_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", UUID(int=99)),
        ("owner_id", UUID(int=99)),
        ("workspace_id", UUID(int=99)),
        ("fencing_epoch", 3),
        ("last_operation_id", UUID(int=99)),
        ("active_generation_run_id", UUID(int=99)),
        ("active_generation_fencing_epoch", 2),
        ("bundle_state", "running"),
    ],
)
async def test_candidate_cleanup_rejects_changed_identity(cleanup_engine, field, value):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine, req, candidate_id, manager, state, _ = cleanup_engine
    await engine._cleanup_candidate(manager, req, candidate_id)
    setattr(state, field, value)
    with pytest.raises(CellIdentityConflict):
        await engine.cancel(req, None)
    assert manager.destroy_compute_without_lock.await_count == 1
    manager._release_capacity.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", UUID(int=99)),
        ("kind", "ensure"),
        ("request_digest", "other"),
        ("fencing_epoch", 1),
        ("checkpoint_ref", "foreign"),
        ("generation_run_id", UUID(int=99)),
        ("phase", "containers_removed"),
    ],
)
async def test_candidate_cleanup_rejects_changed_receipt(cleanup_engine, field, value):
    from dataclasses import replace

    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine, req, candidate_id, manager, state, _ = cleanup_engine
    await engine._cleanup_candidate(manager, req, candidate_id)
    receipt = replace(state.operation(None), **{field: value})
    state.operation = lambda _: receipt
    with pytest.raises(CellIdentityConflict):
        await engine.cancel(req, None)
    assert manager.destroy_compute_without_lock.await_count == 1
    manager._release_capacity.assert_not_called()


@pytest.mark.parametrize("resource", ["containers", "networks"])
async def test_completed_candidate_cleanup_rejects_remaining_compute(cleanup_engine, resource):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine, req, candidate_id, manager, _, _ = cleanup_engine
    await engine._cleanup_candidate(manager, req, candidate_id)
    getattr(manager.docker, "list_workspace_" + resource).return_value = [object()]
    with pytest.raises(CellIdentityConflict, match="still has compute"):
        await engine.cancel(req, None)
    manager._release_capacity.assert_not_called()


@pytest.mark.parametrize("label", ["workspace_id", "project_id", "owner_id"])
async def test_candidate_cleanup_checks_all_volume_labels_before_any_removal(cleanup_engine, label):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine, req, candidate_id, manager, _, volumes = cleanup_engine
    volumes[-1].labels["omnia." + label] = str(UUID(int=99))
    with pytest.raises(CellIdentityConflict, match="volume cleanup identity"):
        await engine._cleanup_candidate(manager, req, candidate_id)
    assert len(volumes) == 2
    manager.docker.remove_volume.assert_not_awaited()


async def test_running_candidate_cleanup_reuses_admitted_fence(cleanup_engine):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    engine, req, candidate_id, manager, state, _ = cleanup_engine
    await engine._cleanup_candidate(manager, req, candidate_id)
    receipt = replace(state.operation(None), status="running", phase="networks_removed")
    state.operation = lambda _: receipt
    state.phase = "networks_removed"
    manager.destroy_compute_without_lock = AsyncMock()
    await engine.cancel(req, None)
    mutation = manager.destroy_compute_without_lock.await_args.args[1]
    assert mutation.operation_id == receipt.operation_id
    assert mutation.fencing_epoch == receipt.fencing_epoch == 2
    assert mutation.request_digest == receipt.request_digest


def test_current_source_inventory_rejects_untracked_business_files():
    import hashlib

    from omnia_orchestrator.services.code_restoration_engine import verify_source_inventory

    expected = [{"path": "src/app/page.tsx", "sha256": hashlib.sha256(b"source").hexdigest()}]
    verify_source_inventory(
        {"src/app/page.tsx": b"source", "next-env.d.ts": b"generated"}, expected
    )
    with pytest.raises(ValueError, match="файлы"):
        verify_source_inventory(
            {"src/app/page.tsx": b"source", "uploads/client.pdf": b"data"}, expected
        )
    with pytest.raises(ValueError):
        verify_source_inventory({"src/app/page.tsx": b"changed"}, expected)


def test_source_inventory_checks_binary_and_empty_files():
    import hashlib

    from omnia_orchestrator.services.code_restoration_engine import verify_source_inventory

    actual = {"public/photo.png": b"\x00\xff", "src/empty": b""}
    expected = [
        {"path": name, "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in actual.items()
    ]
    verify_source_inventory(actual, expected)
    with pytest.raises(ValueError):
        verify_source_inventory({**actual, "public/photo.png": b"another"}, expected)


class Lock:
    @asynccontextmanager
    async def hold(self, _workspace):
        yield


def test_controller_socket_closes_owning_response_before_raw_socket():
    from omnia_orchestrator.services.restoration_database import close_controller_socket

    events = []

    class Response:
        closed = False

        def close(self):
            events.append("response")
            self.closed = True

    response = Response()

    class Connection:
        _response = response

        def close(self):
            if not response.closed:
                raise ValueError("raw socket detached before owning response")
            events.append("socket")

    close_controller_socket(Connection())
    assert events == ["response", "socket"]


def request():
    return CodeRestorationApply(
        operation_id=UUID(int=1),
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        owner_id=UUID(int=4),
        candidate_id=UUID(int=5),
        expected_source_head="a" * 40,
        target_commit_sha="b" * 40,
        planned_commit_sha="c" * 40,
        expected_fencing_epoch=3,
        fencing_epoch=4,
        report_revision=1,
    )


class Engine(CodeRestorationEngine):
    def __init__(self, tmp_path, *, matches=True):
        self.root = tmp_path
        self.matches = matches
        self.calls = []
        self.state = SimpleNamespace(
            workspace_id=UUID(int=2),
            project_id=UUID(int=3),
            owner_id=UUID(int=4),
            fencing_epoch=4,
            active_generation_run_id=None,
        )
        self.backend = SimpleNamespace()
        self.manager = SimpleNamespace(
            operation_lock=Lock(),
            state_store=SimpleNamespace(load=lambda _: self.state),
            machine_runtime=SimpleNamespace(parts=lambda _: (None, self.backend)),
        )

    def _manager(self, _workspace):
        return self.manager

    async def _running_matches(self, *args):
        self.calls.append("observe")
        return self.matches

    async def _recover_old(self, *args):
        self.calls.append("recover")
        self.matches = True

    async def _complete_activation(self, *args):
        self.calls.append("complete")
        return "d" * 64

    async def _activate_code(self, *args):
        pytest.fail("Observation must not blindly replay activation")


def save_intent(engine, stage):
    value = request()
    intent = {
        **value.model_dump(
            mode="json",
            include={
                "operation_id",
                "workspace_id",
                "project_id",
                "owner_id",
                "candidate_id",
                "fencing_epoch",
            },
        ),
        "source_commit_sha": value.planned_commit_sha,
        "source_revision": "d" * 64,
        "volume": "new-code",
        "state": stage,
        "old": {"workspace_volume": "old-code", "machine": {"manifest": {}}},
    }
    write_controller_json(engine._directory(value.operation_id) / "activation.json", intent)
    return value


def save_empty_forward_intent(engine, stage="target_writers_admitted"):
    value = save_intent(engine, stage)
    path = engine._directory(value.operation_id) / "activation.json"
    intent = json.loads(path.read_text())
    intent.update(
        database_strategy="replace_verified_empty",
        database_volume="new-db",
        binding_digest=value.binding_digest,
    )
    intent["old"]["database_volume"] = "old-db"
    write_controller_json(path, intent)
    return value, path


class ForwardEngine(Engine):
    def __init__(self, tmp_path, *, volumes):
        super().__init__(tmp_path, matches=False)
        self.volumes = set(volumes)
        self.backend = SimpleNamespace(
            client=SimpleNamespace(volumes=object()),
            _lookup=lambda _collection, name, _kind: object() if name in self.volumes else None,
            remove=lambda: self.calls.append("remove-target-runtime"),
        )
        self.manager.machine_runtime.parts = lambda _: (None, self.backend)

    async def _activate_code(self, _manager, _state, _backend, prepared, volume, epoch, **kwargs):
        self.calls.append(("activate-target", volume, prepared["target_database_volume"], epoch))
        kwargs["before_writers"]()


async def test_forward_recovery_missing_target_volume_fails_without_old_recovery(tmp_path):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine = ForwardEngine(tmp_path, volumes={"new-code"})
    value, path = save_empty_forward_intent(engine)

    with pytest.raises(CellIdentityConflict, match="target restoration pair is incomplete"):
        await engine.observe(value, {"manifest": {}})

    assert "recover" not in engine.calls
    assert not any(
        isinstance(call, tuple) and call[0] == "activate-target" for call in engine.calls
    )
    intent = json.loads(path.read_text())
    assert intent["state"] == "target_recovery"
    assert intent["database_volume"] == "new-db"


async def test_forward_recovery_restarts_only_bound_target_pair(tmp_path):
    engine = ForwardEngine(tmp_path, volumes={"new-code", "new-db"})
    value, path = save_empty_forward_intent(engine)

    result = await engine.observe(value, {"manifest": {}})

    assert result["applied"] is True
    assert "recover" not in engine.calls
    assert engine.calls == [
        "observe",
        "remove-target-runtime",
        ("activate-target", "new-code", "new-db", 4),
        "complete",
    ]
    intent = json.loads(path.read_text())
    assert intent["state"] == "active"
    assert intent["database_volume"] == "new-db"


async def test_preserved_database_forward_recovery_restarts_only_target_code(tmp_path):
    engine = ForwardEngine(tmp_path, volumes={"new-code", "live-db"})
    value = save_intent(engine, "target_writers_admitted")
    path = engine._directory(value.operation_id) / "activation.json"
    intent = json.loads(path.read_text())
    intent.update(
        database_strategy="preserve_current",
        database_volume="live-db",
        effects_admitted=True,
    )
    intent["old"]["database_volume"] = "live-db"
    write_controller_json(path, intent)

    result = await engine.observe(value, {"manifest": {}})

    assert result["applied"] is True
    assert "recover" not in engine.calls
    assert engine.calls == [
        "observe",
        "remove-target-runtime",
        ("activate-target", "new-code", "live-db", 4),
        "complete",
    ]


async def test_lost_success_response_observes_without_restarting_app(tmp_path):
    engine = Engine(tmp_path)
    value = save_intent(engine, "active")
    result = await engine.observe(value, {"manifest": {}})
    assert result["applied"] is True
    assert engine.calls == ["observe", "complete"]


async def test_archive_cleanup_failure_does_not_undo_observed_activation(tmp_path, monkeypatch):
    from pathlib import Path

    engine = Engine(tmp_path)
    value = save_intent(engine, "active")

    def denied(*args, **kwargs):
        raise PermissionError("archive busy")

    monkeypatch.setattr(Path, "unlink", denied)
    result = await engine.observe(value, {"manifest": {}})
    assert result["applied"] is True
    assert "recover" not in engine.calls


async def test_reverted_pair_cleanup_receipt_retries_exact_bound_operation(tmp_path):
    engine = Engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "b" * 64})
    path = engine._directory(value.operation_id) / "activation.json"
    intent = {
        "state": "reverted",
        "cleanup_pending": True,
        "database_strategy": "replace_verified_empty",
        "binding_digest": value.binding_digest,
    }
    write_controller_json(path, intent)
    attempts = []

    def cleanup(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise OSError("docker busy")

    backend = SimpleNamespace(cleanup_reverted_restoration=cleanup)
    prepared = {"code_digest": "c" * 64, "database_digest": "d" * 64}

    with pytest.raises(OSError, match="docker busy"):
        await engine._cleanup_reverted_pair(backend, value, prepared, intent, path)
    assert json.loads(path.read_text())["cleanup_pending"] is True

    await engine._cleanup_reverted_pair(backend, value, prepared, intent, path)

    assert len(attempts) == 2
    assert attempts[-1][0] == (value.operation_id,)
    assert attempts[-1][1] == {
        "binding_digest": value.binding_digest,
        "code_artifact_digest": "c" * 64,
        "database_artifact_digest": "d" * 64,
    }
    assert "cleanup_pending" not in json.loads(path.read_text())


@pytest.mark.parametrize("state", ["target_recovery", "target_writers_admitted", "active"])
async def test_forward_or_active_pair_is_never_cleanup_eligible(tmp_path, state):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine = Engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "b" * 64})
    path = engine._directory(value.operation_id) / "activation.json"
    intent = {
        "state": state,
        "cleanup_pending": True,
        "database_strategy": "replace_verified_empty",
        "binding_digest": value.binding_digest,
    }
    called = False

    def cleanup(*_args, **_kwargs):
        nonlocal called
        called = True

    backend = SimpleNamespace(cleanup_reverted_restoration=cleanup)

    with pytest.raises(CellIdentityConflict, match="cleanup state"):
        await engine._cleanup_reverted_pair(
            backend,
            value,
            {"code_digest": "c" * 64, "database_digest": "d" * 64},
            intent,
            path,
        )
    assert not called


async def test_empty_database_import_failure_recovers_old_and_cleans_exact_pair(
    tmp_path, monkeypatch
):
    import hashlib
    from dataclasses import dataclass

    from omnia_orchestrator.routers.runtime import _workspace_revision
    from omnia_orchestrator.services.restoration_empty import EmptyDatabaseWitness

    value = request().model_copy(update={"binding_digest": "b" * 64})
    engine = Engine(tmp_path)
    events = []
    volumes: dict[str, dict[str, str]] = {}

    @dataclass
    class ApplyBackend:
        stem: str = "fixture"
        workspace_volume: str = "old-code"
        project_postgres_volume: str = "old-db"

        def _metadata(self):
            return {"manifest": {}}

        def _lookup(self, _collection, name, _kind):
            return object() if name in volumes else None

        def labels(self, _kind):
            return {"omnia.machine": "true"}

        def restoration_volume_labels(
            self, operation_id, *, purpose, binding_digest, artifact_digest
        ):
            return {
                "omnia.restoration_operation_id": str(operation_id),
                "omnia.restoration_purpose": purpose,
                "omnia.restoration_binding_digest": binding_digest,
                "omnia.restoration_artifact_digest": artifact_digest,
            }

        def import_volume(self, name, _path):
            events.append(("code-import", name))

        def import_restoration_database(self, *_args, **_kwargs):
            events.append("database-import-failed")
            raise OSError("database import failed")

        def cleanup_reverted_restoration(self, operation_id, **kwargs):
            events.append(("cleanup", operation_id, kwargs, sorted(volumes)))
            volumes.clear()

    backend = ApplyBackend()
    backend.client = SimpleNamespace(volumes=object())
    engine.backend = backend
    engine.manager.machine_runtime.parts = lambda _state: (
        SimpleNamespace(state=lambda: {"manifest": {}}),
        backend,
    )
    async def read_source_files(_name):
        return {"src/page.tsx": b"current"}

    engine.manager.docker = SimpleNamespace(read_workspace_source_files=read_source_files)
    engine.manager._state_labels = lambda *_args: {"omnia.cell": "true"}

    async def ensure_volume(name, labels):
        volumes[name] = labels

    engine.manager._ensure_volume = ensure_volume

    async def preflight(*_args):
        return engine.state, SimpleNamespace(state=lambda: {"manifest": {}}), backend

    engine._activation_preflight = preflight
    async def recover_old(*_args):
        events.append("recover-old")
        engine.matches = True

    engine._recover_old = recover_old
    async def read_workspace(*_args):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", read_workspace
    )
    witness = EmptyDatabaseWitness(
        project_id=value.project_id,
        workspace_id=value.workspace_id,
        operation_id=value.operation_id,
        database_identity_digest="1" * 64,
        catalog_digest="2" * 64,
        objects_digest="3" * 64,
        technical_state_digest="4" * 64,
        identity_rows_digest="5" * 64,
        observation_kind="source",
        identity_relations=[],
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.code_restoration_engine.observe_empty_database",
        lambda *_args, **_kwargs: witness,
    )
    directory = engine._directory(value.operation_id)
    (directory / "code.tar").write_bytes(b"code")
    (directory / "database.tar").write_bytes(b"database")
    prepared = {
        "workspace_revision": _workspace_revision({"src/page.tsx": "current"}),
        "current_files": [
            {
                "path": "src/page.tsx",
                "sha256": hashlib.sha256(b"current").hexdigest(),
            }
        ],
        "live_contract": {"version": 1, "tables": []},
        "code_digest": hashlib.sha256(b"code").hexdigest(),
        "database_digest": hashlib.sha256(b"database").hexdigest(),
        "database_strategy": "replace_verified_empty",
        "empty_witness": witness.model_dump(mode="json"),
        "manifest": {},
    }

    result = await engine.apply(value, prepared)

    assert result["applied"] is False
    assert "recover-old" in events
    assert "database-import-failed" in events
    cleanup = next(item for item in events if isinstance(item, tuple) and item[0] == "cleanup")
    assert cleanup[1] == value.operation_id
    assert cleanup[3] == [
        backend.stem + "-code-" + value.operation_id.hex,
        backend.stem + "-db-" + value.operation_id.hex,
    ]
    assert volumes == {}
    receipt = json.loads((directory / "activation.json").read_text())
    assert receipt["state"] == "reverted"
    assert "cleanup_pending" not in receipt


@pytest.mark.parametrize("stage", ["intent", "switching", "reverting"])
async def test_crash_before_proven_switch_restores_old_code_and_releases_exact_fence(
    tmp_path, stage
):
    engine = Engine(tmp_path, matches=False)
    value = save_intent(engine, stage)
    result = await engine.observe(value, {"manifest": {}})
    assert result["applied"] is False and result["safe_to_release"] is True
    assert result["fencing_epoch"] == value.fencing_epoch
    assert engine.calls.count("recover") == 1
    assert (
        json.loads((engine._directory(value.operation_id) / "activation.json").read_text())["state"]
        == "reverted"
    )


async def test_repeated_negative_observation_does_not_restart_recovered_app(tmp_path):
    engine = Engine(tmp_path)
    value = save_intent(engine, "reverted")
    assert (await engine.observe(value, {}))["applied"] is False
    assert engine.calls == ["observe"]


async def test_old_operation_cannot_revert_newer_generation(tmp_path):
    engine = Engine(tmp_path)
    value = save_intent(engine, "active")
    engine.state.fencing_epoch = 5
    with pytest.raises(RuntimeError):
        await engine.observe(value, {})
    assert engine.calls == []


def untouched_engine(tmp_path, monkeypatch, *, target_exists=False, physical_epoch=3):
    from omnia_orchestrator.routers.runtime import _workspace_revision

    engine = Engine(tmp_path)
    engine.state.fencing_epoch = 3
    engine.manager.profile = SimpleNamespace(state_path=tmp_path / "state.json")
    engine.manager.machine_runtime.exists = lambda _: True
    engine.backend = SimpleNamespace(
        stem="fixture",
        workspace_volume="old-code",
        client=SimpleNamespace(volumes=object()),
        _metadata=lambda: {"epoch": physical_epoch},
        _lookup=lambda *_: object() if target_exists else None,
        _container=lambda: SimpleNamespace(
            id="app", labels={"omnia.fencing_epoch": str(physical_epoch)}
        ),
        _project_postgres=lambda: SimpleNamespace(
            id="pg", labels={"omnia.fencing_epoch": str(physical_epoch)}
        ),
    )
    machine = SimpleNamespace(state=lambda: {"epoch": physical_epoch, "manifest": {}})
    engine.manager.machine_runtime.parts = lambda _: (machine, engine.backend)

    async def read(*_):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr("omnia_orchestrator.routers.workspace._read_agent_workspace_files", read)
    return engine, {
        "workspace_revision": _workspace_revision({"src/page.tsx": "current"}),
        "source_runtime": engine._runtime_identity(engine.backend, machine),
        "live_contract": {},
        "manifest": {},
    }


@pytest.mark.parametrize("physical_epoch", [2, 3])
async def test_crash_before_activation_journal_recovers_exact_admitted_fence(
    tmp_path, monkeypatch, physical_epoch
):
    engine, prepared = untouched_engine(tmp_path, monkeypatch, physical_epoch=physical_epoch)
    result = await engine.observe(request(), prepared)
    assert result == {
        "candidate_id": str(UUID(int=5)),
        "source_commit_sha": "c" * 40,
        "fencing_epoch": 4,
        "applied": False,
        "safe_to_release": True,
    }
    assert engine.calls == ["recover"]
    assert (
        json.loads((engine._directory(UUID(int=1)) / "activation.json").read_text())["state"]
        == "reverted"
    )


async def test_missing_journal_with_possible_external_effect_cannot_claim_safe_release(
    tmp_path, monkeypatch
):
    engine, prepared = untouched_engine(tmp_path, monkeypatch, target_exists=True)
    with pytest.raises(RuntimeError, match="ambiguous"):
        await engine.observe(request(), prepared)
    assert engine.calls == []


def rejection_engine(tmp_path):
    from dataclasses import replace

    from omnia_orchestrator.core.cell_resources import CellResourceNames
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    receipts = {}
    state = SimpleNamespace(
        workspace_id=UUID(int=2),
        project_id=UUID(int=3),
        owner_id=UUID(int=4),
        fencing_epoch=3,
        active_generation_run_id=None,
        last_operation_id=UUID(int=90),
        resource_names=CellResourceNames.for_workspace(UUID(int=2), namespace="test"),
        bundle_state="resources_ready",
        operation=lambda operation_id: receipts.get(operation_id),
    )

    class Store:
        def load(self, _workspace):
            return state

        def begin(self, _spec, mutation, *, kind, phase, resource_names):
            assert mutation.fencing_epoch == state.fencing_epoch + 1
            assert resource_names is state.resource_names
            receipts[mutation.operation_id] = CellOperationRecord(
                operation_id=mutation.operation_id,
                kind=kind,
                status="running",
                phase=phase,
                request_digest=mutation.request_digest,
                fencing_epoch=mutation.fencing_epoch,
            )
            state.fencing_epoch = mutation.fencing_epoch
            state.last_operation_id = mutation.operation_id
            return state

        def complete(self, _workspace, mutation, *, phase, bundle_state, detail):
            receipts[mutation.operation_id] = replace(
                receipts[mutation.operation_id],
                status="completed",
                phase=phase,
                bundle_state=bundle_state,
                detail=detail,
            )
            state.bundle_state = bundle_state
            return state

    manager = SimpleNamespace(
        operation_lock=Lock(),
        state_store=Store(),
        _spec_from_state=lambda _state: object(),
    )
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine._manager = lambda _workspace: manager
    return engine, manager, state, receipts


async def test_pre_effect_binding_rejection_is_durable_and_restart_safe(tmp_path):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict
    from omnia_orchestrator.services.restoration_binding import serving_fencing_epoch

    engine, manager, state, receipts = rejection_engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "f" * 64})
    engine._state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        CellIdentityConflict("ABA generation")
    )
    archive = engine._directory(value.operation_id) / "code.tar"
    archive.write_bytes(b"candidate")

    first = await engine.apply(value, {})
    assert first["applied"] is False and first["safe_to_release"] is True
    assert first["rejected_before_effect"] is True
    assert first["retained_source_fencing_epoch"] == 3
    assert engine.activation_journal_exists(value)
    assert not archive.exists()
    assert state.fencing_epoch == 4
    assert serving_fencing_epoch(state) == 3
    rejection = receipts[state.last_operation_id]
    assert rejection.kind == "restoration_rejection"
    assert rejection.detail == "retained_source_fencing_epoch=3"

    restarted = object.__new__(CodeRestorationEngine)
    restarted.root = tmp_path
    restarted._manager = lambda _workspace: manager
    second = await restarted.apply(value, {})
    assert second == first

    # A delayed retry never downgrades or overwrites a later operation.
    newer = state.last_operation_id = UUID(int=91)
    state.fencing_epoch = 5
    receipts[newer] = receipts[rejection.operation_id].__class__(
        operation_id=newer,
        kind="ensure",
        status="completed",
        phase="completed",
        request_digest="newer",
        fencing_epoch=5,
    )
    assert await restarted.apply(value, {}) == first
    assert state.fencing_epoch == 5 and state.last_operation_id == newer


async def test_consecutive_rejections_retain_actual_serving_epoch(tmp_path):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict
    from omnia_orchestrator.services.restoration_binding import serving_fencing_epoch

    engine, _, state, receipts = rejection_engine(tmp_path)
    engine._state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        CellIdentityConflict("detached database")
    )
    first_request = request().model_copy(update={"binding_digest": "f" * 64})
    first = await engine.apply(first_request, {})
    assert first["retained_source_fencing_epoch"] == 3
    assert state.fencing_epoch == 4

    second_request = first_request.model_copy(
        update={
            "operation_id": UUID(int=11),
            "candidate_id": UUID(int=15),
            "expected_fencing_epoch": 4,
            "fencing_epoch": 5,
        }
    )
    second = await engine.apply(second_request, {})

    assert second["retained_source_fencing_epoch"] == 3
    assert state.fencing_epoch == 5
    assert serving_fencing_epoch(state) == 3
    assert receipts[state.last_operation_id].detail == "retained_source_fencing_epoch=3"


async def test_rejection_restart_carries_serving_epoch_across_fence_change(tmp_path):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine, manager, state, _ = rejection_engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "c" * 64})
    engine._state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        CellIdentityConflict("recreated resource")
    )
    complete = manager.state_store.complete

    def interrupted_complete(*args, **kwargs):
        manager.state_store.complete = complete
        raise OSError("controller restart")

    manager.state_store.complete = interrupted_complete
    with pytest.raises(OSError, match="controller restart"):
        await engine.apply(value, {})
    receipt = json.loads((engine._directory(value.operation_id) / "activation.json").read_text())
    assert receipt["retained_source_fencing_epoch"] == 3
    assert state.fencing_epoch == 4

    restarted = object.__new__(CodeRestorationEngine)
    restarted.root = tmp_path
    restarted._manager = lambda _workspace: manager
    result = await restarted.apply(value, {})
    assert result["retained_source_fencing_epoch"] == 3


async def test_interrupted_unbound_preflight_is_superseded_by_newer_lifecycle(tmp_path):
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    engine, manager, state, receipts = rejection_engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "b" * 64})
    engine.begin_preflight(value)
    assert engine._claim_preflight(value) == "start"
    receipt_path = engine._directory(value.operation_id) / "activation.json"
    assert json.loads(receipt_path.read_text())["retained_source_fencing_epoch"] is None
    archive = receipt_path.parent / "code.tar"
    archive.write_bytes(b"candidate")

    newer = UUID(int=91)
    state.fencing_epoch = 5
    state.last_operation_id = newer
    receipts[newer] = CellOperationRecord(
        operation_id=newer,
        kind="ensure",
        status="completed",
        phase="completed",
        request_digest="newer",
        fencing_epoch=5,
    )

    restarted = object.__new__(CodeRestorationEngine)
    restarted.root = tmp_path
    restarted._manager = lambda _workspace: manager
    first = await restarted.apply(value, {})
    assert first["applied"] is False and first["safe_to_release"] is True
    assert first["superseded_before_effect"] is True
    assert "retained_source_fencing_epoch" not in first
    assert state.fencing_epoch == 5 and state.last_operation_id == newer
    assert not archive.exists()
    assert json.loads(receipt_path.read_text())["state"] == "superseded"

    second = await restarted.apply(value, {})
    assert second == first
    assert state.fencing_epoch == 5 and state.last_operation_id == newer


async def test_equal_target_fence_foreign_owner_is_terminal_superseded(tmp_path):
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    engine, manager, state, receipts = rejection_engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "a" * 64})
    engine.begin_preflight(value)
    assert engine._claim_preflight(value) == "start"
    receipt_path = engine._directory(value.operation_id) / "activation.json"
    archive = receipt_path.parent / "code.tar"
    archive.write_bytes(b"candidate")

    foreign = UUID(int=92)
    state.fencing_epoch = value.fencing_epoch
    state.last_operation_id = foreign
    receipts[foreign] = CellOperationRecord(
        operation_id=foreign,
        kind="ensure",
        status="completed",
        phase="completed",
        request_digest="foreign",
        fencing_epoch=value.fencing_epoch,
    )

    restarted = object.__new__(CodeRestorationEngine)
    restarted.root = tmp_path
    restarted._manager = lambda _workspace: manager
    first = await restarted.apply(value, {})
    assert first["superseded_before_effect"] is True
    assert "retained_source_fencing_epoch" not in first
    assert state.fencing_epoch == value.fencing_epoch
    assert state.last_operation_id == foreign
    assert receipts[foreign].request_digest == "foreign"
    assert not archive.exists()

    assert await restarted.apply(value, {}) == first
    assert state.last_operation_id == foreign


@pytest.mark.parametrize("checkpoint", range(3))
async def test_cancel_at_each_preflight_await_recovers_safe_negative(tmp_path, checkpoint):
    import asyncio

    engine, manager, state, _ = rejection_engine(tmp_path / str(checkpoint))
    value = request().model_copy(update={"binding_digest": "e" * 64})
    reached = [asyncio.Event() for _ in range(3)]
    gates = [asyncio.Event() for _ in range(3)]

    async def interrupted_preflight(*_args):
        for index in range(3):
            reached[index].set()
            await gates[index].wait()
        pytest.fail("preflight must be cancelled")

    engine._activation_preflight = interrupted_preflight
    archive = engine._directory(value.operation_id) / "code.tar"
    archive.write_bytes(b"candidate")
    task = asyncio.create_task(engine.apply(value, {}))
    for index in range(checkpoint):
        await reached[index].wait()
        gates[index].set()
    await reached[checkpoint].wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    receipt = json.loads((archive.parent / "activation.json").read_text())
    assert receipt["state"] == "preflight"
    assert receipt["preflight_attempted"] is True
    assert receipt["effects_admitted"] is False
    assert receipt["retained_source_fencing_epoch"] == 3

    restarted = object.__new__(CodeRestorationEngine)
    restarted.root = engine.root
    restarted._manager = lambda _workspace: manager
    restarted._activation_preflight = lambda *_args: pytest.fail(
        "recovery must not repeat a possibly interrupted preflight"
    )
    observed = await restarted.apply(value, {})
    assert observed["applied"] is False
    assert observed["rejected_before_effect"] is True
    assert observed["retained_source_fencing_epoch"] == 3
    assert state.fencing_epoch == 4
    assert not archive.exists()


async def test_rejected_archive_cleanup_retries_after_failure(tmp_path, monkeypatch):
    from pathlib import Path

    from omnia_orchestrator.core.cell_resources import CellIdentityConflict

    engine, manager, state, _ = rejection_engine(tmp_path)
    value = request().model_copy(update={"binding_digest": "d" * 64})
    engine._state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        CellIdentityConflict("detached database")
    )
    archive = engine._directory(value.operation_id) / "code.tar"
    archive.write_bytes(b"candidate")
    original_unlink = Path.unlink
    failed = False

    def flaky_unlink(path, *args, **kwargs):
        nonlocal failed
        if path == archive and not failed:
            failed = True
            raise PermissionError("archive busy")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    with pytest.raises(PermissionError, match="archive busy"):
        await engine.apply(value, {})
    assert archive.exists()
    assert state.fencing_epoch == 4

    restarted = object.__new__(CodeRestorationEngine)
    restarted.root = tmp_path
    restarted._manager = lambda _workspace: manager
    result = await restarted.apply(value, {})
    assert result["rejected_before_effect"] is True
    assert not archive.exists()


def test_ddl_during_dump_is_rejected_before_ready():
    from omnia_orchestrator.services.code_restoration_engine import verify_post_dump_catalog
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    before = DataContract(version=1, tables=[])
    after = DataContract.model_validate(
        {
            "version": 1,
            "tables": [{"name": "clients", "columns": [{"name": "id", "type": "uuid"}]}],
        }
    )
    with pytest.raises(RuntimeError, match="during export"):
        verify_post_dump_catalog(before, [], [], after, [], [])


async def test_coordinator_restart_closes_gap_before_engine_journal(tmp_path, monkeypatch):
    import base64

    from omnia_orchestrator.schemas.code_restoration import (
        CodeRestorationPrepare,
        RestorationSourceBindingV2,
    )
    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services.code_restorations import CodeRestorationService
    from omnia_orchestrator.services.restoration_data_contract import DataContract
    from tests.test_code_restorations import binding_payload

    engine, prepared = untouched_engine(tmp_path / "engine", monkeypatch)
    _, rejection_manager, _, _ = rejection_engine(tmp_path / "rejection-state")
    engine.manager.state_store = rejection_manager.state_store
    engine.manager._spec_from_state = rejection_manager._spec_from_state
    binding_model = RestorationSourceBindingV2.model_validate(binding_payload())
    contract = DataContract(version=1, tables=[])
    prepared["live_contract"] = contract.model_dump(mode="json")

    async def read_source(_volume):
        return {"src/page.tsx": b"current"}

    engine.manager.docker = SimpleNamespace(read_workspace_source_files=read_source)
    monkeypatch.setattr(module, "catalog_contract", lambda _backend: (contract, []))
    monkeypatch.setattr(
        module,
        "observe_live_source",
        lambda *_args, **_kwargs: {
            name: getattr(binding_model, name)
            for name in (
                "serving_route_digest",
                "serving_release_digest",
                "controller_resource_digest",
                "controller_incarnation_digest",
                "controller_generation_digest",
                "provider_digest",
                "source_artifact_digest",
                "database_identity_digest",
                "database_schema_digest",
                "database_role_binding_digest",
                "database_system_identifier",
            )
        },
    )

    async def prepare(_):
        return {
            **prepared,
            "binding": binding_payload(),
            "state": "ready",
            "candidate_id": str(UUID(int=5)),
            "report": {
                "revision": 1,
                "mode": "adapted",
                "changes": [],
                "retained_data": [],
                "unavailable_features": [],
                "warnings": [],
                "blockers": [],
                "next_actions": [],
            },
        }

    async def failed_apply(*_):
        raise RuntimeError("controller failed before creating activation intent")

    engine.prepare, engine.apply = prepare, failed_apply
    service = CodeRestorationService(root=tmp_path / "journal", engine=engine)
    binding_digest = RestorationSourceBindingV2.model_validate(binding_payload()).digest()
    body = request().model_copy(update={"binding_digest": binding_digest})
    await service.prepare(
        CodeRestorationPrepare(
            **body.model_dump(
                exclude={
                    "candidate_id",
                    "report_revision",
                    "expected_fencing_epoch",
                    "fencing_epoch",
                    "binding_digest",
                }
            ),
            fencing_epoch=3,
            binding_contract_version=2,
            files=[{"path": "src/page.tsx", "content_base64": base64.b64encode(b"old").decode()}],
        )
    )
    await service.drain()
    await service.apply(body)
    await service.drain()
    assert service._read(body.workspace_id, body.operation_id)["state"] == "reconciling"
    restarted = CodeRestorationService(root=tmp_path / "journal", engine=engine)
    await restarted.recover()
    await restarted.drain()
    observed = restarted._read(body.workspace_id, body.operation_id)
    assert observed["state"] == "failed"
    assert observed["observed"]["safe_to_release"] is True
    assert engine.calls == []


def plain_prepare_request():
    import base64

    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare

    return CodeRestorationPrepare(
        **request().model_dump(
            exclude={
                "candidate_id",
                "report_revision",
                "expected_fencing_epoch",
                "fencing_epoch",
                "binding_digest",
            }
        ),
        fencing_epoch=3,
        files=[{"path": "src/page.tsx", "content_base64": base64.b64encode(b"old").decode()}],
    )


async def test_prepare_copies_current_data_into_candidate_without_database_policy(
    tmp_path, monkeypatch
):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services import project_machine
    from omnia_orchestrator.services.restoration_data_contract import DataContract
    from tests.test_project_machine_manifest import payload

    for removed in ("load_policy", "stage_policy", "install_policy", "recover_policy"):
        assert not hasattr(module, removed)
    manifest = MachineManifest.model_validate(payload())
    # An ordinary table without any owner column must not block restoration.
    contract = DataContract.model_validate(
        {
            "version": 1,
            "tables": [
                {
                    "name": "price_list",
                    "columns": [{"name": "id", "type": "uuid"}, {"name": "title", "type": "text"}],
                }
            ],
        }
    )
    events, statements = [], []
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(cell_required_free_disk_bytes=0)
    source = SimpleNamespace(
        workspace_volume="live-code",
        is_running=lambda: True,
        name="source",
        _metadata=lambda: {"epoch": 3},
        _container=lambda: None,
        _project_postgres=lambda: None,
    )
    machine = SimpleNamespace(state=lambda: {"epoch": 3, "manifest": manifest.model_dump()})
    candidate = SimpleNamespace(
        name="candidate",
        workspace_volume="candidate-code",
        base_image="image",
        stop=lambda: events.append("stop"),
        remove=lambda: pytest.fail("candidate must not restart for a database policy"),
        ensure=lambda *args: pytest.fail("candidate must not restart for a database policy"),
        _container=lambda: __import__(
            "tests.test_restoration_execution_cancellation",
            fromlist=["candidate_container"],
        ).candidate_container(plain_prepare_request()),
    )
    state = SimpleNamespace(workspace_id=UUID(int=2))

    async def read_sources(_volume):
        return {}

    manager = SimpleNamespace(
        operation_lock=Lock(),
        machine_runtime=SimpleNamespace(parts=lambda _: (machine, source)),
        docker=SimpleNamespace(read_workspace_source_files=read_sources),
    )
    engine._manager = lambda _: manager
    engine._state = lambda *args, **kwargs: state
    monkeypatch.setattr(module, "validate_supported_runtime", lambda _files: manifest)
    monkeypatch.setattr(module, "verify_source_inventory", lambda *_: None)
    monkeypatch.setattr(
        module,
        "empty_database_materializer",
        lambda *_args: pytest.fail("nonempty restoration must not select an empty materializer"),
    )
    monkeypatch.setattr(project_machine, "machine_remaining_seconds", lambda value: value or 1)

    async def workspace_files(*_):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", workspace_files
    )

    def catalog(backend):
        events.append("catalog:" + backend.name)
        return contract, []

    def sql(backend, text, **_kwargs):
        statements.append((backend.name, text))
        return b""

    def inventory(backend, *, observed_on):
        from omnia_orchestrator.services.versioning.contracts import InventoryReport

        events.append("inventory:" + backend.name)
        return InventoryReport(
            presence="present",
            coverage="complete",
            schema_analysis="complete",
            observed_on=observed_on,
        )

    monkeypatch.setattr(module, "catalog_contract", catalog)
    monkeypatch.setattr(module, "describe_live_catalog", lambda backend: (*catalog(backend), []))
    monkeypatch.setattr(module, "candidate_contract", lambda *_: contract)
    monkeypatch.setattr(module, "admin_sql", sql)
    monkeypatch.setattr(module, "observe_database", inventory)
    engine._dump = lambda backend: events.append("dump:" + backend.name) or b"COPY price_list;"

    async def make_candidate(*_):
        return candidate

    async def start(backend, _manifest, epoch):
        events.append(f"start:{backend.name}:{epoch}")

    async def cleanup(*_):
        events.append("cleanup")

    engine._candidate = make_candidate
    engine._seed_source = lambda *_: events.append("seed")
    engine._command = lambda backend, container, argv, *_: events.append("command:" + argv[0])
    engine._disable_egress = lambda backend: events.append("egress-off")
    engine._start = start
    engine._verify_source = lambda *_: events.append("verify")
    engine._capture_code = lambda *_args, **_kwargs: "digest"
    engine._cleanup_candidate = cleanup
    result = await engine.prepare(plain_prepare_request())
    assert result["state"] == "ready", result["report"]
    assert statements == [("candidate", "COPY price_list;")]
    assert "omnia_runtime" not in json.dumps(result)
    assert "policy" not in json.dumps(result)
    assert (
        events.index("dump:source") < events.index("egress-off") < events.index("catalog:candidate")
    )
    assert "start:candidate:1" in events
    assert result["report"]["blockers"] == []
    assert result["report"]["database_state"] == "present"
    # Row presence is observed on the source before any schema analysis.
    assert events.index("inventory:source") < events.index("catalog:source")
    assert "live-code" not in json.dumps(statements)


def test_prepare_legacy_release_repair_uses_exact_proven_epochs():
    from omnia_orchestrator.services.cell_state import CellOperationRecord

    request = plain_prepare_request()
    generation_run_id = UUID(int=31)
    ensure = CellOperationRecord(
        operation_id=UUID(int=32),
        kind="ensure",
        status="completed",
        phase="completed",
        request_digest="a" * 64,
        fencing_epoch=2,
        generation_run_id=generation_run_id,
        bundle_state="resources_ready",
    )
    release = CellOperationRecord(
        operation_id=UUID(int=33),
        kind="release",
        status="completed",
        phase="completed",
        request_digest="b" * 64,
        fencing_epoch=3,
        generation_run_id=generation_run_id,
        bundle_state="resources_ready",
    )
    state = SimpleNamespace(
        fencing_epoch=3,
        last_operation_id=release.operation_id,
        operations=(ensure, release),
        operation=lambda operation_id: {
            ensure.operation_id: ensure,
            release.operation_id: release,
        }.get(operation_id),
    )
    repaired = object()
    calls = []

    def repair(workspace_id, **proof):
        calls.append((workspace_id, proof))
        return repaired

    manager = SimpleNamespace(
        state_store=SimpleNamespace(repair_legacy_release_serving_epoch=repair)
    )

    result = CodeRestorationEngine._repair_legacy_release_receipt(
        manager,
        request,
        state,
        {"epoch": 2, "ready_epoch": 2},
    )

    assert result is repaired
    assert calls == [
        (
            request.workspace_id,
            {
                "expected_control_fencing_epoch": 3,
                "expected_last_operation_id": release.operation_id,
                "retained_fencing_epoch": 2,
                "machine_epoch": 2,
                "machine_ready_epoch": 2,
            },
        )
    ]


async def test_activation_and_recovery_reuse_live_database_without_policy(tmp_path):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from tests.test_project_machine_manifest import payload

    manifest = MachineManifest.model_validate(payload()).model_dump(mode="json")
    events = []
    metadata_path = tmp_path / "machine" / "metadata.json"
    machine_path = tmp_path / "machine" / "machine.json"
    write_controller_json(metadata_path, {"epoch": 3})
    write_controller_json(machine_path, {"epoch": 3, "manifest": manifest})

    def metadata():
        return json.loads(metadata_path.read_text())

    backend = SimpleNamespace(
        metadata_path=metadata_path,
        _metadata=metadata,
        workspace_volume="old-code",
        ensure=lambda _manifest, epoch: events.append(("ensure", backend.workspace_volume, epoch)),
        remove=lambda: events.append("remove"),
        import_volume=lambda *_: pytest.fail("the live database is never overwritten"),
    )
    machine = SimpleNamespace(
        path=machine_path,
        state=lambda: json.loads(machine_path.read_text()),
    )
    adapter = SimpleNamespace(
        parts=lambda _: (machine, backend),
        _start_boundary=lambda *args: events.append("boundary"),
    )
    manager = SimpleNamespace(machine_runtime=adapter)
    engine = object.__new__(CodeRestorationEngine)

    async def start(_backend, _manifest, epoch):
        events.append(("start", epoch))

    async def fence(*args):
        events.append("fence")

    engine._start = start
    engine._complete_fence = fence
    prepared = {"manifest": manifest, "base_image": "image"}
    await engine._activate_code(
        manager,
        object(),
        backend,
        prepared,
        "new-code",
        4,
        before_writers=lambda: events.append("admit"),
    )
    assert events == [
        "admit",
        ("ensure", "new-code", 4),
        ("start", 4),
        "boundary",
        "fence",
    ]
    assert metadata()["active_code_volume"] == "new-code"
    events.clear()
    old = {
        "metadata": {"epoch": 3},
        "machine": {"epoch": 3, "manifest": manifest},
        "workspace_volume": "old-code",
        "contract": {"version": 1, "tables": []},
        # Intents recorded by the removed protected mode are ignored.
        "policy": {"epoch": 2, "contract": {"version": 1, "tables": []}},
    }
    await engine._recover_old(manager, object(), backend, old, 4)
    assert events == ["remove", ("ensure", "old-code", 4), ("start", 4), "boundary", "fence"]
    assert metadata()["active_code_volume"] == "old-code"


async def test_preserved_database_start_failure_after_admission_is_forward_only(
    tmp_path, monkeypatch
):
    import hashlib
    from dataclasses import dataclass

    from omnia_orchestrator.core.cell_resources import CellResourceError
    from omnia_orchestrator.routers.runtime import _workspace_revision

    value = request().model_copy(update={"binding_digest": "b" * 64})
    engine = Engine(tmp_path)
    events = []

    @dataclass
    class PreserveBackend:
        stem: str = "fixture"
        workspace_volume: str = "old-code"
        project_postgres_volume: str = "live-business-db"

        def _metadata(self):
            return {"manifest": {}}

        def _lookup(self, _collection, _name, _kind):
            return None

        def labels(self, _kind):
            return {"omnia.machine": "true"}

        def import_volume(self, name, _path):
            events.append(("code-import", name))

        def remove(self):
            events.append("old-stopped")

    backend = PreserveBackend()
    backend.client = SimpleNamespace(volumes=object())
    machine = SimpleNamespace(state=lambda: {"manifest": {}})
    engine.backend = backend
    engine.manager.machine_runtime.parts = lambda _state: (machine, backend)

    async def read_source_files(_name):
        return {"src/page.tsx": b"current"}

    engine.manager.docker = SimpleNamespace(read_workspace_source_files=read_source_files)
    engine.manager._state_labels = lambda *_args: {"omnia.cell": "true"}

    async def ensure_volume(name, _labels):
        events.append(("volume-prepared", name))

    engine.manager._ensure_volume = ensure_volume
    engine.begin_preflight = lambda _request: None
    engine._capture_preflight_serving_epoch = lambda _manager, _request: None
    engine._claim_preflight = lambda _request: "new"

    async def preflight(*_args):
        return engine.state, machine, backend

    engine._activation_preflight = preflight

    async def read_workspace(*_args):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", read_workspace
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.code_restoration_engine.catalog_contract",
        lambda _backend: ({"version": 1, "tables": []}, []),
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.code_restoration_engine.contract_matches",
        lambda _actual, _expected: True,
    )
    directory = engine._directory(value.operation_id)
    (directory / "code.tar").write_bytes(b"code")
    prepared = {
        "workspace_revision": _workspace_revision({"src/page.tsx": "current"}),
        "current_files": [
            {
                "path": "src/page.tsx",
                "sha256": hashlib.sha256(b"current").hexdigest(),
            }
        ],
        "live_contract": {"version": 1, "tables": []},
        "code_digest": hashlib.sha256(b"code").hexdigest(),
        "database_strategy": "preserve_current",
        "manifest": {},
    }

    async def fail_after_admission(*_args, before_writers):
        receipt = json.loads((directory / "activation.json").read_text())
        assert receipt["effects_admitted"] is False
        before_writers()
        admitted = json.loads((directory / "activation.json").read_text())
        assert admitted["state"] == "target_writers_admitted"
        assert admitted["effects_admitted"] is True
        events.append("target-writer-started")
        raise RuntimeError("target start failed after writer admission")

    engine._activate_code = fail_after_admission

    try:
        await engine.apply(value, prepared)
    except CellResourceError as exc:
        assert "forward recovery" in str(exc)
    else:
        pytest.fail(
            f"activation returned after PONR; events={events!r}; "
            f"error={(directory / 'activation-error.log').read_text()!r}"
        )

    receipt = json.loads((directory / "activation.json").read_text())
    assert receipt["state"] == "target_recovery"
    assert receipt["database_volume"] == "live-business-db"
    assert "recover" not in engine.calls
    assert events[-1] == "target-writer-started"


def _activation_replay_fixture(monkeypatch, *, services_ready: bool):
    import hashlib
    from uuid import uuid5

    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services.cell_state import CellOperationRecord
    from omnia_orchestrator.services.project_machine import MachineManifest
    from tests.test_project_machine_manifest import payload

    manifest = MachineManifest.model_validate(payload())
    workspace_id = UUID(int=82)
    generation_run_id = UUID(int=83)
    source_epoch = 11
    target_epoch = 12
    target_volume = "target-code"
    live_database = "live-db"
    operations = {}
    state = SimpleNamespace(
        workspace_id=workspace_id,
        project_id=UUID(int=84),
        owner_id=UUID(int=85),
        profile_version=1,
        resource_names=object(),
        fencing_epoch=source_epoch,
        active_generation_run_id=generation_run_id,
        active_generation_fencing_epoch=source_epoch,
        last_operation_id=None,
        operation=lambda operation_id: operations.get(operation_id),
    )

    class Store:
        def load(self, _workspace_id):
            return state

        def begin(self, spec, mutation, *, kind, phase, resource_names):
            operations[mutation.operation_id] = CellOperationRecord(
                operation_id=mutation.operation_id,
                kind=kind,
                status="running",
                phase=phase,
                request_digest=mutation.request_digest,
                fencing_epoch=mutation.fencing_epoch,
                generation_run_id=spec.generation_run_id,
            )
            state.fencing_epoch = mutation.fencing_epoch
            state.last_operation_id = mutation.operation_id

        def complete(self, _workspace_id, mutation, *, phase, bundle_state):
            operation = operations[mutation.operation_id]
            operations[mutation.operation_id] = CellOperationRecord(
                operation_id=operation.operation_id,
                kind=operation.kind,
                status="completed",
                phase=phase,
                request_digest=operation.request_digest,
                fencing_epoch=operation.fencing_epoch,
                generation_run_id=operation.generation_run_id,
                bundle_state=bundle_state,
            )

    class NamedLock:
        @asynccontextmanager
        async def hold_named(self, _name):
            yield

    class Container:
        def __init__(self):
            self.status = "running"
            self.labels = {"omnia.fencing_epoch": str(target_epoch)}
            self.attrs = {
                "Config": {"Labels": self.labels},
                "Mounts": [{"Destination": "/workspace", "Name": target_volume}],
            }

        def reload(self):
            return None

    events = []
    container = Container()
    backend = SimpleNamespace(
        workspace_volume=target_volume,
        project_postgres_volume=live_database,
        _container=lambda: container,
        service_status=lambda *_args, **_kwargs: {
            "state": "running" if services_ready else "failed",
            "ready": services_ready,
        },
        remove_machine=lambda: events.append("remove-app"),
    )
    runtime = SimpleNamespace(
        parts=lambda _state: (object(), backend),
        _start_boundary=lambda *_args: events.append("boundary"),
    )
    manager = SimpleNamespace(
        state_store=Store(),
        machine_runtime=runtime,
        capacity_lock=NamedLock(),
        operation_lock=NamedLock(),
        _capacity_reservation_store=lambda: SimpleNamespace(
            rebind=lambda *_args: events.append("rebind")
        ),
    )
    engine = object.__new__(CodeRestorationEngine)
    monkeypatch.setattr(module, "validate_supported_runtime", lambda _files: manifest)

    async def workspace_files(*_args):
        return {"src/page.tsx": "candidate"}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files",
        workspace_files,
    )
    expected_operation_id = uuid5(workspace_id, "code-activation-" + str(target_epoch))
    expected_digest = hashlib.sha256(
        (str(workspace_id) + ":restore:" + str(target_epoch)).encode()
    ).hexdigest()
    return SimpleNamespace(
        engine=engine,
        manager=manager,
        state=state,
        backend=backend,
        manifest=manifest,
        events=events,
        target_epoch=target_epoch,
        target_volume=target_volume,
        live_database=live_database,
        operations=operations,
        operation_id=expected_operation_id,
        operation_digest=expected_digest,
    )


async def test_running_target_replay_durably_completes_controller_fence(monkeypatch):
    setup = _activation_replay_fixture(monkeypatch, services_ready=True)
    setup.state.fencing_epoch = setup.target_epoch
    setup.state.active_generation_fencing_epoch = setup.target_epoch
    setup.state.last_operation_id = setup.operation_id
    setup.operations[setup.operation_id] = SimpleNamespace(
        operation_id=setup.operation_id,
        kind="code_restore",
        status="running",
        fencing_epoch=setup.target_epoch,
        request_digest=setup.operation_digest,
        generation_run_id=setup.state.active_generation_run_id,
    )

    async def running(*_args):
        return True

    setup.engine._running_matches = running
    setup.engine._activate_code = lambda *_args, **_kwargs: pytest.fail(
        "running target must not be started twice"
    )

    await setup.engine.activation_start_code_only_target(
        setup.manager,
        setup.state,
        code_volume=setup.target_volume,
        database_volume=setup.live_database,
        epoch=setup.target_epoch,
    )

    operation = setup.state.operation(setup.operation_id)
    assert setup.state.fencing_epoch == setup.target_epoch
    assert setup.state.last_operation_id == setup.operation_id
    assert operation.status == "completed"
    assert operation.request_digest == setup.operation_digest
    assert setup.events == ["rebind"]


async def test_running_services_replay_repairs_boundary_without_double_start(monkeypatch):
    setup = _activation_replay_fixture(monkeypatch, services_ready=True)
    observations = 0

    async def running(*_args):
        nonlocal observations
        observations += 1
        return observations > 1

    setup.engine._running_matches = running
    setup.engine._activate_code = lambda *_args, **_kwargs: pytest.fail(
        "running services must not be started twice"
    )

    await setup.engine.activation_start_code_only_target(
        setup.manager,
        setup.state,
        code_volume=setup.target_volume,
        database_volume=setup.live_database,
        epoch=setup.target_epoch,
    )

    assert setup.events == ["boundary", "rebind"]
    assert observations == 2


async def test_source_restart_replay_repairs_boundary_without_duplicate_writer_start(
    monkeypatch,
):
    setup = _activation_replay_fixture(monkeypatch, services_ready=True)
    source_epoch = 11
    source_volume = "source-code"
    setup.state.fencing_epoch = source_epoch
    setup.backend.workspace_volume = source_volume
    setup.backend._container().labels["omnia.fencing_epoch"] = str(source_epoch)
    setup.backend._container().attrs["Mounts"][0]["Name"] = source_volume
    setup.manager.machine_runtime.parts = lambda _state: (
        SimpleNamespace(
            state=lambda: {
                "epoch": source_epoch,
                "manifest": setup.manifest.model_dump(mode="json"),
            }
        ),
        setup.backend,
    )
    observations = 0

    async def running(*_args):
        nonlocal observations
        observations += 1
        return observations > 1

    setup.engine._running_matches = running

    async def duplicate_start(*_args, **_kwargs):
        pytest.fail("source writers must not be started twice")

    setup.engine._start = duplicate_start

    await setup.engine.activation_restart_source(
        setup.manager,
        setup.state,
        code_volume=source_volume,
        database_volume=setup.live_database,
    )

    assert setup.events == ["boundary"]
    assert observations == 2


@pytest.mark.asyncio
async def test_empty_activation_and_recovery_switch_code_and_database_as_one_pair(tmp_path):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from tests.test_project_machine_manifest import payload

    events = []
    manifest = MachineManifest.model_validate(payload()).model_dump(mode="json")
    metadata_path = tmp_path / "docker.json"
    machine_path = tmp_path / "machine" / "machine.json"
    write_controller_json(
        metadata_path,
        {"epoch": 3, "active_code_volume": "old-code", "active_database_volume": "old-db"},
    )
    write_controller_json(machine_path, {"epoch": 3, "manifest": manifest})

    backend = SimpleNamespace(
        metadata_path=metadata_path,
        workspace_volume="old-code",
        _metadata=lambda: json.loads(metadata_path.read_text()),
        ensure=lambda _manifest, epoch: events.append(("ensure", backend.workspace_volume, epoch)),
        remove=lambda: events.append("remove"),
    )
    machine = SimpleNamespace(path=machine_path, state=lambda: json.loads(machine_path.read_text()))
    adapter = SimpleNamespace(
        parts=lambda _: (machine, backend),
        _start_boundary=lambda *args: events.append("boundary"),
    )
    manager = SimpleNamespace(machine_runtime=adapter)
    engine = object.__new__(CodeRestorationEngine)

    async def start(_backend, _manifest, epoch):
        events.append(("start", epoch))

    async def fence(*_args):
        events.append("fence")

    engine._start = start
    engine._complete_fence = fence
    prepared = {
        "manifest": manifest,
        "base_image": "image",
        "database_strategy": "replace_verified_empty",
        "target_database_volume": "new-db",
    }
    await engine._activate_code(
        manager,
        object(),
        backend,
        prepared,
        "new-code",
        4,
        before_writers=lambda: events.append("admit"),
    )
    active = json.loads(metadata_path.read_text())
    assert (active["active_code_volume"], active["active_database_volume"]) == (
        "new-code",
        "new-db",
    )

    old = {
        "metadata": {"epoch": 3},
        "machine": {"epoch": 3, "manifest": manifest},
        "workspace_volume": "old-code",
        "database_volume": "old-db",
        "contract": {"version": 1, "tables": []},
    }
    await engine._recover_old(manager, object(), backend, old, 4)
    recovered = json.loads(metadata_path.read_text())
    assert (recovered["active_code_volume"], recovered["active_database_volume"]) == (
        "old-code",
        "old-db",
    )


@pytest.mark.asyncio
async def test_empty_apply_stops_writers_then_fails_closed_on_race_insert(monkeypatch):
    from omnia_orchestrator.core.cell_resources import CellIdentityConflict
    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services.restoration_empty import EmptyDatabaseWitness

    events = []
    prepared_witness = EmptyDatabaseWitness(
        project_id=UUID(int=3),
        workspace_id=UUID(int=2),
        operation_id=UUID(int=1),
        database_identity_digest="a" * 64,
        catalog_digest="b" * 64,
        objects_digest="c" * 64,
        technical_state_digest="d" * 64,
        identity_rows_digest="e" * 64,
        observation_kind="source",
        identity_relations=[],
    )
    changed = prepared_witness.model_copy(
        update={"observation_kind": "quiesced_source", "objects_digest": "f" * 64}
    )
    backend = SimpleNamespace(stop_machine=lambda: events.append("writers-stopped"))
    monkeypatch.setattr(
        module,
        "observe_empty_database",
        lambda *_args, **_kwargs: events.append("witness") or changed,
    )
    engine = object.__new__(CodeRestorationEngine)

    with pytest.raises(CellIdentityConflict, match="no longer empty"):
        await engine._quiesce_empty_source(
            backend,
            request().model_copy(update={"operation_id": UUID(int=1)}),
            {"empty_witness": prepared_witness.model_dump(mode="json")},
        )
    assert events == ["writers-stopped", "witness"]


async def test_prepared_runtime_identity_from_protected_era_still_recovers(tmp_path, monkeypatch):
    engine, prepared = untouched_engine(tmp_path, monkeypatch)
    prepared["source_runtime"] = {**prepared["source_runtime"], "policy_epoch": 2}
    result = await engine.observe(request(), prepared)
    assert result["applied"] is False and result["safe_to_release"] is True
    assert engine.calls == ["recover"]


async def test_sleeping_draft_is_reported_as_needs_changes_not_failure(tmp_path, monkeypatch):
    """A draft whose machine is not running (editor closed) must yield an
    actionable report, not the generic «Не удалось завершить проверку»."""
    from types import SimpleNamespace

    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services import code_restoration_engine as module
    from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine
    from tests.test_project_machine_manifest import payload

    manifest = MachineManifest.model_validate(payload())
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(cell_required_free_disk_bytes=0)
    source = SimpleNamespace(is_running=lambda: False, name="source")
    manager = SimpleNamespace(
        operation_lock=Lock(),
        machine_runtime=SimpleNamespace(parts=lambda _: (object(), source)),
    )
    engine._manager = lambda _: manager
    engine._state = lambda *args, **kwargs: SimpleNamespace(workspace_id=UUID(int=2))
    monkeypatch.setattr(module, "validate_supported_runtime", lambda _files: manifest)
    result = await engine.prepare(plain_prepare_request())
    assert result["state"] == "needs_changes"
    assert result["report"]["blockers"] == [
        "Откройте текущую версию и повторите подготовку восстановления."
    ]
