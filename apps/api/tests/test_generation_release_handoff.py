"""Exercise the real cleanup/tracker handoff without models, Docker or a database."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services import generation_runs
from yleum_api.services import project_cell_executor as executor
from yleum_api.services import project_cell_lifecycle as cell_lifecycle
from yleum_api.services.generation import agent_pipeline, lifecycle, recovery_execution, supervisor
from yleum_api.services.generation.agent_finalization import AdaptationActivationPending
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    ContinuationClaim,
    ExecutionCursor,
    RecoveryIdentity,
    RecoveryStateV2,
)
from yleum_api.services.generation.recovery_execution import GenerationYielded
from yleum_api.services.orchestrator_client import (
    OrchestratorReaderInventoryUnavailable,
    OrchestratorUnavailable,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cell_lifecycle import ProjectCellOperationOutcome
from yleum_api.services.project_cells import ClaimedCellOperation

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tracker(monkeypatch):
    """Double external effects, keeping the real tracker and runtime cleanup."""
    trace = []
    cancel_requested = asyncio.Event()

    async def wait_cancel(*_):
        await cancel_requested.wait()

    async def status(_run_id, value, **_):
        trace.append(value)

    async def finalize(*_):
        trace.append("finalized")

    async def cancelled(*_):
        trace.append("cancelled")

    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_cancel)
    monkeypatch.setattr(supervisor, "set_generation_run_status", AsyncMock(side_effect=status))
    monkeypatch.setattr(supervisor, "finalize_generation_run", AsyncMock(side_effect=finalize))
    monkeypatch.setattr(
        supervisor, "_finalize_cancelled_generation", AsyncMock(side_effect=cancelled),
    )
    monkeypatch.setattr(supervisor, "recovery_failure_outcome", AsyncMock(return_value="legacy"))
    monkeypatch.setattr(supervisor, "_emergency_error", AsyncMock())
    monkeypatch.setattr(supervisor, "clear_generation_cancel", AsyncMock())

    async def run(work):
        await supervisor._run_tracked_prompt(
            work, run_id=uuid4(), project_id=uuid4(), assistant_message_id=uuid4(),
            label="synthetic-release-handoff",
        )

    return SimpleNamespace(trace=trace, cancel_requested=cancel_requested, run=run)


@pytest.mark.parametrize("stream_failure", [False, True])
@pytest.mark.parametrize("failure_outcome", ["legacy", "preserve"])
async def test_successful_build_with_unconfirmed_release_never_finalizes(
    monkeypatch, tracker, stream_failure, failure_outcome,
):
    release_error = RuntimeError("synthetic release indeterminate")

    async def release():
        tracker.trace.append("release_failed")
        raise release_error

    async def clear(*_):
        tracker.trace.append("stream_cleanup")
        if stream_failure:
            raise RuntimeError("synthetic Redis unavailable")

    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock(side_effect=clear))
    supervisor.recovery_failure_outcome.return_value = failure_outcome
    continuation = SimpleNamespace(account_work=AsyncMock())
    runtime = GenerationRuntime(
        handle=SimpleNamespace(release=release), continuation=continuation,
    )

    async def successful_build():
        tracker.trace.append("snapshot_committed")
        await lifecycle.cleanup_generation_runtime(runtime, uuid4(), uuid4())

    await tracker.run(successful_build())

    supervisor.finalize_generation_run.assert_not_awaited()
    continuation.account_work.assert_not_awaited()
    lifecycle.clear_stream_state.assert_awaited_once()
    assert tracker.trace == [
        "running", "snapshot_committed", "release_failed", "stream_cleanup",
        *(["failed"] if failure_outcome == "legacy" else []),
    ]
    if failure_outcome == "legacy":
        assert supervisor.set_generation_run_status.await_args.kwargs["error"] == (
            "RuntimeError: synthetic release indeterminate"
        )
    else:
        supervisor._emergency_error.assert_not_awaited()


@pytest.mark.parametrize("stream_failure", [False, True])
async def test_successful_release_precedes_accounting_and_completion(
    monkeypatch, tracker, stream_failure,
):
    async def release():
        tracker.trace.append("released")

    async def account_work(*, stopped):
        assert stopped is True
        tracker.trace.append("accounted")

    async def clear(*_):
        tracker.trace.append("stream_cleanup")
        if stream_failure:
            raise RuntimeError("synthetic Redis unavailable")

    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock(side_effect=clear))
    runtime = GenerationRuntime(
        handle=SimpleNamespace(release=release),
        continuation=SimpleNamespace(account_work=account_work),
    )

    async def successful_build():
        tracker.trace.append("snapshot_committed")
        await lifecycle.cleanup_generation_runtime(runtime, uuid4(), uuid4())

    await tracker.run(successful_build())

    assert tracker.trace == [
        "running", "snapshot_committed", "released", "accounted", "stream_cleanup", "finalized",
    ]
    supervisor.finalize_generation_run.assert_awaited_once()
    supervisor._emergency_error.assert_not_awaited()


async def test_cancellation_waits_for_real_release_before_terminal_handoff(monkeypatch, tracker):
    working, release_started, finish_release = (asyncio.Event() for _ in range(3))

    async def release():
        release_started.set()
        await finish_release.wait()
        tracker.trace.append("released")

    async def clear(*_):
        tracker.trace.append("stream_cleanup")

    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock(side_effect=clear))
    runtime = GenerationRuntime(handle=SimpleNamespace(release=release))

    async def work():
        working.set()
        try:
            await asyncio.Future()
        finally:
            await lifecycle.cleanup_generation_runtime(runtime, uuid4(), uuid4())

    task = asyncio.create_task(tracker.run(work()))
    try:
        await asyncio.wait_for(working.wait(), 1)
        tracker.cancel_requested.set()
        await asyncio.wait_for(release_started.wait(), 1)
        assert not task.done()
        supervisor.finalize_generation_run.assert_not_awaited()
        supervisor._finalize_cancelled_generation.assert_not_awaited()
        finish_release.set()
        await asyncio.wait_for(task, 1)
    finally:
        finish_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert tracker.trace == ["running", "released", "stream_cleanup", "cancelled"]


@pytest.fixture
def release_io(monkeypatch):
    """Only persistence/dispatch are doubles; the release contract stays real."""
    project_id, owner_id, run_id, workspace_id, operation_id = (uuid4() for _ in range(5))
    run = SimpleNamespace(id=run_id, project_id=project_id, user_id=owner_id, agent_state={})
    project = SimpleNamespace(id=project_id, owner_id=owner_id)
    workspace = SimpleNamespace(
        id=workspace_id, project_id=project_id, owner_id=owner_id,
        generation_run_id=run_id, fencing_epoch=8, updated_at=None,
    )
    operation = SimpleNamespace(
        id=operation_id, workspace_id=workspace_id, kind="release", status="running",
        fencing_epoch=8, error=None, result_payload=None,
    )
    claimed = ClaimedCellOperation(
        operation_id=operation_id, workspace_id=workspace_id, project_id=project_id,
        owner_id=owner_id, generation_run_id=run_id, kind="release", request={},
        request_digest="a" * 64, fencing_epoch=8,
    )
    rows = {
        (GenerationRun, run_id): run,
        (Project, project_id): project,
        (ProjectCellWorkspace, workspace_id): workspace,
        (ProjectCellOperation, operation_id): operation,
    }
    trace = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, model, key, **_):
            return rows.get((model, key))

        async def commit(self):
            trace.append(("commit", workspace.generation_run_id, workspace.fencing_epoch))

        async def scalar(self, *_):
            return operation

        async def flush(self):
            pass

    reserve = AsyncMock(return_value=(operation, False))
    monkeypatch.setattr(executor, "lock_workspace_operations", AsyncMock())
    monkeypatch.setattr(executor, "reserve_cell_operation", reserve)
    client = SimpleNamespace(control=AsyncMock())
    monkeypatch.setattr(executor, "HttpProjectCellOrchestratorClient", lambda: client)

    def outcome(*, status="completed", error=None, response_fence=8):
        response = ProjectCellResourceResponse(
            workspace_id=workspace_id, state="resources_ready", provider_ref="synthetic",
            fencing_epoch=response_fence, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        ) if status == "completed" else None
        return ProjectCellOperationOutcome(
            operation_id=operation_id, workspace_id=workspace_id, kind="release",
            status=status, fencing_epoch=8, response=response, result_payload=None,
            error=error, reconciles_operation_id=None,
        )

    async def release():
        await executor._release_generation_lease(
            session_factory=Session, workspace_id=workspace_id, generation_run_id=run_id,
            profile_version="synthetic-profile",
        )

    async def dispatch(*_):
        return await cell_lifecycle._dispatch_claimed_cell_operation(Session, claimed, client)

    return SimpleNamespace(
        workspace=workspace, run=run, operation=operation, reserve=reserve,
        outcome=outcome, release=release, trace=trace, client=client,
        dispatch=dispatch, claimed=claimed,
    )


async def test_release_clears_sql_only_after_owned_completed_response(monkeypatch, release_io):
    execute = AsyncMock(return_value=release_io.outcome())
    monkeypatch.setattr(executor, "execute_cell_operation", execute)

    await release_io.release()

    assert release_io.workspace.generation_run_id is None
    assert release_io.trace == [("commit", release_io.run.id, 8), ("commit", None, 8)]
    assert execute.await_args.args[1:] == (release_io.operation.id, release_io.client)
    release_io.reserve.assert_awaited_once()
    assert release_io.reserve.await_args.kwargs == {
        "workspace_id": release_io.workspace.id, "generation_run_id": release_io.run.id,
        "kind": "release",
        "idempotency_key": f"generation:{release_io.run.id}:release:synthetic-profile",
        "request": {},
    }


@pytest.mark.parametrize("change", ["fence", "lease", "response_fence"])
async def test_release_never_succeeds_after_sql_handoff_binding_changes(
    monkeypatch, release_io, change,
):
    async def complete(*_):
        if change == "fence":
            release_io.workspace.fencing_epoch = 9
        elif change == "lease":
            release_io.workspace.generation_run_id = uuid4()
        return release_io.outcome(response_fence=9 if change == "response_fence" else 8)

    monkeypatch.setattr(executor, "execute_cell_operation", AsyncMock(side_effect=complete))

    with pytest.raises(executor.ProjectCellExecutorUnavailable):
        await release_io.release()

    assert release_io.workspace.generation_run_id is not None
    assert len(release_io.trace) == 1, "an unconfirmed SQL handoff must not commit"


@pytest.fixture
def release_clock(monkeypatch):
    """Bound the actual retry policy with deterministic time and no real waits."""
    clock = SimpleNamespace(now=100.0, waits=[])

    async def sleep(delay):
        assert delay > 0
        clock.waits.append(delay)
        clock.now += delay

    monkeypatch.setattr(executor, "monotonic", lambda: clock.now)
    monkeypatch.setattr(executor, "asyncio", SimpleNamespace(sleep=sleep))
    return clock


async def test_typed_reader_denial_survives_real_persistence_and_replays_same_release(
    monkeypatch, release_io, release_clock,
):
    release_io.client.control.side_effect = OrchestratorReaderInventoryUnavailable(
        "synthetic reader denial",
    )
    execute = AsyncMock(side_effect=release_io.dispatch)
    replay = AsyncMock(return_value=release_io.outcome())
    monkeypatch.setattr(executor, "execute_cell_operation", execute)
    monkeypatch.setattr(executor, "replay_indeterminate_cell_operation", replay)

    await release_io.release()

    assert release_io.workspace.generation_run_id is None
    execute.assert_awaited_once()
    replay.assert_awaited_once_with(*execute.await_args.args)
    release_io.reserve.assert_awaited_once()
    assert release_clock.waits == [1]
    request = release_io.client.control.await_args.args[0]
    assert request.operation_id == release_io.claimed.operation_id
    assert request.fencing_epoch == release_io.claimed.fencing_epoch
    assert request.request_digest == release_io.claimed.request_digest


async def test_generic_unavailable_persisted_outcome_never_enters_reader_replay(
    monkeypatch, release_io, release_clock,
):
    release_io.client.control.side_effect = OrchestratorUnavailable(
        "synthetic upstream unavailable",
    )
    execute = AsyncMock(side_effect=release_io.dispatch)
    replay = AsyncMock()
    monkeypatch.setattr(executor, "execute_cell_operation", execute)
    monkeypatch.setattr(executor, "replay_indeterminate_cell_operation", replay)

    with pytest.raises(executor.ProjectCellExecutorUnavailable, match="indeterminate"):
        await release_io.release()

    execute.assert_awaited_once()
    replay.assert_not_awaited()
    assert release_clock.waits == []
    assert release_io.workspace.generation_run_id == release_io.run.id


async def test_persistent_reader_denial_is_bounded_and_keeps_sql_lease(
    monkeypatch, release_io, release_clock,
):
    release_io.client.control.side_effect = OrchestratorReaderInventoryUnavailable(
        "synthetic persistent reader denial",
    )
    execute = AsyncMock(side_effect=release_io.dispatch)

    async def same_unknown(*_):
        return await cell_lifecycle._load_operation_outcome(
            execute.await_args.args[0], release_io.operation.id,
        )

    replay = AsyncMock(side_effect=same_unknown)
    monkeypatch.setattr(executor, "execute_cell_operation", execute)
    monkeypatch.setattr(executor, "replay_indeterminate_cell_operation", replay)

    with pytest.raises(executor.ProjectCellExecutorUnavailable, match="indeterminate"):
        await release_io.release()

    execute.assert_awaited_once()
    assert 0 < replay.await_count <= 30
    assert 0 < sum(release_clock.waits) <= 30
    assert all(call.args == execute.await_args.args for call in replay.await_args_list)
    assert release_io.workspace.generation_run_id == release_io.run.id
    release_io.reserve.assert_awaited_once()


async def test_reader_retry_stops_on_new_generic_uncertainty(
    monkeypatch, release_io, release_clock,
):
    release_io.client.control.side_effect = OrchestratorReaderInventoryUnavailable(
        "synthetic first reader denial",
    )
    execute = AsyncMock(side_effect=release_io.dispatch)
    replay = AsyncMock(return_value=release_io.outcome(status="indeterminate", error="timeout"))
    monkeypatch.setattr(executor, "execute_cell_operation", execute)
    monkeypatch.setattr(executor, "replay_indeterminate_cell_operation", replay)

    with pytest.raises(executor.ProjectCellExecutorUnavailable, match="indeterminate"):
        await release_io.release()

    replay.assert_awaited_once_with(*execute.await_args.args)
    assert release_clock.waits == [1]
    assert release_io.workspace.generation_run_id == release_io.run.id


@pytest.mark.parametrize("expires_during", ["first_request", "readiness_sleep"])
async def test_reader_retry_never_dispatches_after_total_deadline(
    monkeypatch, release_io, release_clock, expires_during,
):
    release_io.client.control.side_effect = OrchestratorReaderInventoryUnavailable(
        "synthetic slow reader denial",
    )

    async def execute(*_):
        if expires_during == "first_request":
            release_clock.now += 31
        return await release_io.dispatch()

    async def oversleep(delay):
        release_clock.waits.append(delay)
        release_clock.now += 31

    monkeypatch.setattr(executor, "execute_cell_operation", AsyncMock(side_effect=execute))
    monkeypatch.setattr(executor.asyncio, "sleep", oversleep)
    replay = AsyncMock()
    monkeypatch.setattr(executor, "replay_indeterminate_cell_operation", replay)

    with pytest.raises(executor.ProjectCellExecutorUnavailable, match="indeterminate"):
        await release_io.release()

    replay.assert_not_awaited()
    assert release_clock.waits == ([] if expires_during == "first_request" else [1])
    assert release_io.workspace.generation_run_id == release_io.run.id


async def test_reader_retry_attempt_cap_survives_nonadvancing_clock(
    monkeypatch, release_io, release_clock,
):
    release_io.client.control.side_effect = OrchestratorReaderInventoryUnavailable(
        "synthetic persistent reader denial",
    )
    execute = AsyncMock(side_effect=release_io.dispatch)

    async def same_unknown(*_):
        return await cell_lifecycle._load_operation_outcome(
            execute.await_args.args[0], release_io.operation.id,
        )

    async def frozen_sleep(delay):
        release_clock.waits.append(delay)

    replay = AsyncMock(side_effect=same_unknown)
    monkeypatch.setattr(executor, "execute_cell_operation", execute)
    monkeypatch.setattr(executor, "replay_indeterminate_cell_operation", replay)
    monkeypatch.setattr(executor.asyncio, "sleep", frozen_sleep)

    with pytest.raises(executor.ProjectCellExecutorUnavailable, match="indeterminate"):
        await release_io.release()

    execute.assert_awaited_once()
    assert 0 < replay.await_count <= 30
    assert len(release_clock.waits) == replay.await_count
    assert release_io.workspace.generation_run_id == release_io.run.id


@pytest.fixture
def cold_resume(monkeypatch):
    """Use a real decoded cold entry and cleanup, doubling persistence and agent I/O."""
    from yleum_api.services.generation import recovery_store

    ids = GenerationIds(*(uuid4() for _ in range(5)))
    baseline = SourceBaseline(snapshot_id=uuid4(), sha="b" * 40, files={"src/kept.ts": "kept"})
    project_info = ProjectGenerationFacts(
        template="max_miniapp", slug="synthetic", name="Synthetic", design_preset_id=None,
        discovery_spec=None, image_gen_enabled=False, language="ru", is_imported=False,
        memory_context="", restoration_context="",
    )
    dispatch = generation_runs.GenerationDispatch(
        schema_version=1, project_id=ids.project_id, user_id=ids.user_id,
        user_message_id=ids.user_message_id, assistant_message_id=ids.assistant_message_id,
        current_snapshot_id=baseline.snapshot_id, prompt_text="synthetic", model_id="synthetic",
        force_model=None, is_free=False, orchestrate=True, selected_elements=None,
    )
    claim = ContinuationClaim(
        run_id=ids.run_id, token=uuid4(), attempt_seq=0, worker_id="synthetic-cold",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    state = RecoveryStateV2(
        identity=RecoveryIdentity(
            run_id=ids.run_id, project_id=ids.project_id, user_id=ids.user_id,
            prompt_hash="a" * 64, accepted_snapshot_id=baseline.snapshot_id, supersedes_run_id=None,
        ),
        continuation_claim=claim,
        execution=ExecutionCursor(
            stage="bootstrap", substep="runtime_prepare", physical_state="active",
            execution_context_ref=CheckpointRef(object_key="synthetic-context", sha256="c" * 64),
        ),
    )
    run = SimpleNamespace(
        id=ids.run_id, project_id=ids.project_id, user_id=ids.user_id,
        prompt_hash="a" * 64, user_message_id=ids.user_message_id,
        assistant_message_id=ids.assistant_message_id,
        agent_state={"dispatch": dispatch.to_json(), "recovery_v1": state.model_dump(mode="json")},
    )
    project = SimpleNamespace(id=ids.project_id, owner_id=ids.user_id)
    payload = {
        "ids": TypeAdapter(GenerationIds).dump_python(ids, mode="json"),
        "baseline": TypeAdapter(SourceBaseline).dump_python(baseline, mode="json"),
        "project_info": TypeAdapter(ProjectGenerationFacts).dump_python(project_info, mode="json"),
        **{key: getattr(dispatch, key) for key in (
            "prompt_text", "force_model", "is_free", "model_id", "orchestrate", "selected_elements",
        )},
    }

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, model, key, **_):
            assert model is GenerationRun and key == ids.run_id
            return run

        async def commit(self):
            pass

    monkeypatch.setattr(agent_pipeline, "get_engine", lambda: object())
    monkeypatch.setattr(agent_pipeline, "async_sessionmaker", lambda *_a, **_k: Session)
    monkeypatch.setattr(recovery_execution, "_require_session_owner", AsyncMock())
    monkeypatch.setattr(recovery_store, "_locked_owner", AsyncMock(return_value=(run, project)))
    monkeypatch.setattr(recovery_store, "_baseline_sha", AsyncMock(return_value=baseline.sha))
    monkeypatch.setattr(recovery_execution, "load_execution_context", AsyncMock(return_value={
        "schema_version": 1, "entry": "pipeline", "pipeline": payload,
        "completed": {}, "active_frame": None,
    }))
    trace = []
    control = SimpleNamespace(
        primary=None, release_error=RuntimeError("synthetic cold release failed"),
        stream_failure=False,
    )

    async def release():
        trace.append("release_attempted")
        if control.release_error is not None:
            raise control.release_error

    async def agent(**kwargs):
        assert kwargs["ids"] == ids
        assert kwargs["baseline"] == baseline
        assert kwargs["project_info"] == project_info
        assert kwargs["_resume_state"] == state
        trace.append("cold_stage")
        kwargs["runtime"].handle = SimpleNamespace(release=release)
        if control.primary is not None:
            raise control.primary

    async def clear(*_):
        trace.append("stream_cleanup")
        if control.stream_failure:
            raise RuntimeError("synthetic cold Redis unavailable")

    monkeypatch.setattr(agent_pipeline, "run_agent_generation", AsyncMock(side_effect=agent))
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock(side_effect=clear))
    return SimpleNamespace(
        ids=ids, control=control, trace=trace, claim=claim,
        run=lambda: agent_pipeline.resume_agent_generation(claim),
    )


@pytest.mark.parametrize("stream_failure", [False, True])
@pytest.mark.parametrize("primary", ["normal", "activation_pending", "yielded", "cancelled"])
async def test_real_cold_resume_preserves_primary_but_never_hides_normal_release_failure(
    cold_resume, stream_failure, primary,
):
    cold_resume.control.stream_failure = stream_failure
    errors = {
        "normal": None,
        "activation_pending": AdaptationActivationPending("synthetic cold pending activation"),
        "yielded": GenerationYielded(
            run_id=cold_resume.ids.run_id, checkpoint=None, attempt_seq=0, phase="reconciling",
        ),
        "cancelled": asyncio.CancelledError("synthetic cold primary cancellation"),
    }
    cold_resume.control.primary = errors[primary]
    expected = errors[primary] if primary != "normal" else cold_resume.control.release_error

    with pytest.raises(type(expected)) as caught:
        await cold_resume.run()

    assert caught.value is expected
    assert cold_resume.trace == ["cold_stage", "release_attempted", "stream_cleanup"]
    agent_pipeline.run_agent_generation.assert_awaited_once()
    lifecycle.clear_stream_state.assert_awaited_once_with(
        cold_resume.ids.project_id, cold_resume.ids.assistant_message_id,
    )


async def test_real_cold_resume_release_failure_blocks_tracker_completion(cold_resume, tracker):
    supervisor.recovery_failure_outcome.return_value = "preserve"

    await supervisor._run_tracked_prompt(
        cold_resume.run(), run_id=cold_resume.ids.run_id, project_id=cold_resume.ids.project_id,
        assistant_message_id=cold_resume.ids.assistant_message_id, label="synthetic-cold-handoff",
    )

    supervisor.finalize_generation_run.assert_not_awaited()
    supervisor._finalize_cancelled_generation.assert_not_awaited()
    supervisor._emergency_error.assert_not_awaited()
    assert tracker.trace == ["running"]
    assert cold_resume.trace == ["cold_stage", "release_attempted", "stream_cleanup"]


async def test_real_cold_resume_success_still_releases_and_clears_stream(cold_resume):
    cold_resume.control.release_error = None

    await cold_resume.run()

    assert cold_resume.trace == ["cold_stage", "release_attempted", "stream_cleanup"]
