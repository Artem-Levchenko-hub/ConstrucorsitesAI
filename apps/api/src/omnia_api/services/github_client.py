"""HTTP client for the GitHub REST API + OAuth token exchange.

Every github.com / api.github.com call lives behind this module so base URLs
(configurable, to allow proxying when github.com is throttled from the
deployment region), auth headers, timeouts, and error translation sit in one
place. Errors map onto the ApiError taxonomy: `github_unavailable` for
network/5xx, `github_rejected` for 4xx passed through.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from omnia_api.core.config import get_settings
from omnia_api.core.errors import ApiError

log = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_API_VERSION = "2022-11-28"


class GithubUnavailable(ApiError):
    """github.com unreachable or returning a network/5xx error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("github_unavailable", message, status_code=503, details=details)


class GithubRejected(ApiError):
    """GitHub returned a 4xx — surface the reason."""

    def __init__(
        self, message: str, status_code: int = 400, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__("github_rejected", message, status_code=status_code, details=details)


def _api_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {"raw": resp.text[:300]}
    return data if isinstance(data, dict) else {"data": data}


async def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    url = f"{settings.github_api_base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, json=json, headers=_api_headers(token))
    except httpx.RequestError as exc:
        log.exception("github.network_error", path=path, err=str(exc))
        raise GithubUnavailable(f"Cannot reach GitHub at {url}") from exc

    if resp.status_code >= 500:
        log.error("github.upstream_5xx", path=path, status=resp.status_code)
        raise GithubUnavailable(f"GitHub returned {resp.status_code}")
    if resp.status_code >= 400:
        detail = _safe_json(resp)
        raise GithubRejected(
            f"GitHub rejected request: {detail.get('message', 'unknown')}",
            status_code=resp.status_code,
            details=detail,
        )
    return _safe_json(resp)


async def exchange_code_for_token(code: str) -> tuple[str, str]:
    """Exchange the OAuth `code` for an access token. Returns (token, scopes)."""
    settings = get_settings()
    if settings.github_oauth_client_id is None or settings.github_oauth_client_secret is None:
        raise GithubUnavailable(
            "GitHub OAuth App is not configured (set GITHUB_OAUTH_CLIENT_ID/SECRET)."
        )
    url = f"{settings.github_oauth_base.rstrip('/')}/login/oauth/access_token"
    data = {
        "client_id": settings.github_oauth_client_id,
        "client_secret": settings.github_oauth_client_secret.get_secret_value(),
        "code": code,
        "redirect_uri": settings.github_oauth_redirect_uri,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, data=data, headers={"Accept": "application/json"})
    except httpx.RequestError as exc:
        log.exception("github.token_exchange.network_error", err=str(exc))
        raise GithubUnavailable(f"Cannot reach GitHub at {url}") from exc

    if resp.status_code >= 500:
        raise GithubUnavailable(f"GitHub returned {resp.status_code} on token exchange")
    body = _safe_json(resp)
    if body.get("error"):
        raise GithubRejected(
            f"GitHub OAuth error: {body.get('error_description', body['error'])}"
        )
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise GithubRejected("GitHub did not return an access token")
    scope = body.get("scope")
    return token, scope if isinstance(scope, str) else ""


async def get_authenticated_user(token: str) -> dict[str, Any]:
    """GET /user — the OAuth-authorized account (login, id, ...)."""
    return await _request("GET", "/user", token=token)


async def create_repo(
    token: str, *, name: str, private: bool, description: str | None
) -> dict[str, Any]:
    """POST /user/repos — create an empty repo under the authorized account."""
    payload: dict[str, Any] = {"name": name, "private": private, "auto_init": False}
    if description:
        payload["description"] = description
    return await _request("POST", "/user/repos", token=token, json=payload)


async def get_repo(token: str, full_name: str) -> dict[str, Any] | None:
    """GET /repos/{owner}/{repo} — None if it does not exist (404)."""
    try:
        return await _request("GET", f"/repos/{full_name}", token=token)
    except GithubRejected as exc:
        if exc.status_code == 404:
            return None
        raise
