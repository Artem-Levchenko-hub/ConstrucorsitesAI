"""Stopped source candidates restore safely even when the draft runtime is absent."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import agent_recovery
from yleum_api.services.generation.contracts import GenerationRuntime, SourceBaseline


@pytest.fixture
def stopped_recovery(monkeypatch):
    baseline = {"src/app/page.tsx": "accepted", "empty.ts": ""}
    tree = {"src/app/page.tsx": "broken", "empty.ts": "changed", "new.ts": "partial"}
    trace = []
    fault = {"build": None, "patch": None}

    async def stage(writes, deletes):
        if fault["patch"]:
            raise RuntimeError(fault["patch"])
        tree.update(writes)
        for path in deletes:
            tree.pop(path, None)
        trace.append("restore")

    async def sync():
        trace.append("preview")
        return SimpleNamespace(failure="draft runtime is not running", runtime_absent=True)

    async def build():
        trace.append("source_check")
        assert tree == baseline
        if fault["build"] == "red":
            return {"ok": False, "detail": "source check failed"}
        if fault["build"]:
            raise RuntimeError(fault["build"])
        return {"ok": True}

    async def emit(*args):
        trace.append("rollback_event")

    async def restore_runtime(role, operation_id):
        assert role.value == "restore_runtime"
        result = await build()
        if result["ok"]:
            trace.append("compile_and_restart")
        return SimpleNamespace(ok=result["ok"], redacted_detail=result.get("detail", ""))

    async def render():
        return dict(baseline)

    monkeypatch.setattr(agent_recovery.repo_svc, "read_files", lambda *_: dict(baseline))
    kwargs = dict(
        _agent_res=AgentResult(
            done=False, summary="TS2322 WorkoutClient.tsx:220", files=dict(tree),
            steps=18, stop_reason="max_steps",
        ),
        _max_has_generated_snapshot=True,
        _max_seed_files={},
        _provider_failure=None,
        _render_current_max_starter_files=render,
        baseline=SourceBaseline(None, "accepted-sha", baseline),
        ids=SimpleNamespace(project_id=uuid4()),
        project_info=SimpleNamespace(slug="recovery"),
        runtime=GenerationRuntime(
            handle=SimpleNamespace(stage_patch=stage, sync_preview=sync, run_role=restore_runtime),
            coordinator=object(),
        ),
        operations=SimpleNamespace(probe_build=build, emit=emit),
    )
    return kwargs, tree, baseline, trace, fault


async def test_stopped_candidate_restores_without_preview_and_preserves_cause(stopped_recovery):
    kwargs, tree, baseline, trace, _ = stopped_recovery
    with pytest.raises(RuntimeError, match=r"max_steps.*TS2322") as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert "rolled_back" in str(error.value)
    assert tree == baseline
    assert trace == ["restore", "source_check", "compile_and_restart", "rollback_event"]
    assert "no source changes" not in str(error.value)


@pytest.mark.parametrize("fault_at", ["build", "patch"])
async def test_rollback_failure_does_not_resume_stopped_candidate(stopped_recovery, fault_at):
    kwargs, tree, baseline, trace, fault = stopped_recovery
    fault[fault_at] = "source store unavailable"
    with pytest.raises(RuntimeError, match=r"max_steps.*TS2322") as error:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert "rollback_unverified" in str(error.value)
    assert "source store unavailable" in str(error.value.__cause__)
    assert "preview" not in trace
    if fault_at == "build":
        assert tree == baseline


async def test_red_restored_source_is_not_claimed_verified(stopped_recovery):
    kwargs, tree, baseline, trace, fault = stopped_recovery
    fault["build"] = "red"
    with pytest.raises(RuntimeError, match=r"max_steps.*rollback_unverified"):
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert tree == baseline
    assert trace == ["restore", "source_check"]


async def test_first_generation_restores_only_core_without_preview(stopped_recovery):
    kwargs, tree, baseline, trace, _ = stopped_recovery
    baseline.pop("src/app/page.tsx")
    kwargs["_max_has_generated_snapshot"] = False
    with pytest.raises(RuntimeError, match=r"max_steps.*rolled_back"):
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert tree == {"empty.ts": ""}
    assert trace == ["restore", "source_check", "compile_and_restart", "rollback_event"]


async def test_provider_failure_keeps_precedence_after_source_restoration(stopped_recovery):
    kwargs, tree, baseline, trace, _ = stopped_recovery
    kwargs["_provider_failure"] = "Provider response rejected (output_limit)"
    with pytest.raises(RuntimeError, match="output_limit"):
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert tree == baseline
    assert trace == ["restore", "source_check", "compile_and_restart", "rollback_event"]


async def test_provider_http_rejection_restores_source_and_keeps_public_failure(stopped_recovery):
    from yleum_api.services.generation.agent_generation import _primary_provider_failure
    from yleum_api.services.generation_failure import failure_for_error

    kwargs, tree, baseline, trace, _ = stopped_recovery
    result = kwargs["_agent_res"]
    result.stop_reason = "provider_error"
    result.summary = "provider_http_401: Автоматический повтор остановлен."
    kwargs["_provider_failure"] = _primary_provider_failure(result)
    with pytest.raises(RuntimeError, match="provider_http_401") as failed:
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert tree == baseline
    assert trace == ["restore", "source_check", "compile_and_restart", "rollback_event"]
    public = failure_for_error(failed.value)
    assert public.code == "provider_access" and not public.retryable


@pytest.mark.parametrize("done,needs_finalization", [(True, False), (False, True)])
async def test_verified_candidate_keeps_normal_finalization_path(
    stopped_recovery, done, needs_finalization,
):
    kwargs, tree, _, trace, _ = stopped_recovery
    candidate = kwargs["_agent_res"]
    candidate.done = done
    candidate.needs_finalization = needs_finalization
    result, _, _, _ = await agent_recovery.recover_stopped_candidate(**kwargs)
    assert result is candidate and result.files == tree
    assert trace == []


async def test_stopped_rollback_replaces_running_candidate_after_restoring_source(stopped_recovery):
    kwargs, tree, baseline, trace, _ = stopped_recovery
    served = {"version": "failed candidate"}

    async def restore_runtime(role, operation_id):
        assert role.value == "restore_runtime"
        assert tree == baseline
        served["version"] = "accepted"
        trace.append("compile_and_restart")
        return SimpleNamespace(ok=True, redacted_detail="restored runtime ready")

    kwargs["runtime"].handle.run_role = restore_runtime
    with pytest.raises(RuntimeError, match=r"max_steps.*rolled_back"):
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert served["version"] == "accepted"
    assert trace == ["restore", "compile_and_restart", "rollback_event"]


async def test_green_typecheck_without_runtime_restore_cannot_verify_rollback(stopped_recovery):
    kwargs, _, _, trace, _ = stopped_recovery
    kwargs["runtime"].handle.run_role = None
    with pytest.raises(RuntimeError, match=r"max_steps.*rollback_unverified"):
        await agent_recovery.recover_stopped_candidate(**kwargs)
    assert "rollback_event" not in trace
