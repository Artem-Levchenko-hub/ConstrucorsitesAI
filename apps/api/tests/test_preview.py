"""Tests for the preview-render worker's live container-URL resolution."""

from __future__ import annotations

from uuid import uuid4

from omnia_api.workers import preview


async def test_resolve_live_url_running_returns_internal_url(monkeypatch) -> None:
    pid = uuid4()

    async def fake_status(project_id):
        assert project_id == pid
        return {"state": "running", "container_name": "omnia-dev-shop-abc123"}

    monkeypatch.setattr(preview.orchestrator_client, "get_status", fake_status)
    assert await preview._resolve_live_url(pid) == (
        "http://omnia-dev-shop-abc123:3000"
    )


async def test_resolve_live_url_paused_returns_none(monkeypatch) -> None:
    async def fake_status(project_id):
        return {"state": "paused", "container_name": "omnia-dev-shop-abc123"}

    monkeypatch.setattr(preview.orchestrator_client, "get_status", fake_status)
    assert await preview._resolve_live_url(uuid4()) is None


async def test_resolve_live_url_missing_name_returns_none(monkeypatch) -> None:
    async def fake_status(project_id):
        return {"state": "running", "container_name": None}

    monkeypatch.setattr(preview.orchestrator_client, "get_status", fake_status)
    assert await preview._resolve_live_url(uuid4()) is None


async def test_resolve_live_url_orchestrator_error_returns_none(monkeypatch) -> None:
    async def boom(project_id):
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(preview.orchestrator_client, "get_status", boom)
    assert await preview._resolve_live_url(uuid4()) is None


def test_container_next_matches_messages_router() -> None:
    """Keep the worker's container-template list in sync with the router's."""
    from omnia_api.routers import messages

    assert preview.CONTAINER_NEXT == messages.CONTAINER_NEXT
