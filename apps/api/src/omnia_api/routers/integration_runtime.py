"""Secretless runtime capabilities consumed by generated MAX Mini Apps."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlparse
from uuid import UUID

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Header, status
from sqlalchemy import select

from omnia_api.core.config import PRIMARY_LLM_MODEL
from omnia_api.core.crypto import decrypt_strong, encrypt_strong
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
    RuntimeCatalogItem,
    RuntimeCatalogPublic,
    RuntimeIntegrationStatus,
    RuntimeLeadPublic,
    RuntimeLeadRequest,
    RuntimePaymentPublic,
    RuntimePaymentRequest,
    RuntimePaymentStatusRequest,
)
from omnia_api.services import integration_oauth, integration_providers, llm_client
from omnia_api.services.secret_safety import redact_provider_secrets

router = APIRouter(prefix="/api/runtime/projects", tags=["integration-runtime"])
MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60


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
    session: SessionDep, project_id: UUID, init_data: str
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
    try:
        value = json.loads(decrypt_strong(connection.credentials_enc))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(
            "integration_credentials_corrupted",
            "Подключение сервиса повреждено. Переподключите его в Integration Hub.",
            status.HTTP_409_CONFLICT,
        ) from exc
    if not isinstance(value, dict):
        raise ApiError(
            "integration_credentials_corrupted",
            "Подключение сервиса повреждено. Переподключите его в Integration Hub.",
            status.HTTP_409_CONFLICT,
        )
    result = {str(key): str(item) for key, item in value.items()}
    if (
        connection.auth_mode == "oauth"
        and connection.token_expires_at is not None
        and connection.token_expires_at <= datetime.now(UTC) + timedelta(minutes=2)
    ):
        try:
            result, expires_at = await integration_oauth.refresh_access_token(
                connection.provider,
                dict(connection.public_config or {}),
                result,
            )
        except integration_providers.IntegrationProviderError as exc:
            connection.status = "error"
            connection.last_error = str(exc)[:500]
            await session.commit()
            raise ApiError(
                "integration_credentials_invalid",
                str(exc),
                status.HTTP_409_CONFLICT,
            ) from exc
        connection.credentials_enc = encrypt_strong(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        connection.token_expires_at = expires_at
        connection.last_checked_at = datetime.now(UTC)
        await session.commit()
    return result


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
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")],
) -> RuntimeIntegrationStatus:
    await _runtime_context(session, project_id, x_max_init_data)
    connections = await _connections(session, project_id)
    metrica = connections.get("yandex_metrica")
    return RuntimeIntegrationStatus(
        providers=sorted(connections),
        capabilities=sorted(
            {"Управляемый Google AI"}
            | {
                capability
                for connection in connections.values()
                for capability in (connection.capabilities or [])
            }
        ),
        analytics_counter_id=(
            str(metrica.public_config.get("counter_id"))
            if metrica and metrica.public_config.get("counter_id")
            else None
        ),
    )


async def _enforce_runtime_ai_limits(
    project_id: UUID,
    max_user_id: int,
    *,
    fail_closed: bool = False,
) -> None:
    """Bound owner-funded inference even when a bot user scripts the endpoint."""

    buckets = (
        (f"omnia:runtime-ai:minute:{project_id}:{max_user_id}", 8, 60),
        (f"omnia:runtime-ai:day:{project_id}:{max_user_id}", 120, 86_400),
        (f"omnia:runtime-ai:project-day:{project_id}", 2_000, 86_400),
    )
    try:
        redis = get_redis()
        for key, limit, ttl in buckets:
            count = int(await redis.incr(key))
            if count == 1:
                await redis.expire(key, ttl)
            if count > limit:
                raise ApiError(
                    "rate_limited",
                    "Лимит ИИ-запросов временно исчерпан. Попробуйте позже.",
                    status.HTTP_429_TOO_MANY_REQUESTS,
                )
    except ApiError:
        raise
    except Exception as exc:
        if fail_closed:
            raise ApiError(
                "integration_provider_unavailable",
                "Проверка лимита ИИ временно недоступна. Попробуйте позже.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        # Billing still fails closed at the gateway wallet. A transient Redis
        # outage must not take every generated MAX product offline.
        return


async def _request_aitunnel_ai(
    session: SessionDep,
    connection: BusinessIntegration,
    *,
    system_prompt: str,
    user_message: str,
) -> RuntimeAIPublic:
    """Use the owner's encrypted AITUNNEL key without exposing it to the app or agent."""

    credentials = await _secrets(session, connection)
    api_key = credentials.get("api_key", "")
    if not api_key:
        raise ApiError(
            "integration_credentials_corrupted",
            "Подключение AITUNNEL повреждено. Подключите сервис заново.",
            status.HTTP_409_CONFLICT,
        )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35.0)) as client:
            response = await client.post(
                "https://api.aitunnel.ru/v1/chat/completions",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Omnia-MAX-Runtime/1.0",
                },
                json={
                    "model": "auto",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": 1_600,
                    "temperature": 0.35,
                },
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "AITUNNEL временно недоступен. Попробуйте ещё раз.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if response.status_code >= 300:
        raise _provider_failure("AITUNNEL", response)
    try:
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        answer = message.get("content") if isinstance(message, dict) else None
        model = body.get("model") if isinstance(body, dict) else None
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ApiError(
            "integration_request_failed",
            "AITUNNEL вернул ответ в неизвестном формате.",
            status.HTTP_502_BAD_GATEWAY,
        ) from exc
    if not isinstance(answer, str) or not answer.strip():
        raise ApiError(
            "integration_request_failed",
            "AITUNNEL не вернул ответ. Попробуйте ещё раз.",
            status.HTTP_502_BAD_GATEWAY,
        )
    safe_answer = redact_provider_secrets(
        answer.replace(api_key, "[CREDENTIAL REDACTED]")
    )
    safe_model = redact_provider_secrets(
        str(model or "aitunnel:auto").replace(api_key, "[CREDENTIAL REDACTED]")
    )
    return RuntimeAIPublic(answer=safe_answer.strip()[:16_000], model=safe_model[:200])


@router.post("/{project_id}/ai", response_model=RuntimeAIPublic)
async def request_runtime_ai(
    project_id: UUID,
    payload: RuntimeAIRequest,
    session: SessionDep,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")],
) -> RuntimeAIPublic:
    """Run real AI inference without exposing platform or owner provider keys."""

    context = await _runtime_context(session, project_id, x_max_init_data)
    project = await session.get(Project, project_id)
    if project is None:
        raise ApiError("not_found", "Приложение не найдено", status.HTTP_404_NOT_FOUND)

    connections = await _connections(session, project_id)
    aitunnel = connections.get("aitunnel")
    if aitunnel is not None:
        await _enforce_runtime_ai_limits(
            project_id,
            context.max_user_id,
            fail_closed=True,
        )
    else:
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
    if aitunnel is not None:
        return await _request_aitunnel_ai(
            session,
            aitunnel,
            system_prompt=system_prompt,
            user_message=user_message,
        )
    try:
        answer = await llm_client.complete_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            PRIMARY_LLM_MODEL,
            user_id=str(project.owner_id),
            project_id=str(project.id),
            max_tokens=1_600,
            temperature=0.35,
            stage="runtime_ai",
        )
    except llm_client.LLMError as exc:
        raise ApiError(
            "integration_provider_unavailable",
            "ИИ временно недоступен. Попробуйте ещё раз.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if not answer.strip():
        raise ApiError(
            "integration_provider_unavailable",
            "ИИ не вернул ответ. Попробуйте ещё раз.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return RuntimeAIPublic(answer=answer.strip(), model=PRIMARY_LLM_MODEL)


@router.post("/{project_id}/payments", response_model=RuntimePaymentPublic)
async def create_runtime_payment(
    project_id: UUID,
    payload: RuntimePaymentRequest,
    session: SessionDep,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")],
) -> RuntimePaymentPublic:
    context = await _runtime_context(session, project_id, x_max_init_data)
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
        "Idempotence-Key": payload.idempotency_key,
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
    result = response.json()
    confirmation = result.get("confirmation") or {}
    return RuntimePaymentPublic(
        id=str(result.get("id") or ""),
        status=str(result.get("status") or "pending"),
        confirmation_url=confirmation.get("confirmation_url"),
    )


@router.post("/{project_id}/payments/status", response_model=RuntimePaymentPublic)
async def get_runtime_payment_status(
    project_id: UUID,
    payload: RuntimePaymentStatusRequest,
    session: SessionDep,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")],
) -> RuntimePaymentPublic:
    await _runtime_context(session, project_id, x_max_init_data)
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
    result = response.json()
    confirmation = result.get("confirmation") or {}
    return RuntimePaymentPublic(
        id=str(result.get("id") or payload.payment_id),
        status=str(result.get("status") or "pending"),
        confirmation_url=confirmation.get("confirmation_url"),
    )


@router.post("/{project_id}/leads", response_model=RuntimeLeadPublic)
async def create_runtime_lead(
    project_id: UUID,
    payload: RuntimeLeadRequest,
    session: SessionDep,
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")],
) -> RuntimeLeadPublic:
    context = await _runtime_context(session, project_id, x_max_init_data)
    connections = await _connections(session, project_id)
    connection = connections.get("bitrix24") or connections.get("amocrm")
    if connection is None:
        raise ApiError(
            "crm_integration_required",
            "Подключите Битрикс24 или amoCRM к этому приложению",
            status.HTTP_409_CONFLICT,
        )
    credentials = await _secrets(session, connection)
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
                result = response.json().get("result")
                return RuntimeLeadPublic(provider="bitrix24", id=str(result or ""))

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
            return RuntimeLeadPublic(provider="amocrm", id=str(lead.get("id") or ""))
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
    x_max_init_data: Annotated[str, Header(alias="X-MAX-Init-Data")],
) -> RuntimeCatalogPublic:
    await _runtime_context(session, project_id, x_max_init_data)
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
                rows = response.json().get("rows") or []
                items = [
                    RuntimeCatalogItem(
                        id=str(row.get("id") or ""),
                        name=str(row.get("name") or "Товар"),
                        description=str(row.get("description") or ""),
                        price=(
                            Decimal(str((row.get("salePrices") or [{}])[0].get("value", 0)))
                            / Decimal(100)
                            if row.get("salePrices")
                            else None
                        ),
                        available=float(row.get("quantity") or 0) > 0,
                    )
                    for row in rows
                    if isinstance(row, dict) and row.get("id")
                ]
                return RuntimeCatalogPublic(provider="moysklad", items=items)

            token_response = await client.post(
                "https://api-ru.iiko.services/api/1/access_token",
                json={"apiLogin": credentials.get("api_login", "")},
            )
            if token_response.status_code >= 300:
                raise _provider_failure("iikoCloud", token_response)
            access_token = str(token_response.json().get("token") or "")
            auth_headers = {"Authorization": f"Bearer {access_token}"}
            organizations_response = await client.post(
                "https://api-ru.iiko.services/api/1/organizations",
                headers=auth_headers,
                json={},
            )
            if organizations_response.status_code >= 300:
                raise _provider_failure("iikoCloud", organizations_response)
            organizations = organizations_response.json().get("organizations") or []
            organization_id = str(
                (organizations[0] if organizations else {}).get("id") or ""
            )
            if not organization_id:
                raise ApiError(
                    "integration_configuration_invalid",
                    "В iikoCloud не найдена доступная организация",
                    status.HTTP_409_CONFLICT,
                )
            menu_response = await client.post(
                "https://api-ru.iiko.services/api/1/nomenclature",
                headers=auth_headers,
                json={"organizationId": organization_id, "startRevision": 0},
            )
            if menu_response.status_code >= 300:
                raise _provider_failure("iikoCloud", menu_response)
            products = menu_response.json().get("products") or []
            items = []
            for product in products[:200]:
                if not isinstance(product, dict) or not product.get("id"):
                    continue
                size_prices = product.get("sizePrices") or []
                first_price = (
                    (size_prices[0].get("price") or {}).get("currentPrice")
                    if size_prices and isinstance(size_prices[0], dict)
                    else None
                )
                image_links = product.get("imageLinks") or []
                items.append(
                    RuntimeCatalogItem(
                        id=str(product["id"]),
                        name=str(product.get("name") or "Позиция"),
                        description=str(product.get("description") or ""),
                        price=Decimal(str(first_price)) if first_price is not None else None,
                        available=not bool(product.get("isDeleted")),
                        image_url=str(image_links[0]) if image_links else None,
                    )
                )
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
