"""Владелец сохраняет или отбрасывает несохранённые правки черновика.

Откат отказывает, когда файлы черновика отличаются от сохранённой версии, и это
честно: восстанавливать поверх несохранённой работы — терять её молча. Но до сих
пор у владельца не было ни одного выхода из этого состояния (QA-проект 361b3326
был неоткатываем, «зафиксировать» генерацией падало с unsafe_changes_rolled_back).
Здесь закреплены два осознанных действия — и то, что они работают только между
генерациями. DB-кейсы требуют одноразовой базы из conftest.
"""

from __future__ import annotations

from uuid import uuid4

from omnia_api.core.security import create_access_token
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services import draft_changes, orchestrator_client, repo
from omnia_api.services.draft_changes import draft_reset_patch
from omnia_api.services.orchestrator_client import (
    ProjectCellAgentWriteResponse,
    ProjectCellDraftFiles,
)

PAGE = "src/app/page.tsx"
SAVED = {PAGE: "saved page\n", "src/lib/db.ts": "export const db = 1\n"}
EDITED = {PAGE: "edited page\n", "src/lib/db.ts": "export const db = 1\n", "scratch.ts": "x\n"}


def test_patch_rewrites_what_differs_and_deletes_what_the_version_lacks() -> None:
    patch = draft_reset_patch(EDITED, SAVED)

    assert patch.writes == {PAGE: "saved page\n"}
    assert patch.deletes == ("scratch.ts",)
    assert not patch.empty


def test_patch_restores_files_the_draft_lost() -> None:
    patch = draft_reset_patch({PAGE: "saved page\n"}, SAVED)

    assert patch.writes == {"src/lib/db.ts": "export const db = 1\n"}
    assert patch.deletes == ()


def test_patch_is_empty_when_the_draft_equals_the_version() -> None:
    assert draft_reset_patch(dict(SAVED), SAVED).empty


def test_draft_files_response_is_strict() -> None:
    parsed = ProjectCellDraftFiles.from_json({"files": dict(SAVED), "workspace_revision": "a" * 64})
    assert parsed.files == SAVED

    for broken in (
        {"files": dict(SAVED)},
        {"files": {PAGE: 1}, "workspace_revision": "a" * 64},
        {"files": dict(SAVED), "workspace_revision": "a" * 64, "extra": True},
    ):
        try:
            ProjectCellDraftFiles.from_json(broken)  # type: ignore[arg-type]
        except Exception as error:
            assert "invalid draft files response" in str(error)
        else:
            raise AssertionError(f"accepted {broken!r}")


async def _fixture(db):
    user = User(email="draft-owner@example.test")
    db.add(user)
    await db.flush()
    project = Project(owner_id=user.id, name="Draft", slug="draft", template="max_miniapp")
    db.add(project)
    await db.flush()
    head = Snapshot(project_id=project.id, commit_sha="a" * 40)
    db.add(head)
    await db.flush()
    project.current_snapshot_id = head.id
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=user.id,
        state="ready",
        provider="docker_owner_canary",
        generation_run_id=None,
        fencing_epoch=3,
    )
    db.add(workspace)
    await db.commit()
    return user, project, head, workspace


def _stub_orchestrator(monkeypatch, *, files, revision="1" * 64):
    calls: dict[str, object] = {}

    async def draft_files(workspace_id):
        calls["files_workspace"] = workspace_id
        return ProjectCellDraftFiles(files=dict(files), workspace_revision=revision)

    async def draft_reset(workspace_id, *, expected_revision, files, deletes=()):
        calls["reset"] = {
            "workspace_id": workspace_id,
            "expected_revision": expected_revision,
            "files": dict(files),
            "deletes": list(deletes),
        }
        return ProjectCellAgentWriteResponse(
            written=len(files), deleted=len(deletes), workspace_revision="2" * 64
        )

    monkeypatch.setattr(orchestrator_client, "project_cell_draft_files", draft_files)
    monkeypatch.setattr(orchestrator_client, "project_cell_draft_reset", draft_reset)
    monkeypatch.setattr(repo, "read_files", lambda project_id, commit_sha: dict(SAVED))

    def commit_files(project_id, committed, message, parent_sha=None, *, exact_tree=False):
        calls["commit"] = {
            "files": dict(committed),
            "message": message,
            "parent_sha": parent_sha,
            "exact_tree": exact_tree,
        }
        return "b" * 40

    monkeypatch.setattr(repo, "commit_files", commit_files)
    return calls


async def test_save_records_the_draft_as_the_new_head_version(client, db_session, monkeypatch):
    owner, project, head, workspace = await _fixture(db_session)
    calls = _stub_orchestrator(monkeypatch, files=EDITED)
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"

    response = await client.post(f"/api/projects/{project.id}/draft/save-version")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["number"] == 1
    assert calls["commit"] == {
        "files": EDITED,
        "message": draft_changes.SAVED_BY_OWNER,
        "parent_sha": "a" * 40,
        "exact_tree": True,
    }
    assert calls["files_workspace"] == workspace.id
    db_session.expire_all()
    refreshed = await db_session.get(Project, project.id)
    snapshot = await db_session.get(Snapshot, refreshed.current_snapshot_id)
    assert snapshot is not None and snapshot.id != head.id
    assert snapshot.commit_sha == "b" * 40 and snapshot.parent_id == head.id
    version = await db_session.get(ProjectVersion, body["version_id"])
    assert version is not None and version.snapshot_id == snapshot.id
    assert version.generation_run_id is None and version.status == "ready"
    assert version.prompt_text == draft_changes.SAVED_BY_OWNER


async def test_save_refuses_a_draft_that_equals_the_version(client, db_session, monkeypatch):
    owner, project, _head, _workspace = await _fixture(db_session)
    calls = _stub_orchestrator(monkeypatch, files=SAVED)
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"

    response = await client.post(f"/api/projects/{project.id}/draft/save-version")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == draft_changes.DRAFT_CLEAN
    assert "commit" not in calls


async def test_discard_sends_exactly_the_patch_under_the_current_revision(
    client, db_session, monkeypatch
):
    owner, project, head, workspace = await _fixture(db_session)
    calls = _stub_orchestrator(monkeypatch, files=EDITED, revision="7" * 64)
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"

    response = await client.post(f"/api/projects/{project.id}/draft/discard")

    assert response.status_code == 200, response.text
    assert response.json() == {"written": 1, "deleted": 1, "workspace_revision": "2" * 64}
    assert calls["reset"] == {
        "workspace_id": workspace.id,
        "expected_revision": "7" * 64,
        "files": {PAGE: "saved page\n"},
        "deletes": ["scratch.ts"],
    }
    assert "commit" not in calls
    db_session.expire_all()
    refreshed = await db_session.get(Project, project.id)
    assert refreshed.current_snapshot_id == head.id


async def test_actions_refuse_while_the_workspace_belongs_to_a_generation(
    client, db_session, monkeypatch
):
    owner, project, _head, workspace = await _fixture(db_session)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="lease",
        prompt_hash="hash",
        status="completed",
    )
    db_session.add(run)
    await db_session.flush()
    workspace.generation_run_id = run.id
    await db_session.commit()
    calls = _stub_orchestrator(monkeypatch, files=EDITED)
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"

    save = await client.post(f"/api/projects/{project.id}/draft/save-version")
    discard = await client.post(f"/api/projects/{project.id}/draft/discard")

    assert save.status_code == 409 and discard.status_code == 409
    assert save.json()["error"]["code"] == "generation_active"
    assert "reset" not in calls and "commit" not in calls


async def test_actions_need_the_owner(client, db_session, monkeypatch):
    _owner, project, _head, _workspace = await _fixture(db_session)
    _stub_orchestrator(monkeypatch, files=EDITED)
    stranger = User(email="stranger@example.test")
    db_session.add(stranger)
    await db_session.commit()

    anonymous = await client.post(f"/api/projects/{project.id}/draft/save-version")
    client.headers["Authorization"] = f"Bearer {create_access_token(stranger.id)}"
    foreign = await client.post(f"/api/projects/{project.id}/draft/discard")

    assert anonymous.status_code == 401
    assert foreign.status_code == 404
    _ = uuid4()
