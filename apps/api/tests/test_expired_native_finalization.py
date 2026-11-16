"""A sealed historical request can earn one new exact source-repair segment."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import func, select

from tests import test_generation_retired_native_repair as fixture
from tests.test_generation_retired_native_repair import _compiler_after_retirement, _held_repair
from tests.test_generation_retired_native_repair import clock_owner as clock_owner
from tests.test_generation_retired_native_repair import response_schema as response_schema
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.project_cell import ProjectCellOperation
from yleum_api.models.provider_call import ProviderCall
from yleum_api.services.generation import expired_native_finalization as retirement
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_pipeline as pipeline
from yleum_api.services.generation.continuation import SourceNoveltyTracker
from yleum_api.services.generation.expired_native_finalization import (
    RetiredFinalizationPlan,
    prepare_retired_finalization,
    restore_retired_finalization_cursor,
    retired_finalization_plan,
    retired_repair_edit_deadline,
)
from yleum_api.services.generation.recovery_contract import (
    CompilerWitness,
    IncompleteResponseBudget,
)
from yleum_api.services.generation.recovery_reconciliation import provider_root_effect_id


@pytest.mark.parametrize(
    "interrupted", (None, "object-write", "clock-commit-ack", "repair-entry-ack"),
)
async def test_retired_repair_grant_commits_source_plan_and_budget_once(
    clock_owner, response_schema, monkeypatch, interrupted,
):
    c = clock_owner
    setup_files = fixture.connection_files
    path = "src/lib/send-fixture.ts"

    def library_request(_source, modules):
        entry = (
            'import {sendFixture} from "@/lib/send-fixture";\n'
            + fixture.BASE.replace("EXTRA", "sendFixture(unknownPayload());")
        )
        return {
            **setup_files(entry, modules),
            path: 'export function sendFixture(value: unknown) {'
            'return fetch("/api/fixture", {method:"POST", body:JSON.stringify(value)}); }',
        }

    monkeypatch.setattr(fixture, "connection_files", library_request)
    _released, files, workspace, _attempt, _root = await _held_repair(c, monkeypatch, "historical")
    state = c.driver.state
    previous = state.model_copy(update={
        "clock": state.clock.model_copy(update={
            "stage": "repair", "repair_granted": True, "segment_seq": 1,
            "segment_deadline": datetime.now(UTC) - timedelta(minutes=1),
        }),
    })
    c.run.agent_state = {
        **c.run.agent_state, execution.RECOVERY_KEY: previous.model_dump(mode="json"),
    }
    await c.session.commit()
    c.driver.state = previous
    async with c.owned():
        await pipeline.retire_expired_native_source_repair(c.driver, released=previous)
        prog = pipeline.FinalizationProgress(
            source_history=SourceNoveltyTracker(files).source_history(),
            feedback=("earlier repair",),
            pending_detail="earlier repair", pending_fast_check=True,
        )
        await pipeline.save_finalization_progress(c.driver, prog)
        proof = await _compiler_after_retirement(c, workspace, with_bootstrap=True)
        before = c.driver.state
        compiler = AsyncMock(return_value=SimpleNamespace(id=proof))
        coordinator = SimpleNamespace(retired_fast_check=compiler)
        if interrupted in {"object-write", "clock-commit-ack"}:
            original_update = retirement.update_owned_clock

            async def lose_ack(*args):
                await original_update(*args)
                raise TimeoutError("clock commit acknowledgement lost")

            if interrupted == "object-write":
                monkeypatch.setattr(
                    retirement, "store_execution_context",
                    AsyncMock(side_effect=TimeoutError("object write unavailable")),
                )
            else:
                monkeypatch.setattr(retirement, "update_owned_clock", lose_ack)
            with pytest.raises(execution.GenerationYielded) as yielded:
                await prepare_retired_finalization(c.driver, coordinator)
            assert pipeline.retain_retired_attachment_on_wait(c.driver, yielded.value)
            current = c.driver.state
            assert current.execution.physical_state == "active"
            assert current.phase == "waiting_dependency"
            assert current.execution.budget == before.execution.budget
            assert current.execution.control_fencing_epoch == before.execution.control_fencing_epoch
            committed = interrupted == "clock-commit-ack"
            assert current.clock.segment_seq == before.clock.segment_seq + int(committed)
            stored = await execution.load_execution_context(
                current.identity, current.context_ref, binding=current.binding,
            )
            assert (retirement._KEY in stored["completed"]) is committed
            return
        plan = await prepare_retired_finalization(c.driver, coordinator)
        actual = c.driver.state
        assert plan.proof.binding == before.binding and plan.repair_path == path
        assert "unproven_json_stringify" in plan.feedback and plan.maximum_repairs == 2
        assert actual.state_revision == before.state_revision + 1
        assert actual.identity == before.identity and actual.checkpoint == before.checkpoint
        assert actual.active_work_ms == before.active_work_ms
        assert actual.execution.budget == before.execution.budget
        assert actual.clock.segment_seq == before.clock.segment_seq + 1
        assert actual.clock.interval_id != before.clock.interval_id
        assert actual.clock.window_ms == 420000
        deadline = plan.repair_deadline
        assert deadline is not None
        assert deadline == actual.clock.segment_deadline
        assert retired_repair_edit_deadline(plan, None) == deadline - timedelta(seconds=84)
        assert retired_repair_edit_deadline(plan, deadline + timedelta(minutes=30)) == (
            deadline - timedelta(seconds=84)
        )
        assert retired_repair_edit_deadline(plan, deadline - timedelta(minutes=5)) == (
            deadline - timedelta(minutes=5)
        )
        assert pipeline.pending_source_repair(c.driver) is None
        await c.session.refresh(c.run)
        assert execution.recovery_state(c.run) == actual
        loaded = await execution.load_execution_context(
            actual.identity, actual.context_ref, binding=actual.binding,
        )
        assert loaded == c.driver.context
        assert await pipeline.rebind_continuation(c.driver, allow_expired_retirement=True) is True
        assert await prepare_retired_finalization(c.driver, coordinator) == plan
        assert c.driver.state == actual
        compiler.assert_awaited_once()
        count = await c.session.scalar(select(func.count()).select_from(GenerationProviderAttempt))
        assert count == 1

        assert await restore_retired_finalization_cursor(c.driver) == "pre_entry"
        budget = IncompleteResponseBudget(total=3, since_progress=1)
        if interrupted == "repair-entry-ack":
            save = type(c.driver).save

            async def lose_entry_ack(driver, *args, **kwargs):
                await save(driver, *args, **kwargs)
                raise TimeoutError("source repair entry acknowledgement lost")

            monkeypatch.setattr(type(c.driver), "save", lose_entry_ack)
            with pytest.raises(execution.GenerationYielded) as yielded:
                await pipeline.prepare_source_repair(
                    c.driver, task=plan.feedback, baseline=files,
                    edit_deadline=retired_repair_edit_deadline(plan, None),
                    targeted=True, response_budget=budget,
                )
            assert pipeline.retain_retired_attachment_on_wait(c.driver, yielded.value)
            assert c.driver.state.execution.budget.repair_calls == 2
            assert c.driver.state.execution.provider_effect_id is None
            assert c.driver.state.clock.segment_deadline == deadline
            assert await c.session.scalar(select(func.count()).select_from(
                GenerationProviderAttempt,
            )) == 1
            return
        await pipeline.prepare_source_repair(
            c.driver, task=plan.feedback, baseline=files,
            edit_deadline=retired_repair_edit_deadline(plan, None),
            targeted=True, response_budget=budget,
        )
        active = c.driver.state
        assert await restore_retired_finalization_cursor(c.driver) == "repair"
        active_context = c.driver.context
        seq = active.execution.logical_step_seq + 1
        provider = provider_root_effect_id(active.identity.run_id, seq)
        response = active.execution.model_copy(update={
            "logical_step_seq": seq, "substep": "response_wait",
            "provider_effect_id": provider, "pending_effect_id": provider,
        })
        c.driver.state = active.model_copy(update={"execution": response})
        c.driver.context = {
            **active_context, "active_frame": {"kind": "native", "version": 1, "value": {}},
        }
        assert await restore_retired_finalization_cursor(c.driver) == "repair"
        pending = uuid5(active.identity.run_id, f"recovery-tool-v1:{seq}:owned-write")
        c.driver.state = active.model_copy(update={
            "execution": response.model_copy(update={
                "substep": "tool_wait", "pending_effect_id": pending,
            }),
        })
        with pytest.raises(ValueError, match="cannot reuse a changed cursor"):
            await restore_retired_finalization_cursor(c.driver)
        c.driver.context["active_tool"] = {
            "version": 1, "index": response.next_tool_index,
            "key": "owned-write", "input_digest": "a" * 64,
        }
        assert await restore_retired_finalization_cursor(c.driver) == "repair"
        c.driver.context = active_context
        c.driver.state = active.model_copy(update={
            "execution": active.execution.model_copy(update={
                "physical_state": "released",
                "release_operation_id": previous.execution.release_operation_id,
            }),
        })
        c.driver.snapshot.reset_mock()
        assert await restore_retired_finalization_cursor(c.driver) is None
        c.driver.snapshot.assert_not_awaited()
        c.driver.state = active

        await pipeline.finish_source_repair(
            c.driver, fixture.pipeline.AgentResult(
                done=False, summary="repair deadline", files={}, steps=0,
                stop_reason="source_deadline",
            ),
            budget,
        )
        assert await restore_retired_finalization_cursor(c.driver) == "verification"
        progress = pipeline.FinalizationProgress.model_validate(
            c.driver.context["completed"]["finalization_progress"], strict=False,
        )
        await pipeline.save_finalization_progress(
            c.driver, progress.model_copy(update={
                "pending_detail": None, "pending_fast_check": False,
            }), consume_repair=True,
        )
        assert await restore_retired_finalization_cursor(c.driver) == "verification"
        c.driver.state = c.driver.state.model_copy(update={
            "clock": c.driver.state.clock.model_copy(update={"segment_seq": plan.segment_seq + 1}),
        })
        with pytest.raises(ValueError, match="does not own"):
            retired_finalization_plan(c.driver)


@pytest.mark.parametrize(("case", "prior_maximum"), [
    ("catalog", 2), ("missing_css", 1), ("existing_hook", 2),
    ("cold_pregrant", 2), ("grant_ack", 2), ("third_no_wire", 2),
    ("pending_pregrant", 2), ("pending_postgrant", 2), ("pending_conflict", 2),
    ("pending_bootstrap_pregrant", 2), ("pending_bootstrap_postgrant", 2),
])
async def test_sealed_second_repair_grants_new_catalog_entry_once(
    clock_owner, response_schema, monkeypatch, case, prior_maximum,
):
    from yleum_api.services.max_generation_contract import max_source_completion_gap
    from yleum_api.services.max_management_generation import (
        ManagedSourceGap,
        ManagedSourceObligation,
    )
    from yleum_api.services.max_project_kit import _template_file
    from yleum_api.services.promotion_permit import workspace_revision_digest

    c = clock_owner
    setup_files = fixture.connection_files
    page = "src/app/page.tsx"
    source = """
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
export default function Page() {
  const {profile} = useYleumAppProfile();
  return <main><h1>{profile?.app_name}</h1></main>;
}"""
    if case == "existing_hook":
        source = (
            'import {useYleumCatalog} from "@/lib/omnia/catalog-client";\n'
            + source.replace("  return", "  useYleumCatalog();\n  return")
        )

    def catalog_files(_source, modules):
        if case != "missing_css":
            return setup_files(source, modules)
        entry = (
            'import "./meal.css";\n'
            'import {getYleumOrderCheckoutReceipt} from "@/lib/omnia/integration-client";\n'
            + source
        )
        files = setup_files(entry, modules)
        for path in (
            "next.config.ts", "tsconfig.json", "src/lib/omnia/integration-client.ts",
            "src/lib/omnia/app-profile-client.ts", "src/lib/omnia/identity-scope.ts",
            "src/lib/max/bridge.ts",
        ):
            files[path] = _template_file(path)
        return files

    monkeypatch.setattr(fixture, "connection_files", catalog_files)
    released, files, workspace, attempt, _root = await _held_repair(c, monkeypatch, "rebound")
    gap = max_source_completion_gap(fixture._PROMPT, files, portable=True)
    assert isinstance(gap, ManagedSourceGap)
    if case == "missing_css":
        assert gap.obligation is None and "reason missing_checkout_stylesheet" in gap
    else:
        assert gap.obligation is ManagedSourceObligation.CATALOG_RENDER
    owner = released.identity
    historical_files = {page: "historical source"}
    historical_digest = workspace_revision_digest(historical_files)
    prior = released.binding.model_copy(update={
        "fencing_epoch": 7, "source_sha256": historical_digest,
    })
    first_step = 80
    current_root = await c.session.get(GenerationRecoveryEffect, _root)
    assert current_root is not None
    dispatch = current_root.pre_binding["dispatch_v1"]
    historical = prior.model_copy(update={"fencing_epoch": 5})
    first, _ = await _catalog_closed_attempt(
        c, first_step, historical, attempt.request_sha256, dispatch,
    )
    first_attempt = first.attempt_id
    before_deadline = datetime.now(UTC) - timedelta(minutes=1)
    old_feedback = fixture._OLD_DETAIL
    plan = RetiredFinalizationPlan(
        proof=CompilerWitness(
            receipt_id=uuid4(), operation_id=uuid4(), run_id=owner.run_id,
            project_id=owner.project_id, user_id=owner.user_id, prompt_hash=owner.prompt_hash,
            binding=prior, comparison_sha256="d" * 64, dimension="fast_check", passed=True,
        ),
        attempt_id=first_attempt,
        repair_path="src/lib/send-fixture.ts", feedback=old_feedback,
        repair_deadline=before_deadline, segment_seq=2, maximum_repairs=prior_maximum,
    )
    completed = c.driver.context["completed"]
    completed["source_repair"] = pipeline.SourceRepairEntry(
        task=f"FINAL SOURCE CHECK FEEDBACK:\n{old_feedback}", baseline=historical_files,
        edit_deadline=retired_repair_edit_deadline(plan, None), targeted=True,
        response_budget=IncompleteResponseBudget(total=1),
    ).model_dump(mode="json")
    completed[pipeline._RETIRED_NATIVE_REPAIR_KEY] = pipeline._RetiredNativeRepair(
        version=1, attempt_id=first_attempt, logical_step_seq=first_step, binding=prior,
        checked_at=before_deadline - timedelta(seconds=420),
    ).model_dump(mode="json")
    completed[retirement._KEY] = plan.model_dump(mode="json")
    completed["finalization_progress"]["feedback"] = (
        ["Initial source repair", old_feedback] if prior_maximum == 2 else [old_feedback]
    )
    ref = await execution.store_execution_context(owner, c.driver.context, binding=released.binding)
    released = released.model_copy(update={
        "context_ref": ref,
        "execution": released.execution.model_copy(update={
            "execution_context_ref": ref,
            "budget": released.execution.budget.model_copy(update={"repair_calls": 2}),
        }),
        "clock": released.clock.model_copy(update={
            "segment_seq": 2, "window_ms": 420000,
            "segment_deadline": before_deadline, "segment_used_ms": 1_200_000,
        }),
    })
    attachment = execution.ExpiredProviderAttachment(
        version=1, run_id=owner.run_id, attempt_id=first_attempt, logical_step_seq=first_step,
        attempt_seq=released.attempt_seq, state_revision=released.state_revision,
        binding=historical,
        context_ref=ref, checkpoint=released.checkpoint, release_operation_id=uuid4(),
        release_fencing_epoch=6, lease_cycle_seq=1, clock_sha256="e" * 64,
    )
    c.driver.state = released
    c.run.agent_state = {
        **c.run.agent_state, execution.RECOVERY_KEY: released.model_dump(mode="json"),
        execution.EXPIRED_PROVIDER_ATTACHMENT_KEY: attachment.model_dump(mode="json"),
    }
    await c.session.commit()

    async with c.owned():
        assert await pipeline.rebind_continuation(c.driver, allow_expired_retirement=True) is None
        assert pipeline.expired_repair_proof_only(c.driver)
        pending_bootstrap = case in {"pending_bootstrap_pregrant", "pending_bootstrap_postgrant"}
        role = "bootstrap" if pending_bootstrap else "fast_check"
        if case in {
            "cold_pregrant", "pending_pregrant", "pending_conflict", "pending_bootstrap_pregrant",
        }:
            transient = AsyncMock(side_effect=TimeoutError("compiler acknowledgement lost"))
            coordinator = SimpleNamespace(fast_check=transient)
            if case == "cold_pregrant":
                with pytest.raises(execution.GenerationYielded):
                    await prepare_retired_finalization(c.driver, coordinator)
            else:
                await _catalog_role_before_intent(c, role)
            if case == "pending_conflict":
                state = c.driver.state
                c.session.add(GenerationRecoveryEffect(
                    effect_id=uuid4(), run_id=owner.run_id, project_id=owner.project_id,
                    user_id=owner.user_id, logical_step_seq=state.execution.logical_step_seq + 1,
                    effect_ordinal=1, kind="tool", state="inflight", input_digest="a" * 64,
                    attempt_seq=state.attempt_seq, claim_token=state.continuation_claim.token,
                ))
                await c.session.commit()
            binding = await _reacquire_catalog_candidate(
                c, workspace, monkeypatch, files, worker="fresh-compiler",
            )
            if case == "pending_conflict":
                with pytest.raises(execution.GenerationYielded):
                    await pipeline.rebind_continuation(c.driver, allow_expired_retirement=True)
                assert pipeline.pending_role(c.driver) is not None
                assert c.driver.state.execution.substep == "proof_wait"
                assert c.driver.state.execution.budget.repair_calls == 2
                return
            assert await pipeline.rebind_continuation(
                c.driver, allow_expired_retirement=True,
            ) is None
            assert c.driver.state.binding == binding
            assert pipeline.pending_role(c.driver) is None
            assert pipeline.expired_repair_proof_only(c.driver)
        fresh = await _compiler_after_retirement(
            c, workspace, with_bootstrap=pending_bootstrap, check_historical_witness=False,
        )
        before = c.driver.state
        compiler = AsyncMock(return_value=SimpleNamespace(id=fresh))
        coordinator = SimpleNamespace(retired_fast_check=compiler, fast_check=compiler)
        if case == "grant_ack":
            original_update = retirement.update_owned_clock

            async def lose_grant_ack(*args):
                driver = args[0]
                stale = driver.state
                await original_update(*args)
                driver.state = stale
                raise TimeoutError("grant acknowledgement lost")

            with monkeypatch.context() as patch:
                patch.setattr(retirement, "update_owned_clock", lose_grant_ack)
                with pytest.raises(execution.GenerationYielded) as yielded:
                    await prepare_retired_finalization(c.driver, coordinator)
            assert not pipeline.retain_retired_attachment_on_wait(c.driver, yielded.value)
            successor = retired_finalization_plan(c.driver)
        else:
            successor = await prepare_retired_finalization(c.driver, coordinator)
        if case == "existing_hook":
            assert successor == plan and c.driver.state == before
            await c.session.refresh(c.run)
            assert execution.EXPIRED_PROVIDER_ATTACHMENT_KEY in c.run.agent_state
            assert retirement.CATALOG_REPAIR_RESUME_KEY not in c.run.agent_state
            return
        assert successor.repair_path == page
        if case == "missing_css":
            assert "Remove the missing relative stylesheet import" in successor.feedback
            assert files[page].startswith('import "./meal.css";')
        else:
            assert successor.feedback == gap
        assert successor.attempt_id == attempt.attempt_id != first_attempt
        assert successor.maximum_repairs == prior_maximum + 1
        assert c.driver.state.identity == owner
        assert c.driver.state.execution.budget == before.execution.budget
        assert c.driver.state.active_work_ms == before.active_work_ms
        assert c.driver.state.clock.segment_seq == before.clock.segment_seq + 1
        assert c.driver.restore_frame("native") is None
        assert attempt.provider_call_id is None
        assert await prepare_retired_finalization(c.driver, coordinator) == successor
        assert compiler.await_count == 1
        await c.session.refresh(c.run)
        assert execution.EXPIRED_PROVIDER_ATTACHMENT_KEY not in c.run.agent_state
        archived = c.run.agent_state[retirement.CATALOG_REPAIR_RESUME_KEY]
        assert archived["predecessor_attachment"] == attachment.model_dump(mode="json")
        if case == "grant_ack":
            stopped, now = await _release_catalog_candidate(c, workspace)
            async with c.factory() as session:
                claim = await execution.claim_continuation(
                    session, run_id=owner.run_id, expected_attempt=stopped.attempt_seq,
                    worker_id="grant-ack-cold", now=max(now, stopped.next_check_at),
                )
                assert claim is not None and claim.run_id == owner.run_id
                await session.commit()
            await c.session.refresh(c.run)
            state = execution.recovery_state(c.run)
            assert state.execution.budget.repair_calls == 2
            assert state.clock.segment_seq == successor.segment_seq
            assert state.clock.segment_deadline <= successor.repair_deadline
            assert attempt.provider_call_id is None
            return
        assert await restore_retired_finalization_cursor(c.driver) == "pre_entry"
        fresh_entry = await pipeline.prepare_source_repair(
            c.driver, task=f"FINAL SOURCE CHECK FEEDBACK:\n{successor.feedback}", baseline=files,
            edit_deadline=retired_repair_edit_deadline(successor, None), targeted=True,
            response_budget=IncompleteResponseBudget(total=3),
        )
        assert await restore_retired_finalization_cursor(c.driver) == "repair"
        assert c.driver.state.execution.budget.repair_calls == 3
        again = await pipeline.prepare_source_repair(
            c.driver, task="ignored reentry", baseline={}, edit_deadline=None, targeted=False,
            response_budget=IncompleteResponseBudget(),
        )
        assert again == fresh_entry and c.driver.state.execution.budget.repair_calls == 3
        if case in {"pending_postgrant", "pending_bootstrap_postgrant"}:
            await _catalog_role_before_intent(c, role)
            binding = await _reacquire_catalog_candidate(
                c, workspace, monkeypatch, files, worker="pending-postgrant",
            )
            assert await pipeline.rebind_continuation(
                c.driver, allow_expired_retirement=True,
            ) is None
            assert c.driver.state.binding == binding
            assert pipeline.pending_role(c.driver) is None
            assert c.driver.state.execution.budget.repair_calls == 3
            assert await prepare_retired_finalization(c.driver, coordinator) == successor
            assert pipeline.pending_source_repair(c.driver) == fresh_entry
            assert c.driver.state.clock.segment_deadline <= successor.repair_deadline
            if pending_bootstrap:
                reset_step = c.driver.state.execution.logical_step_seq
                await _compiler_after_retirement(
                    c, workspace, with_bootstrap=True, check_historical_witness=False,
                )
                assert c.driver.state.execution.logical_step_seq == reset_step + 2
        if case == "third_no_wire":
            active = c.driver.state
            step = active.execution.logical_step_seq + 1
            third, root = await _catalog_closed_attempt(
                c, step, active.binding, attempt.request_sha256, dispatch,
            )
            await c.session.commit()
            await c.driver.save(
                active.execution.model_copy(update={
                    "substep": "response_wait", "logical_step_seq": step,
                    "provider_effect_id": root, "pending_effect_id": root,
                }),
                context={**c.driver.context, "active_frame": {
                    "version": 1, "kind": "native", "value": {
                        "response_budget": fresh_entry.response_budget.model_dump(mode="json"),
                    },
                }},
            )
            await _reacquire_catalog_candidate(
                c, workspace, monkeypatch, files, worker="third-cold",
            )
            assert await pipeline.rebind_continuation(
                c.driver, allow_expired_retirement=True,
            ) is None
            assert pipeline.expired_repair_proof_only(c.driver)
            assert c.driver.state.execution.budget.repair_calls == 3
            assert c.driver.state.identity == owner
            assert c.driver.state.execution.logical_step_seq == step
            assert c.driver.state.execution.provider_effect_id is None
            assert c.driver.restore_frame("native") is None and third.provider_call_id is None
            assert await prepare_retired_finalization(c.driver, coordinator) == successor
            assert compiler.await_count == 1
            assert await c.session.scalar(select(func.count(ProviderCall.id)).where(
                ProviderCall.run_id == owner.run_id,
            )) == 0
            return


        # A cold worker uses only the current ordinary release, never the archived
        # generation lease or either of the original two provider frames.
        active = c.driver.state
        stopped, now = await _release_catalog_candidate(c, workspace)
        state = stopped
        for worker in ("cold-one", "cold-two"):
            async with c.factory() as session:
                claim = await execution.claim_continuation(
                    session, run_id=owner.run_id, expected_attempt=state.attempt_seq,
                    worker_id=worker, now=now,
                )
                assert claim is not None and claim.run_id == owner.run_id
                await session.commit()
            await c.session.refresh(c.run)
            state = execution.recovery_state(c.run)
            assert state.clock.segment_deadline <= successor.repair_deadline
            assert state.execution.budget.repair_calls == 3
            assert state.execution.logical_step_seq == active.execution.logical_step_seq
            context = await execution.load_execution_context(
                owner, state.context_ref, binding=state.binding,
            )
            c.driver = execution.ExecutionDriver(c.factory, state, context, c.driver.snapshot)
            assert c.driver.restore_frame("native") is None
            assert pipeline.pending_source_repair(c.driver) == fresh_entry
            assert await prepare_retired_finalization(c.driver, coordinator) == successor
        assert await c.session.scalar(select(func.count(ProviderCall.id)).where(
            ProviderCall.run_id == owner.run_id,
        )) == 0


async def _release_catalog_candidate(c, workspace):
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cells import _canonical_operation_envelope

    await c.session.refresh(c.run)
    active = c.driver.state
    fence = active.binding.fencing_epoch + 1
    now = datetime.now(UTC)
    release_id = uuid4()
    cycle = active.execution.lease_cycle_seq
    request, digest = _canonical_operation_envelope(
        workspace.id, c.run.id, "release", {"require_environment_proof": {
            "source_sha256": active.binding.source_sha256,
            "environment_sha256": active.binding.environment_sha256,
        }},
    )
    c.session.add(ProjectCellOperation(
        id=release_id, workspace_id=workspace.id, generation_run_id=c.run.id,
        kind="release", status="completed", fencing_epoch=fence,
        idempotency_key=f"generation:{c.run.id}:release:fixture:cycle:{cycle}",
        request_payload=request, request_digest=digest, finished_at=now,
        result_payload=ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", fencing_epoch=fence,
            provider_ref="fixture", checkpoint_ref=None,
            has_workspace=True, has_agent_home=True, has_postgres=True, has_redis=True,
        ).to_wire_json(),
    ))
    workspace.fencing_epoch = fence
    workspace.generation_run_id = None
    stopped = active.model_copy(update={
        "execution": active.execution.model_copy(update={
            "physical_state": "released", "release_operation_id": release_id,
            "control_fencing_epoch": fence,
        }),
        "clock": active.clock.model_copy(update={"interval_open": False}),
    })
    c.run.agent_state = {
        **c.run.agent_state, execution.RECOVERY_KEY: stopped.model_dump(mode="json"),
    }
    await c.session.commit()

    return stopped, now


async def _reacquire_catalog_candidate(c, workspace, monkeypatch, files, *, worker):
    from yleum_api.services.generation import recovery_requirements, recovery_store
    from yleum_api.services.orchestrator_client import (
        ProjectCellEnvironmentRecoveryProof,
        ProjectCellWorkspaceIdentity,
    )

    owner = c.driver.state.identity
    stopped, now = await _release_catalog_candidate(c, workspace)
    async with c.factory() as session:
        claim = await execution.claim_continuation(
            session, run_id=owner.run_id, expected_attempt=stopped.attempt_seq,
            worker_id=worker, now=max(now, stopped.next_check_at or now),
        )
        assert claim is not None and claim.run_id == owner.run_id
        await session.commit()
    await c.session.refresh(c.run)
    claimed = execution.recovery_state(c.run)
    binding = claimed.binding.model_copy(update={
        "fencing_epoch": workspace.fencing_epoch + 1, "environment_sha256": "f" * 64,
    })
    workspace.fencing_epoch = binding.fencing_epoch
    workspace.generation_run_id = owner.run_id
    await c.session.commit()

    async def later_identity(workspace_id, *, generation_run_id, fencing_epoch):
        assert (workspace_id, generation_run_id, fencing_epoch) == (
            workspace.id, owner.run_id, binding.fencing_epoch,
        )
        return ProjectCellWorkspaceIdentity(
            binding.source_sha256, "d" * 64, binding.schema_sha256,
            "a" * 64, binding.environment_sha256, "f" * 64,
        )

    monkeypatch.setattr(recovery_store, "project_cell_agent_identity", later_identity)
    monkeypatch.setattr(recovery_requirements, "project_cell_agent_identity", later_identity)
    context = await execution.load_execution_context(
        owner, claimed.context_ref, binding=claimed.binding,
    )
    c.driver = execution.ExecutionDriver(
        c.factory, claimed, context, AsyncMock(return_value=(binding, files)),
        runtime_control=(workspace.id, binding.fencing_epoch),
        prove_environment_recovery=AsyncMock(return_value=ProjectCellEnvironmentRecoveryProof(
            version=1, workspace_id=workspace.id, project_id=owner.project_id,
            owner_id=owner.user_id, generation_run_id=owner.run_id,
            release_operation_id=claimed.execution.release_operation_id,
            previous_fencing_epoch=binding.fencing_epoch - 2,
            release_fencing_epoch=binding.fencing_epoch - 1,
            fencing_epoch=binding.fencing_epoch, source_sha256=binding.source_sha256,
            manifest_sha256="a" * 64,
            previous_environment_sha256=claimed.binding.environment_sha256,
            environment_sha256=binding.environment_sha256, artifact_sha256="1" * 64,
        )),
    )
    return binding


async def _catalog_closed_attempt(c, step, binding, digest, dispatch):
    state = c.driver.state
    owner = state.identity
    root = provider_root_effect_id(owner.run_id, step)
    wire = uuid5(root, "wire-v1:0:0")
    attempt_id = uuid5(root, "provider-attempt-v1")
    owned = dict(run_id=owner.run_id, project_id=owner.project_id, user_id=owner.user_id)
    attempt = GenerationProviderAttempt(
        **owned, attempt_id=attempt_id, dispatch_token=uuid4(), prompt_hash=owner.prompt_hash,
        request_sha256=digest, phase="closed", terminal_reason="expired_before_dispatch",
    )
    c.session.add(attempt)
    await c.session.flush()
    receipt = {
        "version": 1, "kind": "gateway_expired_before_dispatch", "attempt_id": str(attempt_id),
        "root_effect_id": str(root), "wire_effect_id": str(wire),
        "source_sha256": binding.source_sha256,
        "request_sha256": digest, "deadline": dispatch["deadline"],
    }
    for ordinal, effect in enumerate((root, wire)):
        pre_binding = (
            {"source": binding.model_dump(mode="json"), "dispatch_v1": dispatch}
            if ordinal == 0 else {"dispatch_v1": {
                **dispatch, "root_effect_id": str(root), "provider_effect_id": str(root),
                "attempt_id": str(attempt_id), "parent_attempt_id": None,
                "request_sha256": digest, "wire_attempt": 0,
            }}
        )
        c.session.add(GenerationRecoveryEffect(
            **owned, effect_id=effect, logical_step_seq=step, effect_ordinal=ordinal,
            kind="provider" if ordinal == 0 else "progress",
            parent_effect_id=root if ordinal else None,
            provider_attempt_id=None if ordinal else attempt_id,
            input_digest=digest, state="confirmed", receipt_ref=receipt,
            pre_binding=pre_binding, attempt_seq=state.attempt_seq,
            claim_token=state.continuation_claim.token,
        ))
    return attempt, root


async def _catalog_role_before_intent(c, role):
    operation = uuid4()
    with pytest.raises(execution.GenerationYielded):
        await pipeline.run_durable_role(
            c.driver, role=role, operation_id=operation,
            invoke=AsyncMock(side_effect=TimeoutError("before physical command intent")),
        )
    state = c.driver.state
    assert state.execution.substep == "proof_wait"
    pending = pipeline.pending_role(c.driver)
    assert (pending.operation_id, pending.role) == (operation, role)
    assert await c.session.get(
        GenerationRecoveryEffect, uuid5(state.execution.pending_effect_id, "physical:0"),
    ) is None
