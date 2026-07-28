"""DB-backed public API flow for protected BYO target selection."""

from __future__ import annotations

import httpx
import pytest

from omnia_api.routers import projects as projects_router
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc

pytestmark = pytest.mark.asyncio


async def test_target_requires_fingerprint_confirmation_and_preflight(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repo_svc, "init_repo", lambda *_args: "a" * 40)
    monkeypatch.setattr(projects_router, "enqueue_preview", lambda *_args: None)

    async def fake_publish(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(projects_router, "publish_event", fake_publish)

    await client.post(
        "/api/auth/register",
        json={"email": "byo-owner@example.com", "password": "secret123"},
    )
    target_response = await client.post(
        "/api/deploy-targets",
        json={
            "label": "Production VPS",
            "ssh_host": "vps.example.com",
            "ssh_port": 22,
            "ssh_user": "deploy",
            "auth_type": "password",
            "secret": "ssh-password",
        },
    )
    assert target_response.status_code == 201
    target_id = target_response.json()["id"]

    project_response = await client.post(
        "/api/projects",
        json={"name": "BYO project", "template": "blank"},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    selected_too_early = await client.patch(
        f"/api/projects/{project_id}", json={"deploy_target_id": target_id}
    )
    assert selected_too_early.status_code == 409
    assert selected_too_early.json()["error"]["code"] == "deploy_target_not_verified"

    observed_credentials: list[str] = []

    async def fake_verify(payload):
        observed_credentials.append(payload["secret"])
        if payload.get("known_host_key"):
            return {
                "ok": True,
                "detail": "Сервер готов",
                "docker_ok": True,
                "docker_version": "27.1",
                "host_key": payload["known_host_key"],
                "host_fingerprint": "SHA256:test",
                "resolved_ip": payload["resolved_ip"],
                "requires_confirmation": False,
                "capabilities": {"arch": "x86_64", "memory_mb": 4096},
            }
        return {
            "ok": False,
            "detail": "Подтвердите ключ",
            "docker_ok": False,
            "host_key": "203.0.113.9 ssh-ed25519 QUJD",
            "host_fingerprint": "SHA256:test",
            "resolved_ip": "203.0.113.9",
            "requires_confirmation": True,
        }

    monkeypatch.setattr(orchestrator_client, "verify_deploy_target", fake_verify)
    first_verify = await client.post(f"/api/deploy-targets/{target_id}/verify")
    assert first_verify.status_code == 200
    assert first_verify.json()["verify_status"] == "pending_confirmation"
    assert first_verify.json()["requires_confirmation"] is True
    assert observed_credentials == [""]

    confirmed = await client.post(f"/api/deploy-targets/{target_id}/verify?confirm_host_key=true")
    assert confirmed.status_code == 200
    assert confirmed.json()["ok"] is True
    assert confirmed.json()["capabilities"]["memory_mb"] == 4096
    assert observed_credentials[-1] == "ssh-password"

    selected = await client.patch(
        f"/api/projects/{project_id}", json={"deploy_target_id": target_id}
    )
    assert selected.status_code == 200
    assert selected.json()["deploy_target_id"] == target_id

    listed = await client.get("/api/deploy-targets")
    assert listed.status_code == 200
    assert all("ssh_secret_enc" not in item and "secret" not in item for item in listed.json())

    rotated = await client.patch(
        f"/api/deploy-targets/{target_id}", json={"secret": "new-password"}
    )
    assert rotated.status_code == 200
    assert rotated.json()["verify_status"] == "unverified"
    # Secret rotation requires re-authentication, but keeps the confirmed host
    # identity so an existing remote runtime can still be cleaned up safely.
    assert rotated.json()["resolved_ip"] == "203.0.113.9"
