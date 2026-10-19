"""Release-only handoff contracts with real orchestration and in-memory I/O.

The doubles below model row lookup and dispatch ownership, not SQL locking.
Database races and cross-process advisory locks remain integration-suite work.
"""
import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.models.generation_billing import GenerationBillingIntent, GenerationBillingPolicy
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.services import generation_runs
from yleum_api.services import project_cell_executor as executor
from yleum_api.services import project_cell_lifecycle as cell_lifecycle
from yleum_api.services.generation import completion_release, lifecycle, supervisor
from yleum_api.services.generation.completion_release import GenerationReleasePending
from yleum_api.services.generation.contracts import GenerationRuntime
from yleum_api.services.generation.recovery_contract import RecoveryContractError
from yleum_api.services.orchestrator_client import (
    OrchestratorReaderInventoryUnavailable,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cells import (
    ClaimedCellOperation,
    _hashed_error,
    claim_cell_operation_committed,
    complete_cell_operation,
    reserve_cell_operation,
)
from yleum_api.workers import generation as generation_worker

pytestmark = pytest.mark.asyncio


async def test_completed_cannot_bypass_a_retained_generation_cell_lease(monkeypatch):
    session = SimpleNamespace(scalar=AsyncMock(return_value=True))
    run = SimpleNamespace(id=uuid4(), status="running", agent_state={})
    acceptance = AsyncMock()
    billing = AsyncMock()
    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_acceptance.require_terminal_acceptance",
        acceptance,
    )
    monkeypatch.setattr("yleum_api.services.generation_billing.seal_terminal_locked", billing)

    with pytest.raises(RuntimeError, match="release"):
        await generation_runs.terminalize_generation_run_locked(session, run, status="completed")

    assert run.status == "running"
    billing.assert_not_awaited()


async def test_normal_cleanup_uses_the_completion_release_callback(monkeypatch):
    release = AsyncMock()
    completion_release = AsyncMock()
    runtime = GenerationRuntime(handle=SimpleNamespace(
        release=release, release_for_completion=completion_release,
    ))
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())

    await lifecycle.cleanup_generation_runtime(runtime, uuid4(), uuid4())

    completion_release.assert_awaited_once()
    release.assert_not_awaited()


async def test_primary_failure_never_claims_successful_body(monkeypatch):
    release = AsyncMock()
    completion_release = AsyncMock()
    runtime = GenerationRuntime(handle=SimpleNamespace(
        release=release, release_for_completion=completion_release,
    ))
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())

    await lifecycle.cleanup_generation_runtime(
        runtime, uuid4(), uuid4(), primary_error=RuntimeError("original build failed"),
    )

    release.assert_awaited_once()
    completion_release.assert_not_awaited()


class _Rows:
    def __init__(self, values):
        self.values = values

    def scalar_one_or_none(self):
        assert len(self.values) <= 1
        return self.values[0] if self.values else None

    def one_or_none(self):
        return self.scalar_one_or_none()

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.fixture
def release_store(monkeypatch):
    """Keep actual executor, lifecycle persistence, marker and product finalizer.

    Only SQL row I/O, acquisition of dispatch ownership and external controller
    transport are replaced. The controller initially denies readers, then returns
    the real typed receipt for the SAME operation and fencing epoch.
    """
    project_id, owner_id, run_id, message_id, snapshot_id, workspace_id = (
        uuid4() for _ in range(6)
    )
    operation_id = uuid4()
    run = SimpleNamespace(
        id=run_id, project_id=project_id, user_id=owner_id,
        assistant_message_id=message_id, status="running", response_mode="build",
        agent_state={}, error=None, finished_at=None, execution_backend="worker",
        execution_started_at=datetime.now(UTC), prompt_hash="e" * 64,
    )
    project = SimpleNamespace(
        id=project_id, owner_id=owner_id, current_snapshot_id=snapshot_id,
    )
    message = SimpleNamespace(
        id=message_id, project_id=project_id, role="assistant", tokens_out=25,
        tokens_in=10, snapshot_id=snapshot_id, content="saved build",
    )
    snapshot = SimpleNamespace(
        id=snapshot_id, project_id=project_id, commit_sha="a" * 40, preview_status="ready",
    )
    workspace = SimpleNamespace(
        id=workspace_id, project_id=project_id, owner_id=owner_id,
        generation_run_id=run_id, fencing_epoch=8, deleted_at=None, updated_at=None,
    )
    operation = SimpleNamespace(
        id=operation_id, workspace_id=workspace_id, generation_run_id=run_id,
        kind="release", status="pending", fencing_epoch=None, request_digest="b" * 64,
        result_payload=None, error=None, finished_at=None, next_attempt_at=None,
        capacity_reason=None, execution_run_id=None,
    )
    rows = {
        (GenerationRun, run_id): run, (Project, project_id): project,
        (Message, message_id): message, (Snapshot, snapshot_id): snapshot,
        (ProjectCellWorkspace, workspace_id): workspace,
        (ProjectCellOperation, operation_id): operation,
    }
    store = SimpleNamespace(
        run=run, project=project, message=message, snapshot=snapshot,
        workspace=workspace, operation=operation, rows=rows, commits=[], trace=[],
        active_activity=False, executor_lock_available=True, ownership_held=True, added=[],
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, entity, key, **_):
            return rows.get((entity, key))

        async def scalar(self, query, *_args, **_kwargs):
            rendered = str(query)
            if "pg_try_advisory_lock" in rendered:
                return store.executor_lock_available
            if "EXISTS" in rendered:
                if "project_cell_workspaces" in rendered:
                    return workspace.generation_run_id == run.id
                if "project_cell_activity_leases" in rendered:
                    return store.active_activity
                if "project_cell_operations" in rendered:
                    return False
            descriptions = getattr(query, "column_descriptions", [])
            if descriptions:
                entity = descriptions[0].get("entity")
                name = descriptions[0].get("name")
                if entity is GenerationRun:
                    return run.project_id if name == "project_id" else run
                if entity is Project:
                    return project.id
                if entity is Message:
                    return message
                if entity is ProjectCellOperation:
                    return operation
            raise AssertionError(f"unexpected scalar query: {rendered}")

        async def execute(self, query, *_args, **_kwargs):
            rendered = str(query)
            descriptions = getattr(query, "column_descriptions", [])
            if descriptions and descriptions[0].get("entity") is GenerationRun:
                if "idempotency_key =" in rendered:
                    return _Rows([])
                return _Rows([run])
            if descriptions and descriptions[0].get("entity") is ProjectCellOperation:
                return _Rows([operation] if operation.status in {"pending", "waiting_capacity"}
                             else [])
            return _Rows([])

        async def commit(self):
            store.commits.append({
                "marker": json.loads(json.dumps(run.agent_state)),
                "lease": workspace.generation_run_id,
                "operation_status": operation.status, "run_status": run.status,
            })

        async def rollback(self):
            pass

        async def flush(self):
            pass

        def add(self, value):
            store.added.append(value)

    store.factory = Session
    store.engine = SimpleNamespace(connect=Session)

    async def reserve(_session, **kwargs):
        assert kwargs == {
            "workspace_id": workspace_id, "generation_run_id": run_id,
            "kind": "release",
            "idempotency_key": f"generation:{run_id}:release:fixture-profile",
            "request": {},
        }
        return operation, False

    async def claim(_factory, claimed_id):
        assert claimed_id == operation_id
        assert operation.status == "pending"
        workspace.fencing_epoch += 1
        operation.fencing_epoch = workspace.fencing_epoch
        operation.status = "running"
        return envelope()

    async def reclaim(_factory, claimed_id):
        assert claimed_id == operation_id
        assert operation.status == "indeterminate"
        operation.status = "running"
        return envelope()

    def envelope():
        return ClaimedCellOperation(
            operation_id=operation_id, workspace_id=workspace_id, project_id=project_id,
            owner_id=owner_id, generation_run_id=run_id, kind="release", request={},
            request_digest=operation.request_digest, fencing_epoch=operation.fencing_epoch,
        )

    def receipt():
        return ProjectCellResourceResponse(
            workspace_id=workspace_id, state="resources_ready", provider_ref="fixture",
            fencing_epoch=operation.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        )

    async def control(request):
        # This proves the durable intent was committed BEFORE controller dispatch.
        assert any(completion_release.COMPLETION_RELEASE_KEY in c["marker"]
                   for c in store.commits)
        store.trace.append(("control", request.operation_id, request.fencing_epoch,
                            request.request_digest))
        if len(store.trace) == 1:
            raise OrchestratorReaderInventoryUnavailable("synthetic reader denial")
        return receipt()

    store.client = SimpleNamespace(control=AsyncMock(side_effect=control))
    monkeypatch.setattr(executor, "lock_workspace_operations", AsyncMock())
    monkeypatch.setattr(executor, "reserve_cell_operation", AsyncMock(side_effect=reserve))
    monkeypatch.setattr(executor, "HttpProjectCellOrchestratorClient", lambda: store.client)
    monkeypatch.setattr(
        "yleum_api.services.orchestrator_client.HttpProjectCellOrchestratorClient",
        lambda: store.client,
    )
    monkeypatch.setattr(cell_lifecycle, "claim_cell_operation_committed", claim)
    monkeypatch.setattr(cell_lifecycle, "reclaim_indeterminate_cell_operation_committed", reclaim)
    monkeypatch.setattr(executor, "_PROJECT_CELL_RELEASE_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(generation_runs, "compile_terminal_run_memory", AsyncMock())
    store.acceptance, store.billing = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_acceptance.require_terminal_acceptance",
        store.acceptance,
    )
    monkeypatch.setattr("yleum_api.services.generation_billing.seal_terminal_locked", store.billing)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())

    async def require_owner(owned_run_id, *, compatibility=True):
        assert owned_run_id == run.id and compatibility is False
        if not store.ownership_held:
            raise RecoveryContractError("synthetic executor ownership was lost")

    # Pure tests have no PostgreSQL connection. The SQL test below retains the
    # real _require_session_owner implementation and its pg_locks query.
    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_execution._require_session_owner", require_owner,
    )

    async def release():
        await executor._release_generation_lease(
            session_factory=Session, workspace_id=workspace_id, generation_run_id=run_id,
            profile_version="fixture-profile", completion_ready=True,
        )

    async def prepare_pending():
        with pytest.raises(GenerationReleasePending):
            await release()
        assert run.status == "running" and workspace.generation_run_id == run.id
        assert operation.status == "indeterminate"

    def allow_replay():
        monkeypatch.setattr(executor, "_PROJECT_CELL_RELEASE_RETRY_ATTEMPTS", 2)
        operation.next_attempt_at = None

    async def seed_confirmed():
        assert await completion_release.retain_completion_release(
            Session(), run=run, workspace=workspace, operation=operation,
            profile_version="fixture-profile",
        )
        workspace.fencing_epoch += 1
        workspace.generation_run_id = None
        operation.fencing_epoch = workspace.fencing_epoch
        operation.status = "completed"
        operation.finished_at = datetime.now(UTC)
        operation.result_payload = receipt().to_wire_json()

    store.release, store.prepare_pending = release, prepare_pending
    store.allow_replay, store.seed_confirmed = allow_replay, seed_confirmed
    return store


def _tracker_io(monkeypatch, store):
    async def cancelled(*_):
        await asyncio.Future()

    async def status(_run_id, value, **_):
        store.run.status = value

    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", cancelled)
    monkeypatch.setattr(supervisor, "set_generation_run_status", AsyncMock(side_effect=status))
    monkeypatch.setattr(supervisor, "_emergency_error", AsyncMock())
    monkeypatch.setattr(supervisor, "clear_generation_cancel", AsyncMock())


async def test_real_cleanup_tracker_and_same_release_finish_the_saved_build(
    monkeypatch, release_store,
):
    store = release_store
    _tracker_io(monkeypatch, store)
    runtime = GenerationRuntime(handle=SimpleNamespace(
        release=AsyncMock(), release_for_completion=store.release,
    ))

    async def finished_body():
        await lifecycle.cleanup_generation_runtime(runtime, store.project.id, store.message.id)

    await supervisor._run_tracked_prompt(
        finished_body(), run_id=store.run.id, project_id=store.project.id,
        assistant_message_id=store.message.id, label="saved-build-release",
    )

    assert store.run.status == "running" and store.run.finished_at is None
    marker = completion_release.completion_release(store.run)
    assert marker.operation_id == store.operation.id
    assert marker.snapshot_id == store.snapshot.id
    assert store.workspace.generation_run_id == store.run.id
    assert store.operation.status == "indeterminate"
    store.billing.assert_not_awaited()
    supervisor._emergency_error.assert_not_awaited()
    supervisor.clear_generation_cancel.assert_not_awaited()
    lifecycle.clear_stream_state.assert_awaited_once()
    runtime.handle.release.assert_not_awaited()

    store.allow_replay()
    assert await completion_release.resume_completion_release(store.factory, store.run.id)
    assert store.run.status == "completed" and store.run.finished_at is not None
    assert store.workspace.generation_run_id is None
    assert store.operation.status == "completed"
    assert len(store.trace) == 2 and store.trace[0] == store.trace[1]
    assert store.snapshot.commit_sha == marker.snapshot_commit
    store.acceptance.assert_awaited_once()
    store.billing.assert_awaited_once()
    generation_runs.compile_terminal_run_memory.assert_awaited_once()
    assert not await completion_release.resume_completion_release(store.factory, store.run.id)
    assert store.client.control.await_count == 2
    store.billing.assert_awaited_once()


async def test_confirmed_release_survives_crash_after_sql_clear_before_finalize(release_store):
    store = release_store
    await store.seed_confirmed()
    assert await completion_release.resume_completion_release(store.factory, store.run.id)
    assert store.run.status == "completed"
    store.client.control.assert_not_awaited()
    store.billing.assert_awaited_once()


@pytest.mark.parametrize("backend", ["worker", "api"])
async def test_real_worker_resumes_only_release_for_both_execution_backends(
    monkeypatch, release_store, backend,
):
    store = release_store
    await store.seed_confirmed()
    store.run.execution_backend = backend
    monkeypatch.setattr(generation_worker, "get_engine", lambda: store.engine)
    monkeypatch.setattr(generation_worker, "async_sessionmaker", lambda *_a, **_k: store.factory)
    monkeypatch.setattr(generation_worker, "load_generation_dispatch", AsyncMock())
    monkeypatch.setattr(generation_worker, "_fail_orphan", AsyncMock())
    monkeypatch.setattr(lifecycle, "_process_prompt", AsyncMock())

    assert await generation_worker.execute_dispatch(store.run.id)
    assert store.run.status == "completed"
    lifecycle._process_prompt.assert_not_awaited()
    generation_worker.load_generation_dispatch.assert_not_called()
    generation_worker._fail_orphan.assert_not_awaited()
    store.client.control.assert_not_awaited()


async def test_release_only_worker_respects_the_existing_live_executor_lock(
    monkeypatch, release_store,
):
    store = release_store
    await store.seed_confirmed()
    store.executor_lock_available = False
    monkeypatch.setattr(generation_worker, "get_engine", lambda: store.engine)
    monkeypatch.setattr(generation_worker, "async_sessionmaker", lambda *_a, **_k: store.factory)
    assert not await generation_worker.execute_dispatch(store.run.id)
    assert store.run.status == "running"
    store.billing.assert_not_awaited()
    store.client.control.assert_not_awaited()


@pytest.mark.parametrize("loss", ["before_io", "before_finalize"])
async def test_lost_executor_ownership_never_finishes_a_release_only_handoff(
    release_store, loss,
):
    store = release_store
    await store.prepare_pending()
    store.allow_replay()
    if loss == "before_io":
        store.ownership_held = False
    else:
        original_control = store.client.control.side_effect

        async def finish_then_lose_owner(request):
            response = await original_control(request)
            store.ownership_held = False
            return response

        store.client.control.side_effect = finish_then_lose_owner
    with pytest.raises(RecoveryContractError, match="ownership was lost"):
        await completion_release.resume_completion_release(store.factory, store.run.id)
    assert store.run.status == "running"
    assert store.client.control.await_count == (1 if loss == "before_io" else 2)
    store.billing.assert_not_awaited()
    store.acceptance.assert_not_awaited()


async def test_api_restart_preserves_durable_release_without_replaying_body(
    monkeypatch, release_store,
):
    store = release_store
    await store.prepare_pending()
    store.run.execution_backend = "api"
    before = dict(store.run.agent_state)
    monkeypatch.setattr(generation_runs, "_retry_terminal_adaptation_notifications", AsyncMock())
    assert await generation_runs._recover_interrupted_generation_runs(store.factory()) == 0
    assert store.run.status == "running" and store.run.agent_state == before
    assert store.workspace.generation_run_id == store.run.id
    assert store.operation.status == "indeterminate"
    store.billing.assert_not_awaited()
    assert store.client.control.await_count == 1


async def test_worker_cancel_wins_without_replaying_release_or_generation(
    monkeypatch, release_store,
):
    store = release_store
    await store.prepare_pending()
    store.run.status = "cancel_requested"
    monkeypatch.setattr(generation_worker, "get_engine", lambda: store.engine)
    monkeypatch.setattr(generation_worker, "async_sessionmaker", lambda *_a, **_k: store.factory)
    monkeypatch.setattr(lifecycle, "_process_prompt", AsyncMock())
    assert not await generation_worker.execute_dispatch(store.run.id)
    assert store.run.status == "cancelled"
    assert store.workspace.generation_run_id == store.run.id
    assert store.operation.status == "indeterminate"
    assert store.client.control.await_count == 1
    lifecycle._process_prompt.assert_not_awaited()


@pytest.mark.parametrize("phase", ["before_release", "cancel_after_release"])
async def test_final_message_cannot_admit_next_prompt_while_run_still_active(
    monkeypatch, release_store, phase,
):
    store = release_store
    if phase == "cancel_after_release":
        await store.seed_confirmed()
        store.run.status = "cancel_requested"
    expected_status = store.run.status
    monkeypatch.setattr(
        "yleum_api.services.generation_deployment_drain.require_generation_admission", AsyncMock(),
    )
    monkeypatch.setattr("yleum_api.services.restorations.assert_no_active_restoration", AsyncMock())
    with pytest.raises(ApiError) as rejected:
        await generation_runs.reserve_generation_run(
            store.factory(), project_id=store.project.id, user_id=store.run.user_id,
            idempotency_key="different-next-prompt", prompt="a new distinct edit",
        )
    assert rejected.value.status_code == 409 and rejected.value.code == "generation_active"
    assert store.run.status == expected_status and not store.added
    store.billing.assert_not_awaited()


@pytest.mark.parametrize("mismatch", [
    "owner", "snapshot_commit", "snapshot_pointer", "operation_workspace", "operation_run",
    "request_digest", "operation_fence", "receipt_workspace", "receipt_fence", "receipt_state",
    "foreign_lease", "active_activity", "missing_receipt", "unconfirmed_operation",
    "partial_receipt", "unfinished_receipt",
])
async def test_foreign_or_incomplete_receipt_never_finalizes(release_store, mismatch):
    store = release_store
    await store.seed_confirmed()
    if mismatch == "owner":
        store.project.owner_id = uuid4()
    elif mismatch == "snapshot_commit":
        store.snapshot.commit_sha = "c" * 40
    elif mismatch == "snapshot_pointer":
        store.project.current_snapshot_id = uuid4()
    elif mismatch == "operation_workspace":
        store.operation.workspace_id = uuid4()
    elif mismatch == "operation_run":
        store.operation.generation_run_id = uuid4()
    elif mismatch == "request_digest":
        store.operation.request_digest = "d" * 64
    elif mismatch == "operation_fence":
        store.operation.fencing_epoch += 1
    elif mismatch == "receipt_workspace":
        store.operation.result_payload["workspace_id"] = str(uuid4())
    elif mismatch == "receipt_fence":
        store.operation.result_payload["fencing_epoch"] += 1
    elif mismatch == "receipt_state":
        store.operation.result_payload["state"] = "resources_paused"
    elif mismatch == "foreign_lease":
        store.workspace.generation_run_id = uuid4()
    elif mismatch == "active_activity":
        store.active_activity = True
    elif mismatch == "missing_receipt":
        store.operation.result_payload = None
    elif mismatch == "unconfirmed_operation":
        store.operation.status = "indeterminate"
    elif mismatch == "partial_receipt":
        store.operation.result_payload = {
            key: store.operation.result_payload[key]
            for key in ("workspace_id", "fencing_epoch", "state")
        }
    elif mismatch == "unfinished_receipt":
        store.operation.finished_at = None
    try:
        completed = await completion_release.resume_completion_release(store.factory, store.run.id)
    except GenerationReleasePending:
        completed = False
    assert not completed and store.run.status == "running"
    store.billing.assert_not_awaited()
    store.acceptance.assert_not_awaited()
    store.client.control.assert_not_awaited()


@pytest.mark.parametrize("terminal", ["cancel_requested", "cancelled", "failed"])
async def test_late_release_never_turns_cancel_or_failure_into_success(release_store, terminal):
    store = release_store
    await store.seed_confirmed()
    store.run.status = terminal
    assert not await completion_release.resume_completion_release(store.factory, store.run.id)
    assert store.run.status == terminal
    with pytest.raises(GenerationReleasePending):
        await generation_runs.terminalize_generation_run_locked(
            store.factory(), store.run, status="completed",
        )
    store.billing.assert_not_awaited()


async def test_product_failure_is_not_a_successful_body_marker(release_store):
    store = release_store
    store.run.agent_state = {"product_outcome": {"status": "failed", "error": "red build"}}
    assert not await completion_release.retain_completion_release(
        store.factory(), run=store.run, workspace=store.workspace, operation=store.operation,
        profile_version="fixture-profile",
    )
    assert not completion_release.has_completion_release(store.run)


async def test_late_product_failure_wins_after_confirmed_release(release_store):
    store = release_store
    await store.seed_confirmed()
    store.run.agent_state = {
        **store.run.agent_state, "product_outcome": {"status": "failed", "error": "red build"},
    }
    assert not await completion_release.resume_completion_release(store.factory, store.run.id)
    assert store.run.status == "failed" and store.run.error == "red build"
    store.acceptance.assert_not_awaited()
    store.client.control.assert_not_awaited()


@pytest.mark.parametrize("backend", ["worker", "api"])
@pytest.mark.parametrize("interrupted", [False, True])
async def test_interrupted_release_requires_previous_executor_ownership(
    release_store, backend, interrupted,
):
    store = release_store
    await store.prepare_pending()
    store.run.execution_backend = backend
    store.operation.status = "running"
    store.operation.execution_run_id = store.run.id
    if interrupted:
        store.operation.status = "indeterminate"
        store.operation.error = _hashed_error("generation_executor_interrupted")
    store.allow_replay()
    completed = await completion_release.resume_completion_release(store.factory, store.run.id)
    assert completed is (backend == "worker")
    assert store.run.status == ("completed" if completed else "running")
    assert store.client.control.await_count == (2 if completed else 1)
    if completed:
        assert store.trace[0] == store.trace[1]
        assert store.workspace.generation_run_id is None
    else:
        assert store.operation.status == ("indeterminate" if interrupted else "running")
        assert store.workspace.generation_run_id == store.run.id


@pytest.mark.parametrize("interrupted", ["none", "running", "before_replay"])
async def test_sql_release_handoff_replays_the_same_outbox_and_seals_once(
    db_session, test_engine, monkeypatch, interrupted,
):
    """Real SQL reservation/claim/persistence/CAS/finalization; no model or Docker.

    The accepted message and version are ordinary isolated database fixtures,
    not evidence of a live generation. Billing is explicitly free, so the real
    terminal seal is exercised without creating a payable charge.
    """
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(get_settings(), "generation_recovery_mode", "off")
    monkeypatch.setattr("yleum_api.core.db.get_engine", lambda: test_engine)
    monkeypatch.setattr(generation_worker, "get_engine", lambda: test_engine)
    monkeypatch.setattr(executor, "_PROJECT_CELL_RELEASE_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(supervisor, "clear_generation_cancel", AsyncMock())
    monkeypatch.setattr(supervisor, "_emergency_error", AsyncMock())

    async def wait_cancel(*_):
        await asyncio.Future()

    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_cancel)
    owner = User(email=f"completion-release-{uuid4().hex}@example.test", password_hash="fixture")
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        owner_id=owner.id, name="Release handoff SQL fixture",
        slug=f"completion-release-{uuid4().hex}", template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    snapshot = Snapshot(project_id=project.id, commit_sha="a" * 40, preview_status="ready")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    message = Message(
        project_id=project.id, role="assistant", content="saved build",
        snapshot_id=snapshot.id, tokens_out=25, tokens_in=10,
    )
    db_session.add(message)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id, user_id=owner.id, assistant_message_id=message.id,
        idempotency_key=f"completion-release-{uuid4().hex}", prompt_hash="e" * 64,
        status="running", response_mode="build", execution_backend="worker",
        execution_started_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.flush()
    workspace = ProjectCellWorkspace(
        project_id=project.id, owner_id=owner.id, provider="docker_owner_canary",
        state="ready", generation_run_id=run.id,
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(GenerationBillingPolicy(
        run_id=run.id, is_free=True, version="accepted-build-v1",
    ))
    ensure, _ = await reserve_cell_operation(
        db_session, workspace_id=workspace.id, generation_run_id=run.id,
        kind="ensure", idempotency_key=f"generation:{run.id}:ensure:fixture",
        request={"profile_version": "docker-owner-cell-resources-v2"},
    )
    await db_session.commit()
    run_id, project_id, message_id, workspace_id = run.id, project.id, message.id, workspace.id
    claimed_ensure = await claim_cell_operation_committed(factory, ensure.id)
    assert claimed_ensure.fencing_epoch == 1
    ensure_receipt = ProjectCellResourceResponse(
        workspace_id=workspace_id, state="resources_ready", provider_ref="fixture",
        fencing_epoch=1, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
        has_postgres=True, has_redis=True,
    )
    async with factory() as session:
        await complete_cell_operation(session, ensure.id, ensure_receipt.to_wire_json())
        await session.commit()

    requests = []

    async def control(request):
        # A separate real SQL connection sees both intent and outbox before I/O.
        async with factory() as observed:
            saved_run = await observed.get(GenerationRun, run_id)
            marker = completion_release.completion_release(saved_run)
            saved_operation = await observed.get(ProjectCellOperation, marker.operation_id)
            assert marker.operation_id == request.operation_id
            assert marker.request_digest == request.request_digest
            assert marker.source_fencing_epoch == 1
            assert saved_operation.status == "running"
            assert saved_operation.fencing_epoch == request.fencing_epoch == 2
        requests.append(request)
        if len(requests) == 1:
            raise OrchestratorReaderInventoryUnavailable("synthetic reader denial")
        return ProjectCellResourceResponse(
            workspace_id=workspace_id, state="resources_ready", provider_ref="fixture",
            fencing_epoch=request.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        )

    client = SimpleNamespace(control=AsyncMock(side_effect=control))
    monkeypatch.setattr(executor, "HttpProjectCellOrchestratorClient", lambda: client)
    monkeypatch.setattr(
        "yleum_api.services.orchestrator_client.HttpProjectCellOrchestratorClient", lambda: client,
    )

    async def release():
        await executor._release_generation_lease(
            session_factory=factory, workspace_id=workspace_id, generation_run_id=run_id,
            profile_version="docker-owner-cell-resources-v2", completion_ready=True,
        )

    async def completed_body():
        await lifecycle.cleanup_generation_runtime(
            GenerationRuntime(handle=SimpleNamespace(
                release=AsyncMock(), release_for_completion=release,
            )), project_id, message_id,
        )

    await supervisor._run_tracked_prompt(
        completed_body(), run_id=run_id, project_id=project_id,
        assistant_message_id=message_id, label="sql-release-handoff",
    )
    async with factory() as session:
        saved_run = await session.get(GenerationRun, run_id)
        marker = completion_release.completion_release(saved_run)
        saved_workspace = await session.get(ProjectCellWorkspace, workspace_id)
        saved_operation = await session.get(ProjectCellOperation, marker.operation_id)
        assert saved_run.status == "running" and saved_run.finished_at is None
        assert saved_workspace.generation_run_id == run_id
        assert saved_operation.status == "indeterminate" and saved_operation.fencing_epoch == 2
        assert await session.get(GenerationBillingIntent, run_id) is None
    supervisor._emergency_error.assert_not_awaited()
    if interrupted != "none":
        async with factory() as session:
            crashed = await session.get(ProjectCellOperation, marker.operation_id)
            crashed.status = "running"
            crashed.execution_run_id = run_id
            if interrupted == "before_replay":
                crashed.status = "indeterminate"
                crashed.error = _hashed_error("generation_executor_interrupted")
            await session.commit()
    monkeypatch.setattr(executor, "_PROJECT_CELL_RELEASE_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(lifecycle, "_process_prompt", AsyncMock())
    assert await generation_worker.execute_dispatch(run_id)
    assert len(requests) == 2
    assert requests[0].to_wire_json() == requests[1].to_wire_json()
    lifecycle._process_prompt.assert_not_awaited()

    async with factory() as session:
        saved_run = await session.get(GenerationRun, run_id)
        saved_workspace = await session.get(ProjectCellWorkspace, workspace_id)
        saved_operation = await session.get(ProjectCellOperation, marker.operation_id)
        assert saved_run.status == "completed" and saved_run.finished_at is not None
        assert saved_workspace.generation_run_id is None and saved_workspace.fencing_epoch == 2
        assert saved_operation.status == "completed" and saved_operation.fencing_epoch == 2
        assert saved_operation.attempt_count == 2
        assert await session.scalar(select(func.count()).select_from(ProjectCellOperation)) == 2
        intent = await session.get(GenerationBillingIntent, run_id)
        assert intent is not None and intent.outcome == "waived" and intent.amount_rub == 0
        sealed_receipt = dict(intent.receipt)
        assert await generation_runs.finalize_generation_run(run_id, session=session) == "completed"
    assert not await generation_worker.execute_dispatch(run_id)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(GenerationBillingIntent)) == 1
        assert await session.scalar(select(func.count()).select_from(WalletCharge)) == 0
        assert (await session.get(GenerationBillingIntent, run_id)).receipt == sealed_receipt
    assert client.control.await_count == 2
