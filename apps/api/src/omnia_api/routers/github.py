"""«Push to GitHub» — OAuth-подключение аккаунта + заливка проекта в репозиторий.

Поток: фронт зовёт `/connect` → получает authorize_url → браузер идёт в GitHub →
GitHub редиректит на `/callback?code&state` → меняем code на токен, шифруем и
сохраняем на пользователе → редирект обратно в веб-приложение. Дальше
`/projects/{id}/push` создаёт репозиторий и заливает текущий снапшот проекта.

`state` подписан HMAC(jwt_secret) и несёт user_id + ts (защита от CSRF и
определение пользователя в callback, где наш cookie может не дойти).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import RedirectResponse

from omnia_api.core.config import get_settings
from omnia_api.core.crypto import decrypt_secret, encrypt_secret
from omnia_api.core.deps import CurrentUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.schemas.github import (
    GitHubConnectResponse,
    GitHubStatus,
    PushRequest,
    PushResponse,
)
from omnia_api.services import github_client
from omnia_api.services import repo as repo_svc

router = APIRouter(prefix="/api/github", tags=["github"])

_STATE_TTL_SEC = 600  # подпись state живёт 10 минут


def _sign_state(user_id: UUID) -> str:
    secret = get_settings().jwt_secret.get_secret_value().encode()
    payload = f"{user_id}:{int(time.time())}:{secrets.token_urlsafe(8)}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def _verify_state(state: str) -> UUID:
    try:
        raw = base64.urlsafe_b64decode(state.encode()).decode()
        uid, ts, nonce, sig = raw.split(":")
    except (ValueError, UnicodeDecodeError) as e:
        raise ApiError("github_state_invalid", "Некорректный state", 400) from e
    secret = get_settings().jwt_secret.get_secret_value().encode()
    payload = f"{uid}:{ts}:{nonce}"
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        raise ApiError("github_state_invalid", "Подпись state не совпала", 400)
    if int(time.time()) - int(ts) > _STATE_TTL_SEC:
        raise ApiError("github_state_expired", "state истёк — начни подключение заново", 400)
    return UUID(uid)


@router.get("/connect", response_model=GitHubConnectResponse)
async def github_connect(current_user: CurrentUserDep) -> GitHubConnectResponse:
    state = _sign_state(current_user.id)
    return GitHubConnectResponse(authorize_url=github_client.authorize_url(state))


@router.get("/callback")
async def github_callback(
    session: SessionDep,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    user_id = _verify_state(state)
    token_data = await github_client.exchange_code(code)
    login = await github_client.get_login(token_data["access_token"])

    user = await session.get(User, user_id)
    if user is None:
        raise ApiError("not_found", "user not found", status.HTTP_404_NOT_FOUND)
    user.github_login = login
    user.github_token_enc = encrypt_secret(token_data["access_token"])
    user.github_scope = token_data.get("scope", "")
    user.github_connected_at = datetime.now(UTC)
    await session.commit()

    web = get_settings().web_base_url.rstrip("/")
    return RedirectResponse(url=f"{web}/?github=connected", status_code=status.HTTP_302_FOUND)


@router.get("/status", response_model=GitHubStatus)
async def github_status(current_user: CurrentUserDep) -> GitHubStatus:
    return GitHubStatus(
        connected=bool(current_user.github_token_enc),
        login=current_user.github_login,
    )


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def github_disconnect(session: SessionDep, current_user: CurrentUserDep) -> Response:
    user = await session.get(User, current_user.id)
    if user is not None:
        user.github_login = None
        user.github_token_enc = None
        user.github_scope = None
        user.github_connected_at = None
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/push", response_model=PushResponse)
async def github_push(
    project_id: UUID,
    payload: PushRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> PushResponse:
    if not current_user.github_token_enc:
        raise ApiError("github_not_connected", "Сначала подключи GitHub", 400)

    project = await session.get(Project, project_id)
    if project is None or project.owner_id != current_user.id:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)
    if project.current_snapshot_id is None:
        raise ApiError("project_empty", "В проекте ещё нет файлов для пуша", 400)
    snapshot = await session.get(Snapshot, project.current_snapshot_id)
    if snapshot is None:
        raise ApiError("project_empty", "Снапшот проекта не найден", 400)

    # Экспортируем проект КАК ЕСТЬ — включая assets/omnia-kit.* (сайт должен работать
    # автономно на GitHub Pages / у клиента), поэтому KIT_FILES здесь НЕ вырезаем.
    files = await asyncio.to_thread(repo_svc.read_files, project_id, snapshot.commit_sha)
    if not files:
        raise ApiError("project_empty", "Нет файлов для пуша", 400)

    token = decrypt_secret(current_user.github_token_enc)
    repo = await github_client.create_repo(
        token,
        payload.repo_name,
        private=payload.private,
        description=payload.description or "Сайт, созданный в Omnia.AI",
    )
    await github_client.push_files(
        token,
        repo["full_name"],
        files,
        message="Initial commit from Omnia.AI",
    )
    return PushResponse(repo_url=repo["html_url"], full_name=repo["full_name"])
