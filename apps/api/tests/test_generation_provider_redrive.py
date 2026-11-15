"""A closed Sonnet connection refusal can retry work without replaying its POST."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import select, text

from tests.test_generation_recovery_deadlines import clock_owner as clock_owner
from tests.test_generation_recovery_deadlines import response_schema as response_schema
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services import llm_client
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_reconciliation, recovery_store
from yleum_api.services.generation.recovery_contract import CheckpointRef, RecoveryBinding
from yleum_api.services.generation_deployment_drain import begin_drain, drain_status, end_drain
from yleum_api.services.llm_client import ProviderChainExhausted
from yleum_api.services.orchestrator_client import ProjectCellResourceResponse


async def _closed_chain(c, edge):
    now = datetime.now(UTC)
    workspace = ProjectCellWorkspace(
        project_id=c.run.project_id, owner_id=c.run.user_id, provider="synthetic",
        state="ready", fencing_epoch=3, generation_run_id=c.run.id,
    )
    c.session.add(workspace)
    await c.session.flush()
    ref = CheckpointRef(object_key="refused-native-context", sha256="d" * 64)
    root = uuid5(c.run.id, "recovery-provider-v1:12")
    state = c.driver.state.model_copy(update={
        "cursor": "agent",
        "binding": RecoveryBinding(
            workspace_id=str(workspace.id), fencing_epoch=3,
            source_sha256="a" * 64, schema_sha256="b" * 64, environment_sha256="c" * 64,
        ),
        "checkpoint": ref, "context_ref": ref,
        "clock": c.driver.state.clock.model_copy(update={
            "segment_used_ms": 0, "segment_deadline": now + timedelta(minutes=6),
        }),
        "execution": c.driver.state.execution.model_copy(update={
            "stage": "agent", "substep": "response_wait",
            "logical_step_seq": 12, "provider_effect_id": root, "pending_effect_id": root,
            "physical_state": "active", "control_workspace_id": workspace.id,
            "control_fencing_epoch": 3, "execution_context_ref": ref,
        }),
    })
    c.run.execution_backend = "worker"
    c.run.execution_started_at = now
    scope = dict(run_id=c.run.id, project_id=c.run.project_id, user_id=c.run.user_id)
    c.run.agent_state = {**c.run.agent_state, execution.RECOVERY_KEY: state.model_dump(mode="json")}
    await c.session.execute(text(
        "INSERT INTO generation_response_budgets (bucket,storage_epoch,quota_bytes,"
        "observation_nonce) VALUES ('refused',:epoch,4096,:nonce)"
    ), {"epoch": uuid4(), "nonce": uuid4()})
    parent = None
    parent_effect = None
    for ordinal, provider in enumerate(("llmgw", "aitunnel")):
        attempt = uuid4()
        effect = root if ordinal == 0 else uuid5(root, "attempt-child")
        closed = ordinal == 0 or edge != "unknown"
        reason = "upstream_refused" if ordinal == 0 and edge == "mixed" else "preconnect"
        call_id = uuid4()
        manifest = f"private/qa/{attempt}"
        c.session.add(ProviderCall(
            **scope, id=call_id, route="/v1/messages",
            requested_model="claude-sonnet-5.5", provider_scope=provider,
            expense_owner="user", stage="native_agent", free=False,
            status="failed" if closed else "ambiguous", finished_at=now,
            receipt_hash="d" * 64,
        ))
        await c.session.flush()
        c.session.add(GenerationProviderAttempt(
            **scope, attempt_id=attempt, dispatch_token=uuid4(),
            prompt_hash=c.run.prompt_hash, request_sha256="e" * 64,
            provider_call_id=call_id, parent_attempt_id=parent,
            phase="closed" if closed else "indeterminate",
            terminal_reason=reason if closed else None,
            response_manifest_key=manifest, response_sha256="f" * 64,
        ))
        await c.session.flush()
        c.session.add(GenerationRecoveryEffect(
            **scope, effect_id=effect, logical_step_seq=12, effect_ordinal=ordinal,
            kind="provider", input_digest="e" * 64, provider_attempt_id=attempt,
            attempt_seq=state.attempt_seq, claim_token=state.continuation_claim.token,
            parent_effect_id=parent_effect,
        ))
        await c.session.execute(text(
            "INSERT INTO generation_responses "
            "(attempt_id,bucket,writer_token,state,allocated_bytes,manifest_key,"
            "manifest_sha256,response_sha256,reconciliation_hold,reconciled_at) "
            "VALUES (:attempt,'refused',:writer,'complete',512,:manifest,:sha,:body,:held,:instant)"
        ), {
            "attempt": attempt, "writer": uuid4(), "manifest": manifest,
            "sha": "a" * 64, "body": "f" * 64, "held": not closed or edge == "hold",
            "instant": now,
        })
        parent, parent_effect = attempt, effect
    await c.session.commit()
    frame = {
        "version": 1, "kind": "native", "value": {"request": {"model": "claude-sonnet-5.5"}},
    }
    return execution.ExecutionDriver(
        c.factory, state, {"active_frame": frame}, c.driver.snapshot,
    ), frame, parent


@pytest.mark.parametrize("edge", ["confirmed", "mixed", "unknown", "hold"])
async def test_double_preconnect_uses_real_closed_chain_before_scheduling(
    clock_owner, response_schema, monkeypatch, edge,
):
    c = clock_owner
    driver, frame, refused = await _closed_chain(c, edge)

    async def read_refusal():
        raise ProviderChainExhausted()

    retained = AsyncMock(side_effect=ProviderChainExhausted())
    monkeypatch.setattr(llm_client, "require_recovered_response", retained)

    now = datetime.now(UTC)
    async with c.owned():
        if edge == "confirmed":
            with pytest.raises(execution.GenerationYielded):
                await driver.provider(frame, read_refusal)
        else:
            with pytest.raises(ProviderChainExhausted):
                await driver.provider(frame, read_refusal)
    await c.session.refresh(c.run)
    saved = execution.recovery_state(c.run)
    assert saved.execution.logical_step_seq == 12
    assert saved.identity.run_id == c.run.id
    if edge == "confirmed":
        assert saved.phase == "waiting_dependency"
        assert saved.next_check_at >= now + timedelta(seconds=60)
        assert c.run.agent_state[execution.PROVIDER_REFUSAL_REDRIVE_KEY] == {
            "version": 1, "attempt_id": str(refused),
        }
        assert retained.await_args.args[0].attempt_id == refused
    else:
        assert saved.phase == "working"
        assert saved.next_check_at is None
        retained.assert_not_awaited()


async def _wait_for_redrive(c, monkeypatch):
    driver, frame, refused = await _closed_chain(c, "confirmed")
    monkeypatch.setattr(
        llm_client, "require_recovered_response", AsyncMock(side_effect=ProviderChainExhausted()),
    )
    original_request = AsyncMock(side_effect=ProviderChainExhausted())
    async with c.owned():
        with pytest.raises(execution.GenerationYielded):
            await driver.provider(frame, original_request)
    return driver, frame, refused, original_request


async def _record_owned_release(c, driver, *, close_clock=True, fence_step=1):
    await c.session.refresh(c.run)
    wait = driver.state
    workspace = await c.session.get(ProjectCellWorkspace, wait.execution.control_workspace_id)
    workspace.fencing_epoch += fence_step
    workspace.generation_run_id = None
    op_id = uuid4()
    receipt = ProjectCellResourceResponse(
        workspace_id=workspace.id, state="resources_ready", provider_ref="owned",
        fencing_epoch=workspace.fencing_epoch, checkpoint_ref=None,
        has_workspace=True, has_agent_home=True, has_postgres=True, has_redis=True,
    )
    c.session.add(ProjectCellOperation(
        id=op_id, workspace_id=workspace.id, generation_run_id=c.run.id, kind="release",
        status="completed", fencing_epoch=workspace.fencing_epoch,
        finished_at=datetime.now(UTC), idempotency_key=str(uuid4()), request_digest="a" * 64,
        result_payload=receipt.to_wire_json(),
    ))
    released = wait.model_copy(update={
        "execution": wait.execution.model_copy(update={
            "physical_state": "released", "release_operation_id": op_id,
            "control_fencing_epoch": workspace.fencing_epoch,
        }),
    })
    c.run.agent_state = {
        **c.run.agent_state, execution.RECOVERY_KEY: released.model_dump(mode="json"),
    }
    await c.session.commit()
    driver.state = released
    if close_clock:
        async with c.owned():
            await driver.account_work(stopped=True)
    return driver.state


@pytest.mark.parametrize("edge", ["allowed", "cancelled", "foreign_marker"])
async def test_due_preconnect_redrive_uses_owned_release_and_new_logical_step(
    clock_owner, response_schema, monkeypatch, edge,
):
    c = clock_owner
    driver, frame, refused, original_request = await _wait_for_redrive(c, monkeypatch)
    # This fixture exercises SQL ownership and the claim. The controller and
    # private object store are collaborators; their actual writes are not needed.
    monkeypatch.setattr(recovery_store, "_baseline_sha", AsyncMock())
    due = driver.state.next_check_at
    paused = await _record_owned_release(c, driver)
    assert not paused.clock.interval_open
    assert paused.next_check_at == due

    async with c.owned(), c.factory() as session:
        early = await execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=paused.attempt_seq,
            worker_id="too-early", now=paused.next_check_at - timedelta(seconds=1),
        )
        assert early is None
        await session.commit()
    if edge != "allowed":
        await c.session.refresh(c.run)
        if edge == "cancelled":
            c.run.status = "cancel_requested"
        else:
            c.run.agent_state = {
                **c.run.agent_state, execution.PROVIDER_REFUSAL_REDRIVE_KEY: {
                    "version": 1, "attempt_id": str(uuid4()),
                },
            }
        await c.session.commit()
    async with c.owned(), c.factory() as session:
        claim = await execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=paused.attempt_seq,
            worker_id="cold-worker", now=paused.next_check_at + timedelta(seconds=1),
        )
        await session.commit()
        if edge == "cancelled":
            assert claim is None
            original_request.assert_awaited_once()
            return
        reopened_run = await session.get(type(c.run), c.run.id)
        reopened = execution.recovery_state(reopened_run)
        assert claim is not None and claim.run_id == c.run.id
        assert reopened.execution.logical_step_seq == paused.execution.logical_step_seq
        assert reopened.clock.segment_used_ms == paused.clock.segment_used_ms
        marker = reopened_run.agent_state[execution.PROVIDER_REFUSAL_REDRIVE_KEY]
        assert (marker == {"version": 1, "attempt_id": str(refused)}) == (edge == "allowed")
        restored = execution.ExecutionDriver(
            c.factory, reopened, driver.context, c.driver.snapshot,
        )
        assert restored.restore_frame("native") == frame["value"]
        if edge == "foreign_marker":
            with pytest.raises(ProviderChainExhausted):
                await restored.provider(frame, original_request)
            assert original_request.await_count == 2
            return
        with pytest.raises(execution.NativeProviderRedrive):
            await restored.provider(frame, original_request)
        assert restored.restore_frame("native") is None
        old_root = reopened.execution.pending_effect_id
        advanced = None

        async def record_next_context(self, cursor, *, context):
            nonlocal advanced
            assert context["active_frame"] == frame
            advanced = cursor
            self.state = self.state.model_copy(update={
                "execution": cursor, "state_revision": self.state.state_revision + 1,
            })

        async def dispatch_new():
            assert not recovery_reconciliation.provider_reconciliation_requested()
            assert advanced.logical_step_seq == reopened.execution.logical_step_seq + 1
            assert advanced.pending_effect_id != old_root
            return {"stop_reason": "end_turn"}

        monkeypatch.setattr(execution.ExecutionDriver, "save", record_next_context)
        assert await restored.provider(frame, dispatch_new) == {"stop_reason": "end_turn"}
    assert original_request.await_count == 2  # Initial read and the same cold read.


@pytest.mark.parametrize(
    "edge",
    [
        "armed", "no_marker", "foreign", "malformed", "next_active",
        "generic_open", "unproved_open", "wrong_epoch",
    ],
)
async def test_only_exact_stopped_preconnect_wait_is_quiescent_for_deployment(
    clock_owner, response_schema, monkeypatch, edge,
):
    c = clock_owner
    driver, _, refused, _ = await _wait_for_redrive(c, monkeypatch)
    waiting = await _record_owned_release(
        c, driver, close_clock=edge not in {"generic_open", "unproved_open"},
        fence_step=2 if edge == "wrong_epoch" else 1,
    )
    await c.session.refresh(c.run)
    key = execution.PROVIDER_REFUSAL_REDRIVE_KEY
    if edge in {"no_marker", "foreign", "malformed", "generic_open"}:
        agent_state = dict(c.run.agent_state)
        if edge in {"no_marker", "generic_open"}:
            agent_state.pop(key, None)
        else:
            agent_state[key] = {
                "version": True if edge == "malformed" else 1,
                "attempt_id": str(uuid4() if edge == "foreign" else refused),
            }
        c.run.agent_state = agent_state
    if edge == "unproved_open":
        await c.session.execute(text(
            "UPDATE generation_responses SET reconciliation_hold=true WHERE attempt_id=:id"
        ), {"id": refused})
    if edge == "next_active":
        c.session.add(GenerationProviderAttempt(
            run_id=c.run.id, project_id=c.run.project_id, user_id=c.run.user_id,
            attempt_id=uuid4(), dispatch_token=uuid4(), prompt_hash=c.run.prompt_hash,
            request_sha256="e" * 64, phase="reserved",
        ))
    await c.session.commit()

    release_sha = "a" * 40
    await begin_drain(c.session, release_sha)
    await c.session.commit()
    status = await drain_status(c.session)
    assert status["drained"] is (edge == "armed")
    assert status["active"]["generations"] == (0 if edge == "armed" else 1)
    if edge != "armed":
        return
    assert c.run.agent_state[key] == {"version": 1, "attempt_id": str(refused)}
    raw_clock = c.run.agent_state[execution.RECOVERY_KEY]["clock"]
    assert "provider_refusal_redrive_attempt" not in raw_clock

    async with c.owned(), c.factory() as session:
        assert await execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=waiting.attempt_seq,
            worker_id="fenced", now=waiting.next_check_at + timedelta(seconds=1),
        ) is None
    await end_drain(c.session, release_sha)
    await c.session.commit()
    await c.session.refresh(c.run)
    assert execution.recovery_state(c.run) == waiting
    assert c.run.agent_state[key]["attempt_id"] == str(refused)


async def test_interrupted_release_only_can_finish_drain_with_open_clock(
    clock_owner, response_schema, monkeypatch,
):
    from yleum_api.models.project import Project
    from yleum_api.services import project_cell_lifecycle
    from yleum_api.services.project_cells import (
        claim_cell_operation_committed,
        execution_run_id,
        mark_cell_operation_indeterminate,
        reserve_cell_operation,
    )
    from yleum_api.workers import generation

    c = clock_owner
    driver, _, refused, original_request = await _wait_for_redrive(c, monkeypatch)
    waiting = driver.state
    assert waiting.clock.interval_open
    await c.session.refresh(c.run)
    project = await c.session.get(Project, c.run.project_id)
    project.template = "max_miniapp"
    workspace_id = waiting.execution.control_workspace_id
    proof = {
        "source_sha256": waiting.binding.source_sha256,
        "environment_sha256": waiting.binding.environment_sha256,
    }
    operation, _ = await reserve_cell_operation(
        c.session, workspace_id=workspace_id, generation_run_id=c.run.id, kind="release",
        idempotency_key=f"generation:{c.run.id}:release:profile:cycle:0",
        request={"require_environment_proof": proof},
    )
    releasing = waiting.model_copy(update={
        "execution": waiting.execution.model_copy(update={
            "physical_state": "release_pending", "release_operation_id": operation.id,
        }),
    })
    c.run.agent_state = {
        **c.run.agent_state, execution.RECOVERY_KEY: releasing.model_dump(mode="json"),
    }
    await c.session.commit()

    token = execution_run_id.set(c.run.id)
    try:
        first = await claim_cell_operation_committed(c.factory, operation.id)
    finally:
        execution_run_id.reset(token)
    assert first.fencing_epoch == waiting.binding.fencing_epoch + 1
    async with c.factory() as session:
        await mark_cell_operation_indeterminate(
            session, operation.id, "lost release acknowledgement",
        )
        await session.commit()

    async def controller(_client, method, request):
        assert method == "control"
        assert request.operation_id == operation.id
        assert request.request_digest == operation.request_digest
        assert request.workspace_id == workspace_id and request.fencing_epoch == first.fencing_epoch
        assert request.require_environment_proof.model_dump(mode="json") == proof
        return ProjectCellResourceResponse(
            workspace_id=workspace_id, state="resources_ready", provider_ref="owned",
            fencing_epoch=first.fencing_epoch, checkpoint_ref=None, has_workspace=True,
            has_agent_home=True, has_postgres=True, has_redis=True,
        )

    wire = AsyncMock(side_effect=controller)
    resume = AsyncMock(side_effect=AssertionError("release must not resume provider work"))
    monkeypatch.setattr(project_cell_lifecycle, "_invoke_client", wire)
    monkeypatch.setattr(execution, "resume_generation", resume)
    monkeypatch.setattr(generation, "get_engine", lambda: c.engine)
    release_sha = "a" * 40
    async with c.factory() as session:
        await begin_drain(session, release_sha)
        await session.commit()
        assert (await drain_status(session))["drained"] is False

    assert await generation.execute_dispatch(c.run.id)
    wire.assert_awaited_once()
    resume.assert_not_awaited()
    original_request.assert_awaited_once()
    async with c.factory() as session:
        run = await session.get(type(c.run), c.run.id)
        released = execution.recovery_state(run)
        assert released.execution.physical_state == "released"
        assert released.execution.control_fencing_epoch == first.fencing_epoch
        assert released.clock == waiting.clock and released.clock.interval_open
        assert released.next_check_at == waiting.next_check_at
        expected = {"version": 1, "attempt_id": str(refused)}
        assert run.agent_state[execution.PROVIDER_REFUSAL_REDRIVE_KEY] == expected
        attempts = (await session.scalars(select(GenerationProviderAttempt).where(
            GenerationProviderAttempt.run_id == c.run.id,
        ))).all()
        assert len(attempts) == 2 and all(attempt.phase == "closed" for attempt in attempts)
        status = await drain_status(session)
        assert status["drained"] is True and status["active"]["generations"] == 0

    due = waiting.next_check_at + timedelta(seconds=1)
    async with c.owned(), c.factory() as session:
        assert await execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=waiting.attempt_seq,
            worker_id="drain-fenced", now=due,
        ) is None
    async with c.factory() as session:
        await end_drain(session, release_sha)
        await session.commit()
        run = await session.get(type(c.run), c.run.id)
        assert execution.recovery_state(run) == released
        assert run.agent_state[execution.PROVIDER_REFUSAL_REDRIVE_KEY] == expected

    monkeypatch.setattr(recovery_store, "_baseline_sha", AsyncMock())
    async with c.owned(), c.factory() as session:
        claim = await execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=waiting.attempt_seq,
            worker_id="after-drain", now=due,
        )
        assert claim is not None
        run = await session.get(type(c.run), c.run.id)
        resumed = execution.recovery_state(run)
        assert resumed.clock.segment_used_ms == waiting.clock.window_ms
        assert resumed.clock.segment_deadline == due
        assert resumed.active_work_ms == waiting.active_work_ms
        assert resumed.execution.logical_step_seq == waiting.execution.logical_step_seq
        assert run.agent_state[execution.PROVIDER_REFUSAL_REDRIVE_KEY] == expected
        await session.commit()
    resume.assert_not_awaited()
