from __future__ import annotations

from uuid import UUID

import pytest

from omnia_api.services import orchestrator_client


async def test_project_cell_capability_client_calls_exact_internal_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    project_id = UUID("00000000-0000-0000-0000-000000000005")
    expected = {
        "project_id": str(project_id),
        "provider": "disabled",
        "enabled": False,
        "ready": False,
        "state": "disabled",
        "detail": "workspace provider is disabled",
    }

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return expected

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.get_project_cell_capabilities(project_id)

    assert result is expected
    assert observed == {
        "method": "GET",
        "path": f"/internal/projects/{project_id}/workspace/capabilities",
    }


async def test_provision_waits_for_a_cold_template_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.update(method=method, path=path, **kwargs)
        return {"state": "running"}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)

    result = await orchestrator_client.provision(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        slug="max-preview",
        template="max-miniapp-nextjs",
    )

    assert result == {"state": "running"}
    assert observed["timeout"] == 1320.0
    assert observed["path"] == "/internal/projects/provision"


async def test_project_shell_and_dependency_sync_use_long_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        observed.append({"method": method, "path": path, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    project_id = UUID("00000000-0000-0000-0000-000000000001")

    await orchestrator_client.agent_build(project_id, "max-preview")
    await orchestrator_client.agent_exec(project_id, "max-preview", "pnpm test")
    await orchestrator_client.agent_exec_sandbox(project_id, "max-preview", "pnpm test")
    await orchestrator_client.hot_reload(
        project_id,
        "max-preview",
        {"package.json": '{"name":"app"}'},
    )

    assert [call["timeout"] for call in observed] == [600.0, 210.0, 1500.0, 1800.0]
    assert observed[2]["json"] == {"slug": "max-preview", "cmd": "pnpm test"}
    assert "params" not in observed[2]
