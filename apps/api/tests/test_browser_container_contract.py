"""Task10B routing baseline: actual callers; no screenshot/runtime/model acceptance."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers import messages, rollback, runtime
from omnia_api.schemas.runtime import RuntimeStatus
from omnia_api.workers import preview

BROWSER_CONTAINERS = ("fullstack", "nextjs_entities", "spa", "realtime", "max_miniapp")
NON_BROWSER = ("blank", "landing", "portfolio", "blog", "api", "tgbot", "code")


def test_current_ordered_browser_facets_are_exact():
    assert messages.CONTAINER_NEXT == BROWSER_CONTAINERS
    assert preview.CONTAINER_NEXT == BROWSER_CONTAINERS
    assert runtime._CONTAINER_NEXT == BROWSER_CONTAINERS
    assert rollback._CONTAINER_NEXT == BROWSER_CONTAINERS


class Session:
    def __init__(self, project, snapshot):
        self.project, self.snapshot = project, snapshot

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, model, identity):
        if model is Project and identity == self.project.id:
            return self.project
        if model is Snapshot and identity == self.snapshot.id:
            return self.snapshot
        return None


def rows(template):
    owner, project_id, snapshot_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(
        id=project_id,
        owner_id=owner,
        template=template,
        slug="fixture",
        current_snapshot_id=snapshot_id,
    )
    snapshot = SimpleNamespace(id=snapshot_id, project_id=project_id, commit_sha="a" * 40)
    return project, snapshot, SimpleNamespace(id=owner)


class ReachedNavigation(BaseException):
    """Stop before actual browser/network/PNG/MinIO; prove selected navigation only."""


@pytest.mark.parametrize("template", [*BROWSER_CONTAINERS, *NON_BROWSER])
@pytest.mark.parametrize("has_index", [True, False])
async def test_preview_actual_renderer_selects_source(template, has_index, monkeypatch):
    project, snapshot, _owner = rows(template)
    session = Session(project, snapshot)
    engine = SimpleNamespace(dispose=AsyncMock())
    monkeypatch.setattr(preview, "create_async_engine", lambda *_a, **_kw: engine)
    monkeypatch.setattr(preview, "async_sessionmaker", lambda *_a, **_kw: lambda: session)
    files = {"index.html": "<html>fixture</html>"} if has_index else {"src/app.tsx": "fixture"}
    read = Mock(return_value=files)
    monkeypatch.setattr(preview.repo_svc, "read_files", read)
    live = AsyncMock(return_value="http://fixture-internal.invalid:3000")
    monkeypatch.setattr(preview, "_resolve_live_url", live)
    monkeypatch.setattr(preview, "_block_external_fonts", AsyncMock())
    monkeypatch.setattr(preview, "_route_media_internal", AsyncMock())
    visited = []

    async def goto(url, **kwargs):
        visited.append((url, kwargs))
        raise ReachedNavigation

    browser = SimpleNamespace(
        new_page=AsyncMock(return_value=SimpleNamespace(goto=goto)), close=AsyncMock()
    )

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(preview, "async_playwright", Playwright)
    renders = template != "max_miniapp" and (has_index or template in BROWSER_CONTAINERS)
    if renders:
        with pytest.raises(ReachedNavigation):
            await preview._render_async(str(snapshot.id))
        assert len(visited) == 1
        url, options = visited[0]
        assert options["wait_until"] == "domcontentloaded"
        assert (url == "http://fixture-internal.invalid:3000") == (template in BROWSER_CONTAINERS)
        if template not in BROWSER_CONTAINERS:
            assert url.startswith("file:") and url.endswith("index.html")
        browser.close.assert_awaited_once()
    else:
        await preview._render_async(str(snapshot.id))
        assert visited == []
    assert live.await_count == (1 if template in BROWSER_CONTAINERS[:-1] else 0)
    assert read.call_count == (0 if template == "max_miniapp" else 1)
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("template", [*BROWSER_CONTAINERS, *NON_BROWSER])
async def test_runtime_actual_start_keeps_resync_autoheal_facet(template, monkeypatch):
    project, snapshot, owner = rows(template)
    session = Session(project, snapshot)
    monkeypatch.setattr(
        runtime.project_cell_runtime, "start_project_cell_runtime", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        runtime,
        "_billing_plan_for_user",
        AsyncMock(return_value=(None, SimpleNamespace(code="free"))),
    )
    provision = AsyncMock(return_value={"state": "running"})
    reload = AsyncMock(return_value={"written": 1})
    heal = AsyncMock(return_value="fixture")
    monkeypatch.setattr(runtime.orchestrator_client, "provision", provision)
    monkeypatch.setattr(runtime.orchestrator_client, "hot_reload", reload)
    monkeypatch.setattr(runtime.autoheal_svc, "maybe_autoheal_on_open", heal)
    read = Mock(return_value={"src/a.ts": "fixture"})
    monkeypatch.setattr(runtime.repo_svc, "read_files", read)
    status = await runtime.start_runtime(project.id, session, owner)
    await asyncio.sleep(0)  # Drain the actual background auto-heal coroutine.
    assert status.state == "running"
    provision.assert_awaited_once()
    expected = 1 if template in BROWSER_CONTAINERS else 0
    assert read.call_count == reload.await_count == heal.await_count == expected
    expected_template = {
        "api": "fastapi-postgres",
        "tgbot": "telegram-bot-aiogram",
        "code": "nextjs-postgres-drizzle",
    }
    if template in expected_template:
        # Exclusion from browser resync does not mean exclusion from provisioning.
        assert provision.await_args.kwargs["template"] == expected_template[template]


async def test_runtime_selected_cell_bypasses_legacy_provision_resync_autoheal(monkeypatch):
    project, snapshot, owner = rows("max_miniapp")
    cell = RuntimeStatus(state="running", dev_url="https://fixture.invalid")
    monkeypatch.setattr(
        runtime.project_cell_runtime, "start_project_cell_runtime", AsyncMock(return_value=cell)
    )
    blocked = AsyncMock(side_effect=AssertionError("legacy path reached for selected Cell"))
    monkeypatch.setattr(runtime.orchestrator_client, "provision", blocked)
    monkeypatch.setattr(runtime, "_billing_plan_for_user", blocked)
    monkeypatch.setattr(runtime, "_resync_latest_snapshot", blocked)
    monkeypatch.setattr(runtime.autoheal_svc, "maybe_autoheal_on_open", blocked)
    assert await runtime.start_runtime(project.id, Session(project, snapshot), owner) is cell
    blocked.assert_not_awaited()


@pytest.mark.parametrize("template", [*BROWSER_CONTAINERS, *NON_BROWSER])
def test_rollback_actual_handler_browser_and_negative_facets(template, monkeypatch):
    from tests.test_rollback_container_hot_reload import (
        _make_project,
        _patch_common,
        _run_rollback,
    )

    hot_calls = []
    _patch_common(monkeypatch, hot_calls)
    project = _make_project(template)
    if template == "max_miniapp":
        old = project.current_snapshot_id
        checkout = Mock(side_effect=AssertionError("MAX must reject before Git"))
        monkeypatch.setattr(rollback.repo_svc, "checkout", checkout)
        with pytest.raises(ApiError) as error:
            _run_rollback(project)
        assert error.value.status_code == 409
        assert project.current_snapshot_id == old and hot_calls == []
        checkout.assert_not_called()
    else:
        session, _result = _run_rollback(project)
        assert session.committed
        assert len(hot_calls) == (1 if template in BROWSER_CONTAINERS else 0)
