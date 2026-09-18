"""Characterisation of the ``snapshot.created`` wire payload, consumer by consumer.

Frozen BEFORE the hand-written copies in the routers were replaced by the single
owner, unchanged AFTER. Actual handlers and models; git, queue and pub/sub stay
in memory. (The four manual page edits are pinned in ``test_page_edit_endpoints``,
the generation pipeline in ``test_snapshot_serialization_contract``.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import Response

from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import projects, rollback, style_patch
from omnia_api.schemas.project import ProjectCreate, ProjectImportRequest
from omnia_api.schemas.snapshot import RollbackRequest
from omnia_api.schemas.style_patch import StylePatchRequest

PROJECT = UUID("00000000-0000-0000-0000-000000000001")
OWNER = UUID("00000000-0000-0000-0000-000000000002")
HEAD = UUID("00000000-0000-0000-0000-000000000003")
CREATED = datetime(2026, 9, 19, 9, 8, 7, 654321, tzinfo=UTC)
FIELDS = [
    "id",
    "project_id",
    "commit_sha",
    "prompt_text",
    "model_id",
    "parent_id",
    "preview_url",
    "is_rollback_target",
    "created_at",
]


def wire(row: Snapshot) -> dict[str, Any]:
    return {
        "snapshot": {
            "id": str(row.id),
            "project_id": str(row.project_id),
            "commit_sha": row.commit_sha,
            "prompt_text": row.prompt_text,
            "model_id": row.model_id,
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "preview_url": (
                "https://objects.example.test/previews/" + row.preview_key
                if row.preview_key
                else None
            ),
            "is_rollback_target": row.is_rollback_target,
            "created_at": row.created_at.isoformat(),
        }
    }


class Session:
    """Primary keys appear on flush, server-side columns only on refresh — a
    handler that announced the snapshot before refreshing it would crash here,
    as it would on an expired row in production."""

    def __init__(self, rows: dict[UUID, Any] | None = None) -> None:
        self.rows = rows or {}
        self.added: list[Any] = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid4()

    async def refresh(self, row: Any) -> None:
        if isinstance(row, Snapshot) and row.created_at is None:
            row.created_at = CREATED
            row.is_rollback_target = False

    async def get(self, _model: Any, identity: UUID) -> Any:
        return self.rows.get(identity)

    def snapshot(self) -> Snapshot:
        (row,) = [item for item in self.added if isinstance(item, Snapshot)]
        return row


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from omnia_api.core import minio

    settings = SimpleNamespace(
        minio_public_url="https://objects.example.test/", minio_bucket_previews="previews"
    )
    monkeypatch.setattr(minio, "get_settings", lambda: settings)
    published = AsyncMock()
    for module in (projects, rollback, style_patch):
        monkeypatch.setattr(module, "publish_event", published)
        monkeypatch.setattr(module, "enqueue_preview", Mock())
    return published


def owner() -> User:
    return User(
        id=OWNER,
        email="owner@example.test",
        status="active",
        is_anon=False,
        email_verified_at=CREATED,
        password_hash="unused",
    )


def _assert_announced(events: AsyncMock, project_id: UUID, row: Snapshot) -> None:
    events.assert_awaited_once_with(project_id, "snapshot.created", wire(row))
    assert list(events.await_args.args[2]["snapshot"]) == FIELDS


async def test_created_project_announces_its_first_snapshot(events, monkeypatch):
    monkeypatch.setattr(projects.repo_svc, "init_repo", Mock(return_value="a" * 40))
    session = Session()

    project = await projects.create_project(
        ProjectCreate.model_validate({"name": "Кофейня у дома", "template": "blank"}),
        session,
        Response(),
        owner(),
    )

    row = session.snapshot()
    assert (row.prompt_text, row.parent_id, row.preview_key) == (None, None, None)
    _assert_announced(events, project.id, row)


async def test_imported_project_announces_its_first_snapshot(events, monkeypatch):
    monkeypatch.setattr(projects.repo_import, "fetch_repo_tarball", AsyncMock(return_value=b"tar"))
    monkeypatch.setattr(
        projects.repo_import,
        "tarball_to_files",
        Mock(return_value=SimpleNamespace(files={"index.html": "<h1>hi</h1>"}, template="blank")),
    )
    monkeypatch.setattr(projects.repo_svc, "init_from_files", Mock(return_value="c" * 40))
    session = Session()

    project = await projects.import_project(
        ProjectImportRequest(repo_url="octo/site"), session, Response(), owner()
    )

    row = session.snapshot()
    assert (row.prompt_text, row.parent_id) == ("", None)
    _assert_announced(events, project.id, row)


async def test_fork_announces_the_carried_head_without_a_parent(events, monkeypatch):
    monkeypatch.setattr(
        projects.project_cell_runtime,
        "resolve_project_cell_public_selection",
        AsyncMock(return_value=SimpleNamespace(selected=False)),
    )
    monkeypatch.setattr(projects.repo_svc, "duplicate_repo", Mock())
    source = Project(
        id=PROJECT,
        owner_id=uuid4(),
        name="Источник",
        slug="istochnik-aaaaaa",
        template="blank",
        current_snapshot_id=HEAD,
    )
    head = Snapshot(
        id=HEAD,
        project_id=PROJECT,
        commit_sha="d" * 40,
        prompt_text="сайт кофейни",
        model_id="example-model",
        preview_key="istochnik/head.png",
        parent_id=uuid4(),
    )
    session = Session({HEAD: head})

    fork = await projects.perform_fork(session, Response(), source, owner())

    row = session.snapshot()
    assert (row.project_id, row.commit_sha) == (fork.id, "d" * 40)
    assert row.preview_key == head.preview_key
    assert row.parent_id is None
    _assert_announced(events, fork.id, row)


def _edited_project() -> tuple[Session, SimpleNamespace]:
    project = SimpleNamespace(
        id=PROJECT, owner_id=OWNER, template="blank", slug="qa", current_snapshot_id=HEAD
    )
    head = Snapshot(id=HEAD, project_id=PROJECT, commit_sha="a" * 40)
    return Session({PROJECT: project, HEAD: head}), project


async def test_rollback_announces_the_restored_snapshot(events, monkeypatch):
    monkeypatch.setattr(rollback.repo_svc, "checkout", Mock(return_value="b" * 40))
    monkeypatch.setattr(rollback, "record_restored_version", AsyncMock())
    session, _project = _edited_project()

    await rollback.post_rollback(
        PROJECT, RollbackRequest(snapshot_id=HEAD), session, SimpleNamespace(id=OWNER)
    )

    row = session.snapshot()
    assert (row.prompt_text, row.parent_id) == ("Восстановление версии", HEAD)
    _assert_announced(events, PROJECT, row)


async def test_style_patch_announces_the_new_snapshot(events, monkeypatch):
    page = "<html><head></head><body><h1>Привет</h1></body></html>"
    monkeypatch.setattr(style_patch.repo_svc, "read_files", Mock(return_value={"index.html": page}))
    monkeypatch.setattr(style_patch.repo_svc, "commit_files", Mock(return_value="b" * 40))
    session, _project = _edited_project()

    await style_patch.post_style_patch(
        PROJECT,
        StylePatchRequest(tokens=[{"var": "--accent", "value": "#123456"}]),
        session,
        SimpleNamespace(id=OWNER),
    )

    row = session.snapshot()
    assert (row.prompt_text, row.parent_id) == ("(прямое редактирование стиля)", HEAD)
    _assert_announced(events, PROJECT, row)
