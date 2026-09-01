"""Unit tests for services.coverage_gate — pure status logic + fail-soft exits.

The browser happy-path (login + per-capability fetch) is integration-level and
needs a live preview; here we cover the pure decision logic (status_matches,
verdict assembly) and the fail-soft early-exits that guarantee the gate can never
block a build on its own infrastructure (no caps / bad id / no preview → SKIPPED,
passed=True).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnia_api.services.build_plan import BuildPlan, Capability
from omnia_api.services.coverage_gate import (
    CoverageCheck,
    CoverageVerdict,
    _route_known,
    _verdict_from_checks,
    api_routes_from_files,
    run_coverage_gate,
    status_matches,
)
from omnia_api.services.orchestrator_client import ProjectCellPreviewSession


def test_status_matches_class_and_exact():
    assert status_matches(200, "2xx")
    assert status_matches(201, "2xx")
    assert not status_matches(404, "2xx")
    assert status_matches(403, "403")
    assert not status_matches(404, "403")
    assert status_matches(404, "4xx")
    assert not status_matches(500, "4xx")
    assert status_matches(503, "5xx")


def test_status_matches_defaults_and_network_throw():
    # empty / unparseable expectation → happy path (2xx)
    assert status_matches(200, "")
    assert status_matches(200, "weird")
    # a 0 (request threw at the network layer) never satisfies a 2xx expectation
    assert not status_matches(0, "2xx")


def test_verdict_from_checks():
    cks = [
        CoverageCheck(True, "a"),
        CoverageCheck(False, "b"),
        CoverageCheck(True, "c"),
    ]
    v = _verdict_from_checks(cks)
    assert v.total == 3
    assert v.covered == 2
    assert v.missing == ["b"]
    assert not v.passed
    v2 = _verdict_from_checks([CoverageCheck(True, "a")])
    assert v2.passed and v2.covered == 1 and v2.missing == []


async def test_no_blocking_caps_skipped():
    # a UI-only capability (no path) is not probeable → nothing to block on
    plan = BuildPlan(capabilities=(Capability(id="ui", path="", must_have=True),))
    v = await run_coverage_gate(str(uuid.uuid4()), plan)
    assert v.skipped and v.passed and v.total == 0


async def test_bad_project_id_skipped():
    plan = BuildPlan(capabilities=(Capability(id="c", path="/api/x", must_have=True),))
    v = await run_coverage_gate("not-a-uuid", plan)
    assert v.skipped and v.passed


async def test_no_dev_url_skipped(monkeypatch):
    async def _status(_pid):
        return {}

    monkeypatch.setattr(
        "omnia_api.services.orchestrator_client.get_status", _status
    )
    plan = BuildPlan(capabilities=(Capability(id="c", path="/api/x", must_have=True),))
    v = await run_coverage_gate(str(uuid.uuid4()), plan)
    assert v.skipped and v.passed


@pytest.mark.parametrize("escaped", [False, True])
async def test_cell_coverage_uses_signed_session_without_legacy_runtime(
    monkeypatch, escaped: bool,
):
    workspace_id = uuid.uuid4()
    origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
    preview = ProjectCellPreviewSession(
        workspace_id,
        origin,
        f"{origin}/api/omnia/preview-session?expires=1893456000&signature=" + "a" * 43,
        "2030-01-01T00:00:00Z",
    )
    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(ok=True)),
        url="https://attacker.example/" if escaped else f"{origin}/",
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

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cell coverage must not use legacy runtime or login")

    from omnia_api.services import functional_gate, orchestrator_client

    monkeypatch.setattr("playwright.async_api.async_playwright", fake_playwright)
    monkeypatch.setattr(orchestrator_client, "get_status", forbidden)
    monkeypatch.setattr(functional_gate, "_login", forbidden)
    request = AsyncMock(return_value={"status": 200})
    monkeypatch.setattr(functional_gate, "_api", request)
    plan = BuildPlan(
        capabilities=(Capability(id="products", path="/api/products", must_have=True),)
    )

    verdict = await run_coverage_gate(workspace_id, plan, cell_preview=preview)

    assert verdict.passed
    assert verdict.skipped is escaped
    page.goto.assert_awaited_once_with(
        preview.bootstrap_url,
        wait_until="domcontentloaded",
    )
    if escaped:
        request.assert_not_awaited()
    else:
        request.assert_awaited_once_with(page, "POST", "/api/products", None)


# ── A1 route reconciliation ──────────────────────────────────────────────────


def test_api_routes_from_files():
    files = {
        "src/app/api/clients/route.ts": "...",
        "src/app/api/clients/[id]/route.ts": "...",
        "src/app/api/(admin)/stats/route.ts": "...",  # route group dropped
        "src/app/dashboard/page.tsx": "...",  # not an api route
        "src\\app\\api\\deals\\route.ts": "...",  # backslash path normalised
    }
    r = api_routes_from_files(files)
    assert "/api/clients" in r
    assert "/api/stats" in r
    assert "/api/deals" in r
    # [id] collapses under /api/clients (prefix stops at the dynamic segment)
    assert "/api/clients/[id]" not in r
    assert all(not x.endswith("page.tsx") for x in r)
    assert api_routes_from_files(None) == set()


def test_route_known():
    known = {"/api/clients", "/api/deals"}
    assert _route_known("/api/clients", known)
    assert _route_known("/api/clients/123", known)  # prefix (collection covers /id)
    assert _route_known("/api/clients?x=1", known)  # query stripped
    assert not _route_known("/api/unknown", known)
    assert not _route_known("", known)


def test_hard_vs_soft_missing():
    checks = [
        CoverageCheck(True, "a", kind="ok"),
        CoverageCheck(False, "b", kind="wrong_status"),
        CoverageCheck(False, "c", kind="missing_route"),
    ]
    v = CoverageVerdict(passed=False, covered=1, total=3, checks=checks)
    assert v.hard_missing() == ["b"]
    assert v.soft_missing() == ["c"]
