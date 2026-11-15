"""Recovery prepare retries preserve the last ordinary MAX cursor."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import text

from tests.test_generation_recovery_deadlines import _clock
from tests.test_generation_recovery_deadlines import clock_owner as clock_owner
from tests.test_generation_recovery_deadlines import response_schema as response_schema
from tests.test_generation_recovery_quiescence import release_capture, released_wait
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services import generation_deployment_drain as drain
from yleum_api.services.generation import parked_source_recovery, recovery_store, supervisor
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    RecoveryBinding,
    RecoveryContractError,
)
from yleum_api.services.generation.recovery_reconciliation import (
    ProviderDispatchPlan,
    ProviderWireReservation,
    provider_root_effect_id,
)
from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
from yleum_api.workers import generation


async def _saved_release(c):
    workspace = ProjectCellWorkspace(
        project_id=c.run.project_id,
        owner_id=c.run.user_id,
        provider="synthetic",
        state="ready",
        fencing_epoch=4,
    )
    c.session.add(workspace)
    await c.session.flush()
    now = datetime.now(UTC)
    receipt = ProjectCellResourceResponse(
        workspace_id=workspace.id,
        state="resources_ready",
        provider_ref="owned",
        fencing_epoch=4,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )
    settled = ProjectCellOperation(
        id=uuid4(),
        workspace_id=workspace.id,
        generation_run_id=c.run.id,
        kind="release",
        status="completed",
        fencing_epoch=4,
        finished_at=now,
        idempotency_key=str(uuid4()),
        request_digest="a" * 64,
        result_payload=receipt.to_wire_json(),
    )
    c.session.add(settled)
    ref = CheckpointRef(object_key="ready-context", sha256="d" * 64)
    old = c.driver.state
    return old.model_copy(
        update={
            "cursor": "finalization",
            "phase": "waiting_dependency",
            "next_check_at": now,
            "clock": old.clock.model_copy(update={"interval_open": False, "segment_used_ms": 100}),
            "binding": RecoveryBinding(
                workspace_id=str(workspace.id),
                fencing_epoch=3,
                source_sha256="a" * 64,
                schema_sha256="b" * 64,
                environment_sha256="c" * 64,
            ),
            "context_ref": ref,
            "checkpoint": ref,
            "execution": old.execution.model_copy(
                update={
                    "stage": "finalization",
                    "substep": "checkpoint_ready",
                    "logical_step_seq": 2,
                    "physical_state": "released",
                    "control_workspace_id": workspace.id,
                    "control_fencing_epoch": 4,
                    "release_operation_id": settled.id,
                    "execution_context_ref": ref,
                }
            ),
        }
    )


_RATE_HISTORY = {"rate_limit_history", "rate_limit_unproved"}
_TRANSCRIPT_HISTORY = {"transcript_history", "transcript_receiver"}
_SETTLED_HISTORY = {"settled_history", "rate_limit_history", "transcript_history"}


async def _record_provider_history(c, original, edge):
    run = c.run
    scope = dict(run_id=run.id, project_id=run.project_id, user_id=run.user_id)
    step = original.execution.logical_step_seq
    settled = datetime.now(UTC) - timedelta(seconds=5)
    attempt_seq = original.attempt_seq + (edge == "current_history")
    await c.session.execute(
        text(
            "INSERT INTO generation_response_budgets (bucket,storage_epoch,quota_bytes,"
            "observation_nonce) VALUES ('history',:epoch,4096,:nonce)"
        ),
        {"epoch": uuid4(), "nonce": uuid4()},
    )

    async def append(logical_step, reason, *, parent=None):
        root = provider_root_effect_id(run.id, logical_step)
        parent_id, parent_effect = parent if parent else (None, None)
        attempt_id = uuid4()
        effect_id = uuid5(parent_effect, "definitive-refusal-child-v1") if parent_effect else root
        wire_attempt = int(parent is not None)
        call_id = None if reason == "native_transcript_rejected" else uuid4()
        manifest = f"private/history/{attempt_id}" if call_id else None
        request_sha = "b" * 64 if call_id is None else "e" * 64
        response_sha = "f" * 64 if call_id else None
        if call_id is not None:
            c.session.add(
                ProviderCall(
                    **scope,
                    id=call_id,
                    route="/v1/messages",
                    provider_scope="llmgw",
                    requested_model="synthetic",
                    stage="native_agent",
                    free=False,
                    expense_owner="user",
                    status="completed" if reason == "completed" else "failed",
                    receipt_hash=(
                        None
                        if reason == "rate_limit_refused" and edge == "rate_limit_unproved"
                        else "d" * 64
                    ),
                    created_at=settled,
                    finished_at=settled,
                )
            )
            await c.session.flush()
        c.session.add(
            GenerationProviderAttempt(
                **scope,
                attempt_id=attempt_id,
                dispatch_token=uuid4(),
                prompt_hash=run.prompt_hash,
                request_sha256=request_sha,
                provider_call_id=call_id,
                parent_attempt_id=parent_id,
                phase="closed",
                terminal_reason=reason,
                response_manifest_key=manifest,
                response_sha256=response_sha,
                created_at=settled,
                updated_at=settled,
            )
        )
        await c.session.flush()
        if call_id or edge == "transcript_receiver":
            await c.session.execute(
                text(
                    "INSERT INTO generation_responses (attempt_id,bucket,writer_token,state,"
                    "allocated_bytes,manifest_key,manifest_sha256,response_sha256,"
                    "reconciliation_hold) "
                    "VALUES (:attempt,'history',:writer,'complete',512,:manifest,:sha,:body,:held)"
                ),
                {
                    "attempt": attempt_id,
                    "writer": uuid4(),
                    "manifest": manifest or "private/unexpected-receiver",
                    "sha": "a" * 64,
                    "body": "c" * 64
                    if edge == "mismatched_retained_body"
                    else response_sha or "f" * 64,
                    "held": edge == "held_history",
                },
            )
        plan = ProviderDispatchPlan(
            kind="native",
            deadline=settled + timedelta(minutes=1),
            outer_attempt=0,
            outer_allowance=1,
            wire_allowance=2 if edge in _RATE_HISTORY else 1,
        )
        wire = ProviderWireReservation(
            **plan.model_dump(),
            root_effect_id=root,
            provider_effect_id=effect_id,
            attempt_id=uuid4() if edge == "orphan_wire" else attempt_id,
            parent_attempt_id=parent_id,
            request_sha256=request_sha,
            wire_attempt=wire_attempt,
        )
        common = dict(
            **scope,
            logical_step_seq=logical_step,
            input_digest=request_sha,
            attempt_seq=attempt_seq,
            claim_token=original.continuation_claim.token,
        )
        c.session.add_all(
            [
                GenerationRecoveryEffect(
                    **common,
                    effect_id=effect_id,
                    effect_ordinal=wire_attempt * 2,
                    kind="provider",
                    provider_attempt_id=attempt_id,
                    parent_effect_id=parent_effect,
                    state="unknown" if edge == "unknown_history" else "prepared",
                    pre_binding={
                        "source": original.binding.model_dump(mode="json"),
                        "dispatch_v1": plan.model_dump(mode="json"),
                    },
                ),
                GenerationRecoveryEffect(
                    **common,
                    effect_id=uuid5(root, f"wire-v1:0:{wire_attempt}"),
                    effect_ordinal=wire_attempt * 2 + 1,
                    kind="tool" if edge == "old_tool" else "progress",
                    parent_effect_id=effect_id,
                    state="inflight",
                    pre_binding={"dispatch_v1": wire.model_dump(mode="json")},
                ),
            ]
        )
        return attempt_id, effect_id

    parent = await append(step, "rate_limit_refused") if edge in _RATE_HISTORY else None
    if edge in _TRANSCRIPT_HISTORY:
        await append(step - 1, "native_transcript_rejected")
    await append(step, "completed", parent=parent)
    await c.session.commit()


async def _failed_resume(c, monkeypatch, edge):
    original = await _saved_release(c)
    if (
        edge
        in {
            "settled_history",
            "orphan_wire",
            "mismatched_retained_body",
            "held_history",
            "unknown_history",
            "current_history",
            "old_tool",
        }
        | _RATE_HISTORY
        | _TRANSCRIPT_HISTORY
    ):
        await _record_provider_history(c, original, edge)
    run_id = c.run.id
    c.run.execution_backend = "worker"
    c.run.execution_started_at = datetime.now(UTC)
    c.run.agent_state = {
        **c.run.agent_state,
        execution.RECOVERY_KEY: original.model_dump(mode="json"),
    }
    await c.session.commit()

    monkeypatch.setattr(generation, "get_engine", lambda: c.engine)
    monkeypatch.setattr(execution, "get_engine", lambda: c.engine)
    monkeypatch.setattr(recovery_store, "_baseline_sha", AsyncMock())
    finalize = AsyncMock()
    cancel = AsyncMock()
    monkeypatch.setattr(supervisor, "finalize_generation_run", finalize)
    monkeypatch.setattr(supervisor, "_finalize_cancelled_generation", cancel)

    async def wait_for_cancel(_):
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_for_cancel)
    calls = []

    async def resume(claim):
        calls.append(claim)
        if len(calls) > 1:
            return
        async with c.factory() as session:
            run = await session.get(type(c.run), run_id)
            state = execution.recovery_state(run)
            assert state.continuation_claim == claim and state.phase == "working"
            if edge == "advanced":
                bumped = state.model_copy(update={"state_revision": state.state_revision + 1})
                run.agent_state = {
                    **run.agent_state,
                    execution.RECOVERY_KEY: bumped.model_dump(mode="json"),
                }
            elif edge == "unknown":
                session.add(
                    GenerationProviderAttempt(
                        run_id=run_id,
                        project_id=run.project_id,
                        user_id=run.user_id,
                        prompt_hash=run.prompt_hash,
                        request_sha256="e" * 64,
                        attempt_id=uuid4(),
                        dispatch_token=uuid4(),
                        phase="reserved",
                    )
                )
            elif edge == "cancelled":
                run.status = "cancel_requested"
            await session.commit()
        raise TimeoutError("Project Cell capacity queue deadline exceeded")

    monkeypatch.setattr(execution, "resume_generation", resume)
    assert await generation.execute_dispatch(run_id)
    finalize.assert_not_awaited()
    await c.session.refresh(c.run)
    after = execution.recovery_state(c.run)
    return original, after, calls, finalize, cancel


@pytest.mark.parametrize(
    "edge",
    [
        "idle",
        "advanced",
        "unknown",
        "cancelled",
        "settled_history",
        "orphan_wire",
        "mismatched_retained_body",
        "held_history",
        "unknown_history",
        "current_history",
        "old_tool",
        "rate_limit_history",
        "rate_limit_unproved",
        "transcript_history",
        "transcript_receiver",
    ],
)
async def test_failed_prepare_defers_only_the_unchanged_owned_resume(
    clock_owner,
    response_schema,
    monkeypatch,
    edge,
):
    c = clock_owner
    original, after, calls, finalize, cancel = await _failed_resume(c, monkeypatch, edge)
    run_id = c.run.id
    assert after.execution.physical_state == "released"
    assert after.context_ref == original.context_ref
    assert after.execution.budget == original.execution.budget
    if edge not in _SETTLED_HISTORY | {"idle"}:
        assert after.phase == "working"
        assert after.next_check_at is None
        assert after.clock.interval_open
        assert cancel.await_count == (edge == "cancelled")
        return

    assert c.run.status == "running"
    assert after.phase == "waiting_dependency"
    assert after.next_check_at > datetime.now(UTC)
    assert not after.clock.interval_open
    assert 100 <= after.clock.segment_used_ms < after.clock.window_ms
    assert not await generation.execute_dispatch(run_id)
    assert len(calls) == 1

    class DueClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return after.next_check_at + timedelta(seconds=1)

    monkeypatch.setattr(generation, "datetime", DueClock)
    assert await generation.execute_dispatch(run_id)
    assert len(calls) == 2 and calls[-1].run_id == run_id
    assert calls[-1].attempt_seq == calls[0].attempt_seq + 1
    finalize.assert_awaited_once_with(run_id)


@pytest.mark.parametrize(
    "edge",
    [
        "valid",
        "missing_marker",
        "different_claim",
        "open_clock",
        "new_writer",
        "new_provider",
        "scoped_drain",
    ],
)
async def test_prepare_backoff_drain_requires_the_exact_stopped_wait(
    clock_owner,
    response_schema,
    monkeypatch,
    edge,
):
    c = clock_owner
    _, after, calls, _, _ = await _failed_resume(c, monkeypatch, "settled_history")
    run_id = c.run.id
    assert after.next_check_at > datetime.now(UTC)
    assert len(calls) == 1
    key = "recovery_prepare_backoff_v1"
    if edge == "missing_marker":
        c.run.agent_state = {
            key_: value for key_, value in c.run.agent_state.items() if key_ != key
        }
    elif edge == "open_clock":
        changed = after.model_copy(
            update={
                "clock": after.clock.model_copy(update={"interval_open": True}),
            }
        )
        c.run.agent_state = {
            **c.run.agent_state,
            execution.RECOVERY_KEY: changed.model_dump(mode="json"),
        }
    elif edge == "different_claim":
        changed = after.model_copy(
            update={
                "continuation_claim": after.continuation_claim.model_copy(
                    update={"token": uuid4()}
                ),
            }
        )
        c.run.agent_state = {
            **c.run.agent_state,
            execution.RECOVERY_KEY: changed.model_dump(mode="json"),
        }
    elif edge == "new_writer":
        c.session.add(
            ProjectCellOperation(
                workspace_id=after.execution.control_workspace_id,
                generation_run_id=run_id,
                kind="ensure",
                status="completed",
                fencing_epoch=4,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                idempotency_key=str(uuid4()),
                request_digest="f" * 64,
            )
        )
    elif edge == "new_provider":
        c.session.add(
            GenerationProviderAttempt(
                run_id=run_id,
                project_id=c.run.project_id,
                user_id=c.run.user_id,
                prompt_hash=c.run.prompt_hash,
                request_sha256="e" * 64,
                attempt_id=uuid4(),
                dispatch_token=uuid4(),
                phase="reserved",
            )
        )

    selected_id = None
    if edge == "scoped_drain":
        selected, workspace, _ = await released_wait(c.session)
        raw = selected.agent_state[execution.RECOVERY_KEY]
        selected.agent_state = {
            **selected.agent_state,
            execution.RECOVERY_KEY: {
                **raw,
                "version": 3,
                "phase": "needs_input",
                "next_check_at": None,
                "binding": {**raw["binding"], "fencing_epoch": workspace.fencing_epoch - 1},
                "clock": {**_clock(datetime.now(UTC)), "interval_open": False},
            },
        }
        selected_id = selected.id
        # Selected parked-source proof is separate from the real prepare/SQL proof.
        monkeypatch.setattr(
            parked_source_recovery,
            "parked_source_repair",
            AsyncMock(
                side_effect=lambda _session, run, _state, *, claimed_preparation: (
                    run.id == selected_id and not claimed_preparation
                ),
            ),
        )

    await c.session.commit()
    capture = drain.ReleaseCaptureContext(**release_capture())
    await drain.begin_drain(c.session, capture.release_sha)
    await c.session.commit()
    want_drained = edge in {"valid", "scoped_drain"}
    status = await drain.drain_status(c.session)
    assert status["drained"] is want_drained
    assert status["active"]["generations"] == (0 if want_drained else 1)
    async with c.factory() as checking:
        result = await drain._resume_classification(
            checking, capture, parked_source_run=selected_id
        )
        assert result.drained is want_drained
    assert after.next_check_at > datetime.now(UTC)

    if not want_drained:
        async with c.factory() as ending:
            with pytest.raises(RecoveryContractError, match="physically quiescent"):
                await drain.end_drain(ending, capture.release_sha)
        return

    await drain.end_drain(c.session, capture.release_sha, parked_source_run=selected_id)
    await c.session.commit()
    await c.session.refresh(c.run)
    assert execution.recovery_state(c.run) == after
    assert key in c.run.agent_state
