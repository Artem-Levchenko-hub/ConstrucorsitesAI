"""Authenticated mini-app AI uses the owner's billed internal gateway, never BYO keys."""

import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import encrypt_strong
from omnia_api.models.account import BusinessProfile
from omnia_api.models.app_integration import BusinessIntegration, ProjectIntegrationBinding
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.project import Project
from omnia_api.routers import integration_runtime
from omnia_api.services import llm_client
from tests.test_app_integrations_api import _max_init_data, _register_and_create


async def fixture(client, db_session, monkeypatch):
    project_id = await _register_and_create(client, monkeypatch)
    project = await db_session.get(Project, UUID(project_id))
    project.runtime_ai_enabled = True
    db_session.add(
        MaxIntegration(
            project_id=project.id,
            owner_id=project.owner_id,
            bot_token_enc=encrypt_strong("synthetic-max-token"),
            webhook_secret_enc=encrypt_strong("synthetic-webhook"),
        )
    )
    await db_session.commit()
    assert await db_session.scalar(select(BusinessIntegration.id)) is None
    assert await db_session.scalar(select(ProjectIntegrationBinding.id)) is None
    return project


def gateway(monkeypatch, handler, limit=0):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_gateway_url", "http://internal-llmgw.test:8001/")
    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )

    class Limits:
        async def eval(self, *_args):
            return limit

    monkeypatch.setattr(integration_runtime, "get_redis", Limits)


def launch():
    return {"X-MAX-Init-Data": _max_init_data("synthetic-max-token", 42)}


async def test_builtin_ai_uses_owner_billing_and_explicit_paid_gateway_without_binding(
    client,
    db_session,
    monkeypatch,
):
    project = await fixture(client, db_session, monkeypatch)
    observed = []

    def provider(request):
        observed.append(request)
        return httpx.Response(
            200,
            json={
                "model": "google/gemini-3.1-pro-preview-customtools",
                "choices": [{"message": {"content": "Готово"}}],
            },
        )

    gateway(monkeypatch, provider)
    inherited_free = llm_client._free_generation.set(True)
    try:
        response = await client.post(
            f"/api/runtime/projects/{project.id}/ai",
            headers=launch(),
            json={
                "message": "Помоги",
                "context": {"user": "attacker", "free": True},
                "user": "attacker",
                "model": "attacker-model",
                "metadata": {"free": True},
            },
        )
    finally:
        llm_client._free_generation.reset(inherited_free)
    assert response.status_code == 200
    assert response.json()["answer"] == "Готово"
    request = observed[0]
    assert str(request.url) == "http://internal-llmgw.test:8001/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["user"] == str(project.owner_id)
    assert payload["metadata"]["project_id"] == str(project.id)
    assert payload["metadata"]["free"] is False
    assert payload["metadata"]["require_billing"] is True
    assert payload["model"] == "gemini-3.1-pro-preview-customtools"
    assert payload["stream"] is False
    assert 0 < payload["max_tokens"] <= 1600
    assert "authorization" not in request.headers
    assert len(observed) == 1


@pytest.mark.parametrize(
    "failure,expected_status,expected_code",
    [
        (402, 402, "wallet_empty"),
        (429, 429, "rate_limited"),
        (503, 503, "integration_provider_unavailable"),
        ("timeout", 503, "integration_provider_unavailable"),
        ("network", 503, "integration_provider_unavailable"),
        ("list", 502, "integration_response_invalid"),
        ("empty", 502, "integration_response_invalid"),
        ("wrong_content", 502, "integration_response_invalid"),
    ],
)
async def test_gateway_errors_never_leak_private_provider_details(
    client,
    db_session,
    monkeypatch,
    failure,
    expected_status,
    expected_code,
):
    project = await fixture(client, db_session, monkeypatch)

    def provider(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-private-gateway-secret", request=request)
        if failure == "network":
            raise httpx.ConnectError("synthetic-private-gateway-secret", request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, json={"error": "synthetic-private-gateway-secret"})
        payload = {
            "list": [],
            "empty": {"choices": []},
            "wrong_content": {
                "choices": [
                    {"message": {"content": {"secret": "synthetic-private-gateway-secret"}}}
                ],
            },
        }[failure]
        return httpx.Response(200, json=payload)

    gateway(monkeypatch, provider)
    response = await client.post(
        f"/api/runtime/projects/{project.id}/ai", headers=launch(), json={"message": "Помоги"}
    )
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize("authenticated,limited,expected_status", [(False, 0, 401), (True, 1, 429)])
async def test_max_auth_and_limits_block_gateway_spending(
    client,
    db_session,
    monkeypatch,
    authenticated,
    limited,
    expected_status,
):
    project = await fixture(client, db_session, monkeypatch)
    observed = []

    def provider(request):
        observed.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "unexpected"}}]})

    gateway(monkeypatch, provider, limit=limited)
    response = await client.post(
        f"/api/runtime/projects/{project.id}/ai",
        headers=launch() if authenticated else {},
        json={"message": "Hi"},
    )
    assert response.status_code == expected_status
    assert observed == []


async def test_disabled_ai_cannot_spend_even_with_legacy_aitunnel_binding(
    client,
    db_session,
    monkeypatch,
):
    project = await fixture(client, db_session, monkeypatch)
    project.runtime_ai_enabled = False
    await legacy_aitunnel(db_session, project)
    observed = []

    def provider(request):
        observed.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Unexpected"}}]})

    gateway(monkeypatch, provider)
    response = await client.post(
        f"/api/runtime/projects/{project.id}/ai", headers=launch(), json={"message": "Hi"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_integration_required"
    assert observed == []


async def legacy_aitunnel(db_session, project):
    connection = BusinessIntegration(
        business_id=await db_session.scalar(select(BusinessProfile.id)),
        created_by_user_id=project.owner_id,
        provider="aitunnel",
        credentials_enc="must-not-be-loaded",
        capabilities=["ИИ-ответы"],
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        ProjectIntegrationBinding(
            project_id=project.id,
            integration_id=connection.id,
            provider="aitunnel",
        )
    )
    await db_session.commit()


@pytest.mark.parametrize("enabled", [True, False])
async def test_runtime_status_advertises_only_opted_in_platform_ai(
    client,
    db_session,
    monkeypatch,
    enabled,
):
    project = await fixture(client, db_session, monkeypatch)
    project.runtime_ai_enabled = enabled
    await legacy_aitunnel(db_session, project)
    response = await client.get(
        f"/api/runtime/projects/{project.id}/integrations", headers=launch()
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"] == (["llmgw"] if enabled else [])
    assert ("ИИ-ответы" in payload["capabilities"]) is enabled
