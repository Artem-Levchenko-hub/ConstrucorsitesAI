"""Вход через VK ID (OAuth 2.1 + PKCE) и Яндекс ID (OAuth 2.0).

От провайдера платформа берёт ровно два поля — его идентификатор пользователя
и email. Access-токен живёт только внутри :func:`fetch_identity` на время
запроса профиля и никуда не записывается; имя, телефон, аватар и остальное из
ответа провайдера отбрасываются на месте (аккаунт без реквизитов —
docs/plans/2026-09-23-account-data-minimisation.md).

Адреса провайдеров:

* VK ID — authorize ``https://id.vk.com/authorize``, токен
  ``https://id.vk.com/oauth2/auth``, профиль ``https://id.vk.com/oauth2/user_info``.
  Обмен кода защищён PKCE (S256) и требует ``device_id``, который VK добавляет
  к redirect_uri вместе с ``code``; client secret для обмена не нужен.
* Яндекс ID — authorize ``https://oauth.yandex.ru/authorize``, токен
  ``https://oauth.yandex.ru/token`` (client id + secret), профиль
  ``https://login.yandex.ru/info``. Запрашивается только право ``login:email``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from yleum_api.core.config import get_settings

VK_AUTHORIZE_URL = "https://id.vk.com/authorize"
VK_TOKEN_URL = "https://id.vk.com/oauth2/auth"
VK_USER_INFO_URL = "https://id.vk.com/oauth2/user_info"
YANDEX_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"
YANDEX_USER_INFO_URL = "https://login.yandex.ru/info"

PROVIDER_LABELS: dict[str, str] = {"vk": "VK ID", "yandex": "Яндекс ID"}
PROVIDERS: tuple[str, ...] = tuple(PROVIDER_LABELS)

_USER_AGENT = "MAX-Studio-Login/1.0"


class OAuthLoginError(Exception):
    """Провайдер не выдал код, токен или профиль — вход не состоялся."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str | None


@dataclass(frozen=True)
class ProviderIdentity:
    """Всё, что платформа узнаёт о человеке от провайдера."""

    provider: str
    provider_user_id: str
    email: str | None


def credentials(provider: str) -> OAuthClient | None:
    """Реквизиты приложения у провайдера или None, если вход через него не настроен."""
    settings = get_settings()
    if provider == "vk":
        client_id = (settings.vk_id_client_id or "").strip()
        secret = (
            settings.vk_id_client_secret.get_secret_value().strip()
            if settings.vk_id_client_secret
            else ""
        )
        # Обмен кода у VK ID идёт по PKCE — «защищённый ключ» не обязателен.
        return OAuthClient(client_id, secret or None) if client_id else None
    if provider == "yandex":
        client_id = (settings.yandex_id_client_id or "").strip()
        secret = (
            settings.yandex_id_client_secret.get_secret_value().strip()
            if settings.yandex_id_client_secret
            else ""
        )
        return OAuthClient(client_id, secret) if client_id and secret else None
    return None


def is_configured(provider: str) -> bool:
    return credentials(provider) is not None


def configured_providers() -> list[str]:
    return [provider for provider in PROVIDERS if is_configured(provider)]


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS[provider]


def uses_pkce(provider: str) -> bool:
    return provider == "vk"


def callback_url(provider: str) -> str:
    """Redirect URI, который владелец указывает в кабинете провайдера."""
    settings = get_settings()
    base = (settings.oauth_login_redirect_base_url or settings.web_base_url).rstrip("/")
    return f"{base}/api/auth/oauth/{provider}/callback"


def generate_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) по RFC 7636, метод S256."""
    verifier = secrets.token_urlsafe(64)  # 86 символов — в границах 43…128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorization_url(provider: str, *, state: str, code_challenge: str | None = None) -> str:
    oauth = credentials(provider)
    if oauth is None:
        raise OAuthLoginError("Вход через этого провайдера не настроен")
    redirect_uri = callback_url(provider)
    if provider == "vk":
        if not code_challenge:
            raise OAuthLoginError("VK ID требует PKCE")
        params = {
            "response_type": "code",
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "email",
            "code_challenge": code_challenge,
            "code_challenge_method": "s256",
        }
        return f"{VK_AUTHORIZE_URL}?{urlencode(params)}"
    if provider == "yandex":
        params = {
            "response_type": "code",
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "login:email",
        }
        return f"{YANDEX_AUTHORIZE_URL}?{urlencode(params)}"
    raise OAuthLoginError("Вход через этого провайдера не поддерживается")


def http_client() -> httpx.AsyncClient:
    """Точка подмены в тестах (httpx.MockTransport)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15),
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
    )


def _payload(response: httpx.Response, what: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise OAuthLoginError(f"{what}: HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise OAuthLoginError(f"{what}: неизвестный формат ответа")
    if payload.get("error"):
        raise OAuthLoginError(f"{what}: {payload.get('error')}")
    return payload


def _access_token(token: dict[str, Any], what: str) -> str:
    access = token.get("access_token")
    if not isinstance(access, str) or not access.strip():
        raise OAuthLoginError(f"{what}: нет access_token")
    return access


def _subject(value: object, what: str) -> str:
    if isinstance(value, bool) or value is None:
        raise OAuthLoginError(f"{what}: нет идентификатора пользователя")
    if isinstance(value, int | str):
        subject = str(value).strip()
        if subject:
            return subject
    raise OAuthLoginError(f"{what}: нет идентификатора пользователя")


def _email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip()
    if "@" not in email or " " in email or len(email) > 254:
        return None
    return email


async def fetch_identity(
    provider: str,
    *,
    code: str,
    code_verifier: str | None,
    device_id: str | None,
    state: str,
) -> ProviderIdentity:
    """Обменять код на токен и вернуть только id + email пользователя."""
    oauth = credentials(provider)
    if oauth is None:
        raise OAuthLoginError("Вход через этого провайдера не настроен")
    try:
        async with http_client() as client:
            if provider == "vk":
                if not code_verifier or not device_id:
                    raise OAuthLoginError("VK ID: нет PKCE-verifier или device_id")
                token = _payload(
                    await client.post(
                        VK_TOKEN_URL,
                        data={
                            "grant_type": "authorization_code",
                            "code": code,
                            "code_verifier": code_verifier,
                            "client_id": oauth.client_id,
                            "device_id": device_id,
                            "redirect_uri": callback_url(provider),
                            "state": state,
                        },
                    ),
                    "VK ID token",
                )
                profile = _payload(
                    await client.post(
                        VK_USER_INFO_URL,
                        data={
                            "client_id": oauth.client_id,
                            "access_token": _access_token(token, "VK ID token"),
                        },
                    ),
                    "VK ID user_info",
                )
                user = profile.get("user")
                if not isinstance(user, dict):
                    raise OAuthLoginError("VK ID user_info: нет блока user")
                return ProviderIdentity(
                    provider="vk",
                    provider_user_id=_subject(user.get("user_id"), "VK ID user_info"),
                    email=_email(user.get("email")),
                )
            if provider == "yandex":
                token = _payload(
                    await client.post(
                        YANDEX_TOKEN_URL,
                        data={
                            "grant_type": "authorization_code",
                            "code": code,
                            "client_id": oauth.client_id,
                            "client_secret": oauth.client_secret or "",
                        },
                    ),
                    "Яндекс ID token",
                )
                profile = _payload(
                    await client.get(
                        YANDEX_USER_INFO_URL,
                        params={"format": "json"},
                        headers={
                            "Authorization": f"OAuth {_access_token(token, 'Яндекс ID token')}"
                        },
                    ),
                    "Яндекс ID info",
                )
                email = _email(profile.get("default_email"))
                if email is None:
                    emails = profile.get("emails")
                    if isinstance(emails, list) and emails:
                        email = _email(emails[0])
                return ProviderIdentity(
                    provider="yandex",
                    provider_user_id=_subject(profile.get("id"), "Яндекс ID info"),
                    email=email,
                )
    except OAuthLoginError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise OAuthLoginError(f"{provider}: провайдер недоступен или ответил неожиданно") from exc
    raise OAuthLoginError("Вход через этого провайдера не поддерживается")


__all__ = [
    "PROVIDERS",
    "PROVIDER_LABELS",
    "OAuthClient",
    "OAuthLoginError",
    "ProviderIdentity",
    "authorization_url",
    "callback_url",
    "configured_providers",
    "credentials",
    "fetch_identity",
    "generate_pkce",
    "http_client",
    "is_configured",
    "provider_label",
    "uses_pkce",
]
