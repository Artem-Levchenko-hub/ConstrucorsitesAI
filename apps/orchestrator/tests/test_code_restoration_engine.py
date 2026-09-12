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
    monkeypatch.setattr(
        "omnia_orchestrator.services.code_restoration_engine.load_policy", lambda _: None
    )

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


async def test_coordinator_restart_closes_gap_before_engine_journal(tmp_path, monkeypatch):
    import base64

    from omnia_orchestrator.schemas.code_restoration import CodeRestorationPrepare
    from omnia_orchestrator.services.code_restorations import CodeRestorationService

    engine, prepared = untouched_engine(tmp_path / "engine", monkeypatch)

    async def prepare(_):
        return {
            **prepared,
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
    body = request()
    await service.prepare(
        CodeRestorationPrepare(
            **body.model_dump(
                exclude={
                    "candidate_id",
                    "report_revision",
                    "expected_fencing_epoch",
                    "fencing_epoch",
                }
            ),
            fencing_epoch=3,
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
    assert engine.calls == ["recover"]
