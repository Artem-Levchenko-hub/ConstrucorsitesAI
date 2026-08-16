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


# ── 16/5d route param: the gate can target a content route, not just `/` ───────


def test_normalize_route_root_is_no_suffix() -> None:
    """Root / empty contribute no suffix → default-route URL byte-identical."""
    assert dev_container._normalize_route("/") == ""
    assert dev_container._normalize_route("") == ""


def test_normalize_route_adds_leading_slash() -> None:
    assert dev_container._normalize_route("/dashboard") == "/dashboard"
    assert dev_container._normalize_route("dashboard") == "/dashboard"


async def test_resolve_live_url_appends_route(monkeypatch) -> None:
    async def fake_status(project_id):
        return {"state": "running", "container_name": "omnia-dev-crm-abc"}

    monkeypatch.setattr(dev_container.orchestrator_client, "get_status", fake_status)
    assert await dev_container.resolve_live_url(uuid4(), "/dashboard") == (
        "http://omnia-dev-crm-abc:3000/dashboard"
    )
    # default stays byte-identical to the historical bare URL
    assert await dev_container.resolve_live_url(uuid4()) == (
        "http://omnia-dev-crm-abc:3000"
    )


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


# ── MinIO public→internal rewrite for the preview render (dogfood run #40) ──
# Repro: the preview worker can't hairpin-NAT to the host's public IP, so the
# resolved <img src="{public_minio}/..."> photos never paint inside chromium →
# image-less thumbnail / design-judge view even when the deployed page is fine.
# Fix repoints the in-memory render copy to the internal MinIO endpoint.

def test_rewrite_minio_public_to_internal(monkeypatch) -> None:
    """A resolved public MinIO <img src> is repointed to internal minio:9000."""
    settings = preview.get_settings()
    monkeypatch.setattr(settings, "minio_public_url", "https://constructor.lead-generator.ru/minio")
    monkeypatch.setattr(settings, "minio_endpoint", "minio:9000")
    monkeypatch.setattr(settings, "minio_secure", False)
    html = (
        '<img src="https://constructor.lead-generator.ru/minio/omnia-images/'
        'proj/abc.png" class="absolute inset-0">'
    )
    out = preview._rewrite_minio_to_internal(html)
    assert "http://minio:9000/omnia-images/proj/abc.png" in out
    # The public host must not survive in the render copy (it's unreachable here).
    assert "constructor.lead-generator.ru" not in out


def test_rewrite_minio_noop_without_images(monkeypatch) -> None:
    """A page with no MinIO images is returned untouched (pure str.replace)."""
    settings = preview.get_settings()
    monkeypatch.setattr(settings, "minio_public_url", "https://constructor.lead-generator.ru/minio")
    monkeypatch.setattr(settings, "minio_endpoint", "minio:9000")
    monkeypatch.setattr(settings, "minio_secure", False)
    html = '<section class="omnia-shader"><h1>Привет</h1></section>'
    assert preview._rewrite_minio_to_internal(html) == html


def test_rewrite_minio_noop_when_already_internal(monkeypatch) -> None:
    """Local dev (public URL == internal) must not rewrite onto itself."""
    settings = preview.get_settings()
    monkeypatch.setattr(settings, "minio_public_url", "http://minio:9000")
    monkeypatch.setattr(settings, "minio_endpoint", "minio:9000")
    monkeypatch.setattr(settings, "minio_secure", False)
    html = '<img src="http://minio:9000/omnia-images/p/x.png">'
    assert preview._rewrite_minio_to_internal(html) == html
