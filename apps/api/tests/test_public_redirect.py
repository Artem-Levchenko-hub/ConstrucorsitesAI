"""Tests for the public share-link redirect (`/p/<slug>`) of container apps.

Regression guard for Phase 2.4: a stranger opening the published link of a
HIBERNATED container app must be redirected to the live dev-preview URL (whose
nginx vhost wakes it on first hit, Phase 0.3) — not dropped to the "page not
created yet" shell. Only a project with no container at all falls through.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from omnia_api.routers import public


def _proj(template: str = "nextjs_entities"):
    return SimpleNamespace(id=uuid4(), slug="shop-abc123", template=template)


async def _no_deploy(_pid):
    # Never deployed → no prod_url; forces the dev-status branch.
    return {"phase": "queued", "prod_url": None}


async def test_redirect_when_running(monkeypatch) -> None:
    monkeypatch.setattr(public.orchestrator_client, "get_deploy", _no_deploy)

    async def status(_pid):
        return {"state": "running", "dev_url": "https://shop-abc123-dev.example/"}

    monkeypatch.setattr(public.orchestrator_client, "get_status", status)
    resp = await public._fullstack_redirect(_proj())
    assert resp is not None
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://shop-abc123-dev.example/"


async def test_redirect_when_hibernated(monkeypatch) -> None:
    # The bug: a stopped (hibernated) container reports a dev_url but the old
    # gate (state == "running") dropped it to the dead shell.
    monkeypatch.setattr(public.orchestrator_client, "get_deploy", _no_deploy)

    async def status(_pid):
        return {"state": "stopped", "dev_url": "https://shop-abc123-dev.example/"}

    monkeypatch.setattr(public.orchestrator_client, "get_status", status)
    resp = await public._fullstack_redirect(_proj())
    assert resp is not None
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://shop-abc123-dev.example/"


async def test_redirect_when_paused(monkeypatch) -> None:
    monkeypatch.setattr(public.orchestrator_client, "get_deploy", _no_deploy)

    async def status(_pid):
        return {"state": "paused", "dev_url": "https://shop-abc123-dev.example/"}

    monkeypatch.setattr(public.orchestrator_client, "get_status", status)
    resp = await public._fullstack_redirect(_proj())
    assert resp is not None and resp.status_code == 302


async def test_no_redirect_without_container(monkeypatch) -> None:
    # No container ever provisioned → status returns no dev_url → fall through.
    monkeypatch.setattr(public.orchestrator_client, "get_deploy", _no_deploy)

    async def status(_pid):
        return {"state": "stopped", "dev_url": None}

    monkeypatch.setattr(public.orchestrator_client, "get_status", status)
    assert await public._fullstack_redirect(_proj()) is None


async def test_no_redirect_when_failed(monkeypatch) -> None:
    # A dead container won't recover by waking → don't loop visitors on it.
    monkeypatch.setattr(public.orchestrator_client, "get_deploy", _no_deploy)

    async def status(_pid):
        return {"state": "failed", "dev_url": "https://shop-abc123-dev.example/"}

    monkeypatch.setattr(public.orchestrator_client, "get_status", status)
    assert await public._fullstack_redirect(_proj()) is None


async def test_static_project_never_redirects() -> None:
    # Static templates have no container — short-circuit before any lookup.
    assert await public._fullstack_redirect(_proj(template="blank")) is None


async def test_deployed_prefers_prod_url(monkeypatch) -> None:
    async def deployed(_pid):
        return {"phase": "done", "prod_url": "https://shop.example/"}

    monkeypatch.setattr(public.orchestrator_client, "get_deploy", deployed)
    resp = await public._fullstack_redirect(_proj())
    assert resp is not None
    assert resp.headers["location"] == "https://shop.example/"
