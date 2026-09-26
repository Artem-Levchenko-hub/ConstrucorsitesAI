"""Characterisation of the ``snapshot.created`` wire payload, consumer by consumer.

Frozen BEFORE the hand-written copies in the routers were replaced by the single
owner, unchanged AFTER. Actual handlers and models; git, queue and pub/sub stay
in memory. (The generation pipeline is pinned in
``test_snapshot_serialization_contract``.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.routers import projects, rollback
from yleum_api.schemas.project import ProjectCreate
from yleum_api.schemas.snapshot import RollbackRequest

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
    from yleum_api.core import minio

    settings = SimpleNamespace(
        minio_public_url="https://objects.example.test/", minio_bucket_previews="previews"
    )
    monkeypatch.setattr(minio, "get_settings", lambda: settings)
    published = AsyncMock()
    for module in (projects, rollback):
        monkeypatch.setattr(module, "publish_event", published)
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
    # Plan limits read the billing tables; this test owns a stub session.
    monkeypatch.setattr(projects, "assert_can_create_project", AsyncMock())
    session = Session()

    project = await projects.create_project(
        ProjectCreate.model_validate({"name": "Кофейня у дома", "template": "max_miniapp"}),
        session,
        owner(),
    )

    row = session.snapshot()
    assert (row.prompt_text, row.parent_id, row.preview_key) == (None, None, None)
    _assert_announced(events, project.id, row)


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
