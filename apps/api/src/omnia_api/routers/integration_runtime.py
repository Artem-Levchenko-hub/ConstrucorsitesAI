"""Secretless runtime capabilities consumed by generated MAX Mini Apps."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, cast
from urllib.parse import parse_qsl, urlparse
from uuid import UUID

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Header, Request, status
from sqlalchemy import select

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_strong
from omnia_api.core.deps import SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.core.redis import get_redis
from omnia_api.models.app_integration import (
    BusinessIntegration,
    ProjectIntegrationBinding,
)
from omnia_api.models.max_integration import MaxIntegration
from omnia_api.models.project import Project
from omnia_api.schemas.integration_runtime import (
    RuntimeAIPublic,
    RuntimeAIRequest,
    RuntimeCatalogPublic,
    RuntimeIntegrationStatus,
    RuntimeLeadPublic,
    RuntimeLeadRequest,
    RuntimePaymentPublic,
    RuntimePaymentRequest,
    RuntimePaymentStatusRequest,
)
from omnia_api.services import integration_providers
from omnia_api.services.integration_auth import verify_integration_assertion
from omnia_api.services.integration_responses import (
    invalid_response,
    lead_id,
    payment_response,
    response_object,
)
from omnia_api.services.secret_safety import redact_provider_secrets

router = APIRouter(prefix="/api/runtime/projects", tags=["integration-runtime"])
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60
_RUNTIME_AI_LIMIT_SCRIPT = """
for i = 1, #KEYS do
  local count = redis.call("INCR", KEYS[i])
  if count == 1 then
    redis.call("EXPIRE", KEYS[i], tonumber(ARGV[i * 2]))
  end
  if count > tonumber(ARGV[(i - 1) * 2 + 1]) then
    return i
  end
end
return 0
"""


@dataclass(frozen=True)
class RuntimeContext:
    project_id: UUID
    max_user_id: int


def _validate_init_data(init_data: str, bot_token: str) -> int:
    if not init_data or len(init_data) > 16_384:
        raise ValueError("invalid MAX initData")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate MAX initData parameter")
    values = dict(pairs)
    expected = values.pop("hash", "")
    if len(expected) != 64:
        raise ValueError("invalid MAX initData signature")
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    actual = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.lower(), actual.lower()):
        raise ValueError("invalid MAX initData signature")
    auth_date = int(values.get("auth_date", "0"))
    now = int(time.time())
    if auth_date < now - MAX_INIT_DATA_AGE_SECONDS or auth_date > now + 300:
        raise ValueError("expired MAX initData")
    user = json.loads(values.get("user", ""))
    user_id = user.get("id") if isinstance(user, dict) else None
    if isinstance(user_id, str) and user_id.isdecimal():
        user_id = int(user_id)
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise ValueError("invalid MAX user")
    return user_id


async def _runtime_context(
    session: SessionDep, project_id: UUID, init_data: str, request: Request
) -> RuntimeContext:
    max_integration = (
        await session.execute(
            select(MaxIntegration).where(MaxIntegration.project_id == project_id)
        )
    ).scalar_one_or_none()
    if max_integration is None:
        raise ApiError(
            "max_integration_required",
            "MAX-бот проекта не подключён",
            status.HTTP_409_CONFLICT,
        )
    try:
        token = decrypt_strong(max_integration.bot_token_enc)
        assertion = request.headers.get("X-Omnia-Integration-Assertion")
        if assertion:
            if request.url.query:
                raise ValueError("unsigned integration query")
            max_user_id = verify_integration_assertion(
                assertion, token, project_id=project_id,
                method=request.method, path=request.url.path, body=await request.body(),
            )
        else:
            max_user_id = _validate_init_data(init_data, token)
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(
            "max_init_data_invalid",
            "Не удалось подтвердить запуск приложения из MAX",
            status.HTTP_401_UNAUTHORIZED,
        ) from exc
    return RuntimeContext(project_id=project_id, max_user_id=max_user_id)


async def _connections(
    session: SessionDep, project_id: UUID
) -> dict[str, BusinessIntegration]:
    rows = (
        await session.execute(
            select(BusinessIntegration)
            .join(
                ProjectIntegrationBinding,
                ProjectIntegrationBinding.integration_id == BusinessIntegration.id,
            )
            .where(
                ProjectIntegrationBinding.project_id == project_id,
                ProjectIntegrationBinding.enabled.is_(True),
                ProjectIntegrationBinding.status == "ready",
                BusinessIntegration.status == "active",
            )
        )
    ).scalars()
    return {row.provider: row for row in rows}


async def _secrets(
    session: SessionDep, connection: BusinessIntegration
) -> dict[str, str]:
    from omnia_api.services.integration_credentials import load_credentials

    return await load_credentials(session, connection)


def _provider_failure(provider: str, response: httpx.Response) -> ApiError:
    if response.status_code in {400, 401, 403}:
        return ApiError(
            "integration_request_rejected",
            f"{provider} отклонил запрос. Проверьте настройки подключения.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if response.status_code == 429 or response.status_code >= 500:
        return ApiError(
            "integration_provider_unavailable",
            f"{provider} временно недоступен",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return ApiError(
        "integration_request_failed",
        f"{provider} вернул HTTP {response.status_code}",
        status.HTTP_502_BAD_GATEWAY,
    )


@router.get("/{project_id}/integrations", response_model=RuntimeIntegrationStatus)
async def runtime_integration_status(
    project_id: UUID,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeIntegrationStatus:
    await _runtime_context(session, project_id, x_max_init_data, request)
    project = await session.get(Project, project_id)
    if project is None:
        raise ApiError("not_found", "Проект не найден", status.HTTP_404_NOT_FOUND)
    connections = await _connections(session, project_id)
    connections.pop("aitunnel", None)
    metrica = connections.get("yandex_metrica")
    providers = set(connections)
    capabilities = {
        capability
        for connection in connections.values()
        for capability in integration_providers.get_provider(connection.provider).capabilities
    }
    if project.runtime_ai_enabled:
        providers.add("llmgw")
        capabilities.update(integration_providers.get_provider("llmgw").capabilities)
    return RuntimeIntegrationStatus(
        providers=sorted(providers),
        capabilities=sorted(capabilities),
        analytics_counter_id=(
            str(metrica.public_config.get("counter_id"))
            if metrica and metrica.public_config.get("counter_id")
            else None
        ),
    )


async def _enforce_runtime_ai_limits(project_id: UUID, max_user_id: int) -> None:
    """Fail closed before spending the project owner's wallet balance."""

    buckets = (
        (f"omnia:runtime-ai:minute:{project_id}:{max_user_id}", 8, 60),
        (f"omnia:runtime-ai:day:{project_id}:{max_user_id}", 120, 86_400),
        (f"omnia:runtime-ai:project-day:{project_id}", 2_000, 86_400),
    )
    try:
        redis = get_redis()
        keys = [key for key, _limit, _ttl in buckets]
        args = [str(value) for _key, limit, ttl in buckets for value in (limit, ttl)]
        eval_command = cast(Any, redis.eval)
        exceeded_bucket = int(
            await eval_command(_RUNTIME_AI_LIMIT_SCRIPT, len(keys), *keys, *args)
        )
        if exceeded_bucket:
            raise ApiError(
                "rate_limited",
                "Лимит ИИ-запросов временно исчерпан. Попробуйте позже.",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "Проверка лимита ИИ временно недоступна. Попробуйте позже.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc


async def _request_gateway_ai(
    *,
    owner_id: UUID,
    project_id: UUID,
    system_prompt: str,
    user_message: str,
) -> RuntimeAIPublic:
    """Request billed inference through Omnia's internal LLM gateway."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=5.0)) as client:
            response = await client.post(
                f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Omnia-MAX-Runtime/1.0",
                },
                json={
                    "model": settings.max_runtime_ai_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "user": str(owner_id),
                    "metadata": {
                        "project_id": str(project_id), "free": False, "require_billing": True,
                    },
                    "stream": False,
                    "max_tokens": 1_600,
                    "temperature": 0.35,
                },
            )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "ИИ временно недоступен. Попробуйте ещё раз.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if response.status_code == 402:
        raise ApiError(
            "wallet_empty",
            "На балансе владельца приложения недостаточно средств для ИИ-запроса.",
            status.HTTP_402_PAYMENT_REQUIRED,
        )
    if response.status_code == 429:
        raise ApiError(
            "rate_limited",
            "Лимит ИИ-запросов временно исчерпан. Попробуйте позже.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if response.status_code >= 500:
        raise ApiError(
            "integration_provider_unavailable",
            "ИИ временно недоступен. Попробуйте ещё раз.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if response.status_code >= 300:
        raise ApiError(
            "integration_request_failed",
            "Не удалось выполнить ИИ-запрос. Попробуйте позже.",
            status.HTTP_502_BAD_GATEWAY,
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise invalid_response() from exc
    if not isinstance(body, dict) or body.get("error"):
        raise invalid_response()
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    answer = message.get("content") if isinstance(message, dict) else None
    model = body.get("model", settings.max_runtime_ai_model)
    if not isinstance(answer, str) or not answer.strip() or not isinstance(model, str):
        raise invalid_response()
    return RuntimeAIPublic(
        answer=redact_provider_secrets(answer).strip()[:16_000],
        model=redact_provider_secrets(model)[:200],
    )


@router.post("/{project_id}/ai", response_model=RuntimeAIPublic)
async def request_runtime_ai(
    project_id: UUID,
    payload: RuntimeAIRequest,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeAIPublic:
    """Run owner-billed inference through the internal LLM gateway."""

    context = await _runtime_context(session, project_id, x_max_init_data, request)
    project = await session.get(Project, project_id)
    if project is None:
        raise ApiError("not_found", "Проект не найден", status.HTTP_404_NOT_FOUND)
    if not project.runtime_ai_enabled:
        raise ApiError(
            "ai_integration_required",
            "Включите ИИ в настройках приложения",
            status.HTTP_409_CONFLICT,
        )
    await _enforce_runtime_ai_limits(project_id, context.max_user_id)
    system_prompt = (
        "Ты — ИИ-функция внутри MAX Mini App. Отвечай на русском языке, кратко и "
        "по существу. Используй только переданный контекст, не выдумывай измерения "
        "или действия, которых не было. Для медицинских, юридических и финансовых "
        "тем явно обозначай ограничения и не выдавай ответ за профессиональный диагноз."
    )
    if payload.instructions:
        system_prompt += "\n\nЗадача продукта:\n" + payload.instructions
    user_message = payload.message
    if payload.context:
        user_message += "\n\nКонтекст продукта (JSON):\n" + json.dumps(
            payload.context,
            ensure_ascii=False,
            default=str,
        )
    return await _request_gateway_ai(
        owner_id=project.owner_id,
        project_id=project.id,
        system_prompt=system_prompt,
        user_message=user_message,
    )


@router.post("/{project_id}/payments", response_model=RuntimePaymentPublic)
async def create_runtime_payment(
    project_id: UUID,
    payload: RuntimePaymentRequest,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimePaymentPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    connection = (await _connections(session, project_id)).get("yookassa")
    if connection is None:
        raise ApiError(
            "payment_integration_required",
            "Подключите ЮKassa к этому приложению",
            status.HTTP_409_CONFLICT,
        )
    credentials = await _secrets(session, connection)
    auth: tuple[str, str] | None = None
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        # YooKassa scopes keys to the merchant; reconnecting must not create a new payment.
        "Idempotence-Key": hashlib.sha256(
            (
                f"omnia:payment:v1:{project_id}:"
                f"{context.max_user_id}:{payload.idempotency_key}"
            ).encode()
        ).hexdigest(),
        "User-Agent": "Omnia-MAX-Runtime/1.0",
    }
    if credentials.get("access_token"):
        headers["Authorization"] = f"Bearer {credentials['access_token']}"
    else:
        auth = (
            str(connection.public_config.get("shop_id") or ""),
            credentials.get("secret_key", ""),
        )
    body: dict[str, Any] = {
        "amount": {
            "value": f"{Decimal(payload.amount):.2f}",
            "currency": "RUB",
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": str(payload.return_url),
        },
        "description": payload.description,
        "metadata": {
            **payload.metadata,
            "omnia_project_id": str(project_id),
            "max_user_id": str(context.max_user_id),
        },
    }
    if payload.receipt:
        body["receipt"] = payload.receipt
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            request_kwargs: dict[str, Any] = {"headers": headers, "json": body}
            if auth is not None:
                request_kwargs["auth"] = auth
            response = await client.post(
                "https://api.yookassa.ru/v3/payments", **request_kwargs
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "ЮKassa временно недоступна",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if response.status_code >= 300:
        raise _provider_failure("ЮKassa", response)
    return payment_response(response_object(response))


@router.post("/{project_id}/payments/status", response_model=RuntimePaymentPublic)
async def get_runtime_payment_status(
    project_id: UUID,
    payload: RuntimePaymentStatusRequest,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimePaymentPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    connection = (await _connections(session, project_id)).get("yookassa")
    if connection is None:
        raise ApiError(
            "payment_integration_required",
            "Подключите ЮKassa к этому приложению",
            status.HTTP_409_CONFLICT,
        )
    credentials = await _secrets(session, connection)
    headers = {"Accept": "application/json", "User-Agent": "Omnia-MAX-Runtime/1.0"}
    auth: tuple[str, str] | None = None
    if credentials.get("access_token"):
        headers["Authorization"] = f"Bearer {credentials['access_token']}"
    else:
        auth = (
            str(connection.public_config.get("shop_id") or ""),
            credentials.get("secret_key", ""),
        )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if auth is None:
                response = await client.get(
                    f"https://api.yookassa.ru/v3/payments/{payload.payment_id}",
                    headers=headers,
                )
            else:
                response = await client.get(
                    f"https://api.yookassa.ru/v3/payments/{payload.payment_id}",
                    headers=headers,
                    auth=auth,
                )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "ЮKassa временно недоступна",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if response.status_code >= 300:
        raise _provider_failure("ЮKassa", response)
    result = response_object(response)
    metadata = result.get("metadata")
    if (
        not isinstance(metadata, dict)
        or metadata.get("omnia_project_id") != str(project_id)
        or str(metadata.get("max_user_id")) != str(context.max_user_id)
    ):
        raise ApiError("not_found", "Платёж не найден", 404)
    if result.get("id") != payload.payment_id:
        raise invalid_response()
    return payment_response(result)


@router.post("/{project_id}/leads", response_model=RuntimeLeadPublic)
async def create_runtime_lead(
    project_id: UUID,
    payload: RuntimeLeadRequest,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeLeadPublic:
    context = await _runtime_context(session, project_id, x_max_init_data, request)
    connections = await _connections(session, project_id)
    connection = connections.get("bitrix24") or connections.get("amocrm")
    if connection is None:
        raise ApiError(
            "crm_integration_required",
            "Подключите Битрикс24 или amoCRM к этому приложению",
            status.HTTP_409_CONFLICT,
        )
    credentials = await _secrets(session, connection)
    from omnia_api.services.integration_operations import execute_once

    async def send() -> dict[str, Any]:
        return (await _send_runtime_lead(connection, credentials, context, payload)).model_dump()

    if payload.idempotency_key is None:
        # Compatibility with previously generated clients; new clients always
        # provide a stable operation key. No platform retry occurs on this path.
        return await _send_runtime_lead(connection, credentials, context, payload)
    result = await execute_once(
        session, project_id=project_id, integration_id=connection.id, provider=connection.provider,
        max_user_id=context.max_user_id, kind="lead", client_key=payload.idempotency_key,
        payload=payload.model_dump(exclude={"idempotency_key"}), send=send,
    )
    return RuntimeLeadPublic.model_validate(result)


async def _send_runtime_lead(
    connection: BusinessIntegration, credentials: dict[str, str],
    context: RuntimeContext, payload: RuntimeLeadRequest,
) -> RuntimeLeadPublic:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if connection.provider == "bitrix24":
                fields: dict[str, Any] = {
                    "TITLE": payload.name,
                    "NAME": payload.name,
                    "SOURCE_DESCRIPTION": payload.source,
                    "COMMENTS": (
                        f"{payload.comment or ''}\nMAX user: {context.max_user_id}"
                    ).strip(),
                }
                if payload.phone:
                    fields["PHONE"] = [{"VALUE": payload.phone, "VALUE_TYPE": "WORK"}]
                if payload.email:
                    fields["EMAIL"] = [{"VALUE": payload.email, "VALUE_TYPE": "WORK"}]
                if credentials.get("webhook_url"):
                    url = (
                        credentials["webhook_url"].rstrip("/")
                        + "/crm.lead.add.json"
                    )
                    response = await client.post(url, json={"fields": fields})
                else:
                    endpoint = str(
                        connection.public_config.get("client_endpoint") or ""
                    ).rstrip("/")
                    response = await client.post(
                        f"{endpoint}/crm.lead.add.json",
                        json={
                            "auth": credentials.get("access_token"),
                            "fields": fields,
                        },
                    )
                if response.status_code >= 300:
                    raise _provider_failure("Битрикс24", response)
                result = response_object(response).get("result")
                return RuntimeLeadPublic(provider="bitrix24", id=lead_id(result))

            base_url = str(connection.public_config.get("base_url") or "").rstrip("/")
            parsed = urlparse(base_url)
            if not parsed.hostname:
                raise ApiError(
                    "integration_configuration_invalid",
                    "Переподключите amoCRM",
                    status.HTTP_409_CONFLICT,
                )
            contact: dict[str, Any] = {"name": payload.name, "custom_fields_values": []}
            if payload.phone:
                contact["custom_fields_values"].append(
                    {
                        "field_code": "PHONE",
                        "values": [{"value": payload.phone, "enum_code": "WORK"}],
                    }
                )
            if payload.email:
                contact["custom_fields_values"].append(
                    {
                        "field_code": "EMAIL",
                        "values": [{"value": payload.email, "enum_code": "WORK"}],
                    }
                )
            response = await client.post(
                f"{base_url}/api/v4/leads/complex",
                headers={
                    "Authorization": f"Bearer {credentials.get('access_token', '')}",
                    "Content-Type": "application/json",
                },
                json=[
                    {
                        "name": payload.name,
                        "_embedded": {"contacts": [contact]},
                        "custom_fields_values": [],
                    }
                ],
            )
            if response.status_code >= 300:
                raise _provider_failure("amoCRM", response)
            result = response.json()
            lead = result[0] if isinstance(result, list) and result else {}
            if not isinstance(lead, dict):
                raise invalid_response()
            return RuntimeLeadPublic(provider="amocrm", id=lead_id(lead.get("id")))
    except ApiError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "CRM временно недоступна",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise ApiError(
            "integration_response_invalid",
            "CRM вернула ответ неизвестного формата",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc


@router.get("/{project_id}/catalog", response_model=RuntimeCatalogPublic)
async def get_runtime_catalog(
    project_id: UUID,
    session: SessionDep,
    request: Request,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")] = "",
) -> RuntimeCatalogPublic:
    await _runtime_context(session, project_id, x_max_init_data, request)
    connections = await _connections(session, project_id)
    connection = connections.get("iiko") or connections.get("moysklad")
    if connection is None:
        raise ApiError(
            "integration_not_found",
            "Подключите iikoCloud или МойСклад к этому приложению",
            status.HTTP_409_CONFLICT,
        )
    credentials = await _secrets(session, connection)
    try:
        async with httpx.AsyncClient(
            timeout=20,
            headers={"Accept": "application/json", "User-Agent": "Omnia-MAX-Runtime/1.0"},
        ) as client:
            if connection.provider == "moysklad":
                response = await client.get(
                    "https://api.moysklad.ru/api/remap/1.2/entity/product",
                    params={"limit": 100, "order": "updated,desc"},
                    headers={
                        "Authorization": f"Bearer {credentials.get('token', '')}",
                    },
                )
                if response.status_code >= 300:
                    raise _provider_failure("МойСклад", response)
                from omnia_api.services.integration_catalog import moysklad_items

                items = moysklad_items(response_object(response))
                return RuntimeCatalogPublic(provider="moysklad", items=items)

            token_response = await client.post(
                "https://api-ru.iiko.services/api/1/access_token",
                json={"apiLogin": credentials.get("api_login", "")},
            )
            if token_response.status_code >= 300:
                raise _provider_failure("iikoCloud", token_response)
            access_token = response_object(token_response).get("token")
            if not isinstance(access_token, str) or not access_token.strip():
                raise invalid_response()
            auth_headers = {"Authorization": f"Bearer {access_token}"}
            organizations_response = await client.post(
                "https://api-ru.iiko.services/api/1/organizations",
                headers=auth_headers,
                json={},
            )
            if organizations_response.status_code >= 300:
                raise _provider_failure("iikoCloud", organizations_response)
            organizations = response_object(organizations_response).get("organizations")
            if not isinstance(organizations, list):
                raise invalid_response()
            if not organizations:
                raise ApiError(
                    "integration_configuration_invalid",
                    "В iikoCloud не найдена доступная организация",
                    status.HTTP_409_CONFLICT,
                )
            organization = organizations[0]
            if not isinstance(organization, dict):
                raise invalid_response()
            organization_id = organization.get("id")
            if not isinstance(organization_id, str) or not organization_id.strip():
                raise invalid_response()
            menu_response = await client.post(
                "https://api-ru.iiko.services/api/1/nomenclature",
                headers=auth_headers,
                json={"organizationId": organization_id, "startRevision": 0},
            )
            if menu_response.status_code >= 300:
                raise _provider_failure("iikoCloud", menu_response)
            from omnia_api.services.integration_catalog import iiko_items

            items = iiko_items(response_object(menu_response))
            return RuntimeCatalogPublic(provider="iiko", items=items)
    except ApiError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "Каталог временно недоступен",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        raise ApiError(
            "integration_response_invalid",
            "Сервис каталога вернул неизвестный формат",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
