"""Owner-scoped MAX bot connection and webhook activation for Mini Apps."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Response, status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from yleum_api.core.crypto import decrypt_strong, encrypt_strong
from yleum_api.core.deps import CurrentUserDep, SessionDep
from yleum_api.core.errors import ApiError
from yleum_api.models.max_integration import MaxIntegration
from yleum_api.models.project import Project
from yleum_api.schemas.max_integration import MaxConnectRequest, MaxIntegrationPublic
from yleum_api.services import max_client, orchestrator_client, project_cell_runtime

router = APIRouter(prefix="/api/projects", tags=["max-integrations"])


async def _owned_project(
    session: SessionDep, project_id: UUID, owner_id: UUID, *, lock: bool = False,
) -> Project:
    try:
        project = (
            await session.get(
                Project, project_id, with_for_update={"nowait": True}, populate_existing=True,
            )
            if lock else await session.get(Project, project_id)
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":
            raise
        await session.rollback()
        raise _bot_binding_busy() from None
    if project is None or project.owner_id != owner_id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    return project


async def _integration(session: SessionDep, project_id: UUID) -> MaxIntegration | None:
    return (
        await session.execute(select(MaxIntegration).where(MaxIntegration.project_id == project_id))
    ).scalar_one_or_none()


def _bot_binding_busy() -> ApiError:
    return ApiError(
        "max_bot_binding_busy",
        "Подключение MAX для этого приложения уже изменяется. Повторите попытку позже.",
        status.HTTP_409_CONFLICT,
    )


def _validated_bot_id(bot: max_client.MaxBot) -> str:
    bot_id = (bot.id or "").strip()
    if not bot_id:
        raise ApiError(
            "max_api_unavailable", "MAX API не вернул идентификатор бота. Повторите проверку.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return bot_id


def _require_max_project(project: Project) -> None:
    if project.template != "max_miniapp":
        raise ApiError(
            "max_project_required",
            "Интеграция MAX доступна только для проекта MAX Mini App",
            status.HTTP_409_CONFLICT,
        )


def _deep_link(integration: MaxIntegration) -> str | None:
    if not integration.bot_username:
        return None
    return f"https://max.ru/{integration.bot_username}"


def _public(project: Project, integration: MaxIntegration | None) -> MaxIntegrationPublic:
    if integration is None:
        return MaxIntegrationPublic(
            eligible=project.template == "max_miniapp",
            connected=False,
        )
    return MaxIntegrationPublic(
        eligible=project.template == "max_miniapp",
        connected=True,
        status=integration.status,
        bot_id=integration.bot_id,
        bot_name=integration.bot_name,
        bot_username=integration.bot_username,
        app_url=integration.app_url,
        webhook_url=integration.webhook_url,
        deep_link=_deep_link(integration),
        last_error=integration.last_error,
        verified_at=integration.verified_at,
        published_at=integration.published_at,
    )


def _map_client_error(exc: max_client.MaxClientError) -> ApiError:
    if isinstance(exc, max_client.MaxTokenInvalid):
        return ApiError("max_token_invalid", str(exc), status.HTTP_401_UNAUTHORIZED)
    if isinstance(exc, max_client.MaxTlsConfigurationError):
        return ApiError("max_api_tls_untrusted", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)
    if isinstance(exc, max_client.MaxApiUnavailable):
        return ApiError("max_api_unavailable", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)
    return ApiError("max_webhook_failed", str(exc), status.HTTP_502_BAD_GATEWAY)


async def _lock_public_cell_auth(session: SessionDep, project: Project) -> bool:
    selection = await project_cell_runtime.resolve_project_cell_public_selection(session, project)
    if selection.selected:
        await project_cell_runtime._try_preview_project_lock(session, project.id)
    return selection.selected


async def _sync_public_cell_auth(
    session: SessionDep, project: Project, integration: MaxIntegration | None,
) -> None:
    if await _lock_public_cell_auth(session, project):
        from yleum_api.services.cell_publication import update_public_credentials

        if integration is not None:
            # connect commits before the external sync. Another request can
            # revoke/rotate during that gap: never restore its stale ORM token.
            integration = await session.scalar(
                select(MaxIntegration).where(MaxIntegration.project_id == project.id)
                .execution_options(populate_existing=True)
            )
        await update_public_credentials(project.id, project.owner_id, integration)


@router.get("/{project_id}/integrations/max", response_model=MaxIntegrationPublic)
async def get_max_integration(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxIntegrationPublic:
    project = await _owned_project(session, project_id, current_user.id)
    return _public(project, await _integration(session, project_id))


@router.post("/{project_id}/integrations/max/connect", response_model=MaxIntegrationPublic)
async def connect_max(
    project_id: UUID,
    payload: MaxConnectRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> MaxIntegrationPublic:
    project = await _owned_project(session, project_id, current_user.id)
    _require_max_project(project)
    await _lock_public_cell_auth(session, project)
    token = payload.token.strip()
    try:
        bot = await max_client.get_me(token)
    except max_client.MaxClientError as exc:
        raise _map_client_error(exc) from exc
    bot_id = _validated_bot_id(bot)

    # Preserve the existing public lifecycle's advisory-before-row lock order.
    project = await _owned_project(session, project_id, current_user.id, lock=True)
    integration = await _integration(session, project_id)
    existing = integration is not None
    now = datetime.now(UTC)
    if integration is None:
        integration = MaxIntegration(
            project_id=project.id,
            owner_id=current_user.id,
            bot_token_enc=encrypt_strong(token),
            webhook_secret_enc=encrypt_strong(secrets.token_urlsafe(32)),
        )
    if existing:
        old_token = decrypt_strong(integration.bot_token_enc)
        token_changed = not secrets.compare_digest(old_token, token)
        if token_changed:
            if integration.webhook_url:
                same_bot = integration.bot_id == bot_id
                try:
                    # A rotated token belongs to the same bot; its old token may
                    # already be revoked. Never touch any other project's URL.
                    await max_client.unsubscribe(
                        token if same_bot else old_token, integration.webhook_url,
                    )
                except max_client.MaxTokenInvalid as exc:
                    if same_bot:
                        await session.rollback()
                        raise _map_client_error(exc) from exc
                    # Replacing a different bot remains possible after its old
                    # credential was revoked. Network/other errors still abort.
                except max_client.MaxClientError as exc:
                    await session.rollback()
                    raise _map_client_error(exc) from exc
            integration.bot_token_enc = encrypt_strong(token)
            integration.webhook_secret_enc = encrypt_strong(secrets.token_urlsafe(32))
            integration.app_url = None
            integration.webhook_url = None
            integration.published_at = None

    integration.bot_id = bot_id
    integration.bot_name = bot.name
    integration.bot_username = bot.username
    integration.status = (
        "active" if integration.status == "active" and integration.webhook_url else "verified"
    )
    integration.last_error = None
    integration.verified_at = now
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    await _sync_public_cell_auth(session, project, integration)
    return _public(project, integration)


@router.post("/{project_id}/integrations/max/verify", response_model=MaxIntegrationPublic)
async def verify_max(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxIntegrationPublic:
    project = await _owned_project(session, project_id, current_user.id)
    _require_max_project(project)
    await _lock_public_cell_auth(session, project)
    project = await _owned_project(session, project_id, current_user.id, lock=True)
    integration = await _integration(session, project_id)
    if integration is None:
        raise ApiError(
            "max_integration_not_found",
            "Сначала подключите MAX-бота",
            status.HTTP_404_NOT_FOUND,
        )
    try:
        bot = await max_client.get_me(decrypt_strong(integration.bot_token_enc))
    except max_client.MaxClientError as exc:
        integration.status = "error"
        integration.last_error = str(exc)[:500]
        await session.commit()
        raise _map_client_error(exc) from exc
    integration.bot_id = _validated_bot_id(bot)
    integration.bot_name = bot.name
    integration.bot_username = bot.username
    integration.status = "active" if integration.webhook_url else "verified"
    integration.last_error = None
    integration.verified_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(integration)
    return _public(project, integration)


@router.post("/{project_id}/integrations/max/activate", response_model=MaxIntegrationPublic)
async def activate_max(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> MaxIntegrationPublic:
    project = await _owned_project(session, project_id, current_user.id)
    _require_max_project(project)
    await _lock_public_cell_auth(session, project)
    project = await _owned_project(session, project_id, current_user.id, lock=True)
    integration = await _integration(session, project_id)
    if integration is None:
        raise ApiError(
            "max_integration_not_found",
            "Сначала подключите MAX-бота",
            status.HTTP_404_NOT_FOUND,
        )
    deployed = await orchestrator_client.get_deploy(project_id)
    app_url = str(deployed.get("prod_url") or "").rstrip("/")
    parsed = urlparse(app_url)
    if deployed.get("phase") != "done" or parsed.scheme != "https" or not parsed.netloc:
        raise ApiError(
            "max_deploy_required",
            "Сначала опубликуйте проект на стабильном HTTPS-адресе",
            status.HTTP_409_CONFLICT,
        )
    webhook_url = f"{app_url}/api/max/webhook"
    # A public Cell owns a different trusted core from the private preview.
    # Confirm its current credentials before enabling the real MAX webhook.
    await _sync_public_cell_auth(session, project, integration)
    token = decrypt_strong(integration.bot_token_enc)
    webhook_secret = decrypt_strong(integration.webhook_secret_enc)
    try:
        if integration.webhook_url and integration.webhook_url != webhook_url:
            await max_client.unsubscribe(token, integration.webhook_url)
            integration.app_url = None
            integration.webhook_url = None
        if not await max_client.has_subscription(token, webhook_url):
            await max_client.subscribe(token, webhook_url, webhook_secret)
    except max_client.MaxClientError as exc:
        integration.status = "error"
        integration.last_error = str(exc)[:500]
        await session.commit()
        raise _map_client_error(exc) from exc
    integration.app_url = app_url
    integration.webhook_url = webhook_url
    integration.status = "active"
    integration.last_error = None
    integration.published_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(integration)
    return _public(project, integration)


@router.delete(
    "/{project_id}/integrations/max",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disconnect_max(
    project_id: UUID, session: SessionDep, current_user: CurrentUserDep
) -> Response:
    project = await _owned_project(session, project_id, current_user.id)
    await _lock_public_cell_auth(session, project)
    project = await _owned_project(session, project_id, current_user.id, lock=True)
    integration = await _integration(session, project_id)
    if integration is None:
        await _sync_public_cell_auth(session, project, None)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Fail closed if public session invalidation cannot be confirmed. The
    # integration remains persisted so the same disconnect can be retried.
    await _sync_public_cell_auth(session, project, None)
    if integration.webhook_url:
        try:
            await max_client.unsubscribe(
                decrypt_strong(integration.bot_token_enc),
                integration.webhook_url,
            )
        except max_client.MaxClientError as exc:
            raise _map_client_error(exc) from exc
    await session.delete(integration)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
