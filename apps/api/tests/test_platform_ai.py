from uuid import UUID, uuid4

from sqlalchemy import select

from omnia_api.models.account import BusinessProfile
from omnia_api.models.app_integration import BusinessIntegration, ProjectIntegrationBinding
from omnia_api.models.project import Project
from omnia_api.services.integration_generation import generation_context
from tests.test_app_integrations_api import _register_and_create


async def test_platform_ai_requires_explicit_owner_enable_without_credentials(
    client, db_session, monkeypatch
):
    project_id = await _register_and_create(client, monkeypatch)
    url = f"/api/projects/{project_id}"
    catalog = (await client.get(url + "/app-integrations")).json()
    providers = {p["key"]: p for p in catalog["providers"]}
    assert "aitunnel" not in providers
    assert providers["llmgw"]["connection_mode"] == "platform"
    assert providers["llmgw"]["available"] is True
    assert providers["llmgw"]["enabled"] is False
    assert providers["llmgw"]["fields"] == []
    assert "requestOmniaAI" not in await generation_context(db_session, UUID(project_id))
    for enabled in (True, True, False):
        response = await client.put(url + "/platform-ai", json={"enabled": enabled})
        assert response.status_code == 200
        assert response.json() == {"enabled": enabled}
        catalog = (await client.get(url + "/app-integrations")).json()
        assert next(p for p in catalog["providers"] if p["key"] == "llmgw")["enabled"] is enabled
        assert catalog["connections"] == []
        context = await generation_context(db_session, UUID(project_id))
        assert ("requestOmniaAI" in context) is enabled
        assert "aitunnel" not in context.lower()
    assert await db_session.scalar(select(BusinessIntegration.id)) is None
    assert await db_session.scalar(select(ProjectIntegrationBinding.id)) is None


async def test_platform_ai_owner_gate_and_invalid_payload_do_not_enable(
    client, db_session, monkeypatch
):
    project_id = await _register_and_create(client, monkeypatch)
    project = await db_session.get(Project, UUID(project_id))
    for body in ({}, {"enabled": "yes"}, {"enabled": True, "free": True}):
        response = await client.put(f"/api/projects/{project_id}/platform-ai", json=body)
        assert response.status_code == 422
    assert (
        await client.put(f"/api/projects/{uuid4()}/platform-ai", json={"enabled": True})
    ).status_code == 404
    client.cookies.clear()
    assert (
        await client.put(f"/api/projects/{project_id}/platform-ai", json={"enabled": True})
    ).status_code == 401
    await db_session.refresh(project)
    assert project.runtime_ai_enabled is False


async def test_legacy_aitunnel_is_preserved_but_never_reactivated_or_advertised(
    client, db_session, monkeypatch
):
    project_id = await _register_and_create(client, monkeypatch)
    project = await db_session.get(Project, UUID(project_id))
    business = await db_session.scalar(select(BusinessProfile))
    legacy = BusinessIntegration(
        business_id=business.id,
        created_by_user_id=project.owner_id,
        provider="aitunnel",
        credentials_enc="legacy-untouched",
        status="active",
    )
    db_session.add(legacy)
    await db_session.flush()
    db_session.add(
        ProjectIntegrationBinding(
            project_id=project.id, integration_id=legacy.id, provider="aitunnel"
        )
    )
    await db_session.commit()
    catalog = await client.get(f"/api/projects/{project_id}/app-integrations")
    assert catalog.status_code == 200
    assert catalog.json()["connections"] == []
    assert "requestOmniaAI" not in await generation_context(db_session, project.id)
    for suffix in ("bind", "verify"):
        response = await client.post(
            f"/api/projects/{project_id}/app-integrations/aitunnel/{suffix}"
        )
        assert response.status_code == 409
    response = await client.put(
        f"/api/projects/{project_id}/app-integrations/aitunnel",
        json={"values": {"api_key": "test-key"}},
    )
    assert response.status_code == 400
    await db_session.refresh(legacy)
    assert legacy.credentials_enc == "legacy-untouched"
