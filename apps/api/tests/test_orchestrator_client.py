from __future__ import annotations

from uuid import UUID

import pytest

from omnia_api.services import orchestrator_client


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
    assert observed["timeout"] == 180.0
    assert observed["path"] == "/internal/projects/provision"


@pytest.mark.asyncio
async def test_agent_build_sends_code_intelligence_query_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params_seen: list[dict[str, str]] = []

    async def fake_request(method: str, path: str, **kwargs: object) -> dict[str, object]:
        assert method == "POST"
        params_seen.append(dict(kwargs["params"]))
        return {"ok": True, "detail": "clean"}

    async def track(_paths: object) -> None:
        return None

    monkeypatch.setattr(orchestrator_client, "_request", fake_request)
    monkeypatch.setattr(orchestrator_client, "track_mutation_paths", track)
    project_id = UUID("00000000-0000-0000-0000-000000000001")

    await orchestrator_client.agent_build(project_id, "slug")
    await orchestrator_client.agent_build(project_id, "slug", code_intelligence=True)
    await orchestrator_client.agent_build(project_id, "slug", security_scan=True)

    assert params_seen == [
        {"slug": "slug"},
        {"slug": "slug", "code_intelligence": "true"},
        {"slug": "slug", "security_scan": "true"},
    ]
