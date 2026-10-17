"""SQL/API adaptation acceptance; only controller transport is synthetic.

Run only in the API owner's isolated PostgreSQL harness. The separate root physical
fixture covers real controller/Docker data preservation; this module never creates
a candidate SQL workspace or replaces recovery ownership/checkpoint validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json as jsonlib
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_recovery_execution import _bootstrap_wait
from tests.test_generation_worker import _queued_dispatch
from yleum_api.core import db as core_db
from yleum_api.core.config import get_settings
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import (
    ProjectCellActivityLease,
    ProjectCellOperation,
    ProjectCellWorkspace,
)
from yleum_api.services import orchestrator_client as client
from yleum_api.services import project_cell_executor as executor
from yleum_api.services.generation import (
    agent_pipeline,
    agent_runtime,
    lifecycle,
    recovery_execution,
    recovery_pipeline,
    recovery_store,
    supervisor,
)
from yleum_api.services.generation import progress as generation_progress
from yleum_api.services.generation.contracts import (
    AgentPromptPlan,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)
from yleum_api.services.generation.recovery_contract import (
    ContinuationClaim,
    RecoveryContractError,
    decode_recovery,
)
from yleum_api.services.generation.recovery_pipeline import PreparedAgentEntry
from yleum_api.services.generation_runs import load_generation_dispatch, prompt_hash
from yleum_api.services.project_cell_control import ProjectCellControlReadiness
from yleum_api.services.project_cells import (
    claim_cell_operation_committed,
    complete_cell_operation,
)
from yleum_api.services.promotion_permit import workspace_revision_digest

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _session_owner(engine, run_id):
    async with engine.connect() as connection:
        key = int.from_bytes(run_id.bytes[-4:], "big", signed=True)
        await connection.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {"key": key})
        await connection.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run_id, connection)
        )
        try:
            yield
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await connection.execute(
                text("SELECT pg_advisory_unlock(1195724366,:key)"), {"key": key}
            )
            await connection.commit()


class Controller:
    """Typed, immutable wire receipts; actual client validates the HTTP payload."""

    def __init__(self, run, factory):
        self.run, self.factory = run, factory
        self.candidate = uuid4()
        self.control = None
        self.mode = "held"
        self.calls = []
        self.receipts = {}
        self.current_transition = None
        self.deleted = False
        self.candidate_fence = 1
        self.original_control_fence = None
        self.files = {".omnia/cell.json": '{"version":1}', "src/kept.ts": "kept"}
        self.revision = workspace_revision_digest(self.files)
        self.capabilities = {"portable_machine": True, "database_admin": "isolated_copy",
                             "restoration_adaptation_database_copy_v1": True}

    async def operation(self, factory, operation_id, _client):
        async with factory() as session:
            operation = await session.get(ProjectCellOperation, operation_id)
            if operation.status == "completed":
                return SimpleNamespace(
                    status="completed", operation_id=operation_id,
                    response=client.ProjectCellResourceResponse.from_json(operation.result_payload),
                )
        if self.original_control_fence is None:
            # A real control workspace can predate this candidate by many leases.
            async with factory() as session:
                operation = await session.get(ProjectCellOperation, operation_id)
                control = await session.get(ProjectCellWorkspace, operation.workspace_id)
                control.fencing_epoch = 10
                await session.commit()
        claimed = await claim_cell_operation_committed(factory, operation_id)
        if self.original_control_fence is None:
            self.original_control_fence = claimed.fencing_epoch
        self.calls.append(("operation", claimed.kind, claimed.workspace_id))
        response = client.ProjectCellResourceResponse(
            workspace_id=claimed.workspace_id, state="resources_ready", provider_ref="synthetic",
            fencing_epoch=claimed.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        )
        async with factory() as session:
            await complete_cell_operation(session, operation_id, response.to_wire_json())
            await session.commit()
        return SimpleNamespace(status="completed", response=response, operation_id=operation_id)

    async def prepare(self, workspace_id, **request):
        self.calls.append(("prepare", workspace_id))
        assert len([x for x in self.calls if x[0] == "prepare"]) == 1, "candidate DB copied twice"
        self.control = workspace_id
        if self.mode == "prepare_unknown":
            raise client.OrchestratorUnavailable("synthetic preparation acknowledgement lost")
        return client.RestorationAdaptationWorkspace(
            source_workspace_id=workspace_id, candidate_workspace_id=self.candidate,
            operation_id=request["operation_id"], project_id=self.run.project_id,
            owner_id=self.run.user_id, generation_run_id=self.run.id,
            candidate_fencing_epoch=1, source_database_digest="a" * 64,
            proof_digest="b" * 64, request_digest="c" * 64, capabilities=self.capabilities,
        )

    async def bootstrap(self, workspace_id, *, generation_run_id, fencing_epoch):
        assert generation_run_id == self.run.id
        if workspace_id == self.candidate:
            assert fencing_epoch == self.candidate_fence
        self.calls.append(("snapshot", workspace_id, fencing_epoch))
        return client.ProjectCellAgentWorkspaceSnapshot(
            files=dict(self.files), seeded_from_project=False, generation_run_id=self.run.id,
            fencing_epoch=fencing_epoch, workspace_revision=self.revision,
            capabilities=self.capabilities,
        )

    async def identity(self, workspace_id, *, generation_run_id, fencing_epoch):
        assert generation_run_id == self.run.id
        if workspace_id == self.candidate:
            assert fencing_epoch == self.candidate_fence
        return client.ProjectCellWorkspaceIdentity(
            self.revision, "1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64
        )

    async def cleanup(self, receipt, **kwargs):
        self.calls.append(("cleanup", receipt.candidate_workspace_id, kwargs))
        assert receipt.candidate_workspace_id == self.candidate
        assert set(kwargs) == {"recovery_cycle", "recovery_receipt_digest"}
        async with self.factory() as session:
            run = await session.get(GenerationRun, self.run.id)
            assert run.status in {"cancelled", "failed", "completed"}
        self.deleted = True

    async def wire(self, method, path, *, json, **_kwargs):
        assert method == "POST" and "/recovery/" in path, "unexpected external transport"
        action = path.rsplit("/", 1)[1]
        types = {"hold": client.RestorationAdaptationRecoveryHold,
                 "status": client.RestorationAdaptationRecoveryIdentity,
                 "rebind": client.RestorationAdaptationRecoveryRebind}
        request = types[action].model_validate_json(jsonlib.dumps(json))
        self.calls.append((action, request.transition_id))
        assert request.workspace_id == self.control
        assert request.candidate_workspace_id == self.candidate
        assert request.generation_run_id == self.run.id and request.request_digest == "c" * 64
        if action == "status":
            if self.deleted or request.transition_id != self.current_transition:
                raise client.OrchestratorUnavailable("unknown or historical physical transition")
            assert request.transition_id in self.receipts, "status must read an existing transition"
            return self.receipts[request.transition_id]
        if action == "rebind":
            assert any(x[0] == "status" for x in self.calls), "cold rebind must read status first"
            self.candidate_fence += 1
        pending = self.mode in {"pending", "unknown"}
        state = ("hold_pending" if pending else "held") if action == "hold" else "resumable"
        original = {key: getattr(request, key)
                    for key in client.RestorationAdaptationRecoveryIdentity.model_fields}
        receipt = client.RestorationAdaptationRecoveryReceipt(
            **original, state=state,
            cycle=request.expected_cycle + int(action == "rebind"),
            original_control_fencing_epoch=self.original_control_fence,
            original_candidate_fencing_epoch=1,
            current_control_fencing_epoch=(request.control_fencing_epoch if action == "hold"
                                           else request.new_control_fencing_epoch),
            current_candidate_fencing_epoch=self.candidate_fence,
            barrier_operation_id=uuid5(request.transition_id, "barrier"),
            barrier_receipt_digest=None if pending else "d" * 64,
            checkpoint_ref_digest=None if pending else "e" * 64,
            physical_incarnation_digest=None if pending else "f" * 64,
            physical_proof_digest=None if pending else "1" * 64,
            restore_operation_id=(
                uuid5(request.transition_id, "restore") if action == "rebind" else None
            ),
            terminal_receipt_digest=None if pending else "0" * 64,
        )
        payload = receipt.model_dump(mode="json")
        if self.mode == "foreign":
            payload["candidate_workspace_id"] = str(uuid4())
        if not pending:
            payload["terminal_receipt_digest"] = hashlib.sha256(
                jsonlib.dumps(
                    {k: v for k, v in payload.items() if k != "terminal_receipt_digest"},
                    sort_keys=True, separators=(",", ":"),
                ).encode()
            ).hexdigest()
        self.receipts[request.transition_id] = payload
        self.current_transition = request.transition_id
        if self.mode in {"drift", "cancel"}:
            async with self.factory() as session:
                if self.mode == "cancel":
                    run = await session.get(GenerationRun, self.run.id, with_for_update=True)
                    run.status = "cancel_requested"
                else:
                    workspace = await session.get(
                        ProjectCellWorkspace, self.control, with_for_update=True
                    )
                    workspace.fencing_epoch += 1
                await session.commit()
        if self.mode == "unknown" or (action == "rebind" and self.mode == "rebind_unknown"):
            raise client.OrchestratorUnavailable("synthetic halt acknowledgement lost")
        return payload


async def _setup(monkeypatch, session, engine, *, create=True):
    from tests.test_generation_recovery_readers import install_healthy_reader_inventory

    install_healthy_reader_inventory(monkeypatch, refresh=True)
    run = await _queued_dispatch(session)
    run.status = "running"
    run.prompt_hash = prompt_hash("Собери приложение")
    run.agent_state = {**run.agent_state, "restoration_adaptation": {
        "operation_id": str(uuid4()), "adaptation_run_id": str(run.id),
        "source_snapshot_id": str(uuid4()), "base_draft_snapshot_id": str(uuid4()),
        "source_commit_sha": "a" * 40, "sha256": "b" * 64,
    }}
    initial = _bootstrap_wait(run)
    initial.update(phase="working", next_check_at=None, continuation_claim=ContinuationClaim(
        run_id=run.id, token=uuid4(), attempt_seq=0, worker_id="isolated-initial",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    ).model_dump(mode="json"))
    run.agent_state = {**run.agent_state, "recovery_v1": initial}
    await session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    controller = Controller(run, factory)
    settings = get_settings().model_copy(update={
        "generation_recovery_execution_enabled": True, "generation_recovery_mode": "on",
        "use_max_finalization_coordinator": False, "use_project_cell_activity_watchdog": False,
    })
    for module in (executor, agent_pipeline, recovery_execution):
        monkeypatch.setattr(module, "get_engine", lambda: engine)
    for module in (executor, agent_pipeline, agent_runtime, recovery_execution):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(executor, "inspect_project_cell_control", AsyncMock(return_value=
                        ProjectCellControlReadiness(True, True, "docker_owner_canary", "ready")))
    monkeypatch.setattr(executor, "execute_cell_operation", controller.operation)
    monkeypatch.setattr(executor, "project_cell_prepare_restoration_adaptation", controller.prepare)
    monkeypatch.setattr(executor, "project_cell_cleanup_restoration_adaptation", controller.cleanup)
    monkeypatch.setattr(executor, "project_cell_agent_bootstrap", controller.bootstrap)
    monkeypatch.setattr(executor, "project_cell_agent_identity", controller.identity)
    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", controller.identity)
    monkeypatch.setattr(client, "_request", controller.wire)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(generation_progress, "publish_event", AsyncMock())
    project = await session.get(Project, run.project_id)
    if not create:
        return run, project, factory, controller, None
    handle = await executor.maybe_create_project_cell_executor(
        project_id=run.project_id, project_slug=project.slug, project_template="max_miniapp",
        user_id=run.user_id, generation_run_id=run.id,
        docs_media_execute=AsyncMock(side_effect=AssertionError("legacy executor forbidden")),
    )
    assert handle is not None and handle.workspace_id == controller.candidate
    assert handle.control_workspace_id == controller.control != controller.candidate
    assert (
        handle.control_fencing_epoch
        != handle.restoration_adaptation_workspace.candidate_fencing_epoch
    )
    assert await session.get(ProjectCellWorkspace, controller.candidate) is None
    return run, project, factory, controller, handle


async def _save_wait(run, project, factory, handle, *, phase="waiting_dependency"):
    dispatch = load_generation_dispatch(run)
    ids = GenerationIds(run.id, run.project_id, run.user_id,
                        run.user_message_id, run.assistant_message_id)
    baseline = SourceBaseline(None, None, {})
    info = ProjectGenerationFacts("max_miniapp", project.slug, project.name, None, None,
                                  False, "ru", False, "", "")
    plan = AgentPromptPlan("system", dispatch.prompt_text, "synthetic", None, 2,
                           "seed", None, "system", None, False)
    prepared = PreparedAgentEntry(
        (dispatch.prompt_text, False, False, False), None,
        StackPrompt("seed", None, None, None, "system", False), plan, {}, None, None,
    )
    context = {"schema_version": 1, "entry": "pipeline", "active_frame": None,
               "completed": {"agent_entry": prepared.to_json()}, "pipeline": {
                   "ids": {k: str(v) for k, v in asdict(ids).items()},
                   "baseline": asdict(baseline), "project_info": asdict(info),
                   **{key: getattr(dispatch, key) for key in (
                       "prompt_text", "force_model", "is_free", "model_id", "orchestrate",
                       "selected_elements")},
               }}
    async with factory() as session:
        current = await session.get(GenerationRun, run.id)
        state = decode_recovery(current.agent_state["recovery_v1"])
    driver = recovery_pipeline.attach_driver(factory, state, context, handle)
    await driver.save(state.execution, context=context)
    await driver.save(driver.state.execution.model_copy(update={
        "stage": "agent", "substep": "request_ready",
    }), context=context, phase=phase)
    return driver


def _released_calls(controller):
    return [x for x in controller.calls if x[:2] == ("operation", "release")]


@pytest.mark.parametrize("mode", ["pending", "unknown", "foreign", "drift", "cancel"])
async def test_unconfirmed_adaptation_hold_never_releases_or_cleans_candidate(
    monkeypatch, db_session, test_engine, mode,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        controller.mode = mode
        for attempt in range(2):
            try:
                await handle.release()
            except (client.OrchestratorUnavailable, executor.ProjectCellExecutorUnavailable,
                    RecoveryContractError):
                pass
            if attempt == 0:
                # Cancellation racing a yield invalidates that call's release
                # authority. A later stop-only call must re-read current status.
                assert _released_calls(controller) == []
        assert [x[0] for x in controller.calls].count("hold") == 1
        assert len(_released_calls(controller)) == (1 if mode == "cancel" else 0)
        assert not any(x[0] == "cleanup" for x in controller.calls)
        async with factory() as session:
            workspace = await session.get(ProjectCellWorkspace, controller.control)
            assert workspace.generation_run_id == (None if mode == "cancel" else run.id)
            assert await session.get(ProjectCellWorkspace, controller.candidate) is None


async def test_active_sql_activity_is_not_quiescence_even_after_expiry(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        old = datetime.now(UTC) - timedelta(minutes=10)
        db_session.add(ProjectCellActivityLease(
            workspace_id=controller.control, generation_run_id=run.id, kind="finalization",
            fencing_epoch=handle.control_fencing_epoch, state="active", started_at=old,
            heartbeat_at=old, deadline_at=old + timedelta(seconds=1),
        ))
        await db_session.commit()
        try:
            await handle.release()
        except (client.OrchestratorUnavailable, executor.ProjectCellExecutorUnavailable,
                RecoveryContractError):
            pass
        assert _released_calls(controller) == []
        assert not any(x[0] == "cleanup" for x in controller.calls)


async def test_confirmed_hold_cold_pipeline_reuses_candidate_and_terminal_cleanup_once(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        await handle.release()
        assert len(_released_calls(controller)) == 1
        assert not any(x[0] == "cleanup" for x in controller.calls)
        async with factory() as session:
            claim = await recovery_execution.claim_continuation(
                session, run_id=run.id, expected_attempt=0, worker_id="isolated-cold",
                now=datetime.now(UTC) + timedelta(seconds=1),
            )
            assert claim is not None, "confirmed adaptation hold did not release SQL ownership"
            await session.commit()
        reached, resumed_handles = [], []
        actual_prepare = agent_pipeline.prepare_agent_runtime

        async def observe_real_prepare(**kwargs):
            result = await actual_prepare(**kwargs)
            resumed_handles.append(kwargs["runtime"].handle)
            return result

        monkeypatch.setattr(agent_pipeline, "prepare_agent_runtime", observe_real_prepare)
        actual_rebind = recovery_pipeline.rebind_continuation

        async def stop_after_real_rebind(driver):
            await actual_rebind(driver)
            assert driver.state.binding.workspace_id == str(controller.candidate)
            assert driver.state.execution.control_workspace_id == controller.control
            assert driver.state.execution.control_fencing_epoch > handle.control_fencing_epoch
            reached.append(driver)
            await driver.wait()  # Commit a real next wait; no model or logical tool is executed.

        monkeypatch.setattr(recovery_pipeline, "rebind_continuation", stop_after_real_rebind)
        for name in ("classify_agent_turn", "prepare_stack_prompt", "prepare_agent_prompt",
                     "stage_max_starter"):
            monkeypatch.setattr(agent_pipeline, name,
                                AsyncMock(side_effect=AssertionError("original prompt replay")))
        with pytest.raises(recovery_execution.GenerationYielded):
            await agent_pipeline.resume_agent_generation(claim)
        assert len(reached) == 1
        assert [x[0] for x in controller.calls].count("prepare") == 1
        assert [x[0] for x in controller.calls].count("rebind") == 1
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "cancelled"
            await session.commit()
        # Current acknowledged cycle is the only cleanup authority, never the old handle.
        assert len(resumed_handles) == 1
        with pytest.raises(RecoveryContractError):
            await handle.release()
        assert not any(x[0] == "cleanup" for x in controller.calls)
        await resumed_handles[0].release()
        await resumed_handles[0].release()
        assert len([x for x in controller.calls if x[0] == "cleanup"]) == 1
        assert await db_session.get(ProjectCellWorkspace, controller.candidate) is None


async def test_unknown_original_prepare_is_never_repeated_or_released(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, _ = await _setup(
        monkeypatch, db_session, test_engine, create=False,
    )
    controller.mode = "prepare_unknown"
    async with _session_owner(test_engine, run.id):
        for _ in range(2):
            with pytest.raises((executor.ProjectCellExecutorUnavailable, RecoveryContractError)):
                await executor.maybe_create_project_cell_executor(
                    project_id=run.project_id, project_slug=project.slug,
                    project_template="max_miniapp", user_id=run.user_id,
                    generation_run_id=run.id, docs_media_execute=AsyncMock(),
                )
        assert [x[0] for x in controller.calls].count("prepare") == 1
        assert _released_calls(controller) == []
        assert not any(x[0] == "cleanup" for x in controller.calls)
        async with factory() as session:
            effects = list((await session.scalars(select(GenerationRecoveryEffect).where(
                GenerationRecoveryEffect.run_id == run.id,
            ))).all())
            assert len(effects) == 1 and effects[0].state == "inflight"
            assert effects[0].receipt_ref is None
            claim = await recovery_execution.claim_continuation(
                session, run_id=run.id, expected_attempt=0, worker_id="unknown-prepare",
                now=datetime.now(UTC) + timedelta(minutes=10),
            )
            assert claim is None, "an unknown original preparation is not an empty workspace"


@pytest.mark.parametrize("corruption", ["missing", "foreign", "unknown"])
async def test_candidate_checkpoint_cannot_replace_server_prepare_authority(
    monkeypatch, db_session, test_engine, corruption,
):
    run, project, factory, _controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with factory() as session:
        row = await session.scalar(select(GenerationRecoveryEffect).where(
            GenerationRecoveryEffect.run_id == run.id,
        ))
        if corruption == "missing":
            await session.delete(row)
        elif corruption == "unknown":
            row.state = "unknown"
        else:
            row.receipt_ref = {**row.receipt_ref, "owner_id": str(uuid4())}
            row.receipt_sha256 = hashlib.sha256(jsonlib.dumps(
                row.receipt_ref, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
        await session.commit()
    async with _session_owner(test_engine, run.id):
        with pytest.raises(RecoveryContractError):
            await _save_wait(run, project, factory, handle)


@pytest.mark.parametrize(
    "field", ["workspace_revision", "schema_data_digest", "environment_digest"],
)
async def test_candidate_checkpoint_still_requires_fresh_source_schema_and_environment(
    monkeypatch, db_session, test_engine, field,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)

    async def changed_identity(*args, **kwargs):
        actual = await controller.identity(*args, **kwargs)
        return replace(actual, **{field: "9" * 64})

    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", changed_identity)
    async with _session_owner(test_engine, run.id):
        with pytest.raises(RecoveryContractError, match="source/schema/environment"):
            await _save_wait(run, project, factory, handle)


@pytest.mark.parametrize("race", ["cancel", "activity"])
async def test_cancel_after_hold_before_sql_release_does_not_release_control(
    monkeypatch, db_session, test_engine, race,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    original_release = executor._release_generation_lease

    async def cancelled_release(**kwargs):
        async with factory() as session:
            if race == "cancel":
                current = await session.get(GenerationRun, run.id, with_for_update=True)
                current.status = "cancel_requested"
            else:
                now = datetime.now(UTC)
                session.add(ProjectCellActivityLease(
                    workspace_id=controller.control, generation_run_id=run.id,
                    kind="finalization", state="active", fencing_epoch=handle.control_fencing_epoch,
                    started_at=now, heartbeat_at=now, deadline_at=now + timedelta(minutes=1),
                ))
            await session.commit()
        await original_release(**kwargs)

    monkeypatch.setattr(executor, "_release_generation_lease", cancelled_release)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        with pytest.raises((executor.ProjectCellExecutorUnavailable, RecoveryContractError)):
            await handle.release()
        assert _released_calls(controller) == []
        assert not any(x[0] == "cleanup" for x in controller.calls)


@pytest.mark.parametrize("failure", ["unknown_cleanup", "forged_release"])
async def test_terminal_cleanup_requires_real_release_and_never_repeats_unknown_delete(
    monkeypatch, db_session, test_engine, failure,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        await handle.release()
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "cancelled"
            state = recovery_execution.recovery_state(current)
            if failure == "forged_release":
                operation = await session.get(
                    ProjectCellOperation, state.execution.release_operation_id,
                )
                operation.result_payload = {}
            await session.commit()
        original_cleanup = controller.cleanup

        async def lost_cleanup(*args, **kwargs):
            await original_cleanup(*args, **kwargs)
            raise client.OrchestratorUnavailable("synthetic cleanup acknowledgement lost")

        if failure == "unknown_cleanup":
            monkeypatch.setattr(
                executor, "project_cell_cleanup_restoration_adaptation", lost_cleanup,
            )
        for _ in range(2):
            with pytest.raises((RecoveryContractError, client.OrchestratorUnavailable)):
                await handle.release()
        assert len([x for x in controller.calls if x[0] == "cleanup"]) == (
            1 if failure == "unknown_cleanup" else 0
        )


async def test_rebind_ack_loss_reads_current_transition_not_historical_hold(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle)
        await handle.release()
        async with factory() as session:
            claim = await recovery_execution.claim_continuation(
                session, run_id=run.id, expected_attempt=0, worker_id="cold-lost-rebind",
                now=datetime.now(UTC) + timedelta(seconds=1),
            )
            assert claim is not None
            await session.commit()
        controller.mode = "rebind_unknown"

        async def cold_create():
            return await executor.maybe_create_project_cell_executor(
                project_id=run.project_id, project_slug=project.slug,
                project_template="max_miniapp",
                user_id=run.user_id, generation_run_id=run.id, docs_media_execute=AsyncMock(),
            )

        with pytest.raises(executor.ProjectCellExecutorUnavailable):
            await cold_create()
        assert [x[0] for x in controller.calls].count("rebind") == 1
        rebound = await cold_create()
        assert rebound.workspace_id == controller.candidate
        assert (await rebound.current_identity()).fencing_epoch == controller.candidate_fence
        assert [x[0] for x in controller.calls].count("prepare") == 1
        assert [x[0] for x in controller.calls].count("rebind") == 1
        assert controller.calls[-2][0] == "status"


@pytest.mark.parametrize("runtime_cleanup", [True, False])
async def test_normal_cancel_before_first_hold_stops_releases_and_cleans_after_terminal_commit(
    monkeypatch, db_session, test_engine, runtime_cleanup,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    monkeypatch.setattr(core_db, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    monkeypatch.setattr(supervisor, "clear_generation_cancel", AsyncMock())
    started = asyncio.Event()
    order = []
    actual_finalize = supervisor._finalize_cancelled_generation

    async def finalize(*args):
        order.append("terminalize")
        await actual_finalize(*args)

    async def cancel(_run_id):
        await started.wait()
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "cancel_requested"
            await session.commit()

    async def work():
        await _save_wait(run, project, factory, handle, phase="working")
        started.set()
        try:
            await asyncio.Future()
        finally:
            order.append("runtime_cleanup")
            if runtime_cleanup:
                await lifecycle.cleanup_generation_runtime(
                    GenerationRuntime(handle=handle), run.project_id, run.assistant_message_id,
                )

    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", cancel)
    monkeypatch.setattr(supervisor, "_finalize_cancelled_generation", finalize)
    async with _session_owner(test_engine, run.id):
        await supervisor._run_tracked_prompt(
            work(), run_id=run.id, project_id=run.project_id,
            assistant_message_id=run.assistant_message_id, label="isolated-adaptation-cancel",
        )
        assert order == ["runtime_cleanup", "terminalize"]
        async with factory() as session:
            current = await session.get(GenerationRun, run.id)
            assert current.status == "cancelled"
            control = await session.get(ProjectCellWorkspace, controller.control)
            assert control.generation_run_id is None
        assert [x[0] for x in controller.calls].count("hold") == 1
        assert len(_released_calls(controller)) == 1
        assert [x[0] for x in controller.calls].count("cleanup") == 1
        assert [x[0] for x in controller.calls].count("prepare") == 1
        assert not any(x[0] == "rebind" for x in controller.calls)
        await actual_finalize(run.project_id, run.assistant_message_id, run.id)
        assert [x[0] for x in controller.calls].count("cleanup") == 1


@pytest.mark.parametrize("mode", ["pending", "unknown", "foreign", "drift", "activity"])
async def test_normal_cancellation_retains_unconfirmed_physical_writer(
    monkeypatch, db_session, test_engine, mode,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle, phase="working")
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "cancel_requested"
            if mode == "activity":
                now = datetime.now(UTC)
                session.add(ProjectCellActivityLease(
                    workspace_id=controller.control, generation_run_id=run.id,
                    kind="finalization", state="active", fencing_epoch=handle.control_fencing_epoch,
                    started_at=now, heartbeat_at=now, deadline_at=now + timedelta(minutes=1),
                ))
            await session.commit()
        controller.mode = mode
        await lifecycle.cleanup_generation_runtime(
            GenerationRuntime(handle=handle), run.project_id, run.assistant_message_id,
        )
        await supervisor._finalize_cancelled_generation(
            run.project_id, run.assistant_message_id, run.id,
        )
        async with factory() as session:
            current = await session.get(GenerationRun, run.id)
            assert current.status == "cancelled"
            control = await session.get(ProjectCellWorkspace, controller.control)
            assert control.generation_run_id == run.id
        assert [x[0] for x in controller.calls].count("hold") == (0 if mode == "activity" else 1)
        assert _released_calls(controller) == []
        assert not any(x[0] in {"cleanup", "rebind"} for x in controller.calls)


async def test_cancel_event_loss_retries_terminal_cleanup_without_second_delete(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    event = AsyncMock(side_effect=[RuntimeError("synthetic event acknowledgement lost"), None])
    monkeypatch.setattr(supervisor, "publish_event", event)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle, phase="working")
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "cancel_requested"
            await session.commit()
        with pytest.raises(RuntimeError, match="event acknowledgement"):
            await supervisor._finalize_cancelled_generation(
                run.project_id, run.assistant_message_id, run.id,
            )
        assert [x[0] for x in controller.calls].count("cleanup") == 1
        await supervisor._finalize_cancelled_generation(
            run.project_id, run.assistant_message_id, run.id,
        )
        assert event.await_count == 2
        assert [x[0] for x in controller.calls].count("cleanup") == 1


@pytest.mark.parametrize("mismatch", ["project", "message", "owner"])
async def test_terminal_finalizer_refuses_foreign_scope_before_any_effect(
    monkeypatch, db_session, test_engine, mismatch,
):
    run, _project, factory, controller, _handle = await _setup(monkeypatch, db_session, test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    event = AsyncMock()
    monkeypatch.setattr(supervisor, "publish_event", event)
    async with factory() as session:
        current = await session.get(GenerationRun, run.id, with_for_update=True)
        current.status = "cancel_requested"
        if mismatch == "owner":
            other = await _queued_dispatch(session)
            project = await session.get(Project, run.project_id, with_for_update=True)
            project.owner_id = other.user_id
        await session.commit()
    await supervisor._finalize_cancelled_generation(
        uuid4() if mismatch == "project" else run.project_id,
        uuid4() if mismatch == "message" else run.assistant_message_id,
        run.id,
    )
    async with factory() as session:
        current = await session.get(GenerationRun, run.id)
        assert current.status == "cancel_requested"
    assert not any(x[0] in {"hold", "cleanup", "rebind"} for x in controller.calls)
    assert _released_calls(controller) == []
    event.assert_not_awaited()


async def test_unknown_cancel_commit_requires_fresh_terminal_read_before_cleanup(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    lost = False

    class CommitAcknowledgementLoss:
        def __init__(self):
            self.session = factory()

        def __getattr__(self, name):
            return getattr(self.session, name)

        async def __aenter__(self):
            await self.session.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.session.__aexit__(*args)

        async def commit(self):
            nonlocal lost
            await self.session.commit()
            if not lost:
                lost = True
                raise RuntimeError("synthetic SQL terminal commit acknowledgement lost")

    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle, phase="working")
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "cancel_requested"
            await session.commit()
        monkeypatch.setattr(supervisor, "async_sessionmaker", lambda *_a, **_k:
                            CommitAcknowledgementLoss)
        with pytest.raises(RuntimeError, match="SQL terminal commit acknowledgement"):
            await supervisor._finalize_cancelled_generation(
                run.project_id, run.assistant_message_id, run.id,
            )
        assert not any(x[0] in {"hold", "cleanup"} for x in controller.calls)
        await supervisor._finalize_cancelled_generation(
            run.project_id, run.assistant_message_id, run.id,
        )
        assert [x[0] for x in controller.calls].count("cleanup") == 1
        assert len(_released_calls(controller)) == 1


async def test_failed_active_candidate_uses_only_terminal_stop_and_cleanup(
    monkeypatch, db_session, test_engine,
):
    run, project, factory, controller, handle = await _setup(monkeypatch, db_session, test_engine)
    async with _session_owner(test_engine, run.id):
        await _save_wait(run, project, factory, handle, phase="working")
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "failed"
            await session.commit()
        await handle.release()
        assert [x[0] for x in controller.calls].count("hold") == 1
        assert [x[0] for x in controller.calls].count("cleanup") == 1
        assert len(_released_calls(controller)) == 1
        assert not any(x[0] == "rebind" for x in controller.calls)


async def test_legacy_terminal_finalizer_has_no_adaptation_physical_effects(
    monkeypatch, db_session, test_engine,
):
    run = await _queued_dispatch(db_session)
    run.status = "cancel_requested"
    await db_session.commit()
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    forbidden = AsyncMock(side_effect=AssertionError("legacy run cannot own adaptation effects"))
    monkeypatch.setattr(client, "project_cell_restoration_recovery", forbidden)
    await supervisor._finalize_cancelled_generation(
        run.project_id, run.assistant_message_id, run.id,
    )
    await db_session.refresh(run)
    assert run.status == "cancelled"
    forbidden.assert_not_awaited()
