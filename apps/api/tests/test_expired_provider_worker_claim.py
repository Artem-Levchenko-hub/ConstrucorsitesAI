import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_recovery_deadlines import _clock
from tests.test_generation_recovery_deadlines import clock_owner as clock_owner
from tests.test_generation_recovery_deadlines import response_schema as response_schema
from tests.test_generation_recovery_execution import _bootstrap_wait
from tests.test_generation_retired_native_repair import (
    _attach_repair_marker,
    _compiler_after_retirement,
    _held_repair,
)
from tests.test_generation_worker import _queued_dispatch
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services import generation_deployment_drain as drain
from yleum_api.services import project_cell_executor
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_pipeline as pipeline
from yleum_api.services.generation import recovery_readers, recovery_store
from yleum_api.services.generation.expired_provider_drain import clock_sha256
from yleum_api.services.generation.recovery_contract import (
    IncompleteResponseBudget,
    RecoveryStateV3,
    decode_recovery,
)
from yleum_api.services.generation_runs import prompt_hash
from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
from yleum_api.services.project_cells import _canonical_operation_envelope
from yleum_api.workers import generation


@pytest.mark.parametrize("case", [
    "owned", "off", "wrong_scope", "unsealed", "bad_receipt", "drained", "advanced",
    "completed_ensure", "unverified_ensure",
])
async def test_only_the_owned_sealed_provider_attachment_is_scheduled(
    db_session, test_engine, monkeypatch, case, response_schema,
):
    now = datetime.now(UTC)
    run = await _queued_dispatch(db_session)
    run.status = "running"
    run.execution_backend = "worker"
    run.response_mode = "build"
    run.started_at = now - timedelta(hours=8)
    run.execution_started_at = run.started_at
    run.prompt_hash = prompt_hash("Собери приложение")
    workspace = ProjectCellWorkspace(
        project_id=run.project_id, owner_id=run.user_id, provider="docker_owner_canary",
        state="ready", fencing_epoch=11 if case == "advanced" else 10,
        generation_run_id=run.id if case == "advanced" else None,
    )
    db_session.add(workspace)
    await db_session.flush()
    context_ref = {"object_key": "test-context", "sha256": "b" * 64}
    checkpoint = {"object_key": "test-checkpoint", "sha256": "c" * 64}
    binding = {
        "workspace_id": str(workspace.id), "fencing_epoch": 9, "source_sha256": "d" * 64,
        "schema_sha256": "e" * 64, "environment_sha256": "f" * 64,
    }
    payload, operation_digest = _canonical_operation_envelope(workspace.id, run.id, "release", {
        "require_environment_proof": {"source_sha256": "d" * 64, "environment_sha256": "f" * 64},
    })
    release = ProjectCellOperation(
        id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id, kind="release",
        idempotency_key="test-release", request_payload=payload, request_digest=operation_digest,
        status="completed", fencing_epoch=10, finished_at=now - timedelta(minutes=2),
        result_payload=ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", provider_ref="test",
            fencing_epoch=10, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
            has_postgres=True, has_redis=True,
        ).to_wire_json(),
    )
    db_session.add(release)
    root = uuid5(run.id, "recovery-provider-v1:86")
    wire_id, attempt_id = uuid5(root, "wire-v1:0:0"), uuid5(root, "provider-attempt-v1")
    token = uuid4()
    raw = _bootstrap_wait(run)
    state = decode_recovery({
        **raw, "version": 3, "phase": "needs_input", "next_check_at": None,
        "cursor": "finalization",
        "checkpoint": checkpoint, "context_ref": context_ref,
        "binding": binding, "state_revision": 71, "attempt_seq": 5,
        "clock": {**_clock(now - timedelta(minutes=1)), "stage": "repair", "repair_granted": True},
        "execution": {
            **raw["execution"], "stage": "finalization", "substep": "response_wait",
            "physical_state": "released", "control_workspace_id": str(workspace.id),
            "control_fencing_epoch": 10, "release_operation_id": str(release.id),
            "provider_effect_id": str(root), "pending_effect_id": str(root), "logical_step_seq": 86,
            "lease_cycle_seq": 4, "execution_context_ref": context_ref,
            "budget": {"repair_calls": 1},
        },
    })
    assert isinstance(state, RecoveryStateV3)
    run.agent_state = {**run.agent_state, execution.RECOVERY_KEY: state.model_dump(mode="json")}
    terminal = case != "unsealed"
    attempt = GenerationProviderAttempt(
        attempt_id=attempt_id, run_id=run.id, project_id=run.project_id, user_id=run.user_id,
        dispatch_token=uuid4(), prompt_hash=run.prompt_hash, request_sha256="9" * 64,
        phase="closed" if terminal else "reserved",
        terminal_reason="expired_before_dispatch" if terminal else None,
    )
    db_session.add(attempt)
    await db_session.flush()
    plan = {
        "version": 1, "clock_basis": "utc", "kind": "native",
        "deadline": (now - timedelta(minutes=2)).isoformat(),
        "outer_attempt": 0, "outer_allowance": 1, "wire_allowance": 3,
    }
    receipt = {
        "version": 1, "kind": "gateway_expired_before_dispatch", "attempt_id": str(attempt_id),
        "root_effect_id": str(root), "wire_effect_id": str(wire_id), "request_sha256": "9" * 64,
        "source_sha256": binding["source_sha256"], "deadline": plan["deadline"],
    }
    for i, effect_id in enumerate((root, wire_id)):
        pre_binding = {"source": binding, "dispatch_v1": plan}
        if i:
            pre_binding = {"dispatch_v1": {
                **plan, "root_effect_id": str(root), "provider_effect_id": str(root),
                "attempt_id": str(attempt_id), "parent_attempt_id": None,
                "request_sha256": "9" * 64, "wire_attempt": 0,
            }}
        effect_state = "confirmed" if terminal else "inflight" if i else "prepared"
        result = receipt if terminal else None
        if i and case == "bad_receipt":
            result = {**receipt, "deadline": now.isoformat()}
        db_session.add(GenerationRecoveryEffect(
            effect_id=effect_id, run_id=run.id, project_id=run.project_id, user_id=run.user_id,
            logical_step_seq=86, effect_ordinal=i, kind="progress" if i else "provider",
            input_digest="9" * 64, state=effect_state,
            attempt_seq=5, claim_token=token, provider_attempt_id=None if i else attempt_id,
            parent_effect_id=root if i else None, pre_binding=pre_binding,
            receipt_ref=result,
        ))
    if case == "drained":
        await drain.begin_drain(db_session, "a" * 40)
    await db_session.commit()
    readers = AsyncMock(return_value="a" * 64)
    monkeypatch.setattr(recovery_readers, "require_recovery_readers", readers)
    monkeypatch.setattr(recovery_store, "_validate_retained_checkpoint", AsyncMock())
    monkeypatch.setattr(execution, "load_execution_context", AsyncMock(return_value={
        "active_frame": {"version": 1, "kind": "native", "value": {}},
        "active_tool": None,
        "completed": {
            "source_repair": {"version": 1, "status": "active", "task": "retained",
                              "baseline": {}, "edit_deadline": None, "targeted": True,
                              "response_budget": {"total": 0, "since_progress": 0}, "result": None},
        },
    }))
    name = "GENERATION_EXPIRED_PROVIDER_REATTACH_SCOPE"
    if case == "off":
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, json.dumps({
            "version": 1, "run_id": str(run.id), "attempt_id": str(attempt_id),
            "state_revision": state.state_revision,
            "context_sha256": "b" * 64,
            "source_sha256": "0" * 64 if case == "wrong_scope" else "d" * 64,
            "clock_sha256": clock_sha256(state),
        }))
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        found = (await session.execute(generation._maintenance_candidates_query([]))).all()
        assert any(candidate == run.id for candidate, _host in found) is (
            case in {"owned", "bad_receipt", "drained", "completed_ensure", "unverified_ensure"}
        )
    acquire_again = case in {"owned", "completed_ensure", "unverified_ensure"}
    progress_to_ensure = case in {"completed_ensure", "unverified_ensure"}
    completed_reader = AsyncMock()
    monkeypatch.setattr(project_cell_executor, "verified_completed_attachment_ensure",
                       completed_reader, raising=False)
    for ordinal in range(2 if acquire_again else 1):
        if ordinal == 1 and progress_to_ensure:
            workspace.generation_run_id, workspace.fencing_epoch = run.id, 11
            workspace.state = "provisioning"  # Ensure completed before workspace-ready SQL.
            ensure = ProjectCellOperation(
                id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id, kind="ensure",
                execution_run_id=run.id, idempotency_key=f"generation:{run.id}:ensure:test:cycle:5",
                request_digest="4" * 64, status="completed", fencing_epoch=11, finished_at=now,
            )
            db_session.add(ensure)
            await db_session.commit()
            completed_reader.return_value = ensure if case == "completed_ensure" else None
            async with factory() as session:
                found = (await session.execute(generation._maintenance_candidates_query([]))).all()
                assert any(candidate == run.id for candidate, _host in found)
        async with test_engine.connect() as owner, factory() as session:
            await owner.execute(text("SELECT pg_advisory_lock(:ns, :key)"), {
                "ns": generation._LOCK_NAMESPACE, "key": generation._lock_key(run.id),
            })
            await owner.commit()
            ownership = execution._SESSION_OWNER.set(execution.SessionOwnership(run.id, owner))
            try:
                claim = await execution.claim_continuation(
                    session, run_id=run.id, expected_attempt=state.attempt_seq + ordinal,
                    worker_id="generation-worker", now=now,
                )
                assert (claim is not None) is (
                    acquire_again and (ordinal == 0 or case != "unverified_ensure")
                )
                await session.commit()
            finally:
                execution._SESSION_OWNER.reset(ownership)
                await owner.execute(text("SELECT pg_advisory_unlock(:ns, :key)"), {
                    "ns": generation._LOCK_NAMESPACE, "key": generation._lock_key(run.id),
                })
                await owner.commit()
    async with factory() as session:
        stored = await session.get(GenerationRun, run.id)
        assert stored is not None
        marker = execution.load_expired_provider_attachment(stored)
        current = execution.recovery_state(stored)
        assert isinstance(current, RecoveryStateV3)
        assert await session.scalar(select(func.count()).select_from(ProjectCellOperation)) == (
            2 if progress_to_ensure else 1
        )
        attempts = await session.scalar(select(func.count()).select_from(GenerationProviderAttempt))
        assert attempts == 1
        if acquire_again:
            assert marker is not None and marker.matches_claim(current)
            assert marker.run_id == run.id and marker.attempt_id == attempt_id
            assert marker.state_revision == state.state_revision
            assert current.execution.lease_cycle_seq == state.execution.lease_cycle_seq + 1
            delta = 1 if case == "unverified_ensure" else 2
            assert current.attempt_seq == state.attempt_seq + delta
            assert current.state_revision == state.state_revision + delta
            assert current.clock == state.clock
            assert current.execution.budget == state.execution.budget
            assert current.context_ref == state.context_ref
            assert current.checkpoint == state.checkpoint
        else:
            assert marker is None and current == state
    assert completed_reader.await_count == int(progress_to_ensure)
    if progress_to_ensure:
        assert completed_reader.await_args.kwargs["expected_cycle"] == 5


@pytest.mark.parametrize("case", [
    "legacy_e10", "legacy_without_roles", "compiler_ready", "compiler_pending", "compiler_waited",
    "compiler_wrong_scope", "compiler_wrong_receipt", "compiler_after_grant",
    "pre_wire_ready", "pre_wire_elapsed", "pre_wire_extra_effect", "pre_wire_verification",
    "pre_wire_verification_released",
    "repair_entry_ready", "repair_entry_changed_task", "repair_entry_released",
    "repair_frame_released", "repair_frame_foreign",
])
async def test_scoped_old_controller_release_and_active_compiler_claim(
    clock_owner, response_schema, monkeypatch, case,
):
    c = clock_owner
    recorded = "owned" if case == "legacy_without_roles" else "historical"
    released, files, workspace, attempt, _ = await _held_repair(c, monkeypatch, recorded)
    now = datetime.now(UTC)
    c.run.started_at = c.run.execution_started_at = now - timedelta(hours=8)
    released = released.model_copy(update={
        "clock": released.clock.model_copy(update={"segment_deadline": now - timedelta(minutes=1)}),
    })
    repair_entry = case.startswith(("repair_entry_", "repair_frame_"))
    ordinary_release = case.endswith("_released") or case == "repair_frame_foreign"
    pre_wire = case.startswith("pre_wire_") or repair_entry
    if case.startswith("compiler_") or pre_wire:
        released = await _attach_repair_marker(
            c, released, attempt, claim_delta=1, marker_change={},
        )
        marker = execution.load_expired_provider_attachment(c.run)
        assert marker is not None
        async with c.owned():
            await pipeline.retire_expired_native_source_repair(
                c.driver, released=released, armed_attempt_id=attempt.attempt_id,
            )
            if pre_wire:
                from yleum_api.services.generation import expired_native_finalization as retirement
                from yleum_api.services.max_management_generation import (
                    PARKED_THROW_DIAGNOSTIC_PREFIX,
                )

                receipt_id = await _compiler_after_retirement(c, workspace, with_bootstrap=False)
                anchor = "src/lib/meal-request.ts"
                feedback = (
                    PARKED_THROW_DIAGNOSTIC_PREFIX
                    + f" Source location: {anchor}:3:55; AST call_expression; "
                    "reason unproven_json_stringify; fingerprint 0123456789abcdef."
                )
                monkeypatch.setattr(
                    retirement, "_target",
                    lambda _gap: None if case.startswith("pre_wire_verification")
                    else retirement._Target(anchor, feedback),
                )
                await retirement.prepare_retired_finalization(c.driver, SimpleNamespace(
                    retired_fast_check=AsyncMock(return_value=SimpleNamespace(id=receipt_id)),
                ))
                if case == "pre_wire_verification_released":
                    await pipeline.run_durable_role(
                        c.driver, role="full_build", operation_id=uuid4(), invoke=AsyncMock(),
                    )
                if repair_entry:
                    task = f"FINAL SOURCE CHECK FEEDBACK:\n{feedback}"
                    await pipeline.prepare_source_repair(
                        c.driver, task=task if case != "repair_entry_changed_task" else "changed",
                        baseline=files, edit_deadline=c.driver.soft_edit_deadline(None),
                        targeted=True, response_budget=IncompleteResponseBudget(),
                    )
                if case == "pre_wire_elapsed":
                    spent = c.driver.state.model_copy(update={
                        "clock": c.driver.state.clock.model_copy(update={
                            "segment_deadline": now - timedelta(seconds=1),
                        }),
                    })
                    c.run.agent_state = {
                        **c.run.agent_state, execution.RECOVERY_KEY: spent.model_dump(mode="json"),
                    }
                    await c.session.commit()
                    c.driver.state = spent
                if case == "pre_wire_extra_effect":
                    c.session.add(GenerationRecoveryEffect(
                        effect_id=uuid4(), run_id=c.run.id, project_id=c.run.project_id,
                        user_id=c.run.user_id,
                        logical_step_seq=c.driver.state.execution.logical_step_seq,
                        effect_ordinal=1, kind="progress", state="prepared", input_digest="1" * 64,
                        attempt_seq=c.driver.state.attempt_seq,
                        claim_token=c.driver.state.continuation_claim.token,
                    ))
                    await c.session.commit()
            if case == "compiler_pending":
                await pipeline.run_durable_role(
                    c.driver, role="fast_check", operation_id=uuid4(), invoke=AsyncMock(),
                )
            if case == "compiler_waited":
                from yleum_api.services.generation import lifecycle
                from yleum_api.services.generation.contracts import GenerationRuntime

                with pytest.raises(execution.GenerationYielded) as failure:
                    await pipeline.run_durable_role(
                        c.driver, role="fast_check", operation_id=uuid4(),
                        invoke=AsyncMock(side_effect=TimeoutError("compiler timed out")),
                    )
                release = AsyncMock()
                monkeypatch.setattr(lifecycle, "clear_stream_state", AsyncMock())
                await lifecycle.cleanup_generation_runtime(
                    GenerationRuntime(
                        handle=SimpleNamespace(release=release), continuation=c.driver,
                    ),
                    c.run.project_id, c.run.assistant_message_id, primary_error=failure.value,
                )
                release.assert_not_awaited()
                assert c.driver.state.phase == "waiting_dependency"
                assert c.driver.state.next_check_at is not None
                assert c.driver.state.next_check_at > now
                c.driver.state = c.driver.state.model_copy(update={"next_check_at": now})
                c.run.agent_state = {
                    **c.run.agent_state,
                    execution.RECOVERY_KEY: c.driver.state.model_dump(mode="json"),
                }
                await c.session.commit()
            if case in {"compiler_wrong_receipt", "compiler_after_grant"}:
                completed = dict(c.driver.context["completed"])
                key = "retired_expired_native_repair"
                if case == "compiler_wrong_receipt":
                    completed[key] = {**completed[key], "attempt_id": str(uuid4())}
                else:
                    completed["retired_native_finalization_v1"] = {"version": 1}
                await c.driver.save(c.driver.state.execution, context={
                    **c.driver.context, "completed": completed,
                })
        key = "docker-owner-cell-resources-v1"
        request, request_digest = _canonical_operation_envelope(
            workspace.id, c.run.id, "ensure", {"profile_version": key},
        )
        ensure = ProjectCellOperation(
            id=uuid4(), workspace_id=workspace.id, generation_run_id=c.run.id, kind="ensure",
            execution_run_id=c.run.id, request_payload=request, request_digest=request_digest,
            idempotency_key=f"generation:{c.run.id}:ensure:{key}:cycle:2",
            status="completed", fencing_epoch=11, finished_at=now,
            started_at=now - timedelta(seconds=2), attempt_count=1,
            result_payload=ProjectCellResourceResponse(
                workspace_id=workspace.id, state="resources_ready", provider_ref="fixture",
                fencing_epoch=11, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
                has_postgres=True, has_redis=True,
            ).to_wire_json(),
        )
        c.session.add(ensure)
        workspace.ready_at = now
        await c.session.commit()

        before = c.driver.state
        if ordinary_release:
            current = before.execution
            if case.startswith("repair_frame_"):
                seq = current.logical_step_seq + 1
                root = uuid5(c.run.id, f"recovery-provider-v1:{seq}")
                provider_owner = (
                    await _queued_dispatch(c.session) if case == "repair_frame_foreign" else c.run
                )
                fresh = GenerationProviderAttempt(
                    attempt_id=uuid5(root, "provider-attempt-v1"),
                    run_id=provider_owner.id, project_id=provider_owner.project_id,
                    user_id=provider_owner.user_id, prompt_hash=provider_owner.prompt_hash,
                    dispatch_token=uuid4(), request_sha256="2" * 64, phase="closed",
                )
                c.session.add(fresh)
                await c.session.flush()
                c.session.add(GenerationRecoveryEffect(
                    effect_id=root, run_id=c.run.id, project_id=c.run.project_id,
                    user_id=c.run.user_id, logical_step_seq=seq, effect_ordinal=0,
                    kind="provider", state="confirmed", input_digest=fresh.request_sha256,
                    provider_attempt_id=fresh.attempt_id, attempt_seq=before.attempt_seq,
                    claim_token=before.continuation_claim.token,
                ))
                context = {
                    **c.driver.context,
                    "active_frame": {"version": 1, "kind": "native", "value": {}},
                }
                reference = await execution.store_execution_context(
                    before.identity, context, binding=before.binding,
                )
                current = current.model_copy(update={
                    "logical_step_seq": seq, "substep": "response_wait",
                    "provider_effect_id": root, "pending_effect_id": root,
                    "execution_context_ref": reference,
                })
                before = before.model_copy(update={"context_ref": reference})
            new_release = ProjectCellOperation(
                id=uuid4(), workspace_id=workspace.id, generation_run_id=c.run.id, kind="release",
                idempotency_key=f"generation:{c.run.id}:release:{key}:cycle:2",
                request_digest="3" * 64, status="completed", fencing_epoch=12, finished_at=now,
                result_payload=ProjectCellResourceResponse(
                    workspace_id=workspace.id, state="resources_ready", provider_ref="fixture",
                    fencing_epoch=12, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
                    has_postgres=True, has_redis=True,
                ).to_wire_json(),
            )
            c.session.add(new_release)
            workspace.fencing_epoch, workspace.generation_run_id = 12, None
            before = before.model_copy(update={
                "clock": before.clock.model_copy(update={"interval_open": False}),
                "execution": current.model_copy(update={
                    "physical_state": "released", "control_fencing_epoch": 12,
                    "release_operation_id": new_release.id,
                }),
            })
            c.run.agent_state = {
                **c.run.agent_state, execution.RECOVERY_KEY: before.model_dump(mode="json"),
            }
            await c.session.commit()
        scope = {
            "version": 1, "run_id": str(c.run.id), "attempt_id": str(marker.attempt_id),
            "state_revision": marker.state_revision, "context_sha256": marker.context_ref.sha256,
            "source_sha256": marker.binding.source_sha256, "clock_sha256": marker.clock_sha256,
        }
        op_count = 4 if ordinary_release else 3
    else:
        workspace.fencing_epoch, workspace.generation_run_id = 10, None
        before = released.model_copy(update={
            "phase": "needs_input",
            "execution": released.execution.model_copy(update={"lease_cycle_seq": 1}),
        })
        if case == "legacy_without_roles":
            operation = await c.session.get(
                ProjectCellOperation, before.execution.release_operation_id,
            )
            operation.request_payload, operation.request_digest = _canonical_operation_envelope(
                workspace.id, c.run.id, "release", {},
            )
        c.run.agent_state = {
            **c.run.agent_state, execution.RECOVERY_KEY: before.model_dump(mode="json"),
        }
        await c.session.commit()
        scope = {
            "version": 1, "run_id": str(c.run.id), "attempt_id": str(attempt.attempt_id),
            "state_revision": before.state_revision, "context_sha256": before.context_ref.sha256,
            "source_sha256": before.binding.source_sha256, "clock_sha256": clock_sha256(before),
        }
        op_count = 1 if case == "legacy_without_roles" else 2

    if case == "compiler_wrong_scope":
        scope["clock_sha256"] = "0" * 64
    monkeypatch.setenv("GENERATION_EXPIRED_PROVIDER_REATTACH_SCOPE", json.dumps(scope))
    if ordinary_release:
        # The ordinary handoff is durable even after the release scope is withdrawn.
        monkeypatch.delenv("GENERATION_EXPIRED_PROVIDER_REATTACH_SCOPE")
        async with c.factory() as session:
            plan = await execution.retired_attachment_ordinary_handoff(
                session, c.run, before, marker,
            )
            assert (plan is not None) is (case != "repair_frame_foreign")
        if plan is not None:
            claim_time = (
                plan.repair_deadline - timedelta(seconds=5) if plan.repair_deadline else now
            )
            async with c.owned(), c.factory() as session:
                issued = await execution.claim_continuation(
                    session, run_id=c.run.id, expected_attempt=before.attempt_seq,
                    worker_id="generation-worker", now=claim_time,
                )
                assert issued is not None
                await session.commit()
            async with c.factory() as session:
                row = await session.get(GenerationRun, c.run.id)
                resumed = execution.recovery_state(row)
                assert isinstance(resumed, RecoveryStateV3)
                if plan.repair_deadline is not None:
                    assert resumed.clock.segment_deadline <= plan.repair_deadline
                assert resumed.execution.budget == before.execution.budget
                assert execution.load_expired_provider_attachment(row) == marker
            # Re-open under the actual executor up to its first ordinary MAX
            # readiness read. The old attachment branch must not intercept.
            class OrdinaryReadStarted(Exception):
                pass

            read = AsyncMock(side_effect=OrdinaryReadStarted)
            monkeypatch.setattr(project_cell_executor, "inspect_project_cell_control", read)
            async with c.owned(), c.factory() as session:
                # The AsyncSession connection factory owns the same isolated DB.
                monkeypatch.setattr(project_cell_executor, "get_engine", lambda: session.bind)
                with pytest.raises(OrdinaryReadStarted):
                    await project_cell_executor.maybe_create_project_cell_executor(
                        project_id=c.run.project_id, project_slug="fixture",
                        project_template="max_miniapp",
                        user_id=c.run.user_id, generation_run_id=c.run.id,
                        docs_media_execute=AsyncMock(),
                    )
            read.assert_awaited_once()
        return
    async with c.factory() as session:
        rows = (await session.execute(generation._maintenance_candidates_query([]))).all()
        assert any(candidate == c.run.id for candidate, _ in rows) is (
            case != "compiler_wrong_scope"
        )
    async with c.owned(), c.factory() as session:
        claim = await execution.claim_continuation(
            session, run_id=c.run.id, expected_attempt=before.attempt_seq,
            worker_id="generation-worker", now=now,
        )
        allowed = case in {
            "legacy_e10", "compiler_ready", "compiler_pending", "compiler_waited", "pre_wire_ready",
            "repair_entry_ready", "pre_wire_verification",
        }
        assert (claim is not None) is allowed
        await session.commit()
    async with c.factory() as session:
        run = await session.get(GenerationRun, c.run.id)
        current = execution.recovery_state(run)
        operations = await session.scalar(select(func.count()).select_from(ProjectCellOperation))
        assert operations == op_count
        if allowed and (case.startswith("compiler_") or pre_wire):
            assert current.execution == before.execution
            assert current.clock == before.clock and current.active_work_ms == before.active_work_ms
            assert current.checkpoint == before.checkpoint
            assert current.context_ref == before.context_ref
            assert execution.load_expired_provider_attachment(run) == marker
        elif not allowed:
            assert current == before
