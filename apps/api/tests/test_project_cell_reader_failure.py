"""A classified reader guard denial is fatal to this run, never permission to replay."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from yleum_api.models.project_cell import ProjectCellActivityLease, ProjectCellWorkspace
from yleum_api.services import agent_native, project_cell_activity
from yleum_api.services.orchestrator_client import (
    OrchestratorBadRequest,
    OrchestratorReaderInventoryUnavailable,
    OrchestratorUnavailable,
)
from yleum_api.services.project_cell_activity import (
    ActivityKind,
    ActivityStart,
    ActivityState,
    ProjectCellActivityConflict,
    run_with_activity_lease,
)
from yleum_api.services.project_cell_errors import (
    ProjectCellInfrastructureError,
    raise_if_terminal_cell_error,
    terminal_cell_error,
)

CODE = "reader_inventory_unavailable"
PRIVATE = "PRIVATE_CONTROLLER_DETAIL"


def reader_denial():
    return OrchestratorReaderInventoryUnavailable("Orchestrator returned 503", {
        "upstream_body": PRIVATE,
    })


@pytest.fixture
def activity(monkeypatch):
    """Only the SQL persistence boundary is replaced; run/replay logic is real."""
    lease = ActivityStart(uuid4(), uuid4(), uuid4(), ActivityKind.COMMAND, 7,
                          datetime.now(UTC) + timedelta(minutes=5), "a" * 64, "fast_check")
    workspace = SimpleNamespace(fencing_epoch=7, generation_run_id=lease.generation_run_id)
    record = SimpleNamespace(
        workspace_id=lease.workspace_id, fencing_epoch=7, proof_key=lease.proof_key,
        generation_run_id=lease.generation_run_id, kind=lease.kind.value,
        state=ActivityState.ACTIVE.value, redacted_diagnostic=None,
    )
    state = SimpleNamespace(exists=False, fail_finish=False)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, identifier):
            if model is ProjectCellWorkspace:
                assert identifier == lease.workspace_id
                return workspace
            assert model is ProjectCellActivityLease and identifier == lease.operation_id
            return record if state.exists else None

        async def commit(self):
            return None

    async def start(_session, **kwargs):
        assert kwargs["operation_id"] == lease.operation_id
        state.exists = True

    async def finish(_session, **kwargs):
        assert kwargs["operation_id"] == lease.operation_id
        if state.fail_finish:
            raise OSError("private activity-store outage")
        record.state = kwargs["state"].value
        record.redacted_diagnostic = kwargs.get("diagnostic")

    monkeypatch.setattr(project_cell_activity, "start_activity", start)
    monkeypatch.setattr(project_cell_activity, "finish_activity", finish)
    return SimpleNamespace(lease=lease, workspace=workspace, record=record, state=state,
                           session_factory=Session)


@pytest.mark.parametrize("boundary", [None, "finish", "emit"])
async def test_reader_denial_survives_initial_failure_and_failed_replay(activity, boundary):
    work = AsyncMock(side_effect=reader_denial())
    poll = AsyncMock(side_effect=AssertionError("failed typed activity must not poll"))
    replay = AsyncMock(side_effect=AssertionError("failed typed activity must not decode"))
    activity.state.fail_finish = boundary == "finish"

    async def emit(kind, _payload):
        if boundary == "emit" and kind == "tool.finished":
            raise OSError("private event-store outage")

    for _ in range(1 if boundary == "finish" else 2):
        with pytest.raises(ProjectCellInfrastructureError) as caught:
            await run_with_activity_lease(
                session_factory=activity.session_factory, lease=activity.lease, work=work,
                poll_status=poll, replay_terminal=replay, emit=emit,
            )
        assert caught.value.code == CODE
        assert caught.value.operation_id == activity.lease.operation_id
        assert PRIVATE not in str(caught.value)
    work.assert_awaited_once()
    poll.assert_not_awaited()
    replay.assert_not_awaited()
    if boundary != "finish":
        assert activity.record.state == "failed"
        assert activity.record.redacted_diagnostic == CODE


async def test_generic_503_keeps_existing_failure_and_journal_reconciliation(activity):
    generic = OrchestratorUnavailable("Orchestrator returned 503", {"upstream_body": PRIVATE})
    work = AsyncMock(side_effect=generic)
    poll = AsyncMock(side_effect=generic)
    kwargs = dict(session_factory=activity.session_factory, lease=activity.lease, work=work,
                  poll_status=poll, replay_terminal=AsyncMock(), emit=AsyncMock())
    with pytest.raises(OrchestratorUnavailable) as caught:
        await run_with_activity_lease(**kwargs)
    assert caught.value is generic
    assert activity.record.redacted_diagnostic == "OrchestratorUnavailable"
    with pytest.raises(ProjectCellInfrastructureError, match="activity_replay_unavailable"):
        await run_with_activity_lease(**kwargs)
    work.assert_awaited_once()
    poll.assert_awaited_once_with(activity.lease.operation_id)


@pytest.mark.parametrize("field", ["workspace_id", "generation_run_id", "fencing_epoch",
                                   "proof_key", "kind", "current_fence", "current_run"])
async def test_failed_reader_diagnostic_never_overrides_changed_authority(activity, field):
    activity.state.exists = True
    activity.record.state = "failed"
    activity.record.redacted_diagnostic = CODE
    if field == "current_fence":
        activity.workspace.fencing_epoch += 1
    elif field == "current_run":
        activity.workspace.generation_run_id = None
    else:
        setattr(activity.record, field, {
            "workspace_id": uuid4(), "generation_run_id": uuid4(), "fencing_epoch": 8,
            "proof_key": "b" * 64, "kind": ActivityKind.TOOL.value,
        }[field])
    work, poll = AsyncMock(), AsyncMock()
    with pytest.raises(ProjectCellActivityConflict):
        await run_with_activity_lease(session_factory=activity.session_factory,
            lease=activity.lease, work=work, poll_status=poll,
            replay_terminal=AsyncMock(), emit=AsyncMock())
    work.assert_not_awaited()
    poll.assert_not_awaited()


async def test_cancelled_reader_activity_stays_cancelled_without_poll_or_work(activity):
    activity.state.exists = True
    activity.record.state = "cancelled"
    activity.record.redacted_diagnostic = CODE
    work, poll = AsyncMock(), AsyncMock()
    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await run_with_activity_lease(session_factory=activity.session_factory,
            lease=activity.lease, work=work, poll_status=poll,
            replay_terminal=AsyncMock(), emit=AsyncMock())
    work.assert_not_awaited()
    poll.assert_not_awaited()


async def test_completed_activity_still_requires_real_terminal_journal(activity):
    activity.state.exists = True
    activity.record.state = "completed"
    activity.record.redacted_diagnostic = CODE
    status = SimpleNamespace(operation_id=activity.lease.operation_id, state="completed",
                             terminal_response=42)
    work, poll, replay = AsyncMock(), AsyncMock(return_value=status), AsyncMock(return_value=42)
    assert await run_with_activity_lease(session_factory=activity.session_factory,
        lease=activity.lease, work=work, poll_status=poll,
        replay_terminal=replay, emit=AsyncMock()) == 42
    work.assert_not_awaited()
    poll.assert_awaited_once()
    replay.assert_awaited_once_with(status)


def test_only_preclassified_reader_denial_gets_closed_terminal_code():
    operation_id = uuid4()
    result = terminal_cell_error(reader_denial(), operation_id=operation_id)
    assert isinstance(result, ProjectCellInfrastructureError)
    assert result.code == CODE and result.operation_id == operation_id
    assert PRIVATE not in str(result)


@pytest.mark.parametrize("error", [
    OrchestratorUnavailable("Orchestrator returned 500"),
    OrchestratorUnavailable("reader inventory is incompatible or stale"),
    OrchestratorUnavailable("Orchestrator returned 503", {
        "upstream_body": '{"error":{"code":"workspace_busy"}}',
    }),
    OrchestratorBadRequest("reader inventory", status_code=409,
                           upstream_code=CODE),
])
def test_unclassified_errors_never_gain_reader_terminal_authority(error):
    assert terminal_cell_error(error) is None
    raise_if_terminal_cell_error(error)


async def test_native_reader_denial_stops_before_another_tool_or_model(monkeypatch):
    calls = []

    async def provider(*_args, **_kwargs):
        calls.append("model")
        return {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "first", "name": "read_file",
             "input": {"path": "a.ts"}},
            {"type": "tool_use", "id": "second", "name": "list_dir",
             "input": {"path": "."}},
        ]}

    async def execute(action):
        calls.append(action.name)
        raise reader_denial()

    monkeypatch.setattr(agent_native, "_call_messages", provider)
    with pytest.raises(ProjectCellInfrastructureError, match=CODE) as caught:
        await agent_native.run_native_build(system="s", task="t", execute=execute, max_steps=40)
    assert PRIVATE not in str(caught.value)
    assert calls == ["model", "read_file"]
