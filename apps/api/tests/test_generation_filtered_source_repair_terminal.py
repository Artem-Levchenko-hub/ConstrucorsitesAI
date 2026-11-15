"""A confirmed failure of the native repair settles before the worker exits."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_generation_recovery_deadlines import _state
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.services import agent_builder, agent_native, restorations
from yleum_api.services.generation import (
    agent_finalization,
    agent_pipeline,
    agent_runtime,
    recovery_execution,
    recovery_pipeline,
)
from yleum_api.services.generation.contracts import (
    AgentPromptPlan,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    StackPrompt,
)
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    ContinuationClaim,
    IncompleteResponseBudget,
    RecoveryBinding,
    RecoveryContractError,
)

_PAGE = "src/app/page.tsx"


@pytest.mark.parametrize(
    "settlement", ["saved", "write_failed", "lost_ack", "wait_failed", "cancelled"],
)
async def test_filtered_repair_needs_a_committed_terminal_result_before_cold_exit(
    monkeypatch, settlement,
):
    provider_result = agent_builder.AgentResult(
        done=False, summary="provider private diagnostic", files={_PAGE: "draft"}, steps=3,
        stop_reason="provider_error", continuation_reason=agent_builder.FILTERED_PROVIDER_OUTPUT,
    )
    model = AsyncMock(return_value=provider_result)
    monkeypatch.setattr(agent_native, "run_native_build", model)
    prepare_guard = AsyncMock(side_effect=lambda _runtime, _ids, execute, check: (execute, check))
    monkeypatch.setattr(agent_runtime, "guard_native_source_contract", prepare_guard)
    restore = AsyncMock()
    monkeypatch.setattr(agent_finalization, "_restore_project_cell_source", restore)
    monkeypatch.setattr(restorations, "adaptation_activation_holds_generation_lease",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(agent_finalization.repo_svc, "read_files", lambda *_: {_PAGE: "accepted"})
    state = _state()
    state = state.model_copy(update={"execution": state.execution.model_copy(update={
        "stage": "finalization", "substep": "response_wait", "pending_effect_id": uuid4(),
    })})
    driver = SimpleNamespace(
        state=state,
        context={"completed": {
            "finalization_progress": recovery_pipeline.FinalizationProgress(
                source_history=("a" * 64,), feedback=("compiler red",),
            ).model_dump(mode="json"),
        }, "active_frame": {}},
        soft_edit_deadline=lambda value: value,
    )
    retained = None

    async def save(execution, *, context):
        nonlocal retained
        updated = driver.state.model_copy(update={"execution": execution})
        if execution.substep == "checkpoint_ready":
            if settlement == "lost_ack":
                retained = SimpleNamespace(state=updated, context=context)
            if settlement in {"write_failed", "lost_ack", "wait_failed"}:
                raise ConnectionError("private write diagnostic")
            if settlement == "cancelled":
                raise asyncio.CancelledError
        driver.state = updated
        driver.context = context

    driver.save = save
    wait_error = (
        TimeoutError("dependency wait unavailable") if settlement == "wait_failed"
        else recovery_execution.GenerationYielded(
            run_id=state.identity.run_id,
            checkpoint=state.checkpoint,
            attempt_seq=state.attempt_seq,
            phase="waiting_dependency",
        )
    )
    driver.wait = AsyncMock(side_effect=wait_error)
    checkpoint = AsyncMock()
    operations = SimpleNamespace(
        continuation=driver, execute=AsyncMock(), emit=AsyncMock(), checkpoint=checkpoint,
    )

    async def finalization(**kwargs):
        await kwargs["repair"]("compiler red")
        pytest.fail("filtered repair must exit before checking or repairing again")

    coordinator = SimpleNamespace(
        finalize_with_repair=AsyncMock(side_effect=finalization),
        source_edit_deadline=AsyncMock(return_value=None),
        session_factory=object(),
    )
    runtime = GenerationRuntime(
        handle=SimpleNamespace(
            snapshot_files=AsyncMock(return_value={_PAGE: "draft"}), is_portable=lambda: True,
            prove_restoration_adaptation=None,
        ),
        coordinator=coordinator,
    )
    args = dict(
        _is_edit=True, _max_has_generated_snapshot=True, _max_shell_enabled=False,
        accumulated="", baseline=SourceBaseline(uuid4(), "accepted", {_PAGE: "accepted"}),
        files={_PAGE: "draft"}, ids=GenerationIds(*(uuid4() for _ in range(5))),
        is_free=False, prompt_text="Change the page", runtime=runtime,
        plan=SimpleNamespace(user="repair", model="claude-sonnet-5.5", stack_guide="",
                             skills=None, steps=3), operations=operations,
    )
    initial_error = {
        "saved": agent_finalization.NativeSourceRepairFiltered,
        "write_failed": recovery_execution.GenerationYielded,
        "lost_ack": recovery_execution.GenerationYielded,
        "wait_failed": TimeoutError,
        "cancelled": asyncio.CancelledError,
    }[settlement]
    with pytest.raises(initial_error) as first:
        await agent_finalization.finalize_max_candidate(**args)
    assert "private" not in str(first.value)
    if settlement in {"saved", "cancelled"}:
        driver.wait.assert_not_awaited()
    else:
        driver.wait.assert_awaited_once_with()
    if settlement != "saved":
        pending = recovery_pipeline.pending_source_repair(driver)
        assert pending is not None and pending.status == "active"
        assert driver.state.execution.substep == "response_wait"
        assert driver.state.execution.pending_effect_id is not None
        checkpoint.assert_not_awaited()
    if settlement not in {"saved", "lost_ack"}:
        restore.assert_not_awaited()
        model.assert_awaited_once()
        return

    cold = retained or SimpleNamespace(state=driver.state, context=driver.context)
    completed = recovery_pipeline.pending_source_repair(cold)
    assert completed is not None and completed.status == "complete"
    assert cold.state.execution.substep == "checkpoint_ready"
    assert cold.state.execution.pending_effect_id is None
    assert cold.context["active_frame"] is None
    operations.continuation = cold
    with pytest.raises(agent_finalization.NativeSourceRepairFiltered):
        await agent_finalization.finalize_max_candidate(**{**args, "files": {}})
    assert coordinator.finalize_with_repair.await_count == 1
    assert model.await_count == 1
    assert checkpoint.await_count == (2 if settlement == "saved" else 1)
    restore.assert_not_awaited()


@pytest.mark.parametrize("ownership", ["current", "cancel_requested", "advanced"])
async def test_cold_filtered_repair_keeps_terminal_message_owned(monkeypatch, ownership):
    state = _state()
    identity = state.identity
    ids = GenerationIds(identity.run_id, identity.project_id, identity.user_id, uuid4(), uuid4())
    ref = CheckpointRef(object_key="synthetic/closed-repair", sha256="b" * 64)
    claim = ContinuationClaim(
        run_id=ids.run_id, token=uuid4(), attempt_seq=state.attempt_seq,
        worker_id="unit-worker", expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    state = state.model_copy(update={
        "cursor": "finalization", "phase": "working", "next_check_at": None,
        "context_ref": ref, "continuation_claim": claim,
        "binding": RecoveryBinding(
            source_sha256="c" * 64, schema_sha256="d" * 64, environment_sha256="e" * 64,
            workspace_id=str(uuid4()), fencing_epoch=2,
        ),
        "checkpoint": CheckpointRef(object_key="synthetic/source", sha256="f" * 64),
        "execution": state.execution.model_copy(update={
            "stage": "finalization", "substep": "checkpoint_ready", "physical_state": "active",
            "execution_context_ref": ref,
        }),
    })
    prompt = "Change the page"
    baseline = SourceBaseline(uuid4(), "accepted", {_PAGE: "accepted"})
    budget = IncompleteResponseBudget()
    filtered = agent_builder.AgentResult(
        done=False, summary="private diagnostic", files={_PAGE: "draft"}, steps=1,
        stop_reason="provider_error", continuation_reason=agent_builder.FILTERED_PROVIDER_OUTPUT,
    )
    entry = recovery_pipeline.PreparedAgentEntry(
        (prompt, False, True, True), None, StackPrompt("", None, None, None, "", False),
        AgentPromptPlan("", prompt, "claude-sonnet-5.5", None, 3, "", None, "", None, False),
        {}, None, None,
    )
    turn = recovery_pipeline.CompletedAgentTurn(
        agent_builder.AgentResult(done=True, summary="draft", files={_PAGE: "draft"}, steps=2),
        None, budget,
    )
    repair = recovery_pipeline.SourceRepairEntry(
        status="complete", task="compiler red", baseline=baseline.files, targeted=False,
        edit_deadline=None,
        result=recovery_pipeline._result_data(filtered), response_budget=budget,
    )
    context = {"completed": {
        "agent_entry": entry.to_json(), "agent_turn": turn.to_json(),
        "source_repair": repair.model_dump(mode="json"),
    }, "active_frame": None}

    db_state = state if ownership != "advanced" else state.model_copy(update={
        "state_revision": state.state_revision + 1,
        "continuation_claim": claim.model_copy(update={"token": uuid4()}),
    })
    run = SimpleNamespace(
        id=ids.run_id, project_id=ids.project_id, user_id=ids.user_id,
        user_message_id=ids.user_message_id, assistant_message_id=ids.assistant_message_id,
        prompt_hash=identity.prompt_hash,
        status="cancel_requested" if ownership == "cancel_requested" else "running",
        agent_state={"recovery_v1": db_state.model_dump(mode="json")},
    )
    message = SimpleNamespace(
        project_id=ids.project_id, role="assistant", snapshot_id=None, content="streaming draft",
        tokens_in=11, tokens_out=None,
    )
    rows = {Project: SimpleNamespace(owner_id=ids.user_id), GenerationRun: run, Message: message}
    committed = []

    async def commit():
        committed.append((run.agent_state["product_outcome"], message.content, message.snapshot_id))

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(
            get=AsyncMock(side_effect=lambda model, *_args, **_kwargs: rows[model]),
            scalar=AsyncMock(return_value=run), commit=commit,
        )

    owner = AsyncMock()
    monkeypatch.setattr(recovery_execution, "_require_session_owner", owner)
    monkeypatch.setattr(
        recovery_execution, "load_execution_context", AsyncMock(return_value=context),
    )
    monkeypatch.setattr(recovery_pipeline, "attach_driver", lambda *_: SimpleNamespace(
        state=state, context=context,
    ))
    rebind = AsyncMock()
    monkeypatch.setattr(recovery_pipeline, "rebind_continuation", rebind)
    bindings = SimpleNamespace(execute=AsyncMock(), probe_build=AsyncMock(),
                               probe_runtime=AsyncMock(), preview_url=AsyncMock())
    runtime = GenerationRuntime(handle=SimpleNamespace(
        snapshot_files=AsyncMock(return_value={_PAGE: "draft"}),
    ))
    monkeypatch.setattr(
        agent_pipeline, "prepare_agent_runtime", AsyncMock(return_value=(bindings, None)),
    )
    save = AsyncMock()
    event = AsyncMock()
    monkeypatch.setattr(agent_pipeline, "persist_checkpoint", save)
    monkeypatch.setattr(agent_pipeline, "publish_event", event)
    for hook in ("require_named_behavior_readiness", "_seed_saved_max_config",
                 "finalize_max_candidate", "publish_agent_candidate"):
        monkeypatch.setattr(agent_pipeline, hook, AsyncMock(side_effect=AssertionError(hook)))

    args = dict(
        _consume_free_generation=AsyncMock(), baseline=baseline, capacity_dispatch_token=None,
        factory=factory, force_model=None, ids=ids, is_free=False, model_id="claude-sonnet-5.5",
        orchestrate=True, progress=SimpleNamespace(emit_agent_event=AsyncMock()),
        project_info=ProjectGenerationFacts("max_miniapp", "unit", "Unit", None, None, False,
                                            "ru", False, "", ""),
        prompt_text=prompt, runtime=runtime, selected_elements=None, _resume_state=state,
    )
    if ownership == "advanced":
        with pytest.raises(RecoveryContractError, match="claim changed"):
            await agent_pipeline.run_agent_generation(**args)
    else:
        await agent_pipeline.run_agent_generation(**args)
        if ownership == "current":
            # Replay after the outcome/message commit and before tracked cleanup.
            await agent_pipeline.run_agent_generation(**args)

    if ownership != "current":
        assert not committed and message.content == "streaming draft"
        event.assert_not_awaited()
        owner.assert_not_awaited()
        return
    safe = "Провайдер отфильтровал ответ при исправлении. Новая версия не опубликована."
    assert committed == [({"status": "failed", "error": safe}, safe, None)] * 2
    assert message.tokens_in == 11 and message.tokens_out == 0
    assert run.status == "running"  # The tracker owns terminal settlement after lease cleanup.
    assert run.agent_state["recovery_v1"] == db_state.model_dump(mode="json")
    assert rebind.await_count == save.await_count == owner.await_count == event.await_count == 2
    event.assert_awaited_with(ids.project_id, "llm.error", {
        "message_id": str(ids.assistant_message_id), "error": safe,
    })
