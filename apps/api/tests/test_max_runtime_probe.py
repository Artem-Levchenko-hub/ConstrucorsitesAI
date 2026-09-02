from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest

from omnia_api.services import max_runtime_probe

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
SLUG = "fitness-demo"
ORIGIN = "https://fitness-demo-dev.preview.example.test"
BOOTSTRAP = (
    f"{ORIGIN}/api/omnia/preview-session?expires=1893456000&signature=" + "a" * 43
)


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(
            transport=transport,
            timeout=kwargs.get("timeout"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr(max_runtime_probe.httpx, "AsyncClient", client)


@pytest.mark.asyncio
async def test_probe_max_runtime_proves_cookie_and_protected_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def preview_session(_project_id: UUID) -> dict[str, str]:
        return {"project_id": str(PROJECT_ID), "bootstrap_url": BOOTSTRAP}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] == 20.0
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={
                    "Location": "/",
                    "Set-Cookie": "__Host-max_session=test; Path=/; Secure; SameSite=None",
                },
            )
        if request.url.path == "/":
            return httpx.Response(200, text="preview")
        if request.url.path == "/api/omnia/actions":
            assert request.headers.get("cookie") == "__Host-max_session=test"
            return httpx.Response(200, json={"actions": [], "nextCursor": None})
        return httpx.Response(404)

    monkeypatch.setattr(
        max_runtime_probe.orchestrator_client,
        "create_max_preview_session",
        preview_session,
    )
    _install_transport(monkeypatch, handler)

    result = await max_runtime_probe.probe_max_runtime(
        PROJECT_ID,
        SLUG,
        base_url=ORIGIN,
    )

    assert result.ok is True
    assert "protected MAX data read passed" in result.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bootstrap_status", "actions_status", "expected"),
    [(404, 200, "bootstrap failed (HTTP 404)"), (307, 401, "data read failed (HTTP 401)")],
)
async def test_probe_max_runtime_blocks_auth_or_data_failures(
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_status: int,
    actions_status: int,
    expected: str,
) -> None:
    async def preview_session(_project_id: UUID) -> dict[str, str]:
        return {"project_id": str(PROJECT_ID), "bootstrap_url": BOOTSTRAP}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/omnia/preview-session":
            if bootstrap_status == 307:
                return httpx.Response(
                    307,
                    headers={
                        "Location": "/",
                        "Set-Cookie": "__Host-max_session=test; Path=/; Secure; SameSite=None",
                    },
                )
            return httpx.Response(bootstrap_status)
        if request.url.path == "/":
            return httpx.Response(200)
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(actions_status, json={"error": "Unauthorized"})
        return httpx.Response(404)

    monkeypatch.setattr(
        max_runtime_probe.orchestrator_client,
        "create_max_preview_session",
        preview_session,
    )
    _install_transport(monkeypatch, handler)

    result = await max_runtime_probe.probe_max_runtime(PROJECT_ID, SLUG, base_url=ORIGIN)

    assert result.ok is False
    assert expected in result.detail


@pytest.mark.asyncio
async def test_probe_max_runtime_rejects_unexpected_origin_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def preview_session(_project_id: UUID) -> dict[str, str]:
        return {
            "project_id": str(PROJECT_ID),
            "bootstrap_url": (
                "https://attacker.example/api/omnia/preview-session"
                "?expires=1893456000&signature=" + "a" * 43
            ),
        }

    monkeypatch.setattr(
        max_runtime_probe.orchestrator_client,
        "create_max_preview_session",
        preview_session,
    )

    result = await max_runtime_probe.probe_max_runtime(PROJECT_ID, SLUG, base_url=ORIGIN)

    assert result.ok is False
    assert result.detail == "preview session returned an invalid signed URL"


@pytest.mark.asyncio
async def test_probe_max_runtime_requires_the_orchestrator_runtime_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def preview_session(_project_id: UUID) -> dict[str, str]:
        return {"project_id": str(PROJECT_ID), "bootstrap_url": BOOTSTRAP}

    monkeypatch.setattr(
        max_runtime_probe.orchestrator_client,
        "create_max_preview_session",
        preview_session,
    )

    result = await max_runtime_probe.probe_max_runtime(PROJECT_ID, SLUG)

    assert result.ok is False
    assert result.detail == "preview session returned an invalid signed URL"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_status", "location"),
    [(200, "/"), (500, "/"), (200, "https://attacker.example/")],
)
async def test_cell_runtime_uses_cell_cookie_and_route_without_legacy(
    monkeypatch, route_status, location,
):
    from omnia_api.services.orchestrator_client import ProjectCellPreviewSession

    def forbidden(*args, **kwargs):
        pytest.fail("cell runtime must not resolve legacy preview")

    monkeypatch.setattr(
        max_runtime_probe.orchestrator_client, "create_max_preview_session", forbidden,
    )
    observed: list[str] = []

    def handler(request):
        assert request.extensions["timeout"]["read"] == 120.0
        assert request.extensions["timeout"]["connect"] == 5.0
        observed.append(request.url.path)
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(307, headers={
                "Location": location,
                "Set-Cookie": "__Host-max_session=cell; Path=/; Secure",
            })
        assert request.headers.get("cookie") == "__Host-max_session=cell"
        if request.url.path == "/product":
            return httpx.Response(route_status)
        assert request.url.path == "/api/omnia/actions"
        return httpx.Response(200, json={"actions": []})

    _install_transport(monkeypatch, handler)
    workspace_id = UUID("00000000-0000-0000-0000-000000000002")
    cell_origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
    cell_bootstrap = (
        f"{cell_origin}/api/omnia/preview-session?expires=1893456000&signature=" + "a" * 43
    )
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            workspace_id, cell_origin, cell_bootstrap, "2030-01-01T00:00:00Z",
        ),
        path="/product",
    )
    escaped = location.startswith("https://attacker")
    assert result.ok is (route_status == 200 and not escaped)
    assert observed == (
        ["/api/omnia/preview-session"]
        if escaped
        else (
            ["/api/omnia/preview-session", "/product", "/api/omnia/actions"]
            if route_status == 200
            else ["/api/omnia/preview-session", "/product"]
        )
    )
    assert cell_bootstrap not in result.detail
