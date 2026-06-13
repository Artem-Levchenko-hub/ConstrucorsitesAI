"""Tests for the preview-render worker's live container-URL resolution."""

from __future__ import annotations

from uuid import uuid4

from omnia_api.services import dev_container
from omnia_api.workers import preview


def test_preview_alias_points_at_shared_resolver() -> None:
    """R-04 single source: the preview job's resolver IS dev_container's."""
    assert preview._resolve_live_url is dev_container.resolve_live_url


async def test_resolve_live_url_running_returns_internal_url(monkeypatch) -> None:
    pid = uuid4()

    async def fake_status(project_id):
        assert project_id == pid
        return {"state": "running", "container_name": "omnia-dev-shop-abc123"}

    monkeypatch.setattr(dev_container.orchestrator_client, "get_status", fake_status)
    assert await dev_container.resolve_live_url(pid) == (
        "http://omnia-dev-shop-abc123:3000"
    )


async def test_resolve_live_url_paused_returns_none(monkeypatch) -> None:
    async def fake_status(project_id):
        return {"state": "paused", "container_name": "omnia-dev-shop-abc123"}

    monkeypatch.setattr(dev_container.orchestrator_client, "get_status", fake_status)
    assert await dev_container.resolve_live_url(uuid4()) is None


async def test_resolve_live_url_missing_name_returns_none(monkeypatch) -> None:
    async def fake_status(project_id):
        return {"state": "running", "container_name": None}

    monkeypatch.setattr(dev_container.orchestrator_client, "get_status", fake_status)
    assert await dev_container.resolve_live_url(uuid4()) is None


async def test_resolve_live_url_orchestrator_error_returns_none(monkeypatch) -> None:
    async def boom(project_id):
        raise RuntimeError("orchestrator down")

    monkeypatch.setattr(dev_container.orchestrator_client, "get_status", boom)
    assert await dev_container.resolve_live_url(uuid4()) is None


def test_container_next_matches_messages_router() -> None:
    """Keep the worker's container-template list in sync with the router's."""
    from omnia_api.routers import messages

    assert preview.CONTAINER_NEXT == messages.CONTAINER_NEXT


class _FakePage:
    """Minimal stand-in capturing wait_for_load_state calls."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, float | None]] = []
        self._raises = raises

    async def wait_for_load_state(self, state: str, **kwargs: object) -> None:
        self.calls.append((state, kwargs.get("timeout")))  # type: ignore[arg-type]
        if self._raises is not None:
            raise self._raises


async def test_await_container_ready_waits_networkidle() -> None:
    """Container settle waits for networkidle with the bounded budget."""
    page = _FakePage()
    await preview._await_container_ready(page)
    assert page.calls == [("networkidle", preview._CONTAINER_NETWORKIDLE_MS)]


async def test_await_container_ready_swallows_timeout() -> None:
    """A slow/long-polling app must never hang or fail the capture (R-10)."""
    page = _FakePage(raises=RuntimeError("Timeout 3500ms exceeded"))
    # Must not raise — falls through to the screenshot.
    await preview._await_container_ready(page)
    assert page.calls == [("networkidle", preview._CONTAINER_NETWORKIDLE_MS)]
