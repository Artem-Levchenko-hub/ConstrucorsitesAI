"""Real lifecycle/worker ownership regressions; no provider or customer effects."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_worker import _queued_dispatch
from yleum_api.core import db
from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation import lifecycle, recovery_execution, supervisor
from yleum_api.services.generation.recovery_execution import GenerationYielded
from yleum_api.services.generation_runs import prompt_hash
from yleum_api.workers import generation


def test_completed_turn_keeps_native_payload_without_restoring_proof_authority():
    from yleum_api.services.agent_builder import AgentResult
    from yleum_api.services.agent_native import NativeProofCheckpoint
    from yleum_api.services.generation.recovery_contract import (
        IncompleteResponseBudget,
        RecoveryContractError,
    )
    from yleum_api.services.generation.recovery_pipeline import CompletedAgentTurn

    transcript = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "write_file",
                    "input": {"path": "src/я.ts", "content": "несокращённый текст"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "сохранено"},
            ],
        },
    ]
    result = AgentResult(
        False,
        "готово к проверке",
        {"src/я.ts": "текст"},
        2,
        transcript=transcript,
        needs_finalization=True,
        proof_checkpoint=NativeProofCheckpoint(fast_check_green=True),
    )
    completed = CompletedAgentTurn(result, None, IncompleteResponseBudget())
    raw = completed.to_json()
    assert raw["result"]["proof_checkpoint"] is None
    restored = CompletedAgentTurn.from_json(raw)
    assert restored.result.transcript == transcript
    assert restored.result.files == result.files
    assert restored.result.proof_checkpoint is None
    assert result.proof_checkpoint.fast_check_green is True
    raw["result"]["proof_checkpoint"] = {"fast_check_green": True}
    with pytest.raises(RecoveryContractError):
        CompletedAgentTurn.from_json(raw)


async def test_finalization_repair_provider_does_not_reenter_main_agent_cursor(monkeypatch):
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ContinuationClaim,
        RecoveryBinding,
        decode_recovery,
    )

    run = SimpleNamespace(id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64)
    initial = decode_recovery(_bootstrap_wait(run))
    ref = CheckpointRef(object_key="owned/context", sha256="b" * 64)
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=0,
        worker_id="owned",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    state = initial.model_copy(
        update={
            "cursor": "finalization",
            "phase": "working",
            "continuation_claim": claim,
            "checkpoint": ref,
            "context_ref": ref,
            "binding": RecoveryBinding(
                workspace_id=str(uuid4()),
                fencing_epoch=1,
                source_sha256="a" * 64,
                schema_sha256="b" * 64,
                environment_sha256="c" * 64,
            ),
            "execution": initial.execution.model_copy(
                update={
                    "stage": "finalization",
                    "substep": "checkpoint_ready",
                    "physical_state": "active",
                    "execution_context_ref": ref,
                }
            ),
        }
    )
    driver = recovery_execution.ExecutionDriver(None, state, {}, AsyncMock())
    saved = []

    async def save(execution, *, context, **kwargs):
        from yleum_api.services.generation.recovery_contract import ExecutionCursor

        saved.append(ExecutionCursor.model_validate_json(execution.model_dump_json()))
        driver.state = driver.state.model_copy(update={"execution": execution})
        driver.context = context

    monkeypatch.setattr(driver, "save", save)
    result = await driver.provider(
        {"version": 1, "kind": "native", "value": {}}, AsyncMock(return_value={"content": []})
    )
    assert result == {"content": []}
    assert len(saved) == 1
    assert saved[0].stage == "finalization"
    assert saved[0].substep == "response_wait"
    assert saved[0].budget.steps == 1


def _bootstrap_wait(run):
    return {
        "version": 2,
        "identity": {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
            "user_id": str(run.user_id),
            "prompt_hash": run.prompt_hash,
            "accepted_snapshot_id": None,
            "supersedes_run_id": None,
        },
        "phase": "waiting_dependency",
        "next_check_at": datetime.now(UTC).isoformat(),
        "execution": {
            "schema_version": 1,
            "stage": "bootstrap",
            "substep": "runtime_prepare",
            "logical_step_seq": 0,
            "next_tool_index": 0,
            "lease_cycle_seq": 0,
            "physical_state": "no_effects",
        },
    }


@pytest.mark.parametrize("new_writers_enabled_on_restart", [True, False])
@pytest.mark.parametrize("commit_ack_lost", [False, True, "working"])
async def test_committed_wait_exits_real_lifecycle_tracker_without_terminalizing(
    db_session,
    test_engine,
    monkeypatch,
    new_writers_enabled_on_restart,
    commit_ack_lost,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.prompt_hash = prompt_hash("Собери приложение")
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    for module in (db, generation, supervisor, lifecycle, recovery_execution):
        monkeypatch.setattr(module, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        lifecycle,
        "get_settings",
        lambda: SimpleNamespace(
            use_project_memory=False, use_agentic_builder=True, agentic_builder_canary_users=""
        ),
    )
    monkeypatch.setattr(lifecycle.agent_builder, "is_agentic_enabled", lambda *args: True)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(lifecycle, "publish_event", AsyncMock())
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    monkeypatch.setattr(supervisor, "clear_generation_cancel", AsyncMock())
    counts = {"original_pipeline": 0, "planner": 0, "effect": 0}

    async def no_signal(*args):
        await asyncio.Future()

    for name in ("_wait_for_generation_cancel", "_wait_for_capacity_dispatch_lease_loss"):
        monkeypatch.setattr(supervisor, name, no_signal)

    async def waiting_pipeline(**kwargs):
        counts["original_pipeline"] += 1
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            current.status = "running"
            retained = _bootstrap_wait(current)
            if commit_ack_lost == "working":
                retained.update(phase="working", next_check_at=None)
            current.agent_state = {**current.agent_state, "recovery_v1": retained}
            await session.commit()
        if commit_ack_lost:
            raise OSError("synthetic SQL commit acknowledgement lost")
        raise GenerationYielded(
            run_id=run.id, checkpoint=None, attempt_seq=0, phase="waiting_dependency"
        )

    monkeypatch.setattr(lifecycle, "run_agent_generation", waiting_pipeline)
    assert await asyncio.wait_for(generation.execute_dispatch(run.id), 5)
    await db_session.refresh(run)
    original_started_at = run.execution_started_at
    assert run.status == "running", "committed wait was terminalized by lifecycle/tracker"
    assert run.finished_at is None and original_started_at is not None
    assert counts == {"original_pipeline": 1, "planner": 0, "effect": 0}
    lifecycle.publish_event.assert_not_awaited()

    entered, release = asyncio.Event(), asyncio.Event()
    claims = []

    async def resume(claim):
        claims.append(claim)
        entered.set()
        await release.wait()
        counts["planner"] += 1
        counts["effect"] += 1
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            state = recovery_execution.recovery_state(current)
            state = state.model_copy(update={"phase": "waiting_dependency"})
            current.agent_state = {
                **current.agent_state,
                "recovery_v1": state.model_dump(mode="json"),
            }
            await session.commit()
        raise GenerationYielded(
            run_id=run.id,
            checkpoint=None,
            attempt_seq=claim.attempt_seq,
            phase="waiting_dependency",
        )

    monkeypatch.setattr(recovery_execution, "resume_generation", resume, raising=False)
    monkeypatch.setattr(
        recovery_execution,
        "get_settings",
        lambda: SimpleNamespace(
            generation_recovery_execution_enabled=new_writers_enabled_on_restart,
            generation_recovery_mode="on",
            generation_recovery_canary_project_ids="",
        ),
        raising=False,
    )
    first = asyncio.create_task(generation.execute_dispatch(run.id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert await generation.execute_dispatch(run.id) is False
        release.set()
        assert await asyncio.wait_for(first, 5)
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    await db_session.refresh(run)
    assert run.status == "running" and run.finished_at is None
    assert run.execution_started_at == original_started_at
    assert counts == {"original_pipeline": 1, "planner": 1, "effect": 1}
    assert len(claims) == 1 and claims[0].attempt_seq == 1


async def test_unknown_recovery_shape_is_preserved_not_failed_as_orphan(
    db_session,
    test_engine,
    monkeypatch,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = datetime.now(UTC)
    run.status = "running"
    raw = {"version": 999, "unknown_future_evidence": "preserve exactly"}
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    assert await generation.execute_dispatch(run.id) is False
    await db_session.refresh(run)
    assert run.status == "running" and run.finished_at is None
    assert run.agent_state["recovery_v1"] == raw


@pytest.mark.parametrize("namespace", ["v2", "present_null"])
async def test_executable_run_cannot_mint_unlinked_provider_intent(
    db_session,
    test_engine,
    monkeypatch,
    namespace,
):
    from sqlalchemy import func, select

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.services.generation import provider_attempts

    run = await _queued_dispatch(db_session)
    run.status = "running"
    run.agent_state = {
        **run.agent_state,
        "recovery_v1": (_bootstrap_wait(run) if namespace == "v2" else None),
    }
    await db_session.commit()
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=True,
            generation_response_retention_enabled=True,
        ),
    )
    body = {
        "model": "synthetic",
        "messages": [],
        "metadata": {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
        },
    }
    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
        await provider_attempts.prepare_generation_attempt(body, "/v1/chat/completions")
    assert await db_session.scalar(select(func.count()).select_from(GenerationProviderAttempt)) == 0


async def _provider_context(db_session, test_engine, monkeypatch):
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_contract import ContinuationClaim
    from yleum_api.services.generation.recovery_reconciliation import (
        ProviderEffectCursor,
    )

    run = await _queued_dispatch(db_session)
    run.status = "running"
    claim = ContinuationClaim(
        run_id=run.id,
        token=uuid4(),
        attempt_seq=1,
        worker_id="synthetic",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    cursor = ProviderEffectCursor(claim, 1, 0)
    raw = _bootstrap_wait(run)
    raw.update(
        phase="working",
        next_check_at=None,
        attempt_seq=1,
        state_revision=1,
        continuation_claim=claim.model_dump(mode="json"),
    )
    raw["execution"].update(physical_state="active", pending_effect_id=str(cursor.root_effect_id))
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: test_engine)
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=True,
            generation_response_retention_enabled=True,
        ),
    )
    monkeypatch.setattr(
        recovery_execution,
        "get_settings",
        lambda: SimpleNamespace(
            generation_recovery_execution_enabled=True,
            generation_recovery_mode="on",
            generation_recovery_canary_project_ids="",
        ),
    )
    body = {
        "model": "synthetic",
        "messages": [],
        "metadata": {
            "run_id": str(run.id),
            "project_id": str(run.project_id),
        },
    }
    return run, cursor, body


@pytest.mark.parametrize("resume_existing", [False, True])
async def test_pipeline_commits_bootstrap_context_before_first_runtime_effect(
    db_session,
    test_engine,
    monkeypatch,
    resume_existing,
):
    import hashlib

    from sqlalchemy import text

    from yleum_api.services.generation import agent_pipeline, source_checkpoint
    from yleum_api.services.generation.contracts import (
        GenerationIds,
        GenerationRuntime,
        ProjectGenerationFacts,
        SourceBaseline,
    )
    from yleum_api.services.generation_runs import load_generation_dispatch

    run = await _queued_dispatch(db_session)
    run.status = "running"
    run.prompt_hash = prompt_hash("Собери приложение")
    await db_session.commit()
    dispatch = load_generation_dispatch(run)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    settings = SimpleNamespace(
        generation_recovery_execution_enabled=True,
        generation_recovery_mode="on",
        generation_recovery_canary_project_ids="",
        generation_provider_attempts_enabled=True,
        generation_response_retention_enabled=True,
        use_design_intelligence_plugin=False,
    )
    monkeypatch.setattr(recovery_execution, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_pipeline, "get_engine", lambda: test_engine, raising=False)
    monkeypatch.setattr(recovery_execution, "get_engine", lambda: test_engine)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(
        agent_pipeline,
        "classify_agent_turn",
        AsyncMock(return_value=(dispatch.prompt_text, False, False, False)),
    )
    objects = {}

    async def store(project, run_id, data):
        sha = hashlib.sha256(data).hexdigest()
        key = f"{project}/{run_id}/{sha}"
        objects[key] = data
        return key, sha

    async def read(project, run_id, *, object_key, digest):
        value = objects.get(object_key)
        return value if value is not None and hashlib.sha256(value).hexdigest() == digest else None

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", read)

    class ObservedBoundary(Exception):
        pass

    async def runtime_effect(**kwargs):
        async with factory() as check:
            current = await check.get(GenerationRun, run.id)
            state = recovery_execution.recovery_state(current)
            assert state is not None, "physical runtime entered before durable bootstrap context"
            assert state.version == 2 and state.continuation_claim is not None
            assert state.execution.physical_state == "no_effects"
            context = await recovery_execution.load_execution_context(
                state.identity,
                state.execution.execution_context_ref,
            )
            assert context["pipeline"]["prompt_text"] == dispatch.prompt_text
            assert context["pipeline"]["baseline"]["files"] == {"src/Привет.ts": "original"}
        raise ObservedBoundary

    monkeypatch.setattr(agent_pipeline, "prepare_agent_runtime", runtime_effect)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with pytest.raises(ObservedBoundary):
                await agent_pipeline.run_agent_generation(
                    _consume_free_generation=AsyncMock(),
                    baseline=SourceBaseline(None, None, {"src/Привет.ts": "original"}),
                    capacity_dispatch_token=None,
                    factory=factory,
                    force_model=None,
                    ids=GenerationIds(
                        run.id,
                        run.project_id,
                        run.user_id,
                        run.user_message_id,
                        run.assistant_message_id,
                    ),
                    is_free=False,
                    model_id=dispatch.model_id,
                    orchestrate=True,
                    progress=SimpleNamespace(emit_agent_event=AsyncMock()),
                    project_info=ProjectGenerationFacts(
                        "blank", "synthetic", "Synthetic", None, None, False, "ru", False, "", ""
                    ),
                    prompt_text=dispatch.prompt_text,
                    runtime=GenerationRuntime(),
                    selected_elements=[],
                )
            if resume_existing:
                async with factory() as session:
                    current = await session.get(GenerationRun, run.id)
                    raw = dict(current.agent_state["recovery_v1"])
                    raw.update(
                        phase="waiting_dependency", next_check_at=datetime.now(UTC).isoformat()
                    )
                    current.agent_state = {**current.agent_state, "recovery_v1": raw}
                    await session.commit()
                    claim = await recovery_execution.claim_continuation(
                        session,
                        run_id=run.id,
                        expected_attempt=0,
                        worker_id="cold",
                        now=datetime.now(UTC),
                    )
                    await session.commit()
                assert claim is not None
                settings.generation_recovery_execution_enabled = False
                with pytest.raises(ObservedBoundary):
                    await recovery_execution.resume_generation(claim)
                agent_pipeline.classify_agent_turn.assert_awaited_once()
        finally:
            recovery_execution._SESSION_OWNER.reset(token)


@pytest.mark.parametrize("saved", [False, True])
async def test_read_only_provider_cursor_never_creates_an_intent(
    db_session,
    test_engine,
    monkeypatch,
    saved,
):
    from dataclasses import replace

    from sqlalchemy import func, select, text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            identity = None
            if saved:
                with provider_effect_scope(cursor):
                    identity = await provider_attempts.prepare_generation_attempt(
                        body, "/v1/chat/completions"
                    )
            with provider_effect_scope(replace(cursor, reconcile_only=True)):
                if saved:
                    assert (
                        await provider_attempts.prepare_generation_attempt(
                            body, "/v1/chat/completions"
                        )
                        == identity
                    )
                else:
                    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
                        await provider_attempts.prepare_generation_attempt(
                            body, "/v1/chat/completions"
                        )
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
    assert await db_session.scalar(
        select(func.count())
        .select_from(GenerationProviderAttempt)
        .where(
            GenerationProviderAttempt.run_id == run.id,
        )
    ) == int(saved)


@pytest.mark.parametrize(
    "commit_outcome", ["confirmed", "unknown_after_commit", "failed_before_commit"]
)
async def test_provider_link_is_atomic_and_reuses_exact_intent_with_caller_rows_locked(
    db_session,
    test_engine,
    monkeypatch,
    commit_outcome,
):
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.project import Project
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with test_engine.connect() as owner, factory() as caller:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            await caller.get(Project, run.project_id, with_for_update=True)
            await caller.get(GenerationRun, run.id, with_for_update=True)

            class FaultyCommit(AsyncSession):
                async def commit(self):
                    if commit_outcome == "unknown_after_commit":
                        await super().commit()
                    raise RuntimeError("synthetic commit outcome")

            with provider_effect_scope(cursor):
                if commit_outcome != "confirmed":
                    monkeypatch.setattr(
                        provider_attempts,
                        "async_sessionmaker",
                        lambda *a, **k: async_sessionmaker(
                            test_engine, class_=FaultyCommit, expire_on_commit=False
                        ),
                    )
                    with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
                        await asyncio.wait_for(
                            provider_attempts.prepare_generation_attempt(
                                body, "/v1/chat/completions"
                            ),
                            2,
                        )
                    monkeypatch.setattr(provider_attempts, "async_sessionmaker", async_sessionmaker)
                    async with factory() as check:
                        expected = 1 if commit_outcome == "unknown_after_commit" else 0
                        for model in (GenerationProviderAttempt, GenerationRecoveryEffect):
                            assert (
                                await check.scalar(select(func.count()).select_from(model))
                                == expected
                            )
                first = await asyncio.wait_for(
                    provider_attempts.prepare_generation_attempt(body, "/v1/chat/completions"), 2
                )
                second = await asyncio.wait_for(
                    provider_attempts.prepare_generation_attempt(body, "/v1/chat/completions"), 2
                )
            assert first == second
            async with factory() as check:
                link = await check.get(GenerationRecoveryEffect, cursor.root_effect_id)
                assert link.provider_attempt_id == first.attempt_id
                for model in (GenerationProviderAttempt, GenerationRecoveryEffect):
                    assert await check.scalar(select(func.count()).select_from(model)) == 1
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await caller.rollback()
            await owner.execute(
                text("SELECT pg_advisory_unlock(1195724366,:key)"),
                {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
            )
            await owner.commit()


@pytest.mark.parametrize(
    "failure", ["cancelled", "claim", "revision", "step", "lock_lost", "drain"]
)
async def test_provider_effect_denies_stale_owner_or_drain_without_either_row(
    db_session,
    test_engine,
    monkeypatch,
    failure,
):
    from dataclasses import replace

    from sqlalchemy import func, select, text

    from yleum_api.models.deployment_drain import DeploymentDrain
    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    if failure == "cancelled":
        run.status = "cancel_requested"
    elif failure == "claim":
        cursor = replace(cursor, claim=cursor.claim.model_copy(update={"token": uuid4()}))
    elif failure == "revision":
        cursor = replace(cursor, state_revision=cursor.state_revision + 1)
    elif failure == "step":
        cursor = replace(cursor, logical_step_seq=cursor.logical_step_seq + 1)
    elif failure == "drain":
        db_session.add(DeploymentDrain(scope="generation", release_sha="a" * 40))
    await db_session.commit()
    async with test_engine.connect() as owner:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        if failure != "lock_lost":
            await owner.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {"key": key})
            await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with (
                provider_effect_scope(cursor),
                pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"),
            ):
                await provider_attempts.prepare_generation_attempt(body, "/v1/chat/completions")
            for model in (GenerationProviderAttempt, GenerationRecoveryEffect):
                assert await db_session.scalar(select(func.count()).select_from(model)) == 0
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            if failure != "lock_lost":
                await owner.execute(
                    text("SELECT pg_advisory_unlock(1195724366,:key)"), {"key": key}
                )
                await owner.commit()


@pytest.mark.parametrize("failure", ["session_lost", "claim_lost", "drain", "cancelled"])
async def test_existing_v2_identity_never_bypasses_live_dispatch_authority(
    db_session,
    test_engine,
    monkeypatch,
    failure,
):
    from sqlalchemy import func, select, text

    from yleum_api.models.deployment_drain import DeploymentDrain
    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    async with test_engine.connect() as owner:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        await owner.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {"key": key})
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor):
                identity = await provider_attempts.prepare_generation_attempt(
                    body, "/v1/chat/completions"
                )
                if failure == "session_lost":
                    await owner.execute(
                        text("SELECT pg_advisory_unlock(1195724366,:key)"), {"key": key}
                    )
                    await owner.commit()
                elif failure == "claim_lost":
                    raw = dict(run.agent_state["recovery_v1"])
                    raw["continuation_claim"] = {**raw["continuation_claim"], "token": str(uuid4())}
                    run.agent_state = {**run.agent_state, "recovery_v1": raw}
                elif failure == "drain":
                    db_session.add(DeploymentDrain(scope="generation", release_sha="a" * 40))
                else:
                    run.status = "cancel_requested"
                await db_session.commit()
                with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
                    await provider_attempts.prepare_generation_attempt(
                        body, "/v1/chat/completions", existing=identity
                    )
                for model in (GenerationProviderAttempt, GenerationRecoveryEffect):
                    assert await db_session.scalar(select(func.count()).select_from(model)) == 1
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            if failure != "session_lost":
                await owner.execute(
                    text("SELECT pg_advisory_unlock(1195724366,:key)"), {"key": key}
                )
                await owner.commit()


async def test_monitor_tick_and_effect_session_check_share_one_owned_connection_safely(
    db_session,
    test_engine,
    monkeypatch,
):
    from sqlalchemy import event, text

    run = await _queued_dispatch(db_session)
    run.status = "running"
    await db_session.commit()
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    entered = asyncio.Event()

    def slow_tick(conn, cursor, statement, parameters, context, executemany):
        if statement == "SELECT 1":
            entered.set()
            return "SELECT 1 FROM pg_sleep(0.05)", parameters
        return statement, parameters

    async with test_engine.connect() as owner:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        await owner.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {"key": key})
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        event.listen(test_engine.sync_engine, "before_cursor_execute", slow_tick, retval=True)
        monitor = asyncio.create_task(generation._ownership_monitor(owner, run.id))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(recovery_execution._require_session_owner(run.id), 2)
            await asyncio.sleep(0.02)
            if monitor.done():
                monitor.result()
            assert not monitor.done()
            await recovery_execution._require_session_owner(run.id)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            event.remove(test_engine.sync_engine, "before_cursor_execute", slow_tick)
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.rollback()
            await owner.execute(text("SELECT pg_advisory_unlock(1195724366,:key)"), {"key": key})
            await owner.commit()


async def test_cancel_while_waiting_for_session_mutex_never_reserves_provider_effect(
    db_session,
    test_engine,
    monkeypatch,
):
    from sqlalchemy import func, select, text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    async with test_engine.connect() as connection:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        await connection.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {"key": key})
        await connection.commit()
        owner = recovery_execution.SessionOwnership(run.id, connection)
        token = recovery_execution._SESSION_OWNER.set(owner)
        try:
            async with owner.access:
                with provider_effect_scope(cursor):
                    pending = asyncio.create_task(
                        provider_attempts.prepare_generation_attempt(
                            body,
                            "/v1/chat/completions",
                        )
                    )
                # Wait until the real SQL ownership read reached the mutex, then
                # cancel before any effect or provider intent can be inserted.
                for _ in range(100):
                    if owner.access._waiters:
                        break
                    await asyncio.sleep(0.01)
                assert owner.access._waiters
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            await recovery_execution._require_session_owner(run.id)
            for model in (GenerationProviderAttempt, GenerationRecoveryEffect):
                assert await db_session.scalar(select(func.count()).select_from(model)) == 0
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await connection.execute(
                text("SELECT pg_advisory_unlock(1195724366,:key)"), {"key": key}
            )
            await connection.commit()


@pytest.mark.parametrize(
    "failure",
    [
        "journal_off",
        "retention_off",
        "unknown_off",
        "null_off",
        "missing_metadata",
        "changed_metadata",
        "database_unavailable",
    ],
)
async def test_actual_chat_never_downgrades_durable_run_when_dependencies_are_off(
    db_session,
    test_engine,
    monkeypatch,
    failure,
):
    import httpx

    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    if failure == "unknown_off":
        run.agent_state = {**run.agent_state, "recovery_v1": {"version": 999}}
    elif failure == "null_off":
        run.agent_state = {**run.agent_state, "recovery_v1": None}
    await db_session.commit()
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=failure == "retention_off",
            generation_response_retention_enabled=False,
        ),
    )
    if failure == "database_unavailable":
        monkeypatch.setattr(
            provider_attempts,
            "get_engine",
            lambda: (_ for _ in ()).throw(RuntimeError("synthetic database unavailable")),
        )
    if failure == "missing_metadata":
        body.pop("metadata")
    elif failure == "changed_metadata":
        body["metadata"]["run_id"] = "invalid"
    sent = []

    async def post(*args, **kwargs):
        sent.append(True)
        raise AssertionError("durable run reached unretained HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    # Ordinary persisted V2 with no local cursor must also be detected. The
    # missing-metadata case retains only the actual server logical-call context.
    if failure in {"missing_metadata", "changed_metadata"}:
        with (
            provider_effect_scope(cursor),
            pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"),
        ):
            await llm_client._chat_post(body, "http://gateway.invalid", 1)
    else:
        with pytest.raises(RuntimeError, match="GENERATION_ATTEMPT_UNCONFIRMED"):
            await llm_client._chat_post(body, "http://gateway.invalid", 1)
    assert sent == []


async def test_legacy_generation_off_keeps_result_after_readonly_namespace_check(
    db_session,
    test_engine,
    monkeypatch,
):
    from yleum_api.services.generation import provider_attempts

    run, _, body = await _provider_context(db_session, test_engine, monkeypatch)
    run.agent_state = {key: value for key, value in run.agent_state.items() if key != "recovery_v1"}
    await db_session.commit()
    monkeypatch.setattr(
        provider_attempts,
        "get_settings",
        lambda: SimpleNamespace(
            generation_provider_attempts_enabled=False,
            generation_response_retention_enabled=False,
        ),
    )
    assert await provider_attempts.prepare_generation_attempt(body, "/v1/chat/completions") is None


@pytest.mark.parametrize("commit_unknown", [False, True])
async def test_physical_tool_intent_commit_precedes_dispatch_and_restart_cannot_repeat(
    db_session,
    test_engine,
    monkeypatch,
    commit_unknown,
):
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        prepare_tool_dispatch,
        tool_effect_scope,
    )

    run, provider, _ = await _provider_context(db_session, test_engine, monkeypatch)
    cursor = ToolEffectCursor(provider.claim, provider.state_revision, 0, "tool-1")
    state = dict(run.agent_state["recovery_v1"])
    state["execution"] = {**state["execution"], "pending_effect_id": str(cursor.root_effect_id)}
    run.agent_state = {"recovery_v1": state}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    class UnknownCommit(AsyncSession):
        async def commit(self):
            await super().commit()
            raise RuntimeError("synthetic committed acknowledgement loss")

    uncertain = async_sessionmaker(test_engine, class_=UnknownCommit, expire_on_commit=False)
    calls = []
    kwargs = dict(
        run_id=run.id,
        project_id=run.project_id,
        user_id=run.user_id,
        input_digest="b" * 64,
        binding={"workspace_revision": "a" * 64},
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner),
        )
        try:
            with tool_effect_scope(cursor):
                if commit_unknown:
                    with pytest.raises(RuntimeError, match="acknowledgement loss"):
                        await prepare_tool_dispatch(uncertain, **kwargs)
                else:
                    operation = await prepare_tool_dispatch(factory, **kwargs)
                    calls.append(operation)  # Physical controller POST only after commit returned.
            restarted = ToolEffectCursor(provider.claim, provider.state_revision, 0, "tool-1")
            with tool_effect_scope(restarted), pytest.raises(ValueError, match="already exists"):
                await prepare_tool_dispatch(factory, **kwargs)
            async with factory() as verify:
                effects = (await verify.scalars(select(GenerationRecoveryEffect))).all()
                assert len(effects) == 1
                assert effects[0].state == "inflight"
                assert effects[0].operation_id is not None
                assert calls == ([] if commit_unknown else [effects[0].operation_id])
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize(
    "outcome", ["saved", "stale", "cancelled", "unknown_commit", "working_commit"],
)
async def test_executable_cursor_commit_precedes_handoff_and_never_refunds(
    db_session,
    test_engine,
    monkeypatch,
    outcome,
):
    import hashlib

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from yleum_api.services.generation import source_checkpoint
    from yleum_api.services.generation.recovery_contract import ExecutionBudget

    run, _, _ = await _provider_context(db_session, test_engine, monkeypatch)
    run.prompt_hash = prompt_hash("Собери приложение")
    raw = run.agent_state["recovery_v1"]
    run.agent_state = {
        **run.agent_state,
        "recovery_v1": {
            **raw,
            "identity": {**raw["identity"], "prompt_hash": run.prompt_hash},
        },
    }
    await db_session.commit()
    state = recovery_execution.recovery_state(run)
    context = {"schema_version": 1, "entry": "pipeline", "completed": {"classification": "saved"}}
    objects = {}

    async def put(project_id, run_id, data):
        digest = hashlib.sha256(data).hexdigest()
        key = f"{project_id}/{run_id}/{digest}"
        objects[key] = data
        return key, digest

    async def get(project_id, run_id, *, object_key, digest):
        data = objects.get(object_key)
        return data if data is not None and hashlib.sha256(data).hexdigest() == digest else None

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", put)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", get)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    class UnknownCommit(AsyncSession):
        async def commit(self):
            await super().commit()
            raise RuntimeError("synthetic cursor commit acknowledgement lost")

    write_factory = (
        async_sessionmaker(test_engine, class_=UnknownCommit, expire_on_commit=False)
        if outcome in {"unknown_commit", "working_commit"}
        else factory
    )
    if outcome == "stale":
        changed = state.model_copy(update={"state_revision": state.state_revision + 1})
        run.agent_state = {**run.agent_state, "recovery_v1": changed.model_dump(mode="json")}
    elif outcome == "cancelled":
        run.status = "cancel_requested"
    await db_session.commit()
    execution = state.execution.model_copy(
        update={
            "budget": ExecutionBudget(
                steps=3,
                provider_attempts=2,
                repair_calls=1,
                absolute_deadline=datetime.now(UTC) + timedelta(seconds=30),
            )
        }
    )
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:

            async def save():
                return await recovery_execution.commit_execution_cursor(
                    write_factory,
                    previous=state,
                    execution=execution,
                    context=context,
                    phase="working" if outcome == "working_commit" else "waiting_dependency",
                )

            if outcome in {"stale", "cancelled"}:
                with pytest.raises(ValueError):
                    await save()
            elif outcome in {"unknown_commit", "working_commit"}:
                with pytest.raises(RuntimeError, match="acknowledgement lost"):
                    await save()
                # This invocation still holds the OLD cursor/context. Reload the
                # committed cursor, never replace its newer data or budget.
                driver = recovery_execution.ExecutionDriver(
                    factory, state,
                    {"schema_version": 1, "entry": "pipeline", "completed": {"obsolete": True}},
                    AsyncMock(),
                )
                with pytest.raises(GenerationYielded):
                    await driver.wait()
                retained = await recovery_execution.load_execution_context(
                    driver.state.identity, driver.state.execution.execution_context_ref,
                )
                assert retained == context
                assert driver.state.execution.budget == execution.budget
            else:
                current = await save()
                assert current.state_revision == state.state_revision + 1
                assert current.phase == "waiting_dependency"
                restored = await recovery_execution.load_execution_context(
                    current.identity,
                    current.execution.execution_context_ref,
                )
                assert restored == context
                # A resumed caller cannot turn historical accounting into a new allowance.
                with pytest.raises(ValueError, match="budget"):
                    await recovery_execution.commit_execution_cursor(
                        factory,
                        previous=current,
                        execution=current.execution.model_copy(
                            update={"budget": ExecutionBudget()}
                        ),
                        context=context,
                        phase="working",
                    )
            async with factory() as verify:
                persisted = await verify.get(GenerationRun, run.id)
                saved = recovery_execution.recovery_state(persisted)
                if outcome in {"saved", "unknown_commit", "working_commit"}:
                    assert saved.phase == "waiting_dependency"
                    assert saved.execution.budget == execution.budget
                    assert persisted.finished_at is None and persisted.status == "running"
                else:
                    assert saved.phase == "working"
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("wire_count", [1, 2])
async def test_v2_chat_reserves_every_physical_wire_before_http(
    db_session,
    test_engine,
    monkeypatch,
    wire_count,
):
    import httpx
    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services import llm_client
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    body.update(model="claude-sonnet-5.5", max_tokens=32)
    calls = []
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def transport(request):
        async with factory() as session:
            rows = (
                await session.scalars(
                    select(GenerationRecoveryEffect)
                    .where(
                        GenerationRecoveryEffect.run_id == run.id,
                    )
                    .order_by(GenerationRecoveryEffect.effect_ordinal)
                )
            ).all()
        wires = [r for r in rows if r.kind == "progress"]
        assert len(wires) == len(calls) + 1
        latest = wires[-1]
        assert latest.pre_binding["dispatch_v1"]["wire_attempt"] == len(calls)
        assert latest.pre_binding["dispatch_v1"]["clock_basis"] == "utc"
        assert latest.provider_attempt_id is None
        assert latest.state == "inflight"
        calls.append(request)
        if len(calls) < wire_count:
            raise httpx.ConnectError("synthetic preconnect", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "saved"}}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    monkeypatch.setattr(llm_client, "record_headers", lambda *a: None)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor):
                response = await llm_client._chat_post(body, "https://synthetic.invalid/chat", 10)
            assert response.status_code == 200
            assert len(calls) == wire_count
            assert len({request.content for request in calls}) == 1
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("failure", ["commit_before", "commit_after", "expired_after_commit"])
async def test_wire_unknown_commit_or_expiry_never_reaches_http(
    db_session,
    test_engine,
    monkeypatch,
    failure,
):
    import httpx
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import AsyncSession

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts
    from yleum_api.services.generation import recovery_reconciliation as rec

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    body.update(model="claude-sonnet-5.5", max_tokens=32)
    calls = []
    now = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(rec, "datetime", Clock)

    class CommitFault(AsyncSession):
        async def commit(self):
            nonlocal now
            wire = any(
                isinstance(row, GenerationRecoveryEffect) and row.kind == "progress"
                for row in self.new
            )
            if not wire:
                return await super().commit()
            if failure != "commit_before":
                await super().commit()
            if failure == "expired_after_commit":
                now += timedelta(seconds=11)
            else:
                raise RuntimeError("synthetic wire commit uncertainty")

    monkeypatch.setattr(
        provider_attempts,
        "async_sessionmaker",
        lambda *a, **k: async_sessionmaker(test_engine, class_=CommitFault, expire_on_commit=False),
    )

    async def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": []})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    monkeypatch.setattr(llm_client, "record_headers", lambda *a: None)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with rec.provider_effect_scope(cursor), pytest.raises((RuntimeError, ValueError)):
                await llm_client._chat_post(body, "https://synthetic.invalid/chat", 10)
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()
    assert calls == []
    assert await db_session.scalar(
        select(func.count())
        .select_from(GenerationRecoveryEffect)
        .where(
            GenerationRecoveryEffect.run_id == run.id,
            GenerationRecoveryEffect.kind == "progress",
        )
    ) == (0 if failure == "commit_before" else 1)


@pytest.mark.parametrize("edge", ["sse", "native"])
async def test_v2_stream_native_also_reserve_before_physical_http(
    db_session, test_engine, monkeypatch, edge
):
    import httpx
    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services import agent_native, llm_client
    from yleum_api.services.generation import response_auth
    from yleum_api.services.generation.recovery_reconciliation import provider_effect_scope

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    calls = []

    async def transport(request):
        async with factory() as session:
            wires = (
                await session.scalars(
                    select(GenerationRecoveryEffect).where(
                        GenerationRecoveryEffect.run_id == run.id,
                        GenerationRecoveryEffect.kind == "progress",
                    )
                )
            ).all()
        assert len(wires) == 1 and wires[0].pre_binding["dispatch_v1"]["kind"] == edge
        calls.append(request)
        if edge == "sse":
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"saved"}}]}\n\ndata: [DONE]\n\n'
            )
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "saved"}], "stop_reason": "end_turn"}
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    monkeypatch.setattr(llm_client, "record_headers", lambda *a: None)
    monkeypatch.setattr(response_auth, "record_headers", lambda *a: None)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://synthetic.invalid"),
    )
    monkeypatch.setattr(llm_client, "_billing_metadata", lambda *a: body["metadata"])
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with provider_effect_scope(cursor):
                if edge == "sse":
                    result = [
                        item
                        async for item in llm_client.stream_chat_completion(
                            [],
                            "claude-sonnet-5.5",
                            str(run.user_id),
                            str(run.project_id),
                            str(run.user_message_id),
                        )
                    ]
                    assert result == [{"delta": "saved"}]
                    async with factory() as verify:
                        wire = await verify.scalar(
                            select(GenerationRecoveryEffect).where(
                                GenerationRecoveryEffect.run_id == run.id,
                                GenerationRecoveryEffect.kind == "progress",
                            )
                        )
                        assert wire.post_binding == {"outward_started": True}
                else:
                    async with client(transport=httpx.MockTransport(transport)) as native_client:
                        result = await agent_native._call_messages(
                            native_client,
                            "https://synthetic.invalid/v1/messages",
                            [],
                            "synthetic",
                            model="claude-sonnet-5.5",
                            user_id=str(run.user_id),
                            project_id=str(run.project_id),
                            run_id=str(run.id),
                        )
                    assert result["content"][0]["text"] == "saved"
            assert len(calls) == 1
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("case", ["child", "cold"])
async def test_wire_provider_child_interleaving_and_cold_latest_receipt(
    db_session,
    test_engine,
    monkeypatch,
    case,
):
    from dataclasses import replace

    from sqlalchemy import select, text

    from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.provider_call import ProviderCall
    from yleum_api.services.generation import provider_attempts as attempts
    from yleum_api.services.generation import recovery_reconciliation as rec

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with rec.provider_effect_scope(cursor):
                rec.begin_provider_dispatch("chat", allowance=2, seconds=30)
                parent = await attempts.prepare_generation_attempt(body, "/v1/chat/completions")
                await attempts.reserve_generation_wire(parent, wire_attempt=0)
                # Existing committed financial journal predicate, synthetic rows;
                # actual Gateway dispatch/finish coverage is in the joint suite.
                async with factory() as session:
                    call = ProviderCall(
                        id=uuid4(),
                        provider_scope="llmgw",
                        route="/v1/chat/completions",
                        requested_model="synthetic",
                        expense_owner="user",
                        user_id=run.user_id,
                        project_id=run.project_id,
                        run_id=run.id,
                        stage="synthetic",
                        free=False,
                        status="failed",
                        receipt_hash="b" * 64,
                        finished_at=datetime.now(UTC),
                    )
                    session.add(call)
                    saved = await session.get(GenerationProviderAttempt, parent.attempt_id)
                    saved.phase, saved.terminal_reason, saved.provider_call_id = (
                        "closed",
                        "preconnect",
                        call.id,
                    )
                    await session.commit()
                child = await attempts.prepare_generation_attempt(
                    body, "/v1/chat/completions", parent=parent
                )
                assert child != parent
                if case == "child":
                    await attempts.reserve_generation_wire(child, wire_attempt=1)
            if case == "cold":
                cold = replace(cursor, reconcile_only=True, dispatch=rec._DispatchState())
                with rec.provider_effect_scope(cold):
                    rec.begin_provider_dispatch("chat", allowance=2, seconds=30)
                    resumed = await attempts.prepare_generation_attempt(
                        body, "/v1/chat/completions"
                    )
                    assert resumed == child
                    assert cold.dispatch.plan.deadline == cursor.dispatch.plan.deadline
            async with factory() as session:
                rows = (
                    await session.scalars(
                        select(GenerationRecoveryEffect)
                        .where(
                            GenerationRecoveryEffect.run_id == run.id,
                        )
                        .order_by(GenerationRecoveryEffect.effect_ordinal)
                    )
                ).all()
                assert [r.kind for r in rows] == (
                    ["provider", "progress", "provider", "progress"]
                    if case == "child"
                    else ["provider", "progress", "provider"]
                )
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("damage", [None, "expired", "counter", "parent", "subkind"])
@pytest.mark.parametrize("edge", ["chat", "sse", "native"])
async def test_cold_chat_transport_receipt_uses_remaining_wire_once(
    db_session, test_engine, monkeypatch, damage, edge
):
    from dataclasses import replace

    import httpx
    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services import agent_native, llm_client
    from yleum_api.services.generation import recovery_reconciliation as rec
    from yleum_api.services.generation import response_auth

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    body.update(model="claude-sonnet-5.5", max_tokens=32)
    posts = []

    async def transport(request):
        posts.append(request)
        if len(posts) == 1:
            raise httpx.ConnectError("synthetic preconnect", request=request)
        if edge == "native":
            return httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "saved"}], "stop_reason": "end_turn"},
            )
        if edge == "sse":
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"saved"}}]}\n\ndata: [DONE]\n\n'
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "saved"}}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    monkeypatch.setattr(llm_client, "record_headers", lambda *a: None)
    monkeypatch.setattr(response_auth, "record_headers", lambda *a: None)
    monkeypatch.setattr(
        llm_client,
        "get_settings",
        lambda: SimpleNamespace(mock_llm=False, llm_gateway_url="https://synthetic.invalid"),
    )
    monkeypatch.setattr(llm_client, "_billing_metadata", lambda *a: body["metadata"])

    async def invoke(budget):
        if edge == "chat":
            response = await llm_client._chat_post(body, "https://synthetic.invalid/chat", budget)
            return response.json()["choices"][0]["message"]["content"]
        if edge == "sse":
            values = [
                value
                async for value in llm_client.stream_chat_completion(
                    [],
                    "claude-sonnet-5.5",
                    str(run.user_id),
                    str(run.project_id),
                    str(run.user_message_id),
                )
            ]
            if any("error" in value for value in values):
                raise llm_client.LLMError("synthetic observed public error")
            return values[0]["delta"]
        async with client(transport=httpx.MockTransport(transport)) as native_client:
            value = await agent_native._call_messages(
                native_client,
                "https://synthetic.invalid/v1/messages",
                [],
                "synthetic",
                model="claude-sonnet-5.5",
                user_id=str(run.user_id),
                project_id=str(run.project_id),
                run_id=str(run.id),
            )
            return value["content"][0]["text"]

    reads = AsyncMock(return_value=None)
    monkeypatch.setattr(llm_client, "recover_generation_response", reads)

    class ProcessExit(BaseException):
        pass

    real_sleep = asyncio.sleep

    async def stop_before_second_post(delay):
        if delay == 0:
            return await real_sleep(0)
        raise ProcessExit()

    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with monkeypatch.context() as local, rec.provider_effect_scope(cursor):
                if edge == "native":
                    local.setattr(asyncio, "sleep", stop_before_second_post)
                else:
                    local.setattr(
                        llm_client,
                        "asyncio",
                        SimpleNamespace(
                            get_running_loop=asyncio.get_running_loop,
                            timeout_at=asyncio.timeout_at,
                            sleep=stop_before_second_post,
                        ),
                    )
                with pytest.raises(ProcessExit):
                    await invoke(30)
            assert len(posts) == 1
            if damage == "expired":
                future = cursor.dispatch.plan.deadline + timedelta(seconds=1)

                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return future

                monkeypatch.setattr(rec, "datetime", Clock)
            elif damage is not None:
                async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
                    wire = await session.scalar(
                        select(GenerationRecoveryEffect).where(
                            GenerationRecoveryEffect.run_id == run.id,
                            GenerationRecoveryEffect.kind == "progress",
                        )
                    )
                    binding = {**wire.pre_binding["dispatch_v1"]}
                    if damage == "counter":
                        binding["wire_attempt"] = 1
                    elif damage == "parent":
                        binding["parent_attempt_id"] = str(uuid4())
                    else:
                        binding["unknown_subkind"] = True
                    wire.pre_binding = {"dispatch_v1": binding}
                    await session.commit()
            cold = replace(cursor, reconcile_only=True, dispatch=rec._DispatchState())
            if damage is not None:
                with (
                    rec.provider_effect_scope(cold),
                    pytest.raises((llm_client.LLMError, RuntimeError)),
                ):
                    await invoke(300)
                assert len(posts) == 1
                return
            with rec.provider_effect_scope(cold):
                response = await invoke(300)
            assert response == "saved" and len(posts) == 2
            import json

            assert (
                json.loads(posts[0].content)["metadata"]["generation_provider_identity"]
                == json.loads(posts[1].content)["metadata"]["generation_provider_identity"]
            )
            reads.assert_awaited_once()
            assert cold.dispatch.plan.deadline == cursor.dispatch.plan.deadline
            again = replace(cursor, reconcile_only=True, dispatch=rec._DispatchState())
            with rec.provider_effect_scope(again), pytest.raises(llm_client.LLMError):
                await invoke(300)
            assert len(posts) == 2
            rows = (
                await db_session.scalars(
                    select(GenerationRecoveryEffect).where(
                        GenerationRecoveryEffect.run_id == run.id,
                    )
                )
            ).all()
            assert sum(row.kind == "provider" for row in rows) == 1
            assert sum(row.kind == "progress" for row in rows) == 2
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("actual_wire", [False, True])
async def test_outer_transport_retry_keeps_root_and_distinct_wire_allowance(
    db_session, test_engine, monkeypatch, actual_wire
):
    from dataclasses import replace

    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation import provider_attempts as attempts
    from yleum_api.services.generation import recovery_reconciliation as rec

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    cursor = replace(cursor, outer_allowance=5)
    if actual_wire:
        import httpx

        from yleum_api.services import llm_client

        body.update(model="claude-sonnet-5.5", max_tokens=32)
        calls = []

        async def transport(request):
            calls.append(request)
            raise httpx.ConnectError("synthetic preconnect", request=request)

        client = httpx.AsyncClient
        monkeypatch.setattr(
            llm_client.httpx,
            "AsyncClient",
            lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
        )
        monkeypatch.setattr(llm_client, "record_headers", lambda *a: None)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with rec.provider_effect_scope(cursor):
                if actual_wire:
                    with pytest.raises(llm_client.LLMError) as failure:
                        await llm_client._chat_post(body, "https://synthetic.invalid/chat", 30)
                    assert failure.value.retry_safe is True and len(calls) == 2
                    row = await db_session.get(GenerationRecoveryEffect, cursor.root_effect_id)
                    from yleum_api.models.generation_provider_attempt import (
                        GenerationProviderAttempt,
                    )
                    from yleum_api.services.generation.provider_identity import (
                        GenerationProviderIdentity,
                    )

                    attempt = await db_session.get(
                        GenerationProviderAttempt, row.provider_attempt_id
                    )
                    identity = GenerationProviderIdentity(
                        attempt.run_id,
                        attempt.project_id,
                        attempt.user_id,
                        attempt.attempt_id,
                        attempt.prompt_hash,
                        attempt.request_sha256,
                    )
                else:
                    rec.begin_provider_dispatch("chat", allowance=2, seconds=30)
                    identity = await attempts.prepare_generation_attempt(
                        body, "/v1/chat/completions"
                    )
                    for index in range(2):
                        wire = await attempts.reserve_generation_wire(identity, wire_attempt=index)
                        await attempts.confirm_generation_preconnect(wire, identity)
            if actual_wire:
                from unittest.mock import AsyncMock

                reads = AsyncMock(
                    side_effect=llm_client.LLMError(
                        "pending",
                        code="generation_response_pending",
                        retry_safe=False,
                        provider_charge_ambiguous=True,
                    )
                )
                monkeypatch.setattr(llm_client, "require_recovered_response", reads)
                cold_exhausted = replace(cursor, reconcile_only=True, dispatch=rec._DispatchState())
                with (
                    rec.provider_effect_scope(cold_exhausted),
                    pytest.raises(llm_client.LLMError) as restored,
                ):
                    await llm_client._chat_post(body, "https://synthetic.invalid/chat", 300)
                assert restored.value.code == "preconnect"
                assert restored.value.retry_safe is True
                assert restored.value.provider_charge_ambiguous is False
                assert len(calls) == 2  # Reconstruct the result; no third inner HTTP.
                reads.assert_awaited_once()
            outer = replace(cursor, outer_attempt=1, dispatch=rec._DispatchState())
            with rec.provider_effect_scope(outer):
                rec.begin_provider_dispatch("chat", allowance=2, seconds=30)
                same = await attempts.prepare_generation_attempt(body, "/v1/chat/completions")
                assert same == identity
                await attempts.reserve_generation_wire(same, wire_attempt=0)
            cold = replace(outer, reconcile_only=True, dispatch=rec._DispatchState())
            with rec.provider_effect_scope(cold):
                rec.begin_provider_dispatch("chat", allowance=2, seconds=300)
                assert (
                    await attempts.prepare_generation_attempt(body, "/v1/chat/completions")
                    == identity
                )
                assert cold.dispatch.plan == outer.dispatch.plan
                assert cold.dispatch.wire_used == 1
            rows = (
                await db_session.scalars(
                    select(GenerationRecoveryEffect)
                    .where(
                        GenerationRecoveryEffect.run_id == run.id,
                    )
                    .order_by(GenerationRecoveryEffect.effect_ordinal)
                )
            ).all()
            assert [r.kind for r in rows] == ["provider", "progress", "progress", "progress"]
            assert [r.pre_binding["dispatch_v1"]["outer_attempt"] for r in rows[1:]] == [0, 0, 1]
            assert [r.pre_binding["dispatch_v1"]["wire_attempt"] for r in rows[1:]] == [0, 1, 0]
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("frame_advanced", [False, True])
@pytest.mark.parametrize("damage", [None, "expired", "unknown", "outward", "missing_clock"])
async def test_cold_exhausted_inner_preserves_exact_outer_index(
    db_session, test_engine, monkeypatch, frame_advanced, damage
):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    import httpx
    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services import llm_client
    from yleum_api.services.generation import provider_attempts as attempts
    from yleum_api.services.generation import recovery_reconciliation as rec

    run, cursor, body = await _provider_context(db_session, test_engine, monkeypatch)
    cursor = replace(cursor, outer_allowance=5)
    body.update(model="claude-sonnet-5.5", max_tokens=32)
    posts = []

    async def transport(request):
        posts.append(request)
        return httpx.Response(200, json={"choices": []})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    monkeypatch.setattr(llm_client, "record_headers", lambda *args: None)
    reads = AsyncMock(
        side_effect=llm_client.LLMError(
            "pending",
            code="generation_response_pending",
            retry_safe=False,
            provider_charge_ambiguous=True,
        )
    )
    monkeypatch.setattr(llm_client, "require_recovered_response", reads)
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with rec.provider_effect_scope(cursor):
                rec.begin_provider_dispatch("chat", allowance=2, seconds=30)
                identity = await attempts.prepare_generation_attempt(body, "/v1/chat/completions")
                for index in range(2):
                    wire = await attempts.reserve_generation_wire(identity, wire_attempt=index)
                    if index == 1 and damage == "outward":
                        await attempts.mark_generation_wire_outward(wire, identity)
                    elif index != 1 or damage != "unknown":
                        await attempts.confirm_generation_preconnect(wire, identity)
            original_deadline = datetime.now(UTC) + timedelta(seconds=30)
            cold = replace(
                cursor,
                outer_attempt=int(frame_advanced),
                reconcile_only=True,
                call_deadline=None if damage == "missing_clock" else original_deadline,
                dispatch=rec._DispatchState(),
            )
            if damage == "expired":

                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return original_deadline + timedelta(seconds=1)

                monkeypatch.setattr(rec, "datetime", Clock)
            allowed = damage is None or (damage == "missing_clock" and not frame_advanced)
            with rec.provider_effect_scope(cold):
                if allowed and frame_advanced:
                    response = await llm_client._chat_post(
                        body, "https://synthetic.invalid/chat", 300
                    )
                    assert response.status_code == 200
                    assert cold.dispatch.plan.deadline == original_deadline
                else:
                    with pytest.raises(llm_client.LLMError) as failure:
                        await llm_client._chat_post(body, "https://synthetic.invalid/chat", 300)
                    assert failure.value.code == (
                        "preconnect" if allowed else "generation_response_pending"
                    )
                    assert failure.value.provider_charge_ambiguous is not allowed
            assert len(posts) == int(allowed and frame_advanced)
            reads.assert_awaited_once()
            rows = (
                await db_session.scalars(
                    select(GenerationRecoveryEffect)
                    .where(GenerationRecoveryEffect.run_id == run.id)
                    .order_by(GenerationRecoveryEffect.effect_ordinal)
                )
            ).all()
            assert sum(row.kind == "provider" for row in rows) == 1
            wire_indices = [
                (
                    row.pre_binding["dispatch_v1"]["outer_attempt"],
                    row.pre_binding["dispatch_v1"]["wire_attempt"],
                )
                for row in rows
                if row.kind == "progress"
            ]
            assert wire_indices == [(0, 0), (0, 1)] + ([(1, 0)] if posts else [])
            # A second cold process cannot repeat the newly reserved outer wire.
            again = replace(cold, dispatch=rec._DispatchState())
            with rec.provider_effect_scope(again), pytest.raises(llm_client.LLMError):
                await llm_client._chat_post(body, "https://synthetic.invalid/chat", 300)
            assert len(posts) == int(allowed and frame_advanced)
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("damage", [None, "input", "pending", "cancelled"])
async def test_logical_tool_result_survives_cold_context_without_reexecution(
    db_session,
    test_engine,
    monkeypatch,
    damage,
):
    import hashlib

    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation import recovery_tool_receipts as receipts
    from yleum_api.services.generation import source_checkpoint
    from yleum_api.services.generation.recovery_reconciliation import (
        ToolEffectCursor,
        tool_effect_scope,
    )

    run, provider, _ = await _provider_context(db_session, test_engine, monkeypatch)
    cursor = ToolEffectCursor(provider.claim, provider.state_revision, 0, "tool-one")
    raw = dict(run.agent_state["recovery_v1"])
    raw["execution"] = {**raw["execution"], "pending_effect_id": str(cursor.root_effect_id)}
    run.agent_state = {"recovery_v1": raw}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    objects = {}

    async def store(project, run_id, data):
        sha = hashlib.sha256(data).hexdigest()
        objects[sha] = data
        return sha, sha

    async def load(project, run_id, *, object_key, digest):
        return objects.get(object_key)

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", load)
    kwargs = dict(
        run_id=run.id,
        project_id=run.project_id,
        user_id=run.user_id,
        input_digest="b" * 64,
        index=0,
    )
    observation = {"ok": True, "content": "Привет", "tool": {"arguments": '{"x":1}'}}
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            with tool_effect_scope(cursor):
                assert await receipts.load_tool_result(factory, **kwargs) is None
                await receipts.save_tool_result(factory, observation=observation, **kwargs)
            rows = list(
                (
                    await db_session.scalars(
                        select(GenerationRecoveryEffect).where(
                            GenerationRecoveryEffect.run_id == run.id
                        )
                    )
                ).all()
            )
            assert len(rows) == 1 and rows[0].state == "confirmed"
            if damage == "input":
                kwargs["input_digest"] = "c" * 64
            elif damage == "pending":
                rows[0].state = "inflight"
                await db_session.commit()
            elif damage == "cancelled":
                run.status = "cancel_requested"
                await db_session.commit()
            cold = ToolEffectCursor(provider.claim, provider.state_revision, 0, "tool-one")
            with tool_effect_scope(cold):
                if damage:
                    with pytest.raises(ValueError):
                        await receipts.load_tool_result(factory, **kwargs)
                else:
                    assert await receipts.load_tool_result(factory, **kwargs) == observation
                    await receipts.save_tool_result(factory, observation=observation, **kwargs)
                    assert (
                        len(
                            list(
                                (
                                    await db_session.scalars(
                                        select(GenerationRecoveryEffect).where(
                                            GenerationRecoveryEffect.run_id == run.id
                                        )
                                    )
                                ).all()
                            )
                        )
                        == 1
                    )
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("outcome", ["committed", "unknown", "changed_action"])
async def test_actual_driver_cold_partial_batch_consumes_each_committed_result_once(
    db_session,
    test_engine,
    monkeypatch,
    outcome,
):
    import hashlib
    from copy import deepcopy

    from sqlalchemy import select, text

    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.agent_builder import Action
    from yleum_api.services.generation import source_checkpoint
    from yleum_api.services.generation.recovery_contract import CheckpointRef

    run, provider, _ = await _provider_context(db_session, test_engine, monkeypatch)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    ref = CheckpointRef(object_key="synthetic/source", sha256="b" * 64)
    raw = dict(run.agent_state["recovery_v1"])
    raw.update(
        cursor="agent",
        checkpoint=ref.model_dump(mode="json"),
        context_ref=ref.model_dump(mode="json"),
        binding={
            "workspace_id": str(uuid4()),
            "fencing_epoch": 1,
            "source_sha256": "a" * 64,
            "schema_sha256": "b" * 64,
            "environment_sha256": "c" * 64,
        },
    )
    raw["execution"] = {
        **raw["execution"],
        "stage": "agent",
        "substep": "response_validated",
        "provider_effect_id": str(provider.root_effect_id),
        "execution_context_ref": ref.model_dump(mode="json"),
    }
    run.agent_state = {"recovery_v1": raw}
    await db_session.commit()
    state = recovery_execution.recovery_state(run)
    context = {
        "schema_version": 1,
        "entry": "pipeline",
        "active_frame": {"version": 1, "kind": "native", "value": {"step": 0, "convo": []}},
    }
    objects, calls, checkpoints = {}, [], []

    async def store(project, run_id, data):
        sha = hashlib.sha256(data).hexdigest()
        objects[sha] = data
        return sha, sha

    async def load(project, run_id, *, object_key, digest):
        return objects.get(object_key)

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", load)

    # Isolate the driver/result boundary. Full source-checkpoint CAS has its
    # own PG suite; this saves real current state while retaining that artifact.
    async def commit(factory, *, previous, execution, context, phase="working"):
        updated = previous.model_copy(
            update={
                "execution": execution,
                "phase": phase,
                "next_check_at": datetime.now(UTC) if phase != "working" else None,
                "state_revision": previous.state_revision + 1,
            }
        )
        async with factory() as session:
            current = await session.get(GenerationRun, run.id, with_for_update=True)
            assert recovery_execution.recovery_state(current) == previous
            current.agent_state = {"recovery_v1": updated.model_dump(mode="json")}
            await session.commit()
        return updated

    monkeypatch.setattr(recovery_execution, "commit_execution_cursor", commit)
    fail_checkpoint = True

    def driver_for(state, context):
        driver = recovery_execution.ExecutionDriver(factory, state, context, None)

        async def save(execution, *, context, phase="working"):
            nonlocal fail_checkpoint
            if execution.next_tool_index == 2 and fail_checkpoint:
                fail_checkpoint = False
                raise RuntimeError("synthetic crash after result commit before checkpoint")
            driver.state = await commit(
                factory, previous=driver.state, execution=execution, context=context, phase=phase
            )
            driver.context = context
            checkpoints.append(execution.next_tool_index)

        monkeypatch.setattr(driver, "save", save)
        return driver

    async def execute(name):
        calls.append(name)
        if name == "two" and outcome == "unknown":
            raise RuntimeError("synthetic tool executed without terminal result")
        return {"ok": True, "content": name + "Привет"}

    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            driver = driver_for(state, context)
            one = await driver.tool(
                "one", Action("read_file", {"path": "one"}), lambda: execute("one")
            )
            with pytest.raises(GenerationYielded):
                await driver.tool(
                    "two", Action("read_file", {"path": "two"}), lambda: execute("two")
                )
            assert calls == ["one", "two"] and checkpoints == [1]
            # Fresh process data; no collector, issuer, executor or in-memory result.
            async with factory() as session:
                current = await session.get(GenerationRun, run.id)
                cold_state = recovery_execution.recovery_state(current).model_copy(
                    update={"phase": "working", "next_check_at": None}
                )
                current.agent_state = {"recovery_v1": cold_state.model_dump(mode="json")}
                await session.commit()
            cold = driver_for(cold_state, deepcopy(driver.context))
            assert (
                await cold.tool("one", Action("read_file", {"path": "one"}), lambda: execute("one"))
                == one
            )
            if outcome == "committed":
                assert await cold.tool(
                    "two", Action("read_file", {"path": "two"}), lambda: execute("two")
                ) == {"ok": True, "content": "twoПривет"}
                assert checkpoints == [1, 2]
            else:
                with pytest.raises(GenerationYielded):
                    await cold.tool(
                        "two",
                        Action(
                            "read_file",
                            {
                                "path": "changed" if outcome == "changed_action" else "two",
                            },
                        ),
                        lambda: execute("two"),
                    )
                assert checkpoints == [1]
            assert calls == ["one", "two"]
            rows = (
                await db_session.scalars(
                    select(GenerationRecoveryEffect).where(
                        GenerationRecoveryEffect.run_id == run.id
                    )
                )
            ).all()
            assert len(rows) == (1 if outcome == "unknown" else 2)
            assert len({row.effect_id for row in rows}) == len(rows)
            assert cold.state.execution.next_tool_index == (2 if outcome == "committed" else 1)
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()


@pytest.mark.parametrize("damage,finalization_stage,initially_prepared", [
    (None, False, True), ("source", False, True), ("schema", False, True),
    ("environment", False, True), (None, True, True), (None, False, False),
    ("partial_tool", False, True),
    ("early_check", False, True),
])
async def test_noninitial_worker_reenters_real_pipeline_native_loop_with_fresh_fence(
    db_session,
    test_engine,
    monkeypatch,
    damage,
    finalization_stage,
    initially_prepared,
):
    """Actual worker/pipeline/loop/SQL; physical controller and gateway are fixed transports."""
    import hashlib
    from dataclasses import asdict, replace

    import httpx
    from sqlalchemy import select, text

    from yleum_api.core.config import get_settings
    from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
    from yleum_api.services import agent_native, llm_client, project_cell_executor
    from yleum_api.services.generation import (
        agent_generation,
        agent_pipeline,
        recovery_pipeline,
        recovery_store,
        source_checkpoint,
    )
    from yleum_api.services.generation.contracts import (
        AgentPromptPlan,
        GenerationIds,
        GenerationRuntime,
        ProjectGenerationFacts,
        SourceBaseline,
        StackPrompt,
    )
    from yleum_api.services.generation.recovery_pipeline import PreparedAgentEntry
    from yleum_api.services.generation_runs import load_generation_dispatch
    from yleum_api.services.orchestrator_client import (
        ProjectCellResourceResponse,
        ProjectCellWorkspaceIdentity,
    )
    from yleum_api.services.project_cell_proofs import ProofIdentity
    from yleum_api.services.project_cells import (
        claim_cell_operation_committed,
        complete_cell_operation,
    )
    from yleum_api.services.promotion_permit import workspace_revision_digest

    partial_tool = damage == "partial_tool"
    early_check = damage == "early_check"
    if partial_tool or early_check:
        damage = None
    expected_posts = 1 if partial_tool else 2
    expected_steps = 1 if partial_tool else 2
    run, _, _ = await _provider_context(db_session, test_engine, monkeypatch)
    run.prompt_hash = prompt_hash("Собери приложение")
    run.execution_started_at = datetime.now(UTC)
    run.execution_backend = "worker"
    started = run.execution_started_at
    dispatch = load_generation_dispatch(run)
    workspace = ProjectCellWorkspace(
        project_id=run.project_id,
        owner_id=run.user_id,
        generation_run_id=run.id,
        provider="docker_owner_canary",
        state="ready",
        fencing_epoch=1,
    )
    db_session.add(workspace)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    settings = get_settings().model_copy(
        update={
            "use_native_agent": True,
            "agent_max_segments": 1,
            "generation_recovery_execution_enabled": False,
            "generation_recovery_mode": "on",
            "generation_provider_attempts_enabled": True,
            "generation_response_retention_enabled": True,
            "llm_gateway_url": "http://synthetic.invalid",
            "use_design_intelligence_plugin": False,
            "use_max_finalization_coordinator": early_check,
        }
    )
    for module in (db, generation, supervisor, lifecycle, recovery_execution, agent_pipeline):
        monkeypatch.setattr(module, "get_engine", lambda: test_engine)
    for module in (agent_pipeline, agent_generation, agent_native, llm_client, recovery_execution):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
    monkeypatch.setattr(supervisor, "publish_event", AsyncMock())
    monkeypatch.setattr(supervisor, "clear_generation_cancel", AsyncMock())

    async def no_signal(*args):
        await asyncio.Future()

    for name in ("_wait_for_generation_cancel", "_wait_for_capacity_dispatch_lease_loss"):
        monkeypatch.setattr(supervisor, name, no_signal)

    objects = {}

    async def put(project_id, run_id, data):
        sha = hashlib.sha256(data).hexdigest()
        key = f"{project_id}/{run_id}/{sha}"
        objects[key] = data
        return key, sha

    async def get(project_id, run_id, *, object_key, digest):
        data = objects.get(object_key)
        return data if data is not None and hashlib.sha256(data).hexdigest() == digest else None

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", put)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", get)
    first_path = "src/app/page.tsx" if early_check else "src/first.ts"
    files = {first_path: "первый", "src/second.ts": "второй"}
    tree_sha = workspace_revision_digest(files)
    fence = 1
    effects, posts, recovered, finalization = [], [], [], []
    checks, active_drivers = [], []
    real_attach = recovery_pipeline.attach_driver

    def attach(*args, **kwargs):
        driver = real_attach(*args, **kwargs)
        active_drivers.append(driver)
        return driver

    monkeypatch.setattr(recovery_pipeline, "attach_driver", attach)
    entered, unblock = asyncio.Event(), asyncio.Event()
    identity = ProjectCellWorkspaceIdentity(
        tree_sha, "d" * 64, "b" * 64, "c" * 64, "e" * 64, "f" * 64
    )

    async def current(workspace_id, *, generation_run_id, fencing_epoch):
        assert (workspace_id, generation_run_id, fencing_epoch) == (workspace.id, run.id, fence)
        return identity

    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", current)

    async def controller_release(session_factory, operation_id, client):
        claimed = await claim_cell_operation_committed(session_factory, operation_id)
        response = ProjectCellResourceResponse(
            workspace_id=claimed.workspace_id,
            state="resources_ready",
            provider_ref="fixture",
            fencing_epoch=claimed.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )
        async with session_factory() as session:
            await complete_cell_operation(session, operation_id, response.to_wire_json())
            await session.commit()
        return SimpleNamespace(status="completed", response=response)

    monkeypatch.setattr(project_cell_executor, "execute_cell_operation", controller_release)

    async def prepare_runtime(**kw):
        nonlocal fence
        async with factory() as session:
            ws = await session.get(ProjectCellWorkspace, workspace.id, with_for_update=True)
            if ws.generation_run_id is None:
                ws.generation_run_id = run.id
                ws.fencing_epoch += 1
                fence = ws.fencing_epoch
                await session.commit()

        async def source():
            return dict(files)

        async def physical():
            return ProofIdentity(
                workspace.id,
                run.id,
                fence,
                tree_sha,
                "d" * 64,
                identity.schema_data_digest,
                "c" * 64,
                identity.environment_digest,
                identity.environment_digest,
                "fixture-v1",
                "f" * 64,
            )

        async def execute(action):
            if early_check and action.name == "build":
                driver = active_drivers[-1]
                operation_id = uuid4()

                async def checked():
                    checks.append(operation_id)
                    return {"ok": True, "detail": "current compiler green"}

                result = await recovery_pipeline.run_durable_role(
                    driver, role="fast_check", operation_id=operation_id, invoke=checked,
                )
                await recovery_pipeline.finish_recorded_role(driver, operation_id)
                if len(checks) == 1:
                    await driver.wait()  # Role confirmed, next model frame not yet saved.
                return result
            assert action.name in ({"read_file", "write_file"} if early_check else {"read_file"})
            effects.append(action.args["path"])
            if action.name == "write_file":
                assert action.args["content"] == files[action.args["path"]]
            return {"ok": True, "content": files[action.args["path"]]}

        async def release():
            await project_cell_executor._release_generation_lease(
                session_factory=factory,
                workspace_id=workspace.id,
                generation_run_id=run.id,
                profile_version="fixture-v1",
            )

        kw["runtime"].handle = SimpleNamespace(
            workspace_id=workspace.id,
            control_workspace_id=workspace.id,
            control_fencing_epoch=fence,
            snapshot_files=source,
            refresh_snapshot_files=source,
            current_identity=physical,
            is_portable=lambda: True,
            release=release,
            export_files=AsyncMock(return_value={}),
        )
        return SimpleNamespace(
            execute=execute,
            probe_runtime=AsyncMock(),
            probe_build=AsyncMock(),
            preview_url=AsyncMock(),
            shell_enabled=False,
            locked_files=frozenset(),
        ), None

    monkeypatch.setattr(agent_pipeline, "prepare_agent_runtime", prepare_runtime)
    monkeypatch.setattr(agent_pipeline, "require_named_behavior_readiness", AsyncMock())
    for name in (
        "classify_agent_turn",
        "prepare_stack_prompt",
        "prepare_agent_prompt",
        "stage_max_starter",
    ):
        monkeypatch.setattr(
            agent_pipeline,
            name,
            AsyncMock(side_effect=AssertionError("original preparation replay")),
        )

    bootstrap_calls = []
    if not initially_prepared:
        async def classify(**kw):
            bootstrap_calls.append("classification")
            return dispatch.prompt_text, False, False, False

        async def stack(**kw):
            from yleum_api.services.generation.recovery_reconciliation import _TOOL_EFFECT
            assert _TOOL_EFFECT.get() is not None, "bootstrap source mutation has no durable effect"
            bootstrap_calls.append("stack")
            return prepared.stack

        async def prompt(**kw):
            bootstrap_calls.append("prompt")
            return plan, None

        async def seed(**kw):
            from yleum_api.services.generation.recovery_reconciliation import _TOOL_EFFECT
            assert _TOOL_EFFECT.get() is not None, "starter write has no durable effect"
            bootstrap_calls.append("seed")
            return {}, None

        monkeypatch.setattr(agent_pipeline, "classify_agent_turn", classify)
        monkeypatch.setattr(agent_pipeline, "prepare_stack_prompt", stack)
        monkeypatch.setattr(agent_pipeline, "prepare_agent_prompt", prompt)
        monkeypatch.setattr(agent_pipeline, "stage_max_starter", seed)

    if partial_tool:
        real_tool = recovery_execution.ExecutionDriver._tool
        interrupted = False

        async def stop_after_first_tool(driver, key, action, invoke):
            nonlocal interrupted
            result = await real_tool(driver, key, action, invoke)
            if not interrupted:
                interrupted = True
                await driver.wait()  # After result+checkpoint, before the next tool is admitted.
            return result

        monkeypatch.setattr(recovery_execution.ExecutionDriver, "_tool", stop_after_first_tool)

    async def final_boundary(**kw):
        finalization.append(kw["_agent_res"])
        assert kw["_agent_res"].steps == expected_steps
        await kw["operations"].continuation.wait()

    prefix_calls = []
    if finalization_stage:
        async def stopped(**kw):
            prefix_calls.append("stopped")
            return kw["_agent_res"], False, {}, "summary"

        async def completed(**kw):
            prefix_calls.append("completed")
            return kw["_agent_res"], dict(files), kw["_agent_res"].steps

        async def final_proof_boundary(**kw):
            finalization.append(dict(kw["files"]))
            await kw["operations"].continuation.wait()

        monkeypatch.setattr(agent_pipeline, "recover_stopped_candidate", stopped)
        monkeypatch.setattr(agent_pipeline, "complete_empty_legacy_build", completed)
        monkeypatch.setattr(agent_pipeline, "check_backend_and_normalize_css", AsyncMock())
        monkeypatch.setattr(agent_pipeline, "probe_agent_candidate", AsyncMock(
            return_value=("summary", True, True, "", "")))
        monkeypatch.setattr(agent_pipeline, "repair_legacy_edit", AsyncMock(
            return_value=("summary", True, True, "", "")))
        async def rejected(**kw):
            return False, kw["_agent_res"], kw["files"], True, True, kw["accumulated"]
        monkeypatch.setattr(agent_pipeline, "recover_rejected_candidate", rejected)
        monkeypatch.setattr(agent_pipeline, "apply_legacy_design_and_result_text", AsyncMock(
            return_value=("summary",)))
        monkeypatch.setattr(agent_pipeline, "check_runtime_security_gates", AsyncMock(
            return_value=(None, None, "summary")))
        monkeypatch.setattr(agent_pipeline, "check_coverage_gate", AsyncMock(
            return_value=(None, "summary")))
        monkeypatch.setattr(agent_pipeline, "finalize_max_candidate", final_proof_boundary)
    else:
        monkeypatch.setattr(agent_pipeline, "recover_stopped_candidate", final_boundary)

    def answer(index):
        response = {
            "id": f"paid-{index}",
            "type": "message",
            "role": "assistant",
            "model": "synthetic",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"tool-{index}",
                    "name": "read_file",
                    "input": {"path": first_path if index == 1 else "src/second.ts"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if partial_tool:
            response["content"].append({
                "type": "tool_use", "id": "tool-2", "name": "read_file",
                "input": {"path": "src/second.ts"},
            })
        if early_check and index == 1:
            response["content"][0].update(name="write_file", input={
                "path": first_path, "content": files[first_path],
            })
        return response

    async def transport(request):
        assert request.method == "POST"
        posts.append(request)
        if not partial_tool and not early_check and len(posts) == expected_posts:
            raise httpx.ReadError("synthetic accepted-response ACK lost", request=request)
        assert len(posts) <= (2 if early_check else 1)
        return httpx.Response(200, json=answer(len(posts)))

    async def recover(attempt):
        recovered.append(attempt)
        entered.set()
        await unblock.wait()
        return answer(1 if partial_tool or early_check else 2)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        agent_native.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(transport), **kw),
    )
    monkeypatch.setattr(llm_client, "require_recovered_response", recover)

    ids = GenerationIds(
        run.id, run.project_id, run.user_id, run.user_message_id, run.assistant_message_id
    )
    baseline = SourceBaseline(None, None, dict(files))
    info = ProjectGenerationFacts(
        "blank", "synthetic", "Synthetic", None, None, False, "ru", False, "", ""
    )
    plan = AgentPromptPlan(
        "system", dispatch.prompt_text, "synthetic", None, expected_steps,
        "seed", "MAX VERIFICATION OVERRIDE" if early_check else None, "system", None, False,
    )
    prepared = PreparedAgentEntry(
        (dispatch.prompt_text, False, False, False),
        None,
        StackPrompt("seed", None, None, None, "system", False),
        plan,
        {},
        None,
        None,
    )
    context = {
        "schema_version": 1,
        "entry": "pipeline",
        "pipeline": {
            "ids": {k: str(v) for k, v in asdict(ids).items()},
            "baseline": asdict(baseline),
            "project_info": asdict(info),
            **{
                key: getattr(dispatch, key)
                for key in (
                    "prompt_text",
                    "force_model",
                    "is_free",
                    "model_id",
                    "orchestrate",
                    "selected_elements",
                )
            },
        },
        "completed": {"agent_entry": prepared.to_json()} if initially_prepared else {},
        "active_frame": None,
    }
    from yleum_api.services.generation.recovery_contract import decode_recovery

    state = decode_recovery(run.agent_state["recovery_v1"])
    state = state.model_copy(
        update={"identity": state.identity.model_copy(update={"prompt_hash": run.prompt_hash})}
    )
    ref = await recovery_execution.store_execution_context(state.identity, context)
    state = state.model_copy(
        update={
            "context_ref": ref,
            "execution": state.execution.model_copy(
                update={
                    "execution_context_ref": ref,
                    "pending_effect_id": None,
                    "substep": "agent_entry" if initially_prepared else "runtime_prepare",
                    "control_workspace_id": workspace.id,
                    "control_fencing_epoch": 1,
                }
            ),
        }
    )
    run.agent_state = {**run.agent_state, "recovery_v1": state.model_dump(mode="json")}
    await db_session.commit()
    runtime = GenerationRuntime()
    async with test_engine.connect() as owner:
        await owner.execute(
            text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)},
        )
        await owner.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, owner)
        )
        try:
            from yleum_api.services.generation.recovery_pipeline import attach_driver

            await prepare_runtime(runtime=runtime)
            initial_driver = attach_driver(factory, state, context, runtime.handle)
            await initial_driver.save(state.execution, context=context)
            state = initial_driver.state
            with pytest.raises(GenerationYielded):
                await agent_pipeline.run_agent_generation(
                    _consume_free_generation=AsyncMock(),
                    baseline=baseline,
                    capacity_dispatch_token=None,
                    factory=factory,
                    force_model=None,
                    ids=ids,
                    is_free=False,
                    model_id=dispatch.model_id,
                    orchestrate=True,
                    progress=SimpleNamespace(emit_agent_event=AsyncMock()),
                    project_info=info,
                    prompt_text=dispatch.prompt_text,
                    runtime=runtime,
                    selected_elements=[],
                    _resume_state=state,
                )
            await lifecycle.cleanup_generation_runtime(
                runtime, ids.project_id, ids.assistant_message_id
            )
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock_all()"))
            await owner.commit()
    assert len(posts) == (1 if early_check else expected_posts) and effects == [first_path]
    if early_check:
        assert len(checks) == 1
    await db_session.refresh(run)
    before = recovery_execution.recovery_state(run)
    assert before.phase == "waiting_dependency" and before.execution.physical_state == "released"
    if damage:
        if damage == "source":
            files[first_path] = "unrelated source change"
            tree_sha = workspace_revision_digest(files)
            identity = replace(identity, workspace_revision=tree_sha)
        else:
            field = "schema_data_digest" if damage == "schema" else "environment_digest"
            identity = replace(identity, **{field: "9" * 64})
        assert await generation.execute_dispatch(run.id)
        await db_session.refresh(run)
        after = recovery_execution.recovery_state(run)
        assert run.status == "running" and run.finished_at is None
        assert after.phase == "waiting_dependency" and after.binding == before.binding
        assert effects == [first_path] and len(posts) == expected_posts
        assert recovered == [] and finalization == [] and not entered.is_set()
        assert after.execution.physical_state == "released"
        return
    first = asyncio.create_task(generation.execute_dispatch(run.id))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert await generation.execute_dispatch(run.id) is False
        unblock.set()
        assert await asyncio.wait_for(first, 15)
    finally:
        unblock.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert effects == [first_path, "src/second.ts"], (
        f"cold pipeline source handoff: posts={len(posts)} checks={len(checks)} "
        f"recovered={len(recovered)} finalization={finalization}"
    )
    assert len(posts) == expected_posts and len(recovered) == 1 and len(finalization) == 1
    if early_check:
        # The prior command is not reissued; the new current-fence proof has its own operation.
        assert len(checks) == len(set(checks)) == 2
    await db_session.refresh(run)
    after = recovery_execution.recovery_state(run)
    assert (
        run.status == "running" and run.finished_at is None and run.execution_started_at == started
    )
    assert after.execution.lease_cycle_seq == 1
    assert after.binding.fencing_epoch == before.execution.control_fencing_epoch + 1
    assert after.execution.budget.steps == expected_steps
    assert after.execution.next_tool_index == (2 if partial_tool else 1)
    operations = (
        await db_session.scalars(
            select(ProjectCellOperation).where(ProjectCellOperation.generation_run_id == run.id)
        )
    ).all()
    assert len(operations) == 2 and all(
        op.kind == "release" and op.status == "completed" for op in operations
    )
    assert after.execution.stage == "finalization", "completed main turn has no durable entry"
    assert await generation.execute_dispatch(run.id)
    assert effects == [first_path, "src/second.ts"]
    assert len(posts) == expected_posts and len(recovered) == 1 and len(finalization) == 2
    if not initially_prepared:
        assert bootstrap_calls == ["classification", "stack", "prompt", "seed"]
    if finalization_stage:
        assert finalization[0] == finalization[1]
    else:
        assert finalization[1].proof_checkpoint is None
        assert replace(finalization[0], proof_checkpoint=None) == finalization[1]
    if finalization_stage:
        assert prefix_calls == ["stopped", "completed"], "completed pipeline prefix replayed"
    await db_session.refresh(run)
    again = recovery_execution.recovery_state(run)
    assert again.execution.budget == after.execution.budget
    assert again.execution.lease_cycle_seq == 2 and again.execution.stage == "finalization"
    assert run.execution_started_at == started and run.finished_at is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_finalization_role_commits_pending_before_activity_and_preserves_unknown(
    monkeypatch, cancel,
):
    from yleum_api.services import max_finalization as final
    from yleum_api.services.generation import recovery_reconciliation as rec
    from yleum_api.services.generation.recovery_contract import ContinuationClaim, decode_recovery
    from yleum_api.services.project_cell_executor import ProjectCellCommandRole
    from yleum_api.services.project_cell_proofs import ProofDimension, ProofIdentity

    run = SimpleNamespace(id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64)
    claim = ContinuationClaim(run_id=run.id, token=uuid4(), attempt_seq=0, worker_id="owned",
                              expires_at=datetime.now(UTC)+timedelta(minutes=5))
    state = decode_recovery(_bootstrap_wait(run))
    state = state.model_copy(update={"phase": "working", "continuation_claim": claim,
        "execution": state.execution.model_copy(update={"stage": "finalization",
            "substep": "checkpoint_ready", "physical_state": "active"})})
    driver = recovery_execution.ExecutionDriver(None, state, {"completed": {}}, AsyncMock())
    saved = []

    async def save(cursor, *, context, **kwargs):
        saved.append((cursor, context))
        driver.state = driver.state.model_copy(update={"execution": cursor,
            "state_revision": driver.state.state_revision+1})
        driver.context = context

    async def wait():
        raise GenerationYielded(run_id=run.id, checkpoint=None, attempt_seq=0,
                                phase="waiting_dependency")

    monkeypatch.setattr(driver, "save", save)
    monkeypatch.setattr(driver, "wait", wait)
    identity = ProofIdentity(workspace_id=uuid4(), generation_run_id=run.id, fencing_epoch=1,
        workspace_revision="1"*64, dependency_digest="2"*64, schema_data_digest="3"*64,
        cell_manifest_digest="4"*64, base_image_digest="5"*64, toolchain_digest="6"*64,
        resource_profile_version="verification-v1", build_config_digest="7"*64)
    calls = []

    async def command(role, operation_id):
        calls.append(operation_id)
        pending = driver.context["completed"].get("pending_role")
        assert pending is not None, "role physical dispatch has no durable logical cursor"
        assert pending["operation_id"] == str(operation_id)
        cursor = rec._TOOL_EFFECT.get()
        assert cursor is not None
        assert cursor.root_effect_id == driver.state.execution.pending_effect_id
        if cancel:
            raise asyncio.CancelledError
        raise OSError("synthetic terminal command acknowledgement lost")

    async def activity(**kwargs):
        return await kwargs["work"]()

    monkeypatch.setattr(final, "run_with_activity_lease", activity)
    coordinator = object.__new__(final.MaxFinalizationCoordinator)
    coordinator.generation_run_id = run.id
    coordinator.project_id = run.project_id
    coordinator.session_factory = None
    coordinator.executor = SimpleNamespace(run_role=command, operation_status=AsyncMock())
    coordinator.continuation = driver
    coordinator.emit = AsyncMock()
    coordinator._phase_started = AsyncMock()
    coordinator._phase_finished = AsyncMock()
    coordinator._counter = AsyncMock()
    expected = asyncio.CancelledError if cancel else GenerationYielded
    with pytest.raises(expected):
        await coordinator._execute_role(identity=identity, dimension=ProofDimension.FULL_BUILD,
            role=ProjectCellCommandRole.FULL_BUILD, phase=final.GenerationPhase.FINAL_BUILD)
    assert len(calls) == len(saved) == 1
    assert driver.context["completed"]["pending_role"]["operation_id"] == str(calls[0])
    assert driver.state.execution.budget.steps == state.execution.budget.steps
    coordinator._phase_finished.assert_not_awaited()


@pytest.mark.parametrize("variant,expected", [
    ("legacy", "legacy"), ("v1", "legacy"), ("v2", "preserve"),
    ("null", "preserve"), ("future", "preserve"), ("read_failure", "preserve"),
    ("cancelled", "cancelled"), ("foreign", "preserve"),
])
async def test_failure_guard_preserves_only_unresolved_durable_or_unknown_state(
    db_session, test_engine, monkeypatch, variant, expected,
):
    run = await _queued_dispatch(db_session)
    run.status = "cancel_requested" if variant == "cancelled" else "running"
    if variant not in {"legacy", "cancelled", "read_failure"}:
        raw = _bootstrap_wait(run)
        if variant == "v1":
            raw.pop("execution")
            raw["version"] = 1
        elif variant == "future":
            raw = {"version": 999}
        elif variant == "null":
            raw = None
        elif variant == "foreign":
            raw["identity"]["user_id"] = str(uuid4())
        run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    if variant == "read_failure":
        def unavailable():
            raise OSError("synthetic database read unavailable")
        monkeypatch.setattr(recovery_execution, "get_engine", unavailable)
    else:
        monkeypatch.setattr(recovery_execution, "get_engine", lambda: test_engine)
    before = run.agent_state
    assert await recovery_execution.recovery_failure_outcome(run.id) == expected
    await db_session.refresh(run)
    assert run.agent_state == before and run.finished_at is None


@pytest.mark.parametrize("damage", ["claim", "attempt", "fence", "owner", "cancel", "session"])
async def test_stale_wait_cannot_adopt_changed_owner_or_physical_binding(
    db_session, test_engine, monkeypatch, damage,
):
    from sqlalchemy import text

    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.services.generation.recovery_contract import RecoveryContractError

    run, _, _ = await _provider_context(db_session, test_engine, monkeypatch)
    workspace = ProjectCellWorkspace(
        id=uuid4(), project_id=run.project_id, owner_id=run.user_id,
        provider="synthetic", state="ready", generation_run_id=run.id, fencing_epoch=1,
    )
    db_session.add(workspace)
    raw = dict(run.agent_state["recovery_v1"])
    raw["execution"] = {**raw["execution"], "control_workspace_id": str(workspace.id),
                        "control_fencing_epoch": 1}
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    previous = recovery_execution.recovery_state(run)
    if damage == "claim":
        raw = {**raw, "continuation_claim": {**raw["continuation_claim"], "token": str(uuid4())}}
    elif damage == "attempt":
        raw = {**raw, "attempt_seq": raw["attempt_seq"] + 1}
    elif damage == "fence":
        workspace.fencing_epoch += 1
    elif damage == "owner":
        workspace.generation_run_id = None
    elif damage == "cancel":
        run.status = "cancel_requested"
    run.agent_state = {**run.agent_state, "recovery_v1": raw}
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with test_engine.connect() as connection:
        key = int.from_bytes(run.id.bytes[-4:], "big", signed=True)
        if damage != "session":
            await connection.execute(text("SELECT pg_advisory_lock(1195724366,:key)"), {"key": key})
            await connection.commit()
        token = recovery_execution._SESSION_OWNER.set(
            recovery_execution.SessionOwnership(run.id, connection)
        )
        try:
            with pytest.raises(RecoveryContractError):
                await recovery_execution.commit_current_wait(factory, previous)
            await db_session.refresh(run)
            assert run.agent_state["recovery_v1"] == raw
            assert run.finished_at is None
        finally:
            recovery_execution._SESSION_OWNER.reset(token)
            await connection.execute(text("SELECT pg_advisory_unlock_all()"))
            await connection.commit()


@pytest.mark.parametrize(
    "damage", [None, "run", "workspace", "fence", "kind", "status", "unfinished", "receipt"]
)
def test_wait_ownership_refresh_requires_original_terminal_release(damage):
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse

    run_id, workspace_id = uuid4(), uuid4()
    receipt = ProjectCellResourceResponse(
        workspace_id=workspace_id,
        state="resources_ready",
        provider_ref="synthetic",
        fencing_epoch=3,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    )
    operation = SimpleNamespace(
        workspace_id=workspace_id,
        generation_run_id=run_id,
        fencing_epoch=3,
        kind="release",
        status="completed",
        finished_at=datetime.now(UTC),
        result_payload=receipt.to_wire_json(),
    )
    if damage in {"run", "workspace"}:
        setattr(operation, "generation_run_id" if damage == "run" else "workspace_id", uuid4())
    elif damage == "fence":
        operation.fencing_epoch = 4
    elif damage == "kind":
        operation.kind = "ensure"
    elif damage == "status":
        operation.status = "running"
    elif damage == "unfinished":
        operation.finished_at = None
    elif damage == "receipt":
        operation.result_payload = {}
    assert recovery_execution._owned_release_receipt(
        operation,
        run_id=run_id,
        workspace_id=workspace_id,
        fence=3,
    ) is (damage is None)
    assert not recovery_execution._owned_release_receipt(
        None,
        run_id=run_id,
        workspace_id=workspace_id,
        fence=3,
    )
