import logging
from typing import Any
from uuid import UUID

import httpx
import pytest
from _pytest.logging import LogCaptureFixture

from yleum_api.models.app_integration import AccountIntegration
from yleum_api.routers import integration_runtime
from yleum_api.routers.integration_runtime import RuntimeContext, _provider_failure
from yleum_api.schemas.integration_runtime import RuntimeLeadRequest


def test_provider_failure_logs_only_safe_validation_metadata(
    caplog: LogCaptureFixture,
) -> None:
    response = httpx.Response(
        400,
        json={
            "validation-errors": [
                {
                    "request_id": "customer-action-secret",
                    "errors": [
                        {
                            "code": "NotSupportedChoice",
                            "path": "custom_fields_values.0.field_id",
                            "detail": "Rejected +79000000000 and token=secret-value",
                        }
                    ],
                }
            ],
            "title": "Bad Request",
            "detail": "Request validation failed for QA customer",
        },
    )

    with caplog.at_level(logging.WARNING):
        error = _provider_failure("amoCRM", response)

    assert error.code == "integration_request_rejected"
    assert "provider=amoCRM" in caplog.text
    assert "status=400" in caplog.text
    assert "title=Bad Request" in caplog.text
    assert "codes=NotSupportedChoice" in caplog.text
    assert "paths=custom_fields_values.0.field_id" in caplog.text
    assert "customer-action-secret" not in caplog.text
    assert "+79000000000" not in caplog.text
    assert "secret-value" not in caplog.text
    assert "QA customer" not in caplog.text


@pytest.mark.asyncio
async def test_amocrm_lead_omits_empty_optional_custom_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[list[dict[str, Any]]] = []

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            posted.append(kwargs["json"])
            return httpx.Response(200, json=[{"id": 123}])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    connection = AccountIntegration(
        provider="amocrm",
        public_config={"base_url": "https://example.amocrm.ru"},
    )

    result = await integration_runtime._send_runtime_lead(
        connection,
        {"access_token": "test-token"},
        RuntimeContext(
            project_id=UUID("00000000-0000-0000-0000-000000000001"),
            max_user_id=123,
        ),
        RuntimeLeadRequest(name="QA lead", phone="+79000000000"),
    )

    assert result.id == "123"
    assert len(posted) == 1
    assert "custom_fields_values" not in posted[0][0]
    assert posted[0][0]["_embedded"]["contacts"][0]["custom_fields_values"]
