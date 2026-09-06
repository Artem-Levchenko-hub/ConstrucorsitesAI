"""Load encrypted business credentials and serialize rotating OAuth refreshes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from cryptography.fernet import InvalidToken
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core.crypto import decrypt_strong, encrypt_strong
from omnia_api.core.errors import ApiError
from omnia_api.models.app_integration import BusinessIntegration
from omnia_api.services import integration_oauth, integration_providers


async def load_credentials(
    session: AsyncSession, connection: BusinessIntegration
) -> dict[str, str]:
    """Return current credentials, committing any rotation before releasing its lock.

    Connections are business-shared, so process-local locks cannot protect a
    rotating refresh token. Refresh the identity map *after* acquiring the row
    lock: another request may already have committed a replacement token.
    """
    current = (
        await session.execute(
            select(BusinessIntegration)
            .where(BusinessIntegration.id == connection.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if current is None:
        raise ApiError(
            "integration_not_found",
            "Подключение сервиса больше не существует",
            status.HTTP_404_NOT_FOUND,
        )
    try:
        value = json.loads(decrypt_strong(current.credentials_enc))
        if not isinstance(value, dict):
            raise ValueError("invalid encrypted credentials")
    except (InvalidToken, ValueError, TypeError) as exc:
        current.status = "error"
        current.last_error = "Подключение сервиса повреждено. Переподключите его в Integration Hub."
        current.last_checked_at = datetime.now(UTC)
        await session.commit()
        raise ApiError(
            "integration_credentials_corrupted",
            current.last_error,
            status.HTTP_409_CONFLICT,
        ) from exc
    result = {str(key): str(item) for key, item in value.items()}
    if (
        current.auth_mode == "oauth"
        and current.token_expires_at is not None
        and current.token_expires_at <= datetime.now(UTC) + timedelta(minutes=2)
    ):
        try:
            result, expires_at = await integration_oauth.refresh_access_token(
                current.provider, dict(current.public_config or {}), result
            )
        except integration_providers.IntegrationCredentialsInvalid as exc:
            message = "Срок авторизации истёк. Переподключите сервис в Integration Hub."
            current.status = "error"
            current.last_error = message
            current.last_checked_at = datetime.now(UTC)
            await session.commit()
            raise ApiError(
                "integration_credentials_invalid", message, status.HTTP_409_CONFLICT
            ) from exc
        except integration_providers.IntegrationProviderError as exc:
            # An outage or unrecognised response does not prove credentials invalid.
            await session.rollback()
            raise ApiError(
                "integration_provider_unavailable",
                "Сервис авторизации временно недоступен. Повторите запрос позже.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        current.credentials_enc = encrypt_strong(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        current.token_expires_at = expires_at
        current.last_checked_at = datetime.now(UTC)
    # Release the lock before the caller makes its provider operation, including
    # when another request has already refreshed the token we just read.
    await session.commit()
    return result
