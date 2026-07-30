"""Owner-scoped Integration Hub catalog and encrypted provider connections."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Response, status
from sqlalchemy import select

from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.app_integration import AppIntegration
from omnia_api.models.project import Project
from omnia_api.schemas.app_integration import (
    AppIntegrationPublic,
    IntegrationCatalogPublic,
    IntegrationConnectRequest,
    IntegrationFieldPublic,
    IntegrationProviderPublic,
)
from omnia_api.services import integration_providers

router = APIRouter(prefix="/api/projects", tags=["app-integrations"])


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


async def _connection(
    session: SessionDep, project_id: UUID, provider: str
) -> AppIntegration | None:
    return (
        await session.execute(
            select(AppIntegration).where(
                AppIntegration.project_id == project_id,
                AppIntegration.provider == provider,
            )
        )
    ).scalar_one_or_none()


def _provider_public(
    provider: integration_providers.IntegrationProvider,
) -> IntegrationProviderPublic:
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
        available=provider.available,
        recommended=provider.recommended,
        requirement=provider.requirement,
        docs_url=provider.docs_url,
    )


def _connection_public(connection: AppIntegration) -> AppIntegrationPublic:
    provider = integration_providers.get_provider(connection.provider)
    configured_fields = [field.key for field in provider.fields if field.secret]
    return AppIntegrationPublic(
        id=str(connection.id),
        provider=connection.provider,
        status=connection.status,
        account_label=connection.account_label,
        public_config=dict(connection.public_config or {}),
        capabilities=list(connection.capabilities or []),
        configured_fields=configured_fields,
        last_error=connection.last_error,
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


@router.get(
    "/{project_id}/app-integrations",
    response_model=IntegrationCatalogPublic,
)
async def get_integration_catalog(
    project_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> IntegrationCatalogPublic:
    await _owned_max_project(session, project_id, current_user.id)
    connections = (
        await session.execute(
            select(AppIntegration)
            .where(AppIntegration.project_id == project_id)
            .order_by(AppIntegration.created_at)
        )
    ).scalars()
    return IntegrationCatalogPublic(
        providers=[
            _provider_public(provider) for provider in integration_providers.PROVIDERS
        ],
        connections=[_connection_public(connection) for connection in connections],
    )


@router.put(
    "/{project_id}/app-integrations/{provider_key}",
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
    try:
        provider = integration_providers.get_provider(provider_key)
        if not provider.available:
            raise integration_providers.IntegrationProviderError(
                provider.requirement or "Интеграция пока недоступна"
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
    connection = await _connection(session, project_id, provider_key)
    if connection is None:
        connection = AppIntegration(
            project_id=project_id,
            owner_id=current_user.id,
            provider=provider_key,
            credentials_enc=encrypt_strong(
                json.dumps(secret_values, ensure_ascii=False, sort_keys=True)
            ),
        )
        session.add(connection)
    else:
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
    await session.commit()
    await session.refresh(connection)
    return _connection_public(connection)


@router.post(
    "/{project_id}/app-integrations/{provider_key}/verify",
    response_model=AppIntegrationPublic,
)
async def verify_integration(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> AppIntegrationPublic:
    await _owned_max_project(session, project_id, current_user.id)
    connection = await _connection(session, project_id, provider_key)
    if connection is None:
        raise ApiError(
            "integration_not_found",
            "Сначала подключите интеграцию",
            status.HTTP_404_NOT_FOUND,
        )
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
        await session.commit()
        raise _map_provider_error(exc) from exc
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        connection.status = "error"
        connection.last_error = "Сохранённые реквизиты повреждены. Подключите сервис заново."
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
    await session.commit()
    await session.refresh(connection)
    return _connection_public(connection)


@router.delete(
    "/{project_id}/app-integrations/{provider_key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disconnect_integration(
    project_id: UUID,
    provider_key: str,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> Response:
    await _owned_max_project(session, project_id, current_user.id)
    connection = await _connection(session, project_id, provider_key)
    if connection is not None:
        await session.delete(connection)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
