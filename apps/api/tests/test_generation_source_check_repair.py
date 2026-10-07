"""A last-turn source test failure can be repaired before safe rollback."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.core.config import get_settings
from yleum_api.services import agent_native
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_finalization, agent_recovery
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.max_finalization import MaxFinalizationStatus
from yleum_api.services.project_cell_errors import ProjectCellInfrastructureError

FAILURE = (
    "[typecheck] clean\n[targeted-test]\nTAP version 13\n"
    + "ok - existing test\n" * 100
    + "not ok 8 - priority filter\n  location: '/workspace/tests/qa-tasks.test.mjs:113:1'\n"
    + "  failureType: 'testCodeFailure'\n"
    + "  error: 'TasksScreen filtering pipeline not found'\n  code: 'ERR_ASSERTION'\n"
)
REAL_RESTORE = agent_recovery._restore_touched_tree


@pytest.fixture
def source_check(monkeypatch):
    settings = get_settings().model_copy(
        update={"use_native_agent": True, "use_max_finalization_coordinator": True}
    )
    monkeypatch.setattr(agent_recovery, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_native, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "yleum_api.services.max_generation_contract.max_source_completion_gap",
        lambda *_args, **_kwargs: None,
    )
    workspace = {"src/app/page.tsx": "candidate", "tests/product.test.mjs": "keep assertions"}
    before = dict(workspace)
    calls = []
    mode = {
        "write": True,
        "green": True,
        "extra": False,
        "provider_error": False,
        "build_error": None,
    }

    async def snapshot():
        return dict(workspace)

    async def provider(*args, **kwargs):
        calls.append(args[2])
        if len(calls) > 1 and mode["provider_error"]:
            raise RuntimeError("synthetic provider error")
        if len(calls) == 1 and mode["write"]:
            name, arguments = (
                "write_file",
                {"path": "src/app/page.tsx", "content": "repaired filter"},
            )
        elif mode["write"]:
            name, arguments = "done", {"summary": "repaired"}
        else:
            name, arguments = "build", {}
        return {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": str(len(calls)), "name": name, "input": arguments},
            ],
        }

    async def execute(action):
        if action.name == "write_file":
            workspace[action.path] = action.args["content"]
            if mode["extra"]:
                workspace["src/lib/new-filter.ts"] = "new candidate helper"
        if action.name == "build" and mode["build_error"] is not None:
            raise mode["build_error"]
        return {"ok": mode["green"], "detail": "clean" if mode["green"] else FAILURE}

    async def probe():
        return {"ok": mode["green"] and workspace != before, "detail": FAILURE}

    monkeypatch.setattr(agent_native, "_call_messages", provider)

    async def restore(**kwargs):
        for path in kwargs["touched_files"]:
            if path in before:
                workspace[path] = before[path]
            else:
                workspace.pop(path, None)
        return {"ok": True}

    rollback = AsyncMock(side_effect=restore)
    monkeypatch.setattr(agent_recovery, "_restore_touched_tree", rollback)
    coordinator = SimpleNamespace(
        fast_check=AsyncMock(
            return_value=SimpleNamespace(
                outcome="red",
                dimension="fast_check",
                redacted_detail=FAILURE,
            )
        ),
        source_edit_deadline=AsyncMock(return_value=datetime.now(UTC) + timedelta(seconds=300)),
    )
    runtime = SimpleNamespace(
        handle=SimpleNamespace(snapshot_files=snapshot, export_files=snapshot),
        coordinator=coordinator,
    )
    kwargs = dict(
        _agent_res=AgentResult(
            done=False, summary="handoff", files=before, steps=18, needs_finalization=True
        ),
        _first_max_without_product=False,
        _render_current_max_starter_files=AsyncMock(),
        _rt_error="",
        _runtime_ok=True,
        _tc_error="test failed",
        _typecheck_ok=False,
        accumulated="",
        baseline=SourceBaseline(
            uuid4(),
            "baseline",
            {"src/app/page.tsx": "old", "tests/product.test.mjs": "keep assertions"},
        ),
        files=before,
        ids=GenerationIds(*(uuid4() for _ in range(5))),
        project_info=SimpleNamespace(slug="owned-test"),
        runtime=runtime,
        operations=SimpleNamespace(execute=execute, probe_build=probe, emit=AsyncMock()),
        plan=SimpleNamespace(
            stack_guide="MAX PLATFORM CORE CONTRACT",
            skills=None,
            steps=4,
            user="Add priority filter",
        ),
        prompt_text="Add priority filter",
        is_free=False,
        _max_shell_enabled=False,
    )
    return kwargs, workspace, calls, rollback, mode


async def test_last_turn_assertion_reaches_real_native_repair_before_rollback(source_check):
    kwargs, _workspace, calls, rollback, _ = source_check
    result = await agent_recovery.recover_rejected_candidate(**kwargs)
    assert not result.candidate_failed
    assert result.files["src/app/page.tsx"] == "repaired filter"
    assert result.files["tests/product.test.mjs"] == "keep assertions"
    assert "TasksScreen filtering pipeline not found" in str(calls[0])
    assert len(calls) == 2
    assert not rollback.called


@pytest.mark.parametrize(
    "detail,dimension",
    [
        ("service web readiness failed: 502", "fast_check"),
        ("proof identity mismatch", "fast_check"),
        (FAILURE, "bootstrap"),
        ("provider_error ERR_ASSERTION", "fast_check"),
    ],
)
async def test_infrastructure_or_wrong_proof_does_not_repair(source_check, detail, dimension):
    kwargs, _, calls, rollback, _ = source_check
    proof = kwargs["runtime"].coordinator.fast_check.return_value
    proof.redacted_detail, proof.dimension = detail, dimension
    result = await agent_recovery.recover_rejected_candidate(**kwargs)
    assert result.candidate_failed and not result.files
    assert not calls and rollback.await_count == 1


@pytest.mark.parametrize("write,green", [(False, True), (True, False)])
async def test_unchanged_or_still_red_repairs_roll_back_once(source_check, write, green):
    kwargs, _, calls, rollback, mode = source_check
    mode.update(write=write, green=green)
    result = await agent_recovery.recover_rejected_candidate(**kwargs)
    assert result.candidate_failed and not result.files
    assert rollback.await_count == 1
    assert len(calls) <= kwargs["plan"].steps
    assert kwargs["runtime"].coordinator.source_edit_deadline.await_count == 1


def use_real_restore(monkeypatch, kwargs, workspace):
    monkeypatch.setattr(agent_recovery, "_restore_touched_tree", REAL_RESTORE)
    monkeypatch.setattr(
        agent_recovery.repo_svc,
        "read_files",
        lambda *_: dict(kwargs["baseline"].files),
    )

    async def apply(*, files, project_cell_handle, empty_files=()):
        for path, content in files.items():
            if content or path in empty_files:
                workspace[path] = content
            else:
                workspace.pop(path, None)

    monkeypatch.setattr(agent_recovery, "_apply_project_cell_preview_files", apply)


async def test_failed_repair_rolls_back_new_source_paths(source_check, monkeypatch):
    kwargs, workspace, _, _, mode = source_check
    use_real_restore(monkeypatch, kwargs, workspace)
    mode.update(extra=True, green=False)
    result = await agent_recovery.recover_rejected_candidate(**kwargs)
    assert result.candidate_failed and not result.files
    assert "src/lib/new-filter.ts" not in workspace
    assert workspace == kwargs["baseline"].files


async def test_partial_write_then_provider_error_restores_trusted_source(source_check, monkeypatch):
    kwargs, workspace, _, _, mode = source_check
    use_real_restore(monkeypatch, kwargs, workspace)
    mode.update(extra=True, provider_error=True)
    with pytest.raises(RuntimeError, match="PROVIDER_UNAVAILABLE"):
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert workspace == kwargs["baseline"].files


async def test_cleanup_failure_does_not_mask_primary_provider_failure(source_check):
    kwargs, _, _, rollback, mode = source_check
    mode.update(extra=True, provider_error=True)
    rollback.side_effect = RuntimeError("cleanup failed")
    with pytest.raises(RuntimeError, match="PROVIDER_UNAVAILABLE"):
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert rollback.await_count == 1


async def test_timeout_cleanup_denied_by_fence_preserves_primary_timeout(source_check):
    kwargs, _, _, rollback, mode = source_check
    primary = TimeoutError("source check expired")
    mode.update(extra=True, build_error=primary)
    rollback.side_effect = ProjectCellInfrastructureError("protected_environment_recovery_required")
    with pytest.raises(TimeoutError) as caught:
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert caught.value is primary
    assert rollback.await_count == 1


async def test_trusted_compiler_failure_uses_same_bounded_repair(source_check):
    kwargs, _, calls, rollback, _ = source_check
    detail = "[typecheck]\nsrc/app/page.tsx(12,3): error TS2322: wrong prop"
    kwargs["runtime"].coordinator.fast_check.return_value.redacted_detail = detail
    result = await agent_recovery.recover_rejected_candidate(**kwargs)
    assert not result.candidate_failed
    assert detail in calls[0][0]["content"]
    assert len(calls) == 2 and not rollback.called


async def test_partial_write_timeout_restores_under_the_existing_fence(source_check, monkeypatch):
    kwargs, workspace, _, _, mode = source_check
    use_real_restore(monkeypatch, kwargs, workspace)
    mode.update(extra=True, build_error=TimeoutError("source check expired"))
    with pytest.raises(TimeoutError, match="source check expired"):
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert workspace == kwargs["baseline"].files


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError("ownership lost"),
        ProjectCellInfrastructureError("protected_environment_recovery_required"),
    ],
)
async def test_partial_cancel_or_terminal_fence_never_writes_cleanup(source_check, error):
    kwargs, workspace, calls, rollback, mode = source_check
    mode.update(extra=True, build_error=error)
    with pytest.raises(type(error)) as caught:
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert caught.value is error
    assert not rollback.called
    assert len(calls) == 1
    # Unaccepted draft remains: restoring it through a lost fence is forbidden.
    assert workspace["src/lib/new-filter.ts"] == "new candidate helper"
    assert kwargs["runtime"].coordinator.fast_check.await_count == 1


async def test_green_source_repair_still_requires_final_coordinator_proof(source_check):
    kwargs, _, calls, _, _ = source_check
    recovered = await agent_recovery.recover_rejected_candidate(**kwargs)
    runtime = kwargs["runtime"]
    runtime.handle.prove_restoration_adaptation = None
    runtime.coordinator.finalize_with_repair = AsyncMock(
        return_value=SimpleNamespace(
            status=MaxFinalizationStatus.CANCELLED,
        )
    )
    with pytest.raises(asyncio.CancelledError):
        await agent_finalization.finalize_max_candidate(
            _is_edit=True,
            _max_has_generated_snapshot=True,
            _max_shell_enabled=False,
            accumulated="",
            baseline=kwargs["baseline"],
            files=recovered.files,
            ids=kwargs["ids"],
            is_free=False,
            prompt_text=kwargs["prompt_text"],
            runtime=runtime,
            plan=kwargs["plan"],
            operations=kwargs["operations"],
        )
    assert runtime.coordinator.finalize_with_repair.await_count == 1
    assert len(calls) == 2


@pytest.mark.parametrize("error", [asyncio.CancelledError, TimeoutError])
async def test_cancel_and_deadline_propagate_without_provider_retry(source_check, error):
    kwargs, _, calls, rollback, _ = source_check
    kwargs["runtime"].coordinator.source_edit_deadline.side_effect = error
    with pytest.raises(error):
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert not calls and not rollback.called


async def test_expired_repair_window_never_calls_provider(source_check):
    kwargs, _, calls, rollback, _ = source_check
    kwargs["runtime"].coordinator.source_edit_deadline.return_value = datetime.now(UTC)
    with pytest.raises(TimeoutError):
        await agent_recovery.recover_rejected_candidate(**kwargs)
    assert not calls and not rollback.called
