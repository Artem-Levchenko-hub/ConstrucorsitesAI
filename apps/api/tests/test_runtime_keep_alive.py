from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from omnia_api.routers import runtime
from omnia_api.routers.runtime import _to_runtime_status
from omnia_api.services import orchestrator_client


def test_runtime_status_exposes_keep_alive() -> None:
    status = _to_runtime_status(
        {
            "state": "running",
            "container_name": "omnia-dev-demo",
            "keep_alive": True,
        }
    )

    assert status.state == "running"
    assert status.keep_alive is True
    assert status.hibernate_after_seconds is None


async def test_orchestrator_keep_alive_contract(monkeypatch) -> None:
    project_id = uuid4()
    captured: dict[str, object] = {}

    async def fake_request(method, path, *, json=None, **_kwargs):
        captured.update(method=method, path=path, json=json)
        return {"project_id": str(project_id), "enabled": True}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.set_keep_alive(project_id, enabled=True)

    assert captured == {
        "method": "POST",
        "path": "/internal/projects/keep-alive",
        "json": {"project_id": str(project_id), "enabled": True},
    }
    assert result["enabled"] is True


async def test_orchestrator_preview_heartbeat_contract(monkeypatch) -> None:
    project_id = uuid4()
    captured: dict[str, object] = {}

    async def fake_request(method, path, **_kwargs):
        captured.update(method=method, path=path)
        return {"state": "recorded"}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.heartbeat(project_id)

    assert captured == {
        "method": "POST",
        "path": f"/internal/projects/{project_id}/heartbeat",
    }
    assert result == {"state": "recorded"}


async def test_preview_heartbeat_checks_owner_before_forwarding(monkeypatch) -> None:
    project_id = uuid4()
    user_id = uuid4()
    calls: list[tuple[str, object]] = []

    async def owned_by(_session, checked_project_id, checked_user_id):
        calls.append(("owner", (checked_project_id, checked_user_id)))
        return SimpleNamespace(id=project_id)

    async def heartbeat(checked_project_id):
        calls.append(("heartbeat", checked_project_id))
        return {"state": "recorded"}

    monkeypatch.setattr(runtime, "_project_owned_by", owned_by)
    monkeypatch.setattr(runtime.orchestrator_client, "heartbeat", heartbeat)

    result = await runtime.heartbeat_runtime(
        project_id,
        session=object(),
        current_user=SimpleNamespace(id=user_id),
    )

    assert result is None
    assert calls == [
        ("owner", (project_id, user_id)),
        ("heartbeat", project_id),
    ]
