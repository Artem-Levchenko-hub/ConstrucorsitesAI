"""Delete-project teardown + owner-scoping (P1).

Covers the `DELETE /api/projects/{id}` contract: owner-scoping (404 missing /
403 foreign), runtime teardown only for container-backed templates, git-repo
removal, DB cascade, and idempotency. Orchestrator + MinIO side effects are
faked so the test stays a fast unit of the handler logic (the real teardown is
exercised live in E2E).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.user import User

pytestmark = pytest.mark.asyncio


async def _make_user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _make_project(
    session: AsyncSession, owner: User, *, template: str = "blank"
) -> Project:
    project = Project(
        owner_id=owner.id,
        name="Test Project",
        slug=f"test-{uuid.uuid4().hex[:6]}",
        template=template,
    )
    session.add(project)
    await session.flush()
    return project


@pytest_asyncio.fixture
async def as_user(db_session: AsyncSession):
    """Authenticate the test client as a chosen User by overriding the auth dep."""

    def _login(user: User) -> None:
        async def _override() -> User:
            return user

        app.dependency_overrides[get_current_user] = _override

    yield _login
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def fake_teardown(monkeypatch):
    """Record orchestrator.destroy calls and stub MinIO repo deletion."""
    calls: dict[str, list] = {"destroy": [], "repo": []}

    async def _destroy(project_id, slug):
        calls["destroy"].append((project_id, slug))
        return {"state": "destroyed"}

    def _delete_repo(project_id):
        calls["repo"].append(project_id)

    monkeypatch.setattr(
        "omnia_api.services.orchestrator_client.destroy", _destroy
    )
    monkeypatch.setattr("omnia_api.services.repo.delete_repo", _delete_repo)
    return calls


async def test_delete_static_project_skips_orchestrator(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner@example.com")
    project = await _make_project(db_session, owner, template="landing")
    await db_session.commit()
    as_user(owner)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 204
    assert await db_session.get(Project, project.id) is None
    # Static template → no container to tear down.
    assert fake_teardown["destroy"] == []
    # Git repo is always cleaned up.
    assert fake_teardown["repo"] == [project.id]


async def test_delete_container_project_tears_down_runtime(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner2@example.com")
    project = await _make_project(db_session, owner, template="nextjs_entities")
    slug = project.slug
    await db_session.commit()
    as_user(owner)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 204
    assert await db_session.get(Project, project.id) is None
    assert fake_teardown["destroy"] == [(project.id, slug)]
    assert fake_teardown["repo"] == [project.id]


async def test_delete_cascades_messages(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner3@example.com")
    project = await _make_project(db_session, owner)
    msg = Message(project_id=project.id, role="user", content="hi")
    db_session.add(msg)
    await db_session.commit()
    msg_id = msg.id
    as_user(owner)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 204
    assert await db_session.get(Message, msg_id) is None


async def test_delete_foreign_project_forbidden(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner4@example.com")
    other = await _make_user(db_session, "intruder@example.com")
    project = await _make_project(db_session, owner)
    await db_session.commit()
    as_user(other)

    resp = await client.delete(f"/api/projects/{project.id}")

    assert resp.status_code == 403
    # Untouched — still there, no teardown ran.
    assert await db_session.get(Project, project.id) is not None
    assert fake_teardown["destroy"] == []
    assert fake_teardown["repo"] == []


async def test_delete_missing_project_not_found(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner5@example.com")
    await db_session.commit()
    as_user(owner)

    resp = await client.delete(f"/api/projects/{uuid.uuid4()}")

    assert resp.status_code == 404


async def test_delete_is_idempotent_second_call_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_teardown,
) -> None:
    owner = await _make_user(db_session, "owner6@example.com")
    project = await _make_project(db_session, owner, template="nextjs_entities")
    await db_session.commit()
    as_user(owner)

    first = await client.delete(f"/api/projects/{project.id}")
    second = await client.delete(f"/api/projects/{project.id}")

    assert first.status_code == 204
    assert second.status_code == 404
