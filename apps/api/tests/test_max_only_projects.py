"""Only MAX Mini Apps can be created here; the site-builder entry points are gone.

The classic site builder — static and container templates, GitHub import, the
zero-signup fork/remix loop with its anonymous owners, the lead inbox and the public
``/p/<slug>`` preview — lives in the neighbour project ``omnia-sitebuilder``. What this
repository keeps is pinned below, so none of it quietly comes back.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.main import app
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.routers import projects as projects_router
from yleum_api.services import repo as repo_svc

pytestmark = pytest.mark.asyncio

SITE_BUILDER_TEMPLATES = [
    "blank",
    "landing",
    "portfolio",
    "blog",
    "fullstack",
    "nextjs_entities",
    "spa",
    "tgbot",
    "api",
    "code",
    "realtime",
]


async def _count(session: AsyncSession, model: type) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def init_repo(*args: object) -> str:
        calls.append(args)
        return "a" * 40

    monkeypatch.setattr(repo_svc, "init_repo", init_repo)
    return calls


async def _register(client: httpx.AsyncClient, email: str = "owner@example.com") -> None:
    response = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert response.status_code == 201, response.text


async def test_registered_user_creates_a_max_project(client, db_session, git, monkeypatch):
    previews: list[object] = []
    monkeypatch.setattr(projects_router, "enqueue_preview", previews.append)
    await _register(client)

    response = await client.post(
        "/api/projects", json={"name": "Запись клиентов", "template": "max_miniapp"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["template"] == "max_miniapp"
    assert body["slug"].startswith("zapis-klientov-")
    snapshot = (await db_session.execute(select(Snapshot))).scalar_one()
    assert body["current_snapshot_id"] == str(snapshot.id)
    assert (snapshot.prompt_text, snapshot.parent_id, snapshot.commit_sha) == (None, None, "a" * 40)
    assert previews == [snapshot.id]
    ((_project_id, template_dir, template),) = git
    assert template == "max_miniapp" and str(template_dir).endswith("templates/max_miniapp")


async def test_owner_reads_the_project_back_and_nobody_else_does(client, git):
    await _register(client)
    created = (await client.post("/api/projects", json={"name": "Запись клиентов"})).json()

    one = await client.get(f"/api/projects/{created['id']}")
    listed = await client.get("/api/projects")

    assert one.status_code == 200, one.text
    assert one.json()["id"] == created["id"]
    assert one.json()["current_snapshot_id"] == created["current_snapshot_id"]
    assert (one.json()["forked_from_name"], one.json()["forked_from_slug"]) == (None, None)
    assert [row["id"] for row in listed.json()] == [created["id"]]

    client.cookies.clear()
    await _register(client, "stranger@example.com")
    assert (await client.get(f"/api/projects/{created['id']}")).status_code == 404
    assert (await client.get("/api/projects")).json() == []


async def test_visitor_without_an_account_cannot_create_a_project(client, db_session, git):
    response = await client.post(
        "/api/projects", json={"name": "Запись клиентов", "template": "max_miniapp"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "max_registration_required"
    assert "set-cookie" not in response.headers
    assert await _count(db_session, User) == 0, "no throwaway owner may be minted"
    assert await _count(db_session, Project) == 0 and git == []


@pytest.mark.parametrize("template", SITE_BUILDER_TEMPLATES)
async def test_site_builder_templates_are_refused(client, db_session, git, template):
    await _register(client)

    response = await client.post("/api/projects", json={"name": "Сайт", "template": template})

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed" and "template" in str(error["details"])
    assert await _count(db_session, Project) == 0 and git == []


async def test_template_may_be_omitted(client, db_session, git):
    await _register(client)

    response = await client.post("/api/projects", json={"name": "Запись клиентов"})

    assert response.status_code == 201, response.text
    assert response.json()["template"] == "max_miniapp"


async def test_separated_entry_points_are_not_routed():
    paths = {getattr(route, "path", "") for route in app.routes}

    assert {
        "/api/projects/import",
        "/api/projects/{project_id}/claim",
        "/api/projects/{project_id}/fork",
        "/api/projects/{project_id}/leads",
    }.isdisjoint(paths)
    assert not [path for path in paths if path == "/p" or path.startswith("/p/")]
    assert "/api/projects" in paths and "/api/projects/{project_id}/download" in paths


async def test_cell_previews_still_get_the_inspector_from_the_kit_route(client):
    """Every Project Cell draft vhost proxies `/_omnia/inspector.js` to this address."""
    response = await client.get("/api/kit/omnia-inspector.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "omnia:inspect" in response.text
