"""A working preview is a private sandbox of exactly one interrupted generation."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from tests.test_agent_retry_context import history  # noqa: F401
from tests.test_project_cell_runtime_api import _preview_session, _seed
from yleum_api.core.errors import ApiError
from yleum_api.models.message import Message
from yleum_api.services import orchestrator_client as orch
from yleum_api.services import working_draft_preview as preview
from yleum_api.services.generation.source_checkpoint import encode_checkpoint
from yleum_api.services.promotion_permit import workspace_revision_digest


async def test_owner_http_opens_isolated_cell_after_rollback(client, db_session, monkeypatch):
    owner, project, run, workspace = await _seed(db_session, monkeypatch)
    message = Message(project_id=project.id, role="assistant", content="Saved draft")
    db_session.add(message)
    await db_session.flush()
    digest = "c" * 64
    run.assistant_message_id = message.id
    run.status = "failed"
    run.agent_state = {"source_checkpoint": {"digest": digest, "baseline_sha": "a" * 40}}
    workspace.generation_run_id = None
    await db_session.commit()
    candidate = uuid5(workspace.id, f"owner-draft-preview:{run.id}:{digest}")
    session = _preview_session(candidate)
    transport = AsyncMock(return_value={
        "workspace_id": str(candidate), "state": "draft_running",
        "preview_url": session.preview_url,
        "bootstrap_url": session.bootstrap_url, "expires_at": session.expires_at,
    })
    monkeypatch.setattr(orch, "_request", transport)
    path = f"/api/projects/{project.id}/max/preview-session"
    stale = await client.post(path, params={"working_run_id": str(uuid4())})
    assert stale.status_code == 409
    transport.assert_not_called()
    response = await client.post(path, params={"working_run_id": str(run.id)})
    assert response.status_code == 200, response.text
    assert response.json()["url"] == session.bootstrap_url
    assert response.headers["cache-control"] == "no-store"
    transport.assert_awaited_once_with(
        "POST", f"/internal/workspaces/{workspace.id}/draft/checkpoint-preview",
        json={"owner_id": str(owner.id), "project_id": str(project.id),
              "run_id": str(run.id), "checkpoint_digest": digest},
    )


def refused(reason):
    return orch.OrchestratorBadRequest("private upstream detail", 409, {"reason": reason})


@pytest.fixture
def draft(history, monkeypatch):  # noqa: F811
    project = history.ensure_project()
    run = history.add("Create a journal")
    body = encode_checkpoint(
        project_id=project.id, user_id=project.owner_id, run_id=run.id,
        baseline_sha=history.baseline.sha, files={"src/app/page.tsx": "current journal"},
        deleted_paths=["src/app/old.tsx"],
    )
    sha = hashlib.sha256(body).hexdigest()
    run.agent_state = {"source_checkpoint": {"baseline_sha": history.baseline.sha, "digest": sha}}
    history.session.commit()
    owner = SimpleNamespace(id=project.owner_id)
    ws = SimpleNamespace(id=uuid4(), owner_id=owner.id, project_id=project.id)
    selection = SimpleNamespace(selected=True, workspace=ws, owner=owner)
    monkeypatch.setattr(preview, "require_generation_admission", AsyncMock())
    monkeypatch.setattr(preview.project_cell_runtime, "resolve_project_cell_public_selection",
                        AsyncMock(return_value=selection))
    stored = AsyncMock(return_value=body)
    monkeypatch.setattr(preview, "load_checkpoint_object", stored)
    retained = AsyncMock(return_value=SimpleNamespace(files={
        "src/app/layout.tsx": "platform core", "src/app/page.tsx": "wrong rolled-back starter",
        ".env": "excluded", "src/app/old.tsx": "deleted",
    }))
    monkeypatch.setattr(orch, "project_cell_draft_files", retained)
    expected = object()
    issue = AsyncMock(return_value=expected)
    monkeypatch.setattr(orch, "project_cell_checkpoint_preview", issue)

    async def open_view():
        return await preview.open_checkpoint_preview(history.factory(), project, owner, run.id)

    return SimpleNamespace(hist=history, run=run, project=project, owner=owner, ws=ws, sha=sha,
                           stored=stored, retained=retained, issue=issue, expected=expected,
                           open=open_view)


@pytest.mark.parametrize("phase", ["completed", "failed", "cancelled"])
async def test_reconstructs_editable_files_and_managed_core_only(draft, phase):
    draft.run.status = phase
    draft.hist.session.commit()
    draft.issue.side_effect = [refused("draft_preview_source_required"), draft.expected]
    assert await draft.open() is draft.expected
    draft.retained.assert_awaited_once_with(draft.ws.id)
    assert [call.kwargs["files"] for call in draft.issue.await_args_list] == [
        None, {"src/app/layout.tsx": "platform core", "src/app/page.tsx": "current journal"},
    ]
    for call in draft.issue.await_args_list:
        assert call.kwargs["run_id"] == draft.run.id
        assert call.kwargs["checkpoint_digest"] == draft.sha


async def test_reopening_running_candidate_skips_source_and_canonical_runtime(draft):
    assert await draft.open() is draft.expected
    draft.stored.assert_not_called()
    draft.retained.assert_not_called()


async def test_release_drain_denies_even_a_lookup_that_might_wake_a_candidate(draft, monkeypatch):
    denied = AsyncMock(side_effect=ApiError("generation_draining", "Release", 503))
    monkeypatch.setattr(preview, "require_generation_admission", denied)
    with pytest.raises(ApiError, match="Release"):
        await draft.open()
    denied.assert_awaited_once()
    draft.issue.assert_not_called()


@pytest.mark.parametrize("reason", ["draft_preview_preparing", "failed"])
async def test_preparing_is_only_pollable_reason_and_hides_upstream(draft, reason):
    draft.issue.side_effect = refused(reason)
    with pytest.raises(ApiError) as result:
        await draft.open()
    assert result.value.status_code == 409 and "private" not in result.value.message
    expected = {"reason": reason} if reason == "draft_preview_preparing" else None
    assert result.value.details == expected
    draft.stored.assert_not_called()


@pytest.mark.parametrize("case", ["changed_owner", "superseded", "accepted", "running", "baseline"])
async def test_other_owner_previous_attempt_accepted_or_changed_baseline_cannot_issue(draft, case):
    if case == "changed_owner":
        draft.owner.id = uuid4()
    elif case == "superseded":
        draft.hist.add("Second request", status="running")
    elif case == "accepted":
        message = draft.hist.session.get(Message, draft.run.assistant_message_id)
        message.snapshot_id = draft.hist.baseline.snapshot_id
    elif case == "running":
        draft.run.status = "running"
    else:
        draft.run.agent_state = {"source_checkpoint": {"digest": draft.sha, "baseline_sha": None}}
    draft.hist.session.commit()
    with pytest.raises(ApiError):
        await draft.open()
    draft.issue.assert_not_awaited()


async def test_session_returned_after_a_new_generation_is_discarded(draft):
    async def started_elsewhere(*args, **kwargs):
        draft.hist.add("Second request", status="running")
        return draft.expected
    draft.issue.side_effect = started_elsewhere
    with pytest.raises(ApiError):
        await draft.open()


async def test_corrupt_source_never_reaches_materializer(draft):
    draft.issue.side_effect = refused("draft_preview_source_required")
    draft.stored.return_value = b'{"files": {}}'
    with pytest.raises(ApiError):
        await draft.open()
    assert draft.issue.await_count == 1
    draft.retained.assert_not_called()


async def test_client_binds_candidate_host_and_validates_the_returned_workspace(monkeypatch):
    original, project, owner, run = (uuid4() for _ in range(4))
    digest = "a" * 64
    child = uuid5(original, f"owner-draft-preview:{run}:{digest}")
    files = {"src/app/page.tsx": "journal"}
    request = AsyncMock(return_value={})
    monkeypatch.setattr(orch, "_request", request)
    response = SimpleNamespace(workspace_id=child)
    monkeypatch.setattr(orch.ProjectCellPreviewSession, "from_json", lambda _: response)
    kwargs = dict(project_id=project, owner_id=owner, run_id=run, checkpoint_digest=digest)
    assert await orch.project_cell_checkpoint_preview(original, **kwargs, files=files) is response
    assert request.await_args.kwargs["json"]["source_revision"] == workspace_revision_digest(files)
    response.workspace_id = original
    with pytest.raises(orch.OrchestratorUnavailable, match="different checkpoint"):
        await orch.project_cell_checkpoint_preview(original, **kwargs)
    assert "files" not in request.await_args.kwargs["json"]
