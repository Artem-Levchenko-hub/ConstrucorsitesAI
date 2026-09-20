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
    receipt = json.loads(
        (engine._directory(value.operation_id) / "activation.json").read_text()
    )
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
    after = DataContract.model_validate({
        "version": 1,
        "tables": [{"name": "clients", "columns": [{"name": "id", "type": "uuid"}]}],
    })
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
    contract = DataContract.model_validate({"version": 1, "tables": [{
        "name": "price_list",
        "columns": [{"name": "id", "type": "uuid"}, {"name": "title", "type": "text"}],
    }]})
    events, statements = [], []
    engine = object.__new__(CodeRestorationEngine)
    engine.root = tmp_path
    engine.settings = SimpleNamespace(cell_required_free_disk_bytes=0)
    source = SimpleNamespace(
        workspace_volume="live-code", is_running=lambda: True, name="source",
        _metadata=lambda: {"epoch": 3}, _container=lambda: None, _project_postgres=lambda: None,
    )
    machine = SimpleNamespace(state=lambda: {"epoch": 3, "manifest": manifest.model_dump()})
    candidate = SimpleNamespace(
        name="candidate", workspace_volume="candidate-code", base_image="image",
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
    monkeypatch.setattr(project_machine, "machine_remaining_seconds", lambda value: value or 1)

    async def workspace_files(*_):
        return {"src/page.tsx": "current"}

    monkeypatch.setattr("omnia_orchestrator.routers.workspace._read_agent_workspace_files",
                        workspace_files)

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
            presence="present", coverage="complete", schema_analysis="complete",
            observed_on=observed_on,
        )

    monkeypatch.setattr(module, "catalog_contract", catalog)
    monkeypatch.setattr(
        module, "describe_live_catalog", lambda backend: (*catalog(backend), [])
    )
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
    engine._command = lambda backend, container, argv, *_: events.append(
        "command:" + argv[0]
    )
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
    assert events.index("dump:source") < events.index("egress-off") < events.index(
        "catalog:candidate"
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
        metadata_path=metadata_path, _metadata=metadata, workspace_volume="old-code",
        ensure=lambda _manifest, epoch: events.append(("ensure", backend.workspace_volume, epoch)),
        remove=lambda: events.append("remove"),
        import_volume=lambda *_: pytest.fail("the live database is never overwritten"),
    )
    machine = SimpleNamespace(
        path=machine_path, state=lambda: json.loads(machine_path.read_text()),
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
    await engine._activate_code(manager, object(), backend, prepared, "new-code", 4)
    assert events == [("ensure", "new-code", 4), ("start", 4), "boundary", "fence"]
    assert metadata()["active_code_volume"] == "new-code"
    events.clear()
    old = {"metadata": {"epoch": 3}, "machine": {"epoch": 3, "manifest": manifest},
           "workspace_volume": "old-code", "contract": {"version": 1, "tables": []},
           # Intents recorded by the removed protected mode are ignored.
           "policy": {"epoch": 2, "contract": {"version": 1, "tables": []}}}
    await engine._recover_old(manager, object(), backend, old, 4)
    assert events == ["remove", ("ensure", "old-code", 4), ("start", 4), "boundary", "fence"]
    assert metadata()["active_code_volume"] == "old-code"


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
