"""Characterisation of how a new project gets its first snapshot.

``create_project`` ends with: first snapshot → project pointer → commit (a slug
collision is a 409) → refresh → preview job → ``snapshot.created``. Frozen BEFORE
that tail got one owner, unchanged AFTER. (GitHub import shared the tail until it
moved out with the site builder.) Actual handler and models; git, queue and pub/sub
stay in memory.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import projects
from omnia_api.schemas.project import ProjectCreate

OWNER = UUID("00000000-0000-0000-0000-000000000002")


class Session:
    def __init__(self, *, slug_taken: bool = False) -> None:
        self.slug_taken = slug_taken
        self.order: list[str] = []
        self.added: list[Any] = []
        self.pointer_at_commit: UUID | None = None
        self.rollback = AsyncMock(side_effect=lambda: self.order.append("rollback"))

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid4()

    async def commit(self) -> None:
        self.order.append("commit")
        self.pointer_at_commit = self.project.current_snapshot_id
        if self.slug_taken:
            raise IntegrityError("insert", {}, Exception("projects_slug_key"))

    async def refresh(self, row: Any) -> None:
        self.order.append("refresh-" + type(row).__name__.lower())
        if isinstance(row, Snapshot):
            row.is_rollback_target = False
            row.created_at = datetime(2026, 9, 19, tzinfo=UTC)

    @property
    def project(self) -> Project:
        (row,) = [item for item in self.added if isinstance(item, Project)]
        return row

    @property
    def snapshot(self) -> Snapshot:
        (row,) = [item for item in self.added if isinstance(item, Snapshot)]
        return row


def owner() -> User:
    return User(
        id=OWNER,
        email="owner@example.test",
        status="active",
        is_anon=False,
        email_verified_at=datetime(2026, 9, 1, tzinfo=UTC),
        password_hash="unused",
    )


async def _create(session: Session) -> Project:
    return await projects.create_project(
        ProjectCreate.model_validate({"name": "Кофейня у дома", "template": "max_miniapp"}),
        session,
        owner(),
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from omnia_api.core import minio

    settings = SimpleNamespace(
        minio_public_url="https://objects.example.test/", minio_bucket_previews="previews"
    )
    monkeypatch.setattr(minio, "get_settings", lambda: settings)
    state = SimpleNamespace(order=None, threads={})

    def enqueue(snapshot_id: UUID) -> None:
        state.order.append("preview")
        state.threads["preview"] = threading.get_ident()
        state.enqueued = snapshot_id

    async def publish(project_id: UUID, kind: str, data: dict[str, Any]) -> None:
        state.order.append("event")
        state.event = (project_id, kind, data)

    monkeypatch.setattr(projects, "enqueue_preview", enqueue)
    monkeypatch.setattr(projects, "publish_event", publish)
    monkeypatch.setattr(projects.repo_svc, "init_repo", Mock(return_value="a" * 40))
    return state


async def test_new_project_is_committed_with_its_first_snapshot_then_announced(world):
    session = Session()
    world.order = session.order

    project = await _create(session)

    snapshot = session.snapshot
    assert project is session.project
    assert (snapshot.project_id, snapshot.commit_sha) == (project.id, "a" * 40)
    assert (snapshot.prompt_text, snapshot.model_id) == (None, None)
    assert snapshot.parent_id is None
    assert snapshot.id is not None, "the snapshot must be flushed before it is pointed at"
    assert project.current_snapshot_id == snapshot.id
    assert session.pointer_at_commit == snapshot.id, "the pointer belongs to the same commit"
    assert session.order == ["commit", "refresh-project", "refresh-snapshot", "preview", "event"]
    assert world.enqueued == snapshot.id
    assert world.threads["preview"] != threading.get_ident()
    event_project, kind, data = world.event
    assert (event_project, kind) == (project.id, "snapshot.created")
    assert data["snapshot"]["id"] == str(snapshot.id)
    assert data["snapshot"]["parent_id"] is None


async def test_slug_collision_is_a_conflict_and_announces_nothing(world):
    session = Session(slug_taken=True)
    world.order = session.order

    with pytest.raises(ApiError) as caught:
        await _create(session)

    error = caught.value
    assert (error.code, error.message, error.status_code) == (
        "conflict",
        "slug already exists",
        409,
    )
    assert session.order == ["commit", "rollback"]
    assert not hasattr(world, "enqueued") and not hasattr(world, "event")
