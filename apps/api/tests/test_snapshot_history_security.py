from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Response

from omnia_api.core.errors import ApiError
from omnia_api.routers import snapshots


class _Session:
    def __init__(self, project, snapshot) -> None:
        self.project = project
        self.snapshot = snapshot

    async def get(self, model, ident):
        if model.__name__ == "Project":
            return self.project
        if model.__name__ == "Snapshot":
            return self.snapshot
        return None


def test_incompatible_history_session_returns_409_before_orchestrator(monkeypatch) -> None:
    owner_id = uuid4()
    project_id = uuid4()
    snapshot_id = uuid4()
    project = SimpleNamespace(
        id=project_id,
        owner_id=owner_id,
        template="max_miniapp",
        name="Legacy",
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        project_id=project_id,
        commit_sha="a" * 40,
    )
    monkeypatch.setattr(
        snapshots.repo_svc,
        "read_files",
        lambda *_args: {
            "src/app/page.tsx": "export default function Page() { return null; }",
            "src/instrumentation.ts": "export function register() {}",
        },
    )
    started = False

    async def start(*_args, **_kwargs):
        nonlocal started
        started = True
        return {}

    monkeypatch.setattr(
        snapshots.orchestrator_client,
        "start_history_preview_session",
        start,
    )

    with pytest.raises(ApiError) as raised:
        asyncio.run(
            snapshots.start_snapshot_session(
                project_id,
                snapshot_id,
                _Session(project, snapshot),
                SimpleNamespace(id=owner_id),
            )
        )

    assert raised.value.status_code == 409
    assert started is False


def test_incompatible_snapshot_preview_returns_409_before_enqueue(monkeypatch) -> None:
    owner_id = uuid4()
    project_id = uuid4()
    snapshot_id = uuid4()
    project = SimpleNamespace(
        id=project_id,
        owner_id=owner_id,
        template="max_miniapp",
        name="Legacy",
    )
    snapshot = SimpleNamespace(
        id=snapshot_id,
        project_id=project_id,
        commit_sha="b" * 40,
        preview_key=None,
    )
    monkeypatch.setattr(
        snapshots.repo_svc,
        "read_files",
        lambda *_args: {
            "src/app/page.tsx": "export default function Page() { return null; }",
            "src/proxy.ts": "export default function proxy() {}",
        },
    )
    enqueued = False

    def enqueue(*_args, **_kwargs):
        nonlocal enqueued
        enqueued = True
        return True

    monkeypatch.setattr(snapshots, "enqueue_preview", enqueue)

    with pytest.raises(ApiError) as raised:
        asyncio.run(
            snapshots.prepare_snapshot_preview(
                project_id,
                snapshot_id,
                Response(),
                _Session(project, snapshot),
                SimpleNamespace(id=owner_id),
            )
        )

    assert raised.value.status_code == 409
    assert enqueued is False


def test_reserved_project_refresh_uses_latest_snapshot_before_dispatch() -> None:
    from omnia_api.routers.messages import _refresh_reserved_project

    owner_id = uuid4()
    latest_snapshot_id = uuid4()
    project = SimpleNamespace(owner_id=owner_id, current_snapshot_id=uuid4())

    class RefreshingSession:
        async def refresh(self, target, **kwargs):
            assert kwargs == {"with_for_update": True}
            target.current_snapshot_id = latest_snapshot_id

    refreshed = asyncio.run(_refresh_reserved_project(RefreshingSession(), project, owner_id))

    assert refreshed.current_snapshot_id == latest_snapshot_id
