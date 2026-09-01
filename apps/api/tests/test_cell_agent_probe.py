from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.services import agent_probe, functional_gate, orchestrator_client
from omnia_api.services.orchestrator_client import ProjectCellPreviewSession


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped", [False, True])
async def test_cell_probe_uses_signed_session_without_legacy_login(monkeypatch, escaped):
    workspace_id = uuid4()
    origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
    preview = ProjectCellPreviewSession(
        workspace_id, origin, f"{origin}/api/omnia/preview-session?expires=1893456000&signature="
        + "a" * 43, "2030-01-01T00:00:00Z",
    )
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(ok=True)),
        url="https://elsewhere.example.test/" if escaped else f"{origin}/",
    )
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=page),
        cookies=AsyncMock(return_value=[{"name": "__Host-max_session"}]),
    )
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    chromium = SimpleNamespace(launch=AsyncMock(return_value=browser))

    @asynccontextmanager
    async def fake_playwright():
        yield SimpleNamespace(chromium=chromium)

    def forbidden(*args, **kwargs):
        pytest.fail("cell probe must not use project runtime or email login")

    monkeypatch.setattr("playwright.async_api.async_playwright", fake_playwright)
    monkeypatch.setattr(orchestrator_client, "get_status", forbidden)
    monkeypatch.setattr(functional_gate, "_login", forbidden)
    request = AsyncMock(return_value={"status": 201, "json": {"id": "new"}})
    monkeypatch.setattr(functional_gate, "_api", request)
    result = await agent_probe.run_probe(
        uuid4(), method="POST", path="/api/products", body={"name": "demo"}, cell_preview=preview,
    )
    page.goto.assert_awaited_once_with(preview.bootstrap_url, wait_until="domcontentloaded")
    browser.close.assert_awaited_once()
    assert result["ok"] is not escaped
    if escaped:
        request.assert_not_awaited()
    else:
        request.assert_awaited_once_with(page, "POST", "/api/products", {"name": "demo"})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["//private.example/", "/\\private.example/", "https://x/"])
async def test_cell_probe_rejects_cross_origin_path_before_browser(path):
    result = await agent_probe.run_probe(uuid4(), method="GET", path=path)
    assert result["ok"] is False
    assert "same-origin" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("response_ok,cookies", [(False, []), (True, [])])
async def test_cell_probe_rejects_failed_or_cookieless_bootstrap(
    monkeypatch, response_ok, cookies,
):
    workspace_id = uuid4()
    origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
    preview = ProjectCellPreviewSession(
        workspace_id, origin, f"{origin}/api/omnia/preview-session?expires=1893456000&signature="
        + "a" * 43, "2030-01-01T00:00:00Z",
    )
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(ok=response_ok)), url=f"{origin}/",
    )
    context = SimpleNamespace(
        new_page=AsyncMock(return_value=page), cookies=AsyncMock(return_value=cookies),
    )
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())

    @asynccontextmanager
    async def fake_playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))

    monkeypatch.setattr("playwright.async_api.async_playwright", fake_playwright)
    request = AsyncMock()
    monkeypatch.setattr(functional_gate, "_api", request)
    result = await agent_probe.run_probe(
        uuid4(), method="POST", path="/api/products", cell_preview=preview,
    )
    assert result == {"ok": False, "error": "probe: signed cell session was not established"}
    request.assert_not_awaited()
