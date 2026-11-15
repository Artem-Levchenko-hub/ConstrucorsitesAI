import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import sqlite

from tests.test_generation_recovery_deadlines import _clock
from tests.test_generation_recovery_execution import _bootstrap_wait
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services import project_cell_recovery_release as recovery_release
from yleum_api.services.generation.recovery_contract import (
    CheckpointRef,
    RecoveryBinding,
    decode_recovery,
)
from yleum_api.services.generation.recovery_execution import recovery_state
from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
from yleum_api.services.project_cells import _canonical_operation_envelope, _hashed_error
from yleum_api.workers.generation import _maintenance_candidates_query


def test_worker_schedules_only_due_release_maintenance_for_a_parked_run():
    run_id, release_id = uuid4(), uuid4()
    proof = {"source_sha256": "a" * 64, "environment_sha256": "b" * 64}
    marker = json.dumps({"recovery_v1": {
        "version": "3", "phase": "needs_input",
        "binding": {"workspace_id": "synthetic", **proof},
        "execution": {"physical_state": "release_pending", "release_operation_id": str(release_id)},
    }})
    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE generation_runs (id text, project_id text, execution_backend text, "
            "status text, execution_started_at text, created_at integer, agent_state text)"
        )
        db.execute("CREATE TABLE project_cell_workspaces (project_id text, orchestrator text)")
        db.execute(
            "CREATE TABLE project_cell_operations (id text, generation_run_id text, "
            "execution_run_id text, kind text, status text, error text, next_attempt_at text, "
            "request_payload text)"
        )
        db.execute(
            "INSERT INTO generation_runs VALUES (?, ?, 'worker', 'running', 'started', 0, ?)",
            (run_id.hex, run_id.hex, marker),
        )
        db.execute("INSERT INTO project_cell_workspaces VALUES (?, 'A')", (run_id.hex,))
        db.execute(
            "INSERT INTO project_cell_operations VALUES (?, ?, ?, 'release', 'indeterminate', "
            "NULL, NULL, ?)", (str(release_id), run_id.hex, run_id.hex, json.dumps({
                "request": {"require_environment_proof": proof},
            })),
        )
        query = str(_maintenance_candidates_query([]).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True},
        ))
        assert db.execute(query).fetchall() == [(run_id.hex, "A")]
        db.execute(
            "UPDATE project_cell_operations SET next_attempt_at=?",
            ((datetime.now(UTC) + timedelta(minutes=1)).isoformat(sep=" "),),
        )
        assert db.execute(query).fetchall() == []


@pytest.mark.parametrize("outcome", [
    "ack", "completed", "unknown", "rejected", "stale_fence", "tampered", "legacy_unknown",
    "pending_ack", "pending_tampered",
])
async def test_interrupted_release_reuses_only_the_owned_envelope(monkeypatch, outcome):
    now = datetime.now(UTC)
    run = GenerationRun(
        id=uuid4(), project_id=uuid4(), user_id=uuid4(), prompt_hash="a" * 64,
        status="running", execution_backend="worker", agent_state={},
    )
    pending = outcome.startswith("pending_")
    workspace = ProjectCellWorkspace(
        id=uuid4(), project_id=run.project_id, owner_id=run.user_id, provider="synthetic",
        state="ready", fencing_epoch=3 if pending else 4, generation_run_id=run.id,
    )
    if outcome == "stale_fence":
        workspace.fencing_epoch += 1
    binding = RecoveryBinding(
        workspace_id=str(workspace.id), fencing_epoch=3,
        source_sha256="b" * 64, schema_sha256="c" * 64, environment_sha256="d" * 64,
    )
    request = {} if outcome in {"completed", "legacy_unknown"} else {
        "require_environment_proof": {
            "source_sha256": binding.source_sha256,
            "environment_sha256": binding.environment_sha256,
        },
    }
    envelope, digest = _canonical_operation_envelope(workspace.id, run.id, "release", request)
    operation = ProjectCellOperation(
        id=uuid4(), workspace_id=workspace.id, generation_run_id=run.id,
        execution_run_id=None if pending else run.id,
        kind="release", status="pending" if pending else "indeterminate",
        fencing_epoch=None if pending else 4, attempt_count=int(not pending),
        finished_at=None if pending else now,
        idempotency_key=f"generation:{run.id}:release:profile:cycle:0",
        request_digest="e" * 64 if outcome.endswith("tampered") else digest,
        request_payload=envelope,
    )
    checkpoint = CheckpointRef(object_key="retained", sha256="f" * 64)
    raw = _bootstrap_wait(run)
    state = decode_recovery({
        **raw, "version": 3, "clock": _clock(now), "cursor": "finalization",
        "checkpoint": checkpoint.model_dump(mode="json"),
        "context_ref": checkpoint.model_dump(mode="json"),
        "binding": binding.model_dump(mode="json"),
        "execution": {
            **raw["execution"], "stage": "finalization", "substep": "checkpoint_ready",
            "execution_context_ref": checkpoint.model_dump(mode="json"),
            "physical_state": "release_pending", "release_operation_id": str(operation.id),
            "control_workspace_id": str(workspace.id), "control_fencing_epoch": 3,
        },
    })
    run.agent_state = {"recovery_v1": state.model_dump(mode="json")}

    def finish_operation():
        receipt = ProjectCellResourceResponse(
            workspace_id=workspace.id, state="resources_ready", provider_ref="owned",
            fencing_epoch=4, checkpoint_ref=None, has_workspace=True, has_agent_home=True,
            has_postgres=True, has_redis=True,
        )
        operation.status = "completed"
        operation.finished_at = now
        operation.result_payload = receipt.to_wire_json()

    if outcome == "completed":
        finish_operation()

    async def replay(_factory, _operation, _client, *, expected_workspace_fence):
        assert expected_workspace_fence == operation.fencing_epoch == workspace.fencing_epoch == 4
        operation.attempt_count += 1
        if outcome == "ack":
            finish_operation()
        if outcome == "rejected":
            operation.error = _hashed_error("recovery_release_source_changed")

    async def execute(_factory, _operation, _client, *, expected_workspace_fence):
        assert expected_workspace_fence == workspace.fencing_epoch == 3
        assert operation.fencing_epoch is None and operation.execution_run_id is None
        workspace.fencing_epoch += 1
        operation.fencing_epoch = workspace.fencing_epoch
        operation.execution_run_id = run.id
        operation.attempt_count += 1
        finish_operation()

    operation_replay = AsyncMock(side_effect=replay)
    operation_execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(recovery_release, "replay_indeterminate_cell_operation", operation_replay)
    monkeypatch.setattr(recovery_release, "execute_cell_operation", operation_execute)
    monkeypatch.setattr(recovery_release, "_require_session_owner", AsyncMock())
    monkeypatch.setattr(recovery_release, "lock_workspace_operations", AsyncMock())
    monkeypatch.setattr(recovery_release, "HttpProjectCellOrchestratorClient", lambda: object())
    project = SimpleNamespace(owner_id=run.user_id, template="max_miniapp")
    rows = {
        (Project, run.project_id): project,
        (GenerationRun, run.id): run,
        (ProjectCellWorkspace, workspace.id): workspace,
        (ProjectCellOperation, operation.id): operation,
    }
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.get.side_effect = lambda model, identity, **_kwargs: rows.get((model, identity))
    session.scalar.return_value = None
    def factory():
        return session

    await recovery_release.resume_recovery_release(factory, run.id, state)
    await recovery_release.resume_recovery_release(factory, run.id, state)

    if outcome in {"ack", "unknown", "rejected"}:
        operation_replay.assert_awaited_once()
        assert operation_replay.await_args.args[:2] == (factory, operation.id)
    else:
        operation_replay.assert_not_awaited()
    if outcome == "pending_ack":
        operation_execute.assert_awaited_once()
        assert operation_execute.await_args.args[:2] == (factory, operation.id)
    else:
        operation_execute.assert_not_awaited()
    saved = recovery_state(run)
    if outcome in {"ack", "completed", "pending_ack"}:
        assert workspace.generation_run_id is None
        assert saved.state_revision == state.state_revision + 1
        assert saved.execution.physical_state == "released"
        assert saved.execution.control_fencing_epoch == 4
        assert saved.binding == binding and saved.clock == state.clock
        assert saved.execution.release_operation_id == operation.id
    else:
        assert workspace.generation_run_id == run.id and saved == state
        if outcome == "unknown":
            assert operation.next_attempt_at >= now + timedelta(seconds=30)
        else:
            assert operation.next_attempt_at is None
