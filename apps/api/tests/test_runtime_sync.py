from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.models.project import Project
from omnia_api.services import runtime_sync


def _project() -> Project:
    project = Project(
        id=uuid4(),
        owner_id=uuid4(),
        name="MAX app",
        slug="max-app",
        template="fullstack",
        language="ru",
    )
    project.current_snapshot_id = uuid4()
    project.runtime_sync_required = True
    project.runtime_sync_paths = ["src/app/page.tsx", "src/old.ts"]
    return project


def test_mark_runtime_sync_required_merges_paths() -> None:
    project = _project()

    runtime_sync.mark_runtime_sync_required(
        project,
        ["src/new.ts", "src/app/page.tsx", ""],
    )

    assert project.runtime_sync_required is True
    assert project.runtime_sync_paths == [
        "src/app/page.tsx",
        "src/new.ts",
        "src/old.ts",
    ]


@pytest.mark.asyncio
async def test_reconcile_applies_canonical_writes_and_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    snapshot = SimpleNamespace(commit_sha="abc123")
    session = SimpleNamespace(
        get=AsyncMock(return_value=snapshot),
        flush=AsyncMock(),
    )
    monkeypatch.setattr(
        runtime_sync.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"state": "running"}),
    )
    monkeypatch.setattr(
        runtime_sync.repo_svc,
        "read_files",
        lambda *_args: {"src/app/page.tsx": "canonical"},
    )
    hot_reload = AsyncMock(return_value={"written": 1, "deleted": 1})
    monkeypatch.setattr(runtime_sync.orchestrator_client, "hot_reload_exact", hot_reload)

    synced = await runtime_sync.reconcile_locked_runtime(
        session,
        project,
        ensure_running=False,
    )

    assert synced is True
    hot_reload.assert_awaited_once_with(
        project.id,
        project.slug,
        {"src/app/page.tsx": "canonical", "src/old.ts": ""},
    )
    assert project.runtime_sync_required is False
    assert project.runtime_sync_paths == []
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_keeps_guard_when_runtime_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    session = SimpleNamespace(get=AsyncMock(), flush=AsyncMock())
    monkeypatch.setattr(
        runtime_sync.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"state": "stopped"}),
    )

    synced = await runtime_sync.reconcile_locked_runtime(
        session,
        project,
        ensure_running=False,
    )

    assert synced is False
    assert project.runtime_sync_required is True
    assert project.runtime_sync_paths
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_reconcile_restores_snapshot_and_deletes_stale_starter_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    snapshot = SimpleNamespace(commit_sha="abc123")
    session = SimpleNamespace(get=AsyncMock(return_value=snapshot), flush=AsyncMock())
    monkeypatch.setattr(
        runtime_sync.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"state": "running"}),
    )
    monkeypatch.setattr(
        runtime_sync.orchestrator_client,
        "agent_list_source_files",
        AsyncMock(return_value=["src/app/page.tsx", "src/starter-only.ts"]),
    )
    monkeypatch.setattr(
        runtime_sync.repo_svc,
        "read_files",
        lambda *_args: {
            "src/app/page.tsx": "canonical",
            "src/components/Product.tsx": "product",
        },
    )
    hot_reload = AsyncMock(return_value={"written": 2, "deleted": 1})
    monkeypatch.setattr(runtime_sync.orchestrator_client, "hot_reload_exact", hot_reload)

    synced = await runtime_sync.reconcile_locked_runtime(
        session,
        project,
        ensure_running=False,
        full_tree=True,
    )

    assert synced is True
    hot_reload.assert_awaited_once_with(
        project.id,
        project.slug,
        {
            "src/starter-only.ts": "",
            "src/app/page.tsx": "canonical",
            "src/components/Product.tsx": "product",
        },
    )


@pytest.mark.asyncio
async def test_full_max_reconcile_never_deletes_platform_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    project.template = "max_miniapp"
    snapshot = SimpleNamespace(commit_sha="abc123")
    config = runtime_sync.default_max_project_config("Клиентский FitFlow")
    config_record = SimpleNamespace(config=config.model_dump(mode="json"))

    async def get_model(model: object, _key: object) -> object | None:
        if model is runtime_sync.Snapshot:
            return snapshot
        if model is runtime_sync.MaxProjectConfig:
            return config_record
        return None

    session = SimpleNamespace(get=AsyncMock(side_effect=get_model), flush=AsyncMock())
    monkeypatch.setattr(
        runtime_sync.orchestrator_client,
        "get_status",
        AsyncMock(return_value={"state": "running"}),
    )
    monkeypatch.setattr(
        runtime_sync.orchestrator_client,
        "agent_list_source_files",
        AsyncMock(
            return_value=[
                "package.json",
                "src/app/page.tsx",
                "src/lib/omnia/client.ts",
                "src/components/product/ProductApp.tsx",
                "src/components/product/OldProduct.tsx",
            ]
        ),
    )
    monkeypatch.setattr(
        runtime_sync.repo_svc,
        "read_files",
        lambda *_args: {
            "package.json": "untrusted old core",
            "src/app/page.tsx": "untrusted old route",
            "src/components/product/ProductApp.tsx": "canonical product",
        },
    )
    hot_reload = AsyncMock(return_value={"written": 1, "deleted": 1})
    monkeypatch.setattr(runtime_sync.orchestrator_client, "hot_reload_exact", hot_reload)

    synced = await runtime_sync.reconcile_locked_runtime(
        session,
        project,
        ensure_running=False,
        full_tree=True,
    )

    assert synced is True
    hot_reload.assert_awaited_once()
    call = hot_reload.await_args
    assert call is not None
    runtime_patch = call.args[2]
    assert call.args[:2] == (project.id, project.slug)
    assert runtime_patch["src/components/product/OldProduct.tsx"] == ""
    assert runtime_patch["src/components/product/ProductApp.tsx"] == "canonical product"
    assert "Клиентский FitFlow" in runtime_patch["src/lib/omnia/max-config.ts"]
    assert str(project.id) in runtime_patch["src/app/api/omnia/preview-session/route.ts"]
    assert runtime_patch["package.json"]
    assert runtime_patch["src/app/page.tsx"]
    assert runtime_patch["src/lib/omnia/client.ts"]
