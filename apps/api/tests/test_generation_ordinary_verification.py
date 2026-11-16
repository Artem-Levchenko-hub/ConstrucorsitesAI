from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.test_generation_worker import _queued_dispatch
from tests.test_max_finalization import _new_harness
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellCandidate, ProjectCellWorkspace
from yleum_api.services.generation import lifecycle, source_checkpoint, supervisor
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.generation_failure import public_generation_failure
from yleum_api.services.generation_runs import prompt_hash
from yleum_api.services.max_runtime_probe import MaxRuntimeProbe
from yleum_api.services.project_cell_executor import ProjectCellCommandRole
from yleum_api.workers import generation


class WorkerStopped(BaseException):
    pass


@pytest.mark.parametrize("conflict", [None, "stale_checkpoint", "known_red"])
async def test_ordinary_worker_preserves_post_build_draft_after_verifier_stops(
    db_session, test_engine, monkeypatch, conflict,
):
    run = await _queued_dispatch(db_session)
    run.execution_backend = "worker"
    run.execution_started_at = run.started_at = datetime.now(UTC)
    run.response_mode = "build"
    run.prompt_hash = prompt_hash("Собери приложение")
    harness = await _new_harness(db_session, test_engine, admitted_run=run)
    original = harness.coordinator.executor
    identity = await original.current_identity()
    ids = GenerationIds(
        run.id, run.project_id, run.user_id, run.user_message_id, run.assistant_message_id,
    )

    async def checkpoint(files, proof_key):
        await source_checkpoint.persist_checkpoint(
            factory=harness.coordinator.session_factory,
            ids=ids,
            baseline=SourceBaseline(None, None, {}),
            files=files,
            original_prompt="Собери приложение",
            verification_proof_key=proof_key,
        )

    async def stop_during_read(_proof_key):
        if conflict == "known_red":
            return MaxRuntimeProbe(False, "GET / (HTTP 500)", failure_kind="source")
        raise WorkerStopped

    async def stop_after_verdict(*_args, **_kwargs):
        raise WorkerStopped

    async def build_with_generated_file(role, operation_id):
        if role is ProjectCellCommandRole.FULL_BUILD:
            harness.files["next-env.d.ts"] = "// emitted by the full build"
        return await original.run_role(role, operation_id)

    harness.coordinator.checkpoint_verification_candidate = checkpoint
    harness.coordinator.executor = replace(
        original, runtime_probe=stop_during_read, run_role=build_with_generated_file,
    )
    if conflict == "known_red":
        monkeypatch.setattr(harness.coordinator, "_failed", stop_after_verdict)
    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "_emergency_error", AsyncMock())

    async def unexpected(*_args, **_kwargs):
        pytest.fail("the orphan fallback must not replay the provider")

    monkeypatch.setattr(lifecycle, "_process_prompt", unexpected)
    with pytest.raises(WorkerStopped):
        await harness.coordinator.finalize(files=harness.files, prompt="Собери приложение")

    await db_session.refresh(run)
    checkpoint_state = run.agent_state.get("source_checkpoint")
    assert checkpoint_state is not None
    digest = checkpoint_state["digest"]
    data = await source_checkpoint.load_checkpoint_object(
        run.project_id, run.id,
        object_key=source_checkpoint._object_key(run.project_id, run.id, digest),
        digest=digest,
    )
    assert data is not None
    draft = source_checkpoint.decode_checkpoint(
        data, project_id=run.project_id, user_id=run.user_id, run_id=run.id, baseline_sha=None,
    )
    assert draft is not None
    assert draft["files"] == source_checkpoint.checkpoint_sources(harness.files)
    if conflict == "stale_checkpoint":
        run.agent_state = {
            **run.agent_state,
            "source_checkpoint": {**checkpoint_state, "verification_proof_key": "f" * 64},
        }
        await db_session.commit()

    await generation.execute_dispatch(run.id)

    db_session.expire_all()
    current = await db_session.get(GenerationRun, ids.run_id)
    project = await db_session.get(Project, ids.project_id)
    workspace = await db_session.get(ProjectCellWorkspace, identity.workspace_id)
    assert current is not None and current.status == "failed"
    assert public_generation_failure(current).code == (
        "verification" if conflict else "runtime_unavailable"
    )
    assert current.agent_state["source_checkpoint"]["digest"] == checkpoint_state["digest"]
    assert project is not None and project.current_snapshot_id is None
    assert workspace is not None and workspace.fencing_epoch == identity.fencing_epoch
    assert workspace.generation_run_id == ids.run_id
    assert await db_session.scalar(select(ProjectCellCandidate)) is None
    assert harness.roles.count(ProjectCellCommandRole.FULL_BUILD) == 1
