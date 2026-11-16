"""A sealed historical request can earn one new exact source-repair segment."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid5

import pytest
from sqlalchemy import func, select

from tests import test_generation_retired_native_repair as fixture
from tests.test_generation_retired_native_repair import _compiler_after_retirement, _held_repair
from tests.test_generation_retired_native_repair import clock_owner as clock_owner
from tests.test_generation_retired_native_repair import response_schema as response_schema
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.services.generation import expired_native_finalization as retirement
from yleum_api.services.generation import recovery_execution as execution
from yleum_api.services.generation import recovery_pipeline as pipeline
from yleum_api.services.generation.continuation import SourceNoveltyTracker
from yleum_api.services.generation.expired_native_finalization import (
    prepare_retired_finalization,
    restore_retired_finalization_cursor,
    retired_finalization_plan,
    retired_repair_edit_deadline,
)
from yleum_api.services.generation.recovery_contract import IncompleteResponseBudget
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
