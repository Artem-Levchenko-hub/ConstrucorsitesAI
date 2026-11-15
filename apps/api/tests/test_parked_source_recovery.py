from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_generation_recovery_deadlines import _clock
from tests.test_generation_recovery_execution import _bootstrap_wait
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services.agent_builder import AgentResult
from yleum_api.services.generation import (
    parked_source_recovery as parked,
)
from yleum_api.services.generation import (
    recovery_execution,
    recovery_quiescence,
    source_checkpoint,
)
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    IncompleteResponseBudget,
    RecoveryStateV3,
    decode_recovery,
)
from yleum_api.services.generation.recovery_pipeline import CompletedAgentTurn, FinalizationProgress
from yleum_api.services.orchestrator_client import ProjectCellResourceResponse


@pytest.mark.parametrize(
    "defect", [
        None, "foreign_release", "unknown_provider", "already_woken", "context_changed",
        "source_changed", "active_clock", "repair_reserved", "foreign_baseline",
    ],
)
async def test_parked_source_repair_requires_exact_terminal_checkpoint(monkeypatch, defect):
    now = datetime.now(UTC)
    workspace, release = uuid4(), uuid4()
    detail = parked.PARKED_THROW_DIAGNOSTIC_PREFIX + " Synthetic location for this guard test."
    run = GenerationRun(
        id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64,
        status="running", execution_backend="worker", response_mode="build",
        agent_state={"max_finalization": {"terminal_reason": detail}},
    )
    if defect == "already_woken":
        run.agent_state[parked.PARKED_SOURCE_REDRIVE_KEY] = 12
    reference = CheckpointRef(object_key="retained", sha256="b" * 64).model_dump(mode="json")
    original = _bootstrap_wait(run)
    state = decode_recovery({
        **original, "version": 3, "clock": {
            **_clock(now), "interval_open": defect == "active_clock",
        }, "phase": "needs_input", "next_check_at": None, "cursor": "finalization",
        "checkpoint": reference, "context_ref": reference,
        "binding": {
            "workspace_id": str(workspace), "fencing_epoch": 3, "source_sha256": "c" * 64,
            "schema_sha256": "d" * 64, "environment_sha256": "e" * 64,
        },
        "execution": {
            **original["execution"], "stage": "finalization", "substep": "checkpoint_ready",
            "execution_context_ref": reference, "physical_state": "released",
            "release_operation_id": str(release), "control_workspace_id": str(workspace),
            "control_fencing_epoch": 4,
        },
    })
    assert isinstance(state, RecoveryStateV3)
    page = {"src/app/page.tsx": "throw new Error('test');"}
    payload = source_checkpoint.encode_checkpoint(
        project_id=run.project_id, user_id=run.user_id, run_id=run.id, baseline_sha=None,
        files=page, deleted_paths=[],
    )
    result = AgentResult(
        done=False, summary="", files=page, steps=1, transcript=[], stop_reason="source_deadline",
    )
    context = {
        "completed": {
            "finalization_progress": FinalizationProgress(
                source_history=("f" * 64,), pending_fast_check=True,
                pending_detail=detail if defect != "context_changed" else "Unrelated source gap",
            ).model_dump(mode="json"),
            "agent_turn": CompletedAgentTurn(result, None, IncompleteResponseBudget()).to_json(),
        },
    }
    if defect == "repair_reserved":
        context["completed"]["source_repair"] = {"status": "active"}
    rows = {
        (Project, run.project_id): SimpleNamespace(
            id=run.project_id, owner_id=run.user_id, template="max_miniapp",
            current_snapshot_id=uuid4() if defect == "foreign_baseline" else None,
        ),
        (ProjectCellWorkspace, workspace): SimpleNamespace(
            id=workspace, project_id=run.project_id, owner_id=run.user_id, fencing_epoch=4,
            deleted_at=None, generation_run_id=None,
        ),
        (ProjectCellOperation, release): SimpleNamespace(
            workspace_id=workspace, kind="release", status="completed", finished_at=now,
            fencing_epoch=4, generation_run_id=uuid4() if defect == "foreign_release" else run.id,
            result_payload=ProjectCellResourceResponse(
                workspace_id=workspace, state="resources_ready", provider_ref="owned",
                fencing_epoch=4, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
                has_postgres=True, has_redis=True,
            ).to_wire_json(),
        ),
    }
    session = AsyncMock()
    session.get.side_effect = lambda model, identity, **_kwargs: rows.get((model, identity))
    monkeypatch.setattr(
        recovery_quiescence, "_provider_stopped",
        AsyncMock(return_value=defect != "unknown_provider"),
    )
    monkeypatch.setattr(
        recovery_execution, "load_execution_context", AsyncMock(return_value=context),
    )
    monkeypatch.setattr(
        source_checkpoint, "load_checkpoint_object", AsyncMock(return_value=payload),
    )
    monkeypatch.setattr(
        parked, "matches_parked_throw_diagnostic",
        lambda terminal, files: defect != "source_changed" and (terminal, files) == (detail, page),
    )
    assert await parked.parked_source_repair(session, run, state) is (defect is None)
