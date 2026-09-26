from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from yleum_api.workers import preview


def _fake_playwright(page: object):
    browser = SimpleNamespace(
        new_page=AsyncMock(return_value=page),
        close=AsyncMock(),
    )
    chromium = SimpleNamespace(launch=AsyncMock(return_value=browser))

    @asynccontextmanager
    async def cm():
        yield SimpleNamespace(chromium=chromium)

    return cm, browser


async def _noop(*_args, **_kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_capture_live_url_report_bootstraps_once_then_reuses_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = "https://preview.example/api/omnia/preview-session?expires=1&signature=secret"
    page = SimpleNamespace(
        goto=AsyncMock(return_value=None),
        set_viewport_size=AsyncMock(return_value=None),
        screenshot=AsyncMock(side_effect=[b"wide", b"mobile"]),
        close=AsyncMock(return_value=None),
    )
    fake_playwright, browser = _fake_playwright(page)
    monkeypatch.setattr(preview, "async_playwright", fake_playwright)
    monkeypatch.setattr(preview, "_block_external_fonts", _noop)
    monkeypatch.setattr(preview, "_route_media_internal", _noop)
    monkeypatch.setattr(preview, "_await_container_ready", _noop)
    monkeypatch.setattr(preview, "_await_paint", _noop)
    monkeypatch.setattr(preview, "_await_content", _noop)

    report = await preview.capture_live_url_report(
        "https://preview.example/profile",
        widths=(1440, 360),
        bootstrap_url=bootstrap,
    )

    assert report.screenshots == {1440: b"wide", 360: b"mobile"}
    assert report.issues == ()
    assert page.goto.await_args_list == [
        call(bootstrap, wait_until="domcontentloaded", timeout=120_000),
        call("https://preview.example/profile", wait_until="domcontentloaded", timeout=120_000),
        call("https://preview.example/profile", wait_until="domcontentloaded", timeout=15_000),
        call("https://preview.example/profile", wait_until="domcontentloaded", timeout=15_000),
    ]
    assert page.set_viewport_size.await_args_list == [
        call({"width": 1440, "height": 900}),
        call({"width": 360, "height": 900}),
    ]
    page.close.assert_awaited_once()
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_live_url_report_records_bootstrap_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=RuntimeError("Timeout 120000ms exceeded")),
        set_viewport_size=AsyncMock(return_value=None),
        screenshot=AsyncMock(return_value=b"never"),
        close=AsyncMock(return_value=None),
    )
    fake_playwright, _browser = _fake_playwright(page)
    monkeypatch.setattr(preview, "async_playwright", fake_playwright)
    monkeypatch.setattr(preview, "_block_external_fonts", _noop)
    monkeypatch.setattr(preview, "_route_media_internal", _noop)

    report = await preview.capture_live_url_report(
        "https://preview.example/profile",
        widths=(1440, 360),
        bootstrap_url="https://preview.example/api/omnia/preview-session?expires=1&signature=secret",
    )

    assert report.screenshots == {}
    assert report.summary() == "bootstrap: signed preview bootstrap timed out"


def test_capture_error_redacts_url_credentials() -> None:
    message = preview._redact_text(
        "failed https://fake-user:fake-password@preview.example/path?signature=fake#secret"
    )
    assert message == "failed https://preview.example/path?[REDACTED]"
    assert preview._redact_url("https://[bad-host/path?token=fake") == "[invalid URL]"


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses", [[500], [200, 401], [200, 200, 503]])
async def test_version_capture_rejects_unsuccessful_http(monkeypatch, statuses):
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=[SimpleNamespace(status=status) for status in statuses]),
        set_viewport_size=AsyncMock(),
        screenshot=AsyncMock(return_value=b"png"),
        close=AsyncMock(),
    )
    fake_playwright, _browser = _fake_playwright(page)
    monkeypatch.setattr(preview, "async_playwright", fake_playwright)
    for name in (
        "_block_external_fonts", "_route_media_internal", "_await_container_ready",
        "_await_paint", "_await_content",
    ):
        monkeypatch.setattr(preview, name, _noop)
    report = await preview.capture_live_url_report(
        "https://preview.example/", widths=(390, 1280), require_success_status=True,
    )
    assert report.issues
    assert len(report.screenshots) < 2
