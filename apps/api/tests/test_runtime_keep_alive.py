from __future__ import annotations

from uuid import uuid4

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
