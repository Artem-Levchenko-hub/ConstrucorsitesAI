"""Business Integration Hub, OAuth and per-project capability bindings."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.app_integration import (
    BusinessIntegration,
    IntegrationOAuthState,
    ProjectIntegrationBinding,
)
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.schemas.app_integration import (
    AppIntegrationPublic,
    IntegrationCatalogPublic,
    IntegrationConnectRequest,
    IntegrationFieldPublic,
    IntegrationOAuthStartPublic,
    IntegrationPackApplyPublic,
    IntegrationPackPublic,
    IntegrationProviderPublic,
)
from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.services import integration_oauth, integration_providers
from omnia_api.services.max_access import require_max_business

router = APIRouter(tags=["app-integrations"])


async def _owned_max_project(
    session: SessionDep, project_id: UUID, owner_id: UUID
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.template != "max_miniapp":
        raise ApiError(
            "max_project_required",
            "Каталог интеграций пока доступен только для MAX Mini App",
            status.HTTP_409_CONFLICT,
        )
    return project


async def _business_connection(
    session: SessionDep, business_id: UUID, provider: str
) -> BusinessIntegration | None:
    return (
        await session.execute(
            select(BusinessIntegration).where(
                BusinessIntegration.business_id == business_id,
                BusinessIntegration.provider == provider,
            )
        )
    ).scalar_one_or_none()


async def _binding(
    session: SessionDep, project_id: UUID, provider: str
) -> ProjectIntegrationBinding | None:
    return (
        await session.execute(
            select(ProjectIntegrationBinding).where(
                ProjectIntegrationBinding.project_id == project_id,
                ProjectIntegrationBinding.provider == provider,
            )
        )
    ).scalar_one_or_none()


async def _bind(
    session: SessionDep,
    project_id: UUID,
    connection: BusinessIntegration,
    *,
    config: dict[str, object] | None = None,
) -> ProjectIntegrationBinding:
    binding = await _binding(session, project_id, connection.provider)
    if binding is None:
        binding = ProjectIntegrationBinding(
            project_id=project_id,
            integration_id=connection.id,
            provider=connection.provider,
        )
        session.add(binding)
    binding.integration_id = connection.id
    binding.enabled = True
    binding.status = "ready"
    binding.last_error = None
    defaults: dict[str, dict[str, object]] = {
        "yookassa": {
            "currency": "RUB",
            "capture": True,
            "operations": ["create_payment", "payment_status"],
        },
        "bitrix24": {
            "entity": "lead",
            "field_map": {
                "name": "NAME",
                "phone": "PHONE",
                "email": "EMAIL",
                "comment": "COMMENTS",
            },
        },
        "amocrm": {
            "entity": "lead",
            "field_map": {
                "name": "name",
                "phone": "PHONE",
                "email": "EMAIL",
            },
        },
        "yandex_metrica": {
            "counter_id": connection.public_config.get("counter_id"),
            "auto_pageviews": True,
        },
    }
    if config is not None:
        binding.config = config
    elif not binding.config:
        binding.config = defaults.get(connection.provider, {})
    binding.last_tested_at = connection.last_checked_at
    return binding


def _oauth_available(provider_key: str) -> bool:
    return integration_oauth.credentials(provider_key) is not None


def _provider_public(
    provider: integration_providers.IntegrationProvider,
) -> IntegrationProviderPublic:
    oauth_available = provider.oauth_supported and _oauth_available(provider.key)
    manual_available = provider.available and bool(provider.fields)
    return IntegrationProviderPublic(
        key=provider.key,
        name=provider.name,
        category=provider.category,
        description=provider.description,
        capabilities=list(provider.capabilities),
        fields=[
            IntegrationFieldPublic(
                key=field.key,
                label=field.label,
                placeholder=field.placeholder,
                help=field.help,
                secret=field.secret,
                required=field.required,
            )
            for field in provider.fields
        ],
        available=manual_available or oauth_available,
        recommended=provider.recommended,
        requirement=provider.requirement,
        docs_url=provider.docs_url,
        oauth_supported=provider.oauth_supported,
        oauth_available=oauth_available,
        connection_mode=(
            "oauth"
            if oauth_available
            else "credentials"
            if manual_available
            else "partner"
        ),
    )


def _connection_public(
    connection: BusinessIntegration,
    binding: ProjectIntegrationBinding | None,
) -> AppIntegrationPublic:
    provider = integration_providers.get_provider(connection.provider)
    configured_fields = (
        ["oauth"]
        if connection.auth_mode == "oauth"
        else [field.key for field in provider.fields if field.secret]
    )
    return AppIntegrationPublic(
        id=str(connection.id),
        provider=connection.provider,
        status=connection.status,
        auth_mode=connection.auth_mode,
        bound_to_project=bool(binding and binding.enabled),
        binding_status=binding.status if binding else None,
        binding_config=dict(binding.config or {}) if binding else {},
        account_label=connection.account_label,
        public_config=dict(connection.public_config or {}),
        capabilities=list(connection.capabilities or []),
        configured_fields=configured_fields,
        last_error=(
            binding.last_error
            if binding and binding.last_error
            else connection.last_error
        ),
        verified_at=connection.verified_at,
        last_checked_at=connection.last_checked_at,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _map_provider_error(exc: integration_providers.IntegrationProviderError) -> ApiError:
    if isinstance(exc, integration_providers.IntegrationCredentialsInvalid):
        return ApiError(
            "integration_credentials_invalid",
            str(exc),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if isinstance(exc, integration_providers.IntegrationProviderUnavailable):
        return ApiError(
            "integration_provider_unavailable",
            str(exc),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return ApiError(
        "integration_connection_failed",
        str(exc),
        status.HTTP_400_BAD_REQUEST,
    )


async def _recommended_pack(
    session: SessionDep,
    project: Project,
    connections: list[BusinessIntegration],
    bindings: dict[str, ProjectIntegrationBinding],
) -> IntegrationPackPublic:
    record = await session.get(MaxProjectConfig, project.id)
    config = (
        MaxProjectConfigPayload.model_validate(record.config)
        if record
        else MaxProjectConfigPayload(
            app_name=project.name,
            app_type="custom",
            summary="Мини-приложение для пользователей MAX",
        )
    )
    corpus = " ".join([config.summary, *config.features]).lower()
    if any(word in corpus for word in ("ресторан", "кафе", "кофе", "еда", "меню")):
        key, title, description, providers = (
            "restaurant",
            "Ресторан под ключ",
            "Оплата, меню и заказы, CRM и аналитика.",
            ["yookassa", "iiko", "bitrix24", "yandex_metrica"],
        )
    elif config.app_type == "booking":
        key, title, description, providers = (
            "booking",
            "Онлайн-запись под ключ",
            "Оплата, запись клиентов, CRM и аналитика.",
            ["yookassa", "yclients", "bitrix24", "yandex_metrica"],
        )
    elif config.app_type == "catalog" or any(
        word in corpus for word in ("магазин", "товар", "каталог", "доставка")
    ):
        key, title, description, providers = (
            "commerce",
            "Магазин под ключ",
            "Оплата, товары и остатки, доставка, CRM и аналитика.",
            ["yookassa", "moysklad", "cdek", "bitrix24", "yandex_metrica"],
        )
    else:
        key, title, description, providers = (
            "growth",
            "Запуск и рост",
            "Оплата, заявки и измерение результата.",
            ["yookassa", "bitrix24", "yandex_metrica"],
        )
    connected = {item.provider: item for item in connections if item.status == "active"}
    return IntegrationPackPublic(
        key=key,
        title=title,
        description=description,
        provider_keys=providers,
        bound_count=sum(
            1
            for provider in providers
            if provider in bindings and bindings[provider].enabled
        ),
        reusable_count=sum(
            1
            for provider in providers
            if provider in connected
            and not (provider in bindings and bindings[provider].enabled)
        ),
    )


@router.get(
    "/api/projects/{project_id}/app-integrations",
    response_model=IntegrationCatalogPublic,
)
async def get_integration_catalog(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> IntegrationCatalogPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    connections = list(
        (
            await session.execute(
                select(BusinessIntegration)
                .where(BusinessIntegration.business_id == business.id)
                .order_by(BusinessIntegration.created_at)
            )
        ).scalars()
    )
    bindings = {
        item.provider: item
        for item in (
            await session.execute(
                select(ProjectIntegrationBinding).where(
                    ProjectIntegrationBinding.project_id == project_id
                )
            )
        ).scalars()
    }
    return IntegrationCatalogPublic(
        providers=[
            _provider_public(provider) for provider in integration_providers.PROVIDERS
        ],
        connections=[
            _connection_public(connection, bindings.get(connection.provider))
            for connection in connections
        ],
        recommended_pack=await _recommended_pack(
            session, project, connections, bindings
        ),
    )


@router.put(
    "/api/projects/{project_id}/app-integrations/{provider_key}",
    response_model=AppIntegrationPublic,
)
async def connect_integration(
    project_id: UUID,
    provider_key: str,
    payload: IntegrationConnectRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> AppIntegrationPublic:
    await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    try:
        provider = integration_providers.get_provider(provider_key)
        if not provider.available or not provider.fields:
            raise integration_providers.IntegrationProviderError(
                provider.requirement or "Интеграция доступна только через OAuth"
            )
        public_values, secret_values = integration_providers.split_values(
            provider, payload.values
        )
        account_label = await integration_providers.verify_provider(
            provider_key, public_values, secret_values
        )
    except integration_providers.IntegrationProviderError as exc:
        raise _map_provider_error(exc) from exc

    now = datetime.now(UTC)
    connection = await _business_connection(session, business.id, provider_key)
    if connection is None:
        connection = BusinessIntegration(
            business_id=business.id,
            created_by_user_id=current_user.id,
            provider=provider_key,
            credentials_enc="",
        )
        session.add(connection)
        await session.flush()
    connection.auth_mode = "credentials"
    connection.credentials_enc = encrypt_strong(
        json.dumps(secret_values, ensure_ascii=False, sort_keys=True)
    )
    connection.public_config = public_values
    connection.capabilities = list(provider.capabilities)
    connection.account_label = account_label[:240]
    connection.status = "active"
    connection.last_error = None
    connection.verified_at = now
    connection.last_checked_at = now
    connection.token_expires_at = None
    binding = await _bind(session, project_id, connection)
    await session.commit()
    await session.refresh(connection)
    await session.refresh(binding)
    return _connection_public(connection, binding)


@router.post(
    "/api/projects/{project_id}/app-integrations/{provider_key}/bind",
    response_model=AppIntegrationPublic,
)
async def bind_existing_integration(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> AppIntegrationPublic:
    await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    connection = await _business_connection(session, business.id, provider_key)
    if connection is None:
        raise ApiError(
            "integration_not_found",
            "Сначала подключите сервис к бизнесу",
            status.HTTP_404_NOT_FOUND,
        )
    binding = await _bind(session, project_id, connection)
    await session.commit()
    await session.refresh(binding)
    return _connection_public(connection, binding)


@router.post(
    "/api/projects/{project_id}/app-integrations/pack/apply",
    response_model=IntegrationPackApplyPublic,
)
async def apply_recommended_pack(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> IntegrationPackApplyPublic:
    project = await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    connections = list(
        (
            await session.execute(
                select(BusinessIntegration).where(
                    BusinessIntegration.business_id == business.id
                )
            )
        ).scalars()
    )
    current_bindings = {
        item.provider: item
        for item in (
            await session.execute(
                select(ProjectIntegrationBinding).where(
                    ProjectIntegrationBinding.project_id == project_id
                )
            )
        ).scalars()
    }
    pack = await _recommended_pack(session, project, connections, current_bindings)
    by_provider = {item.provider: item for item in connections if item.status == "active"}
    bound: list[str] = []
    remaining: list[str] = []
    for provider_key in pack.provider_keys:
        connection = by_provider.get(provider_key)
        if connection is None:
            remaining.append(provider_key)
            continue
        await _bind(session, project_id, connection)
        bound.append(provider_key)
    await session.commit()
    return IntegrationPackApplyPublic(
        bound_provider_keys=bound,
        remaining_provider_keys=remaining,
    )


@router.post(
    "/api/projects/{project_id}/app-integrations/{provider_key}/verify",
    response_model=AppIntegrationPublic,
)
async def verify_integration(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> AppIntegrationPublic:
    await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    connection = await _business_connection(session, business.id, provider_key)
    if connection is None:
        raise ApiError(
            "integration_not_found",
            "Сначала подключите интеграцию",
            status.HTTP_404_NOT_FOUND,
        )
    binding = await _binding(session, project_id, provider_key)
    try:
        secret_values = json.loads(decrypt_strong(connection.credentials_enc))
        if not isinstance(secret_values, dict):
            raise ValueError("invalid encrypted credentials")
        account_label = await integration_providers.verify_provider(
            provider_key,
            dict(connection.public_config or {}),
            {str(key): str(value) for key, value in secret_values.items()},
        )
    except integration_providers.IntegrationProviderError as exc:
        connection.status = "error"
        connection.last_error = str(exc)[:500]
        connection.last_checked_at = datetime.now(UTC)
        if binding:
            binding.status = "error"
            binding.last_error = connection.last_error
        await session.commit()
        raise _map_provider_error(exc) from exc
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        connection.status = "error"
        connection.last_error = (
            "Сохранённые реквизиты повреждены. Подключите сервис заново."
        )
        connection.last_checked_at = datetime.now(UTC)
        await session.commit()
        raise ApiError(
            "integration_credentials_corrupted",
            connection.last_error,
            status.HTTP_409_CONFLICT,
        ) from exc

    now = datetime.now(UTC)
    connection.status = "active"
    connection.account_label = account_label[:240]
    connection.last_error = None
    connection.verified_at = now
    connection.last_checked_at = now
    if binding:
        binding.status = "ready"
        binding.last_error = None
        binding.last_tested_at = now
    await session.commit()
    await session.refresh(connection)
    return _connection_public(connection, binding)


@router.delete(
    "/api/projects/{project_id}/app-integrations/{provider_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_integration(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Response:
    await _owned_max_project(session, project_id, current_user.id)
    await require_max_business(session, current_user)
    binding = await _binding(session, project_id, provider_key)
    if binding is not None:
        await session.delete(binding)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/api/projects/{project_id}/app-integrations/{provider_key}/business",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_business_integration(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Response:
    await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    connection = await _business_connection(session, business.id, provider_key)
    if connection is not None:
        await session.delete(connection)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/projects/{project_id}/app-integrations/{provider_key}/oauth/start",
    response_model=IntegrationOAuthStartPublic,
)
async def start_integration_oauth(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> IntegrationOAuthStartPublic:
    await _owned_max_project(session, project_id, current_user.id)
    business = await require_max_business(session, current_user)
    provider = integration_providers.get_provider(provider_key)
    if not provider.oauth_supported or not _oauth_available(provider_key):
        raise ApiError(
            "integration_oauth_unavailable",
            "OAuth-подключение этого сервиса пока не настроено",
            status.HTTP_409_CONFLICT,
        )
    raw_state = secrets.token_urlsafe(40)
    state = IntegrationOAuthState(
        state_hash=hashlib.sha256(raw_state.encode()).hexdigest(),
        business_id=business.id,
        user_id=current_user.id,
        project_id=project_id,
        provider=provider_key,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(state)
    await session.commit()
    try:
        url = integration_oauth.authorization_url(provider_key, raw_state)
    except integration_providers.IntegrationProviderError as exc:
        raise _map_provider_error(exc) from exc
    return IntegrationOAuthStartPublic(authorization_url=url)


def _oauth_redirect(project_id: UUID, outcome: str) -> RedirectResponse:
    base = get_settings().web_base_url.rstrip("/")
    return RedirectResponse(
        f"{base}/max/{project_id}/integrations?oauth={outcome}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/api/integrations/oauth/{provider_key}/callback")
async def integration_oauth_callback(
    provider_key: str,
    session: SessionDep,
    state: str = Query(min_length=20, max_length=256),
    code: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=256),
    referer: str | None = Query(default=None, max_length=512),
) -> RedirectResponse:
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    record = (
        await session.execute(
            select(IntegrationOAuthState)
            .where(IntegrationOAuthState.state_hash == state_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        record is None
        or record.provider != provider_key
        or record.used_at is not None
        or record.expires_at <= now
    ):
        raise ApiError(
            "integration_oauth_state_invalid",
            "Ссылка авторизации устарела. Начните подключение ещё раз.",
            status.HTTP_400_BAD_REQUEST,
        )
    record.used_at = now
    if error or not code:
        await session.commit()
        return _oauth_redirect(record.project_id, "cancelled")
    try:
        result = await integration_oauth.exchange_code(
            provider_key, code, referer=referer
        )
    except integration_providers.IntegrationProviderError:
        await session.commit()
        return _oauth_redirect(record.project_id, "error")

    provider = integration_providers.get_provider(provider_key)
    connection = await _business_connection(
        session, record.business_id, provider_key
    )
    if connection is None:
        connection = BusinessIntegration(
            business_id=record.business_id,
            created_by_user_id=record.user_id,
            provider=provider_key,
            credentials_enc="",
        )
        session.add(connection)
        await session.flush()
    connection.auth_mode = "oauth"
    connection.credentials_enc = encrypt_strong(
        json.dumps(result.secret_values, ensure_ascii=False, sort_keys=True)
    )
    connection.public_config = result.public_config
    connection.capabilities = list(provider.capabilities)
    connection.account_label = result.account_label[:240]
    connection.status = "active"
    connection.last_error = None
    connection.verified_at = now
    connection.last_checked_at = now
    connection.token_expires_at = result.expires_at
    await _bind(session, record.project_id, connection)
    await session.commit()
    return _oauth_redirect(record.project_id, "connected")
