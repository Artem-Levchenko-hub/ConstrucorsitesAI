"""Small amoCRM v4 contracts; provider secrets never enter generated apps."""

from typing import Any
from uuid import UUID

import httpx

from yleum_api.core.errors import ApiError
from yleum_api.models.app_integration import AccountIntegration
from yleum_api.schemas.integration_runtime import RuntimeLeadRequest
from yleum_api.services.integration_oauth import _amocrm_base_url
from yleum_api.services.integration_providers import IntegrationCredentialsInvalid
from yleum_api.services.integration_responses import invalid_response, response_object


def base_url(connection: AccountIntegration) -> str:
    try:
        return _amocrm_base_url(str(connection.public_config.get("base_url") or ""))
    except (ValueError, IntegrationCredentialsInvalid) as exc:
        raise ApiError("integration_configuration_invalid", "Переподключите amoCRM", 409) from exc


def lead_body(payload: RuntimeLeadRequest, config: dict[str, Any]) -> dict[str, Any]:
    contact: dict[str, Any] = {"name": payload.name}
    fields = [
        {"field_code": code, "values": [{"value": value, "enum_code": "WORK"}]}
        for code, value in (("PHONE", payload.phone), ("EMAIL", payload.email))
        if value
    ]
    if fields:
        contact["custom_fields_values"] = fields
    body: dict[str, Any] = {"name": payload.name, "_embedded": {"contacts": [contact]}}
    for key in ("pipeline_id", "status_id"):
        if isinstance(config.get(key), int) and config[key] > 0:
            body[key] = config[key]
    return body


def note_text(payload: RuntimeLeadRequest, project_id: UUID, max_user_id: int) -> str:
    return (
        f"{payload.comment or ''}\n\nИсточник: {payload.source}\n"
        f"Yleum MAX Mini App: {project_id}\nMAX user: {max_user_id}"
    ).strip()


def pipeline_options(body: dict[str, Any], *, include_closed: bool = False) -> list[dict[str, Any]]:
    try:
        pipelines = body["_embedded"]["pipelines"]
        if not isinstance(pipelines, list):
            raise ValueError
        result = []
        for pipeline in pipelines:
            if pipeline.get("is_archive"):
                continue
            statuses = []
            for stage in pipeline["_embedded"]["statuses"]:
                if not include_closed and (stage.get("type") == 1 or stage["id"] in (142, 143)):
                    continue
                statuses.append({"id": positive_id(stage["id"]), "name": label(stage["name"])})
            result.append(
                {
                    "id": positive_id(pipeline["id"]),
                    "name": label(pipeline["name"]),
                    "statuses": statuses,
                }
            )
        return result
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise invalid_response() from exc


def positive_id(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("invalid provider identifier")
    return value


def label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid provider label")
    return value[:200]


async def read(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    # Read errors must never expose a provider body (which can contain customer data).
    from yleum_api.routers.integration_runtime import _provider_failure

    try:
        response = await client.get(url)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        raise ApiError(
            "integration_provider_unavailable", "amoCRM временно недоступна", 503
        ) from exc
    if response.status_code == 404 or response.status_code == 204:
        raise ApiError("not_found", "Запись amoCRM не найдена", 404)
    if response.status_code >= 300:
        raise _provider_failure("amoCRM", response)
    return response_object(response)


def client(credentials: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=15,
        headers={
            "Authorization": f"Bearer {credentials.get('access_token', '')}",
            "Accept": "application/json",
            "User-Agent": "Yleum-MAX-Runtime/1.0",
        },
    )
