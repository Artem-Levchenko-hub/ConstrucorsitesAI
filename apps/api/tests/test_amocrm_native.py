from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from yleum_api.schemas.integration_runtime import RuntimeLeadRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"result": False}, {"result": {}}])
async def test_legacy_bitrix_without_key_keeps_invalid_response_error(monkeypatch, body):
    from yleum_api.core.errors import ApiError
    from yleum_api.models.app_integration import AccountIntegration
    from yleum_api.routers import integration_runtime as runtime

    project_id = uuid4()
    connection = AccountIntegration(id=uuid4(), provider="bitrix24", public_config={})
    monkeypatch.setattr(
        runtime,
        "_runtime_context",
        AsyncMock(
            return_value=runtime.RuntimeContext(
                project_id=project_id,
                max_user_id=42,
            )
        ),
    )
    monkeypatch.setattr(runtime, "_connections", AsyncMock(return_value={"bitrix24": connection}))
    monkeypatch.setattr(
        runtime,
        "_secrets",
        AsyncMock(
            return_value={
                "webhook_url": "https://test.bitrix24.ru/rest/1/synthetic/",
            }
        ),
    )
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
            **kwargs,
        ),
    )
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: None))
    )
    with pytest.raises(ApiError) as caught:
        await runtime.create_runtime_lead(
            project_id, RuntimeLeadRequest(name="Alice"), session, None
        )
    assert caught.value.status_code == 502
    assert caught.value.code == "integration_response_invalid"


@pytest.mark.asyncio
async def test_amocrm_missing_key_fails_before_loading_secrets_or_provider(monkeypatch):
    from yleum_api.core.errors import ApiError
    from yleum_api.models.app_integration import AccountIntegration
    from yleum_api.routers import integration_runtime as runtime

    project_id = uuid4()
    monkeypatch.setattr(
        runtime,
        "_runtime_context",
        AsyncMock(
            return_value=runtime.RuntimeContext(
                project_id=project_id,
                max_user_id=42,
            )
        ),
    )
    monkeypatch.setattr(
        runtime,
        "_connections",
        AsyncMock(
            return_value={
                "amocrm": AccountIntegration(id=uuid4(), provider="amocrm", public_config={}),
            }
        ),
    )
    secrets = AsyncMock()
    monkeypatch.setattr(runtime, "_secrets", secrets)
    with pytest.raises(ApiError) as caught:
        await runtime.create_runtime_lead(project_id, RuntimeLeadRequest(name="Alice"), None, None)
    assert caught.value.status_code == 422
    secrets.assert_not_called()


def test_lead_rejects_blank_name_and_invalid_contact():
    for fields in (
        {"name": "   "},
        {"name": "Test", "email": "bad"},
        {"name": "Test", "phone": "hello"},
    ):
        with pytest.raises(ValidationError):
            RuntimeLeadRequest(**fields)


def test_amocrm_payload_maps_contact_and_project_funnel():
    from yleum_api.services.amocrm import lead_body

    body = lead_body(
        RuntimeLeadRequest(name=" Alice ", phone="+7 (900) 000-00-00", email="alice@example.com"),
        {"pipeline_id": 12, "status_id": 34},
    )
    assert body["name"] == "Alice"
    assert body["pipeline_id"] == 12
    assert body["status_id"] == 34
    assert "custom_fields_values" not in body
    contact = body["_embedded"]["contacts"][0]
    assert contact["name"] == "Alice"
    assert {f["field_code"] for f in contact["custom_fields_values"]} == {"PHONE", "EMAIL"}
    assert (
        "custom_fields_values"
        not in lead_body(RuntimeLeadRequest(name="Alice"), {})["_embedded"]["contacts"][0]
    )


def test_amocrm_options_keep_real_open_stages_only():
    from yleum_api.services.amocrm import pipeline_options

    assert pipeline_options(
        {
            "_embedded": {
                "pipelines": [
                    {
                        "id": 12,
                        "name": "Sales",
                        "_embedded": {
                            "statuses": [
                                {"id": 34, "name": "New", "type": 0},
                                {"id": 142, "name": "Won", "type": 0},
                                {"id": 143, "name": "Lost", "type": 0},
                                {"id": 99, "name": "Unsorted", "type": 1},
                            ]
                        },
                    },
                ]
            }
        }
    ) == [{"id": 12, "name": "Sales", "statuses": [{"id": 34, "name": "New"}]}]


def test_amocrm_note_preserves_comment_source_and_context():
    from yleum_api.services.amocrm import note_text

    project_id = uuid4()
    text = note_text(
        RuntimeLeadRequest(name="Alice", comment="Call after 18:00", source="Coffee catering"),
        project_id,
        42,
    )
    assert "Call after 18:00" in text
    assert "Coffee catering" in text
    assert str(project_id) in text
    assert "42" in text


@pytest.mark.parametrize("include_closed", [False, True])
def test_amocrm_archived_pipeline_labels_are_available_only_for_history(include_closed):
    from yleum_api.services.amocrm import pipeline_options

    stages = [
        {"id": 34, "name": "New", "type": 0},
        {"id": 142, "name": "Won", "type": 0},
        {"id": 143, "name": "Lost", "type": 0},
    ]
    body = {
        "_embedded": {
            "pipelines": [
                {
                    "id": 12,
                    "name": "Current",
                    "is_archive": False,
                    "_embedded": {"statuses": stages},
                },
                {
                    "id": 24,
                    "name": "Archive",
                    "is_archive": True,
                    "_embedded": {"statuses": stages},
                },
            ]
        }
    }
    options = pipeline_options(body, include_closed=include_closed)
    assert [p["id"] for p in options] == ([12, 24] if include_closed else [12])
    assert all(
        p["statuses"]
        == [{"id": s["id"], "name": s["name"]} for s in (stages if include_closed else stages[:1])]
        for p in options
    )
