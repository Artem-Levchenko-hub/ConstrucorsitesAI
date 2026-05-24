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

import asyncio
import logging

_GH_API = "https://api.github.com"
_GH_OAUTH = "https://github.com"
# 60s overall, 30s connect — на проде ловили спорадический ConnectTimeout
# к api.github.com из FastAPI/async-httpx даже когда curl/sync проходил
# за 0.5s. Источник — async DNS resolution через anyio в Docker контейнере
# (первый запрос иногда висит). `transport=retries=5` ниже добавляет
# auto-reconnect на connection-establishment failures.
_TIMEOUT = httpx.Timeout(60.0, connect=30.0)

# Application-level retry policy для POST'ов где transport-retries не помогает
# (например 5xx от GitHub). Connection-level retries — на transport.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 0.5
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

_log = logging.getLogger(__name__)


def _make_client(*, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    """AsyncClient с auto-retry на connection-failures.

    `httpx.AsyncHTTPTransport(retries=5)` ретраит ConnectError/ConnectTimeout
    на уровне транспорта (читай: TCP/TLS handshake фейлы), что обходит async-DNS
    pause без явных try/sleep циклов.
    """
    transport = httpx.AsyncHTTPTransport(retries=5)
    return httpx.AsyncClient(
        timeout=_TIMEOUT,
        transport=transport,
        headers=headers,
    )


async def _post_with_retry(
    url: str, *, data: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    """POST с retry на transient network-сбои.

    Поверх transport-retries — application-level loop на случай если все 5
    transport-попыток провалились ИЛИ GitHub вернул 5xx.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            async with _make_client() as client:
                return await client.post(url, data=data, headers=headers)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt >= _RETRY_ATTEMPTS:
                break
            backoff = _RETRY_BACKOFF_BASE * (3 ** (attempt - 1))
            _log.warning(
                "github_client: %s on attempt %d/%d — retry in %.1fs",
                type(exc).__name__,
                attempt,
                _RETRY_ATTEMPTS,
                backoff,
            )
            await asyncio.sleep(backoff)
    assert last_exc is not None
    raise last_exc


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
    try:
        resp = await _post_with_retry(
            f"{_GH_OAUTH}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": s.github_client_id,
                "client_secret": s.github_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": s.github_callback_url,
            },
        )
    except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
        _log.error("github_client: exchange_code network failure: %r", exc)
        raise ApiError(
            "github_network_error",
            "Не удалось соединиться с GitHub. Попробуйте ещё раз через минуту.",
            502,
        ) from exc
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
    async with _make_client() as client:
        resp = await client.get(f"{_GH_API}/user", headers=_auth_headers(token))
    if resp.status_code != 200:
        raise ApiError("github_token_invalid", "GitHub-токен недействителен", 401)
    return str(resp.json()["login"])


async def create_repo(
    token: str, name: str, *, private: bool, description: str
) -> dict[str, str]:
    """Создать репозиторий у пользователя с auto-init (README + initial commit).

    `auto_init=False` создавал «пустой» repo без default-branch ref'а, и
    последующие вызовы /git/blobs возвращали 409 «Git Repository is empty»
    (GitHub bug — docs утверждают что blob работает, API отказывает).
    С `auto_init=True` GitHub сразу пишет initial commit + main branch,
    и наш push добавляет файлы поверх через base_tree.
    """
    async with _make_client() as client:
        resp = await client.post(
            f"{_GH_API}/user/repos",
            headers=_auth_headers(token),
            json={
                "name": name,
                "private": private,
                "description": description,
                "auto_init": True,
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


async def get_user_repo(token: str, name: str) -> dict[str, str] | None:
    """Найти существующий репозиторий у владельца токена. None если нет."""
    login = await get_login(token)
    async with _make_client() as client:
        resp = await client.get(
            f"{_GH_API}/repos/{login}/{name}", headers=_auth_headers(token)
        )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ApiError(
            "github_repo_failed",
            f"Не удалось получить репо (HTTP {resp.status_code})",
            502,
        )
    j: dict[str, Any] = resp.json()
    return {
        "full_name": str(j["full_name"]),
        "html_url": str(j["html_url"]),
        "default_branch": str(j.get("default_branch") or "main"),
        # Empty repo от GitHub приходит с `"size": 0` И без default-branch HEAD.
        # Используем это чтобы решить — можно ли безопасно пушить first commit.
        "is_empty": "true" if j.get("size", 0) == 0 else "false",
    }


async def push_files(
    token: str,
    full_name: str,
    files: dict[str, str],
    *,
    message: str,
    branch: str = "main",
) -> None:
    """Залить файлы коммитом в инициализированный (auto_init=True) репо.

    Pipeline:
      1) GET /git/refs/heads/{branch}      → head_sha
      2) GET /git/commits/{head_sha}       → base_tree_sha
      3) POST /git/blobs (per файл)        → blob_sha
      4) POST /git/trees (base_tree + новые blobs)  → new_tree_sha
      5) POST /git/commits (parents=[head], tree=new_tree)  → new_commit_sha
      6) PATCH /git/refs/heads/{branch}    → fast-forward к new_commit_sha

    На свежем repo (даже auto_init) GitHub initials API takes ~500ms;
    transport-level retries в _make_client сглаживают это.
    """
    import base64

    if not files:
        raise ApiError("github_push_failed", "Нет файлов для пуша", 400)

    async with _make_client(headers=_auth_headers(token)) as client:
        # 1) HEAD ref → head commit sha
        # GitHub bug: после create_repo(auto_init=True) ref/heads/main иногда
        # пару секунд возвращает 409 «Git Repository is empty» пока GitHub
        # дописывает initial commit. Опрашиваем до 10×1s.
        r_ref_get = None
        for attempt in range(10):
            r_ref_get = await client.get(
                f"{_GH_API}/repos/{full_name}/git/refs/heads/{branch}"
            )
            if r_ref_get.status_code == 200:
                break
            if r_ref_get.status_code == 409 and "empty" in r_ref_get.text.lower():
                _log.info(
                    "github_client: repo %s still initialising (attempt %d/10), wait 1s",
                    full_name,
                    attempt + 1,
                )
                await asyncio.sleep(1.0)
                continue
            break
        assert r_ref_get is not None
        if r_ref_get.status_code != 200:
            body = r_ref_get.text[:400]
            _log.error("github_client: ref-get HTTP %d: %s", r_ref_get.status_code, body)
            raise ApiError(
                "github_push_failed",
                f"git/refs GET HTTP {r_ref_get.status_code}: {body[:160]}",
                502,
            )
        head_sha = str(r_ref_get.json()["object"]["sha"])

        # 2) HEAD commit → base tree sha
        r_commit_get = await client.get(
            f"{_GH_API}/repos/{full_name}/git/commits/{head_sha}"
        )
        if r_commit_get.status_code != 200:
            body = r_commit_get.text[:400]
            _log.error(
                "github_client: commit-get HTTP %d: %s",
                r_commit_get.status_code,
                body,
            )
            raise ApiError(
                "github_push_failed",
                f"git/commits GET HTTP {r_commit_get.status_code}: {body[:160]}",
                502,
            )
        base_tree_sha = str(r_commit_get.json()["tree"]["sha"])

        # 3) Каждый файл → blob (base64)
        tree_entries: list[dict[str, Any]] = []
        for path, content in files.items():
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            r_blob = await client.post(
                f"{_GH_API}/repos/{full_name}/git/blobs",
                json={"content": encoded, "encoding": "base64"},
            )
            if r_blob.status_code not in (200, 201):
                body = r_blob.text[:300]
                _log.error(
                    "github_client: blob HTTP %d for %s: %s",
                    r_blob.status_code,
                    path,
                    body,
                )
                raise ApiError(
                    "github_push_failed",
                    f"git/blobs HTTP {r_blob.status_code} ({path}): {body[:120]}",
                    502,
                )
            tree_entries.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": str(r_blob.json()["sha"]),
                }
            )

        # 4) Tree поверх base_tree (README от auto_init остаётся, файлы юзера
        #    добавляются; одноимённые перетирают).
        r_tree = await client.post(
            f"{_GH_API}/repos/{full_name}/git/trees",
            json={"base_tree": base_tree_sha, "tree": tree_entries},
        )
        if r_tree.status_code not in (200, 201):
            body = r_tree.text[:400]
            _log.error("github_client: tree HTTP %d: %s", r_tree.status_code, body)
            raise ApiError(
                "github_push_failed",
                f"git/trees HTTP {r_tree.status_code}: {body[:160]}",
                502,
            )
        new_tree_sha = str(r_tree.json()["sha"])

        # 5) Commit с parents=[head_sha]
        r_commit = await client.post(
            f"{_GH_API}/repos/{full_name}/git/commits",
            json={
                "message": message,
                "tree": new_tree_sha,
                "parents": [head_sha],
            },
        )
        if r_commit.status_code not in (200, 201):
            body = r_commit.text[:400]
            _log.error(
                "github_client: commit HTTP %d: %s",
                r_commit.status_code,
                body,
            )
            raise ApiError(
                "github_push_failed",
                f"git/commits HTTP {r_commit.status_code}: {body[:160]}",
                502,
            )
        new_commit_sha = str(r_commit.json()["sha"])

        # 6) Fast-forward existing ref (PATCH, не POST — иначе «ref exists»)
        r_ref_upd = await client.patch(
            f"{_GH_API}/repos/{full_name}/git/refs/heads/{branch}",
            json={"sha": new_commit_sha, "force": False},
        )
        if r_ref_upd.status_code not in (200, 201):
            body = r_ref_upd.text[:400]
            _log.error("github_client: ref PATCH HTTP %d: %s", r_ref_upd.status_code, body)
            raise ApiError(
                "github_push_failed",
                f"git/refs PATCH HTTP {r_ref_upd.status_code}: {body[:160]}",
                502,
            )
