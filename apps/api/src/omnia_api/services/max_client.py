"""Small, secret-safe client for the official MAX Bot API."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://platform-api2.max.ru"
_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
_MAX_CA_FILE = Path(__file__).resolve().parents[1] / "certs" / "russian_trusted_root_ca.pem"


class MaxClientError(RuntimeError):
    pass


class MaxTokenInvalid(MaxClientError):
    pass


class MaxApiUnavailable(MaxClientError):
    pass


class MaxTlsConfigurationError(MaxApiUnavailable):
    pass


@dataclass(frozen=True, slots=True)
class MaxBot:
    id: str | None
    name: str | None
    username: str | None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "OmniaAI-MAX-MiniApp/1.0",
    }


@lru_cache(maxsize=1)
def _max_ssl_context() -> ssl.SSLContext:
    """Default WebPKI roots plus the official Russian Trusted Root CA.

    MAX serves ``platform-api2.max.ru`` from the Russian Trusted CA hierarchy,
    which is intentionally absent from Debian/Python's default bundle. Keep the
    additional trust scoped to this client instead of weakening TLS globally.
    """

    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cafile=_MAX_CA_FILE)
    except (OSError, ssl.SSLError) as exc:
        raise MaxTlsConfigurationError(
            "Не удалось загрузить доверенный сертификат MAX API"
        ) from exc
    return context


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=_TIMEOUT,
        verify=_max_ssl_context(),
    )


def _is_tls_verification_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current).upper():
            return True
        current = current.__cause__ or current.__context__
    return False


async def _request(
    method: str,
    path: str,
    token: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    try:
        async with _http_client() as client:
            response = await client.request(
                method,
                path,
                headers=_headers(token),
                json=json,
                params=params,
            )
    except MaxTlsConfigurationError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        if _is_tls_verification_error(exc):
            raise MaxTlsConfigurationError(
                "TLS-сертификат MAX API не прошёл проверку доверия"
            ) from exc
        raise MaxApiUnavailable("MAX API временно недоступен") from exc
    if response.status_code in (401, 403):
        raise MaxTokenInvalid("MAX отклонил токен бота")
    if response.status_code >= 500:
        raise MaxApiUnavailable("MAX API временно недоступен")
    if response.status_code >= 400:
        raise MaxClientError(f"MAX API вернул HTTP {response.status_code}")
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise MaxApiUnavailable("MAX API вернул некорректный ответ") from exc


async def get_me(token: str) -> MaxBot:
    data = await _request("GET", "/me", token)
    if not isinstance(data, dict):
        raise MaxApiUnavailable("MAX API вернул некорректный профиль бота")
    bot_id = data.get("user_id") or data.get("bot_id") or data.get("id")
    first_name = str(data.get("first_name") or "").strip()
    last_name = str(data.get("last_name") or "").strip()
    name = " ".join(part for part in (first_name, last_name) if part)
    if not name:
        name = str(data.get("name") or "").strip()
    username = str(data.get("username") or "").strip().lstrip("@")
    return MaxBot(
        id=str(bot_id) if bot_id is not None else None,
        name=name or None,
        username=username or None,
    )


async def list_subscriptions(token: str) -> list[dict[str, Any]]:
    data = await _request("GET", "/subscriptions", token)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("subscriptions")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


async def subscribe(token: str, url: str, secret: str) -> None:
    await _request(
        "POST",
        "/subscriptions",
        token,
        json={
            "url": url,
            "secret": secret,
            "update_types": ["message_created", "message_callback", "bot_started"],
        },
    )


async def unsubscribe(token: str, url: str) -> None:
    await _request("DELETE", "/subscriptions", token, params={"url": url})


async def has_subscription(token: str, url: str) -> bool:
    return any(str(item.get("url") or "") == url for item in await list_subscriptions(token))
