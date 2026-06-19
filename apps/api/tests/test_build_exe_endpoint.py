"""Owner-scoped POST /{project_id}/build-exe endpoint tests.

Covers:
  (a) foreign project → 404
  (b) use_exe_build flag off → 404
  (c) project with only non-Python files → 400
  (d) feature flag on + Python project → 202 + build_id returned
      (enqueue_build_exe is monkeypatched to a no-op so no real RQ needed)

Mirrors test_projects_delete.py: uses the same _make_user / _make_project
helpers and the `as_user` fixture that overrides the auth dependency.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.config import get_settings
from omnia_api.core.deps import get_current_user
from omnia_api.main import app
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.models.wallet import Wallet

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────


async def _make_user(session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    user.wallet = Wallet(balance_rub=Decimal("0"))
    session.add(user)
    await session.flush()
    return user


async def _make_project(
    session: AsyncSession,
    owner: User,
    *,
    template: str = "blank",
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


async def _attach_snapshot(
    session: AsyncSession,
    project: Project,
    commit_sha: str = "deadbeef" * 5,
) -> Snapshot:
    snap = Snapshot(
        project_id=project.id,
        commit_sha=commit_sha,
        prompt_text=None,
        model_id=None,
        parent_id=None,
    )
    session.add(snap)
    await session.flush()
    project.current_snapshot_id = snap.id
    await session.flush()
    return snap


# ── fixtures ──────────────────────────────────────────────────────────────────


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
def fake_enqueue(monkeypatch):
    """Capture enqueue_build_exe calls and replace with a no-op."""
    calls: list[tuple] = []

    def _fake(project_id, build_id, slug, files):
        calls.append((project_id, build_id, slug, files))

    monkeypatch.setattr(
        "omnia_api.routers.projects.enqueue_build_exe", _fake
    )
    return calls


@pytest_asyncio.fixture
def fake_read_files(monkeypatch):
    """Return a minimal Python project file dict without touching git."""
    _files = {"main.py": 'print("hello")', "requirements.txt": ""}

    def _read(project_id, commit_sha):
        return dict(_files)

    monkeypatch.setattr("omnia_api.services.repo.read_files", _read)
    return _files


@pytest_asyncio.fixture
def fake_read_files_no_python(monkeypatch):
    """Return files with no .py files to trigger the 400 path."""
    _files = {"index.html": "<h1>hi</h1>"}

    def _read(project_id, commit_sha):
        return dict(_files)

    monkeypatch.setattr("omnia_api.services.repo.read_files", _read)
    return _files


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_build_exe_flag_off_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_enqueue,
    monkeypatch,
) -> None:
    """When use_exe_build=False the endpoint is invisible (404)."""
    owner = await _make_user(db_session, "flag-off@example.com")
    project = await _make_project(db_session, owner)
    await db_session.commit()
    as_user(owner)

    # Ensure flag is off regardless of environment.
    settings = get_settings()
    monkeypatch.setattr(settings, "use_exe_build", False)

    resp = await client.post(f"/api/projects/{project.id}/build-exe")
    assert resp.status_code == 404
    assert fake_enqueue == []


async def test_build_exe_foreign_project_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_enqueue,
    fake_read_files,
    monkeypatch,
) -> None:
    """A project owned by another user returns 404 (owner-scoping)."""
    owner = await _make_user(db_session, "owner-foreign@example.com")
    other = await _make_user(db_session, "intruder-foreign@example.com")
    project = await _make_project(db_session, owner)
    await _attach_snapshot(db_session, project)
    await db_session.commit()
    as_user(other)  # authenticated as somebody else

    settings = get_settings()
    monkeypatch.setattr(settings, "use_exe_build", True)

    resp = await client.post(f"/api/projects/{project.id}/build-exe")
    assert resp.status_code == 404
    assert fake_enqueue == []


async def test_build_exe_no_python_files_returns_400(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_enqueue,
    fake_read_files_no_python,
    monkeypatch,
) -> None:
    """A project with no .py files returns 400 (exe build is Python-only)."""
    owner = await _make_user(db_session, "owner-nopy@example.com")
    project = await _make_project(db_session, owner)
    await _attach_snapshot(db_session, project)
    await db_session.commit()
    as_user(owner)

    settings = get_settings()
    monkeypatch.setattr(settings, "use_exe_build", True)

    resp = await client.post(f"/api/projects/{project.id}/build-exe")
    assert resp.status_code == 400
    body = resp.json()
    assert "Python-only" in body.get("detail", "") or "Python" in str(body)
    assert fake_enqueue == []


async def test_build_exe_python_project_enqueues_and_returns_build_id(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    as_user,
    fake_enqueue,
    fake_read_files,
    monkeypatch,
) -> None:
    """A valid Python project with the flag on enqueues and returns a build_id."""
    owner = await _make_user(db_session, "owner-ok@example.com")
    project = await _make_project(db_session, owner)
    await _attach_snapshot(db_session, project)
    await db_session.commit()
    as_user(owner)

    settings = get_settings()
    monkeypatch.setattr(settings, "use_exe_build", True)

    resp = await client.post(f"/api/projects/{project.id}/build-exe")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "build_id" in body
    assert len(body["build_id"]) > 0

    # Exactly one enqueue call was made.
    assert len(fake_enqueue) == 1
    _proj_id, build_id, slug, files = fake_enqueue[0]
    assert str(project.id) == str(_proj_id)
    assert build_id == body["build_id"]
    assert slug == project.slug
    assert "main.py" in files
