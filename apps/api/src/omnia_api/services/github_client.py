"""GitHub API клиент для фичи «Push to GitHub».

OAuth (authorize → exchange code → /user) + создание репозитория и заливка файлов
проекта через Git Data API (blob-контент инлайном в tree → commit → ref). Без
pygit2-push: только httpx с токеном пользователя в заголовке. Каждый вызов — с
таймаутом и явной ApiError при сбое (R-10: fail fast, понятная ошибка наружу).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from omnia_api.core.config import get_settings
from omnia_api.core.errors import ApiError

_GH_API = "https://api.github.com"
_GH_OAUTH = "https://github.com"
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def authorize_url(state: str) -> str:
    """URL, на который фронт отправляет браузер для авторизации в GitHub."""
    s = get_settings()
    if not s.github_client_id:
        raise ApiError("github_not_configured", "GitHub OAuth не настроен на сервере", 503)
    query = urlencode(
        {
            "client_id": s.github_client_id,
            "redirect_uri": s.github_callback_url,
            "scope": s.github_oauth_scope,
            "state": state,
            "allow_signup": "true",
        }
    )
    return f"{_GH_OAUTH}/login/oauth/authorize?{query}"


async def exchange_code(code: str) -> dict[str, str]:
    """Обменять `code` из callback на access_token."""
    s = get_settings()
    if not s.github_client_id or not s.github_client_secret:
        raise ApiError("github_not_configured", "GitHub OAuth не настроен на сервере", 503)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_GH_OAUTH}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": s.github_client_id,
                "client_secret": s.github_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": s.github_callback_url,
            },
        )
    if resp.status_code != 200:
        raise ApiError("github_oauth_failed", "GitHub отклонил обмен кода", 502)
    data: dict[str, Any] = resp.json()
    token = data.get("access_token")
    if not token:
        detail = str(data.get("error_description") or "GitHub не вернул access_token")
        raise ApiError("github_oauth_failed", detail, 502)
    return {"access_token": str(token), "scope": str(data.get("scope") or "")}


def _auth_headers(token: str) -> dict[str, str]:
    return {**_API_HEADERS, "Authorization": f"Bearer {token}"}


async def get_login(token: str) -> str:
    """GitHub-логин владельца токена (проверяет, что токен живой)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_GH_API}/user", headers=_auth_headers(token))
    if resp.status_code != 200:
        raise ApiError("github_token_invalid", "GitHub-токен недействителен", 401)
    return str(resp.json()["login"])


async def create_repo(
    token: str, name: str, *, private: bool, description: str
) -> dict[str, str]:
    """Создать пустой (auto_init=False) репозиторий у пользователя."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_GH_API}/user/repos",
            headers=_auth_headers(token),
            json={
                "name": name,
                "private": private,
                "description": description,
                "auto_init": False,
            },
        )
    if resp.status_code == 422:
        raise ApiError(
            "github_repo_exists",
            f"Репозиторий «{name}» уже существует у вас на GitHub",
            409,
        )
    if resp.status_code not in (200, 201):
        raise ApiError(
            "github_repo_failed",
            f"Не удалось создать репозиторий (HTTP {resp.status_code})",
            502,
        )
    j: dict[str, Any] = resp.json()
    return {
        "full_name": str(j["full_name"]),
        "html_url": str(j["html_url"]),
        "default_branch": str(j.get("default_branch") or "main"),
    }


async def push_files(
    token: str,
    full_name: str,
    files: dict[str, str],
    *,
    message: str,
    branch: str = "main",
) -> None:
    """Залить файлы первым коммитом в пустой репозиторий через Git Data API."""
    if not files:
        raise ApiError("github_push_failed", "Нет файлов для пуша", 400)
    tree = [
        {"path": path, "mode": "100644", "type": "blob", "content": content}
        for path, content in files.items()
    ]
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_auth_headers(token)) as client:
        r_tree = await client.post(
            f"{_GH_API}/repos/{full_name}/git/trees", json={"tree": tree}
        )
        if r_tree.status_code not in (200, 201):
            raise ApiError("github_push_failed", f"git/trees HTTP {r_tree.status_code}", 502)
        tree_sha = str(r_tree.json()["sha"])

        r_commit = await client.post(
            f"{_GH_API}/repos/{full_name}/git/commits",
            json={"message": message, "tree": tree_sha},
        )
        if r_commit.status_code not in (200, 201):
            raise ApiError("github_push_failed", f"git/commits HTTP {r_commit.status_code}", 502)
        commit_sha = str(r_commit.json()["sha"])

        r_ref = await client.post(
            f"{_GH_API}/repos/{full_name}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
        if r_ref.status_code not in (200, 201):
            raise ApiError("github_push_failed", f"git/refs HTTP {r_ref.status_code}", 502)
