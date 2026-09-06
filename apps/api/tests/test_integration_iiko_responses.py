"""iiko pre-menu responses cannot crash or invent successful credentials."""

import json

import httpx
import pytest
from sqlalchemy import select

from omnia_api.core.crypto import encrypt_strong
from omnia_api.models.app_integration import BusinessIntegration, ProjectIntegrationBinding
from tests.test_integration_runtime_contracts import connect, headers, upstream


@pytest.mark.parametrize(
    ("phase", "payload"),
    [
        ("access_token", []),
        ("access_token", None),
        ("access_token", {}),
        ("access_token", {"token": {"secret": "synthetic-private-value"}}),
        ("access_token", {"token": ""}),
        ("access_token", {"token": "   "}),
        ("organizations", []),
        ("organizations", {}),
        ("organizations", {"organizations": {"id": "synthetic-private-value"}}),
        ("organizations", {"organizations": [None]}),
        ("organizations", {"organizations": ["synthetic-private-value"]}),
        ("organizations", {"organizations": [{"id": {"secret": "synthetic-private-value"}}]}),
    ],
)
async def test_iiko_malformed_auth_responses_are_safe_502(
    client, db_session, monkeypatch, phase, payload
):
    project = await _connect_iiko(client, db_session, monkeypatch)
    visited = []

    def provider(request):
        operation = request.url.path.rsplit("/", 1)[-1]
        visited.append(operation)
        if operation == phase:
            return httpx.Response(200, content=json.dumps(payload))
        return httpx.Response(
            200,
            json={
                "access_token": {"token": "synthetic-token"},
                "organizations": {"organizations": [{"id": "organization-1"}]},
                "nomenclature": {"products": []},
            }[operation],
        )

    upstream(monkeypatch, provider)
    result = await client.get(f"/api/runtime/projects/{project}/catalog", headers=headers())
    assert result.status_code == 502
    assert result.json()["error"]["code"] == "integration_response_invalid"
    assert "synthetic" not in result.text
    assert visited[-1] == phase


async def _connect_iiko(client, db_session, monkeypatch):
    project = await connect(client, db_session, monkeypatch)
    connection = (await db_session.execute(select(BusinessIntegration))).scalar_one()
    binding = (await db_session.execute(select(ProjectIntegrationBinding))).scalar_one()
    connection.provider = "iiko"
    connection.credentials_enc = encrypt_strong(json.dumps({"api_login": "synthetic-api-login"}))
    binding.provider = "iiko"
    await db_session.commit()
    return project


@pytest.mark.parametrize("organizations,expected_status", [([], 409), ([{"id": "org-1"}], 200)])
async def test_iiko_valid_organization_contract(
    client, db_session, monkeypatch, organizations, expected_status
):
    project = await _connect_iiko(client, db_session, monkeypatch)

    def provider(request):
        operation = request.url.path.rsplit("/", 1)[-1]
        if operation != "access_token":
            assert request.headers["authorization"] == "Bearer synthetic-token"
        return httpx.Response(
            200,
            json={
                "access_token": {"token": "synthetic-token"},
                "organizations": {"organizations": organizations},
                "nomenclature": {"products": [{"id": "tea", "name": "Tea"}]},
            }[operation],
        )

    upstream(monkeypatch, provider)
    result = await client.get(f"/api/runtime/projects/{project}/catalog", headers=headers())
    assert result.status_code == expected_status
    if expected_status == 200:
        assert result.json()["items"][0]["id"] == "tea"
    else:
        assert result.json()["error"]["code"] == "integration_configuration_invalid"
