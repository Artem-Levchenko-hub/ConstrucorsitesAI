"""Владелец может сохранить или отбросить несохранённые правки черновика.

Откат честно отказывает, когда файлы черновика отличаются от сохранённой версии
(verify_source_inventory): откатывать поверх несохранённой работы — значит молча
её потерять. Но у владельца не было ни одного способа привести черновик в
порядок: обычная генерация падала с unsafe_changes_rolled_back, а агентские
маршруты требуют активную аренду генерации. Эти два маршрута дают платформе
ровно два осознанных действия — прочитать черновик как есть (для «сохранить как
версию») и переписать его файлы (для «отбросить правки») — и работают только
между генерациями: аренда есть → отказ, чтобы два писателя не встретились.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.routers import workspace
from omnia_orchestrator.services.docker_owner_canary_provider import DockerOwnerCanaryProvider
from tests.test_cell_checkpoint import _make_fixture as _make_checkpoint_fixture

_TOKEN = "test-internal-token-not-a-real-secret"
_PAGE = "src/app/page.tsx"
_SAVED = "export default function Page() { return null }\n"
_EDITED = "export default function Page() { return <main>edited</main> }\n"


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.include_router(workspace.router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def _internal_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
    monkeypatch.setenv("INTERNAL_TOKEN", _TOKEN)
    monkeypatch.delenv("WORKSPACE_PROVIDER", raising=False)
    monkeypatch.delenv("DOCKER_OWNER_CANARY_ENABLED", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


async def _ready(tmp_path, monkeypatch, *, generation_run_id: UUID | None):
    manager, checkpoints, docker = _make_checkpoint_fixture(tmp_path)
    provider = DockerOwnerCanaryProvider(resource_manager=manager, checkpoint_manager=checkpoints)
    workspace_id = uuid4()
    await provider.ensure(
        WorkspaceSpec(
            workspace_id=workspace_id,
            project_id=UUID("00000000-0000-0000-0000-000000000092"),
            owner_id=UUID("00000000-0000-0000-0000-000000000093"),
            profile_version="docker-owner-cell-resources-v1",
            generation_run_id=generation_run_id,
        ),
        workspace.LifecycleMutation(UUID(int=76), 4, "a" * 64),
    )
    state = manager.state_store.load(workspace_id)
    assert state is not None and state.resource_names is not None
    volume = state.resource_names.workspace_volume
    await docker.write_volume_files(
        volume, {_PAGE: _EDITED.encode(), "src/lib/scratch.ts": b"export const x = 1\n"}
    )
    monkeypatch.setattr(workspace, "build_workspace_provider", lambda _settings: provider)
    return workspace_id, docker, volume


async def test_draft_files_are_readable_between_generations(tmp_path, monkeypatch) -> None:
    workspace_id, _docker, _volume = await _ready(tmp_path, monkeypatch, generation_run_id=None)

    async with _client() as client:
        response = await client.get(
            f"/internal/workspaces/{workspace_id}/draft/files",
            headers={"X-Internal-Token": _TOKEN},
        )

    assert response.status_code == 200
    files = response.json()["files"]
    assert files[_PAGE] == _EDITED
    assert files["src/lib/scratch.ts"] == "export const x = 1\n"
    assert response.json()["workspace_revision"] == workspace._workspace_revision(files)


async def test_draft_actions_refuse_while_a_generation_holds_the_lease(
    tmp_path, monkeypatch
) -> None:
    """Аренда генерации — единственный писатель; владелец ждёт её конца."""
    workspace_id, _docker, _volume = await _ready(tmp_path, monkeypatch, generation_run_id=uuid4())

    async with _client() as client:
        read = await client.get(
            f"/internal/workspaces/{workspace_id}/draft/files",
            headers={"X-Internal-Token": _TOKEN},
        )
        reset = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/reset",
            headers={"X-Internal-Token": _TOKEN},
            json={"expected_revision": "0" * 64, "files": {_PAGE: _SAVED}, "deletes": []},
        )

    assert read.status_code == 409
    assert read.json()["error"]["message"] == "workspace generation lease is active"
    assert reset.status_code == 409
    assert reset.json()["error"]["message"] == "workspace generation lease is active"


async def test_reset_rewrites_and_deletes_only_what_differs(tmp_path, monkeypatch) -> None:
    workspace_id, docker, volume = await _ready(tmp_path, monkeypatch, generation_run_id=None)
    current = workspace._workspace_revision(
        {_PAGE: _EDITED, "src/lib/scratch.ts": "export const x = 1\n"}
    )

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/reset",
            headers={"X-Internal-Token": _TOKEN},
            json={
                "expected_revision": current,
                "files": {_PAGE: _SAVED},
                "deletes": ["src/lib/scratch.ts"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["written"] == 1 and body["deleted"] == 1
    assert body["workspace_revision"] == workspace._workspace_revision({_PAGE: _SAVED})
    stored = await docker.read_workspace_source_files(volume)
    assert stored[_PAGE] == _SAVED.encode()
    assert "src/lib/scratch.ts" not in stored


async def test_reset_refuses_a_stale_revision(tmp_path, monkeypatch) -> None:
    """Черновик изменился, пока владелец решал: переписывать вслепую нельзя."""
    workspace_id, docker, volume = await _ready(tmp_path, monkeypatch, generation_run_id=None)

    async with _client() as client:
        response = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/reset",
            headers={"X-Internal-Token": _TOKEN},
            json={"expected_revision": "f" * 64, "files": {_PAGE: _SAVED}, "deletes": []},
        )

    assert response.status_code == 409
    stored = await docker.read_workspace_source_files(volume)
    assert stored[_PAGE] == _EDITED.encode()


async def test_draft_actions_require_the_internal_token(tmp_path, monkeypatch) -> None:
    workspace_id, _docker, _volume = await _ready(tmp_path, monkeypatch, generation_run_id=None)

    async with _client() as client:
        read = await client.get(f"/internal/workspaces/{workspace_id}/draft/files")
        reset = await client.post(
            f"/internal/workspaces/{workspace_id}/draft/reset",
            json={"expected_revision": "0" * 64, "files": {}, "deletes": []},
        )

    assert read.status_code in {401, 403}
    assert reset.status_code in {401, 403}
