"""OAuth authorization URLs and code exchange for business integrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from omnia_api.core.config import get_settings
from omnia_api.services.integration_providers import (
    IntegrationCredentialsInvalid,
    IntegrationProviderError,
    IntegrationProviderUnavailable,
)


@dataclass(frozen=True)
class OAuthCredentials:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class OAuthResult:
    public_config: dict[str, str]
    secret_values: dict[str, str]
    account_label: str
    expires_at: datetime | None = None


def credentials(provider: str) -> OAuthCredentials | None:
    settings = get_settings()
    pairs = {
        "yookassa": (
            settings.integration_yookassa_client_id,
            settings.integration_yookassa_client_secret,
        ),
        "yandex_metrica": (
            settings.integration_yandex_client_id,
            settings.integration_yandex_client_secret,
        ),
        "bitrix24": (
            settings.integration_bitrix24_client_id,
            settings.integration_bitrix24_client_secret,
        ),
        "amocrm": (
            settings.integration_amocrm_client_id,
            settings.integration_amocrm_client_secret,
        ),
    }
    pair = pairs.get(provider)
    if pair is None or pair[0] is None or pair[1] is None:
        return None
    return OAuthCredentials(pair[0], pair[1].get_secret_value())


def callback_url(provider: str) -> str:
    base = get_settings().integration_oauth_callback_base_url.rstrip("/")
    return f"{base}/{provider}/callback"


def authorization_url(provider: str, state: str) -> str:
    oauth = credentials(provider)
    if oauth is None:
        raise IntegrationProviderError(
            "OAuth приложения сервиса ещё не настроен владельцем платформы"
        )
    redirect_uri = callback_url(provider)
    if provider == "yookassa":
        base = "https://yookassa.ru/oauth/v2/authorize"
        params = {
            "response_type": "code",
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    elif provider == "yandex_metrica":
        base = "https://oauth.yandex.ru/authorize"
        params = {
            "response_type": "code",
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "force_confirm": "yes",
        }
    elif provider == "bitrix24":
        base = "https://oauth.bitrix.info/oauth/authorize/"
        params = {
            "response_type": "code",
            "client_id": oauth.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    elif provider == "amocrm":
        base = "https://www.amocrm.ru/oauth"
        params = {
            "client_id": oauth.client_id,
            "state": state,
            "mode": "post_message",
        }
    else:
        raise IntegrationProviderError("OAuth для этой интеграции не поддерживается")
    return f"{base}?{urlencode(params)}"


def _expires(payload: dict[str, Any]) -> datetime | None:
    try:
        seconds = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        return None
    return datetime.now(UTC) + timedelta(seconds=max(0, seconds - 60)) if seconds else None


def _token_error(provider: str, response: httpx.Response) -> None:
    if response.status_code in {400, 401, 403}:
        raise IntegrationCredentialsInvalid(
            f"{provider} отклонил код авторизации. Начните подключение ещё раз."
        )
    if response.status_code >= 500 or response.status_code == 429:
        raise IntegrationProviderUnavailable(
            f"{provider} временно недоступен. Повторите подключение позже."
        )
    if response.status_code >= 300:
        raise IntegrationProviderError(
            f"{provider} вернул неожиданный ответ HTTP {response.status_code}."
        )


async def exchange_code(
    provider: str,
    code: str,
    *,
    referer: str | None = None,
) -> OAuthResult:
    oauth = credentials(provider)
    if oauth is None:
        raise IntegrationProviderError("OAuth приложения сервиса не настроен")
    redirect_uri = callback_url(provider)
    headers = {
        "Accept": "application/json",
        "User-Agent": "Omnia-Integration-Hub/2.0",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15), follow_redirects=False, headers=headers
        ) as client:
            if provider == "yookassa":
                token_response = await client.post(
                    "https://yookassa.ru/oauth/v2/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    auth=(oauth.client_id, oauth.client_secret),
                )
                _token_error("ЮKassa", token_response)
                token = token_response.json()
                access = str(token.get("access_token") or "")
                profile = await client.get(
                    "https://api.yookassa.ru/v3/me",
                    headers={**headers, "Authorization": f"Bearer {access}"},
                )
                _token_error("ЮKassa", profile)
                me = profile.json()
                account_id = str(me.get("account_id") or me.get("shop_id") or "")
                return OAuthResult(
                    public_config={"shop_id": account_id} if account_id else {},
                    secret_values={
                        "access_token": access,
                        **(
                            {"refresh_token": str(token["refresh_token"])}
                            if token.get("refresh_token")
                            else {}
                        ),
                    },
                    account_label=f"Магазин {account_id}" if account_id else "ЮKassa",
                    expires_at=_expires(token),
                )

            if provider == "yandex_metrica":
                token_response = await client.post(
                    "https://oauth.yandex.ru/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": oauth.client_id,
                        "client_secret": oauth.client_secret,
                    },
                )
                _token_error("Яндекс", token_response)
                token = token_response.json()
                access = str(token.get("access_token") or "")
                counters_response = await client.get(
                    "https://api-metrika.yandex.net/management/v1/counters",
                    params={"per_page": 100},
                    headers={**headers, "Authorization": f"OAuth {access}"},
                )
                _token_error("Яндекс Метрика", counters_response)
                counters = counters_response.json().get("counters") or []
                first = counters[0] if counters else {}
                counter_id = str(first.get("id") or "")
                return OAuthResult(
                    public_config={
                        **({"counter_id": counter_id} if counter_id else {}),
                        "available_counters": ",".join(
                            str(item.get("id"))
                            for item in counters[:20]
                            if item.get("id")
                        ),
                    },
                    secret_values={
                        "oauth_token": access,
                        **(
                            {"refresh_token": str(token["refresh_token"])}
                            if token.get("refresh_token")
                            else {}
                        ),
                    },
                    account_label=str(first.get("name") or "Яндекс Метрика"),
                    expires_at=_expires(token),
                )

            if provider == "bitrix24":
                token_response = await client.get(
                    "https://oauth.bitrix.info/oauth/token/",
                    params={
                        "grant_type": "authorization_code",
                        "client_id": oauth.client_id,
                        "client_secret": oauth.client_secret,
                        "code": code,
                    },
                )
                _token_error("Битрикс24", token_response)
                token = token_response.json()
                endpoint = str(token.get("client_endpoint") or "").rstrip("/")
                parsed = urlparse(endpoint)
                if parsed.scheme != "https" or not parsed.hostname:
                    raise IntegrationProviderError(
                        "Битрикс24 не вернул безопасный адрес портала"
                    )
                profile = await client.get(
                    f"{endpoint}/profile.json",
                    params={"auth": token.get("access_token")},
                )
                _token_error("Битрикс24", profile)
                result = profile.json().get("result") or {}
                label = result.get("NAME") or result.get("ID") or parsed.hostname
                return OAuthResult(
                    public_config={"client_endpoint": endpoint},
                    secret_values={
                        "access_token": str(token.get("access_token") or ""),
                        "refresh_token": str(token.get("refresh_token") or ""),
                    },
                    account_label=f"{parsed.hostname} · {label}",
                    expires_at=_expires(token),
                )

            if provider == "amocrm":
                host = (urlparse(referer or "").hostname or "").lower()
                if not host.endswith((".amocrm.ru", ".kommo.com")):
                    raise IntegrationCredentialsInvalid(
                        "amoCRM не передал адрес авторизованного аккаунта"
                    )
                base_url = f"https://{host}"
                token_response = await client.post(
                    f"{base_url}/oauth2/access_token",
                    json={
                        "client_id": oauth.client_id,
                        "client_secret": oauth.client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                )
                _token_error("amoCRM", token_response)
                token = token_response.json()
                access = str(token.get("access_token") or "")
                account = await client.get(
                    f"{base_url}/api/v4/account",
                    headers={**headers, "Authorization": f"Bearer {access}"},
                )
                _token_error("amoCRM", account)
                profile = account.json()
                return OAuthResult(
                    public_config={"base_url": base_url},
                    secret_values={
                        "access_token": access,
                        "refresh_token": str(token.get("refresh_token") or ""),
                    },
                    account_label=str(profile.get("name") or host),
                    expires_at=_expires(token),
                )
    except IntegrationProviderError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise IntegrationProviderUnavailable(
            "Сервис авторизации временно недоступен"
        ) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise IntegrationProviderError(
            "Сервис вернул неизвестный формат ответа авторизации"
        ) from exc

    raise IntegrationProviderError("OAuth для этой интеграции не поддерживается")


async def refresh_access_token(
    provider: str,
    public_config: dict[str, Any],
    secret_values: dict[str, str],
) -> tuple[dict[str, str], datetime | None]:
    """Refresh an expiring OAuth token without exposing it to a generated app."""
    oauth = credentials(provider)
    refresh = secret_values.get("refresh_token")
    if oauth is None or not refresh:
        raise IntegrationCredentialsInvalid(
            "Срок авторизации истёк. Переподключите сервис в Integration Hub."
        )
    headers = {
        "Accept": "application/json",
        "User-Agent": "Omnia-Integration-Hub/2.0",
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            if provider == "yookassa":
                response = await client.post(
                    "https://yookassa.ru/oauth/v2/token",
                    data={"grant_type": "refresh_token", "refresh_token": refresh},
                    auth=(oauth.client_id, oauth.client_secret),
                )
            elif provider == "yandex_metrica":
                response = await client.post(
                    "https://oauth.yandex.ru/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                        "client_id": oauth.client_id,
                        "client_secret": oauth.client_secret,
                    },
                )
            elif provider == "bitrix24":
                response = await client.get(
                    "https://oauth.bitrix.info/oauth/token/",
                    params={
                        "grant_type": "refresh_token",
                        "client_id": oauth.client_id,
                        "client_secret": oauth.client_secret,
                        "refresh_token": refresh,
                    },
                )
            elif provider == "amocrm":
                base_url = str(public_config.get("base_url") or "").rstrip("/")
                response = await client.post(
                    f"{base_url}/oauth2/access_token",
                    json={
                        "client_id": oauth.client_id,
                        "client_secret": oauth.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                        "redirect_uri": callback_url(provider),
                    },
                )
            else:
                raise IntegrationProviderError(
                    "Автообновление этой авторизации не поддерживается"
                )
            _token_error(provider, response)
            payload = response.json()
            access = str(payload.get("access_token") or "")
            if not access:
                raise IntegrationProviderError(
                    "Сервис не вернул новый токен авторизации"
                )
            updated = dict(secret_values)
            if provider == "yandex_metrica":
                updated["oauth_token"] = access
            else:
                updated["access_token"] = access
            if payload.get("refresh_token"):
                updated["refresh_token"] = str(payload["refresh_token"])
            return updated, _expires(payload)
    except IntegrationProviderError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise IntegrationProviderUnavailable(
            "Сервис авторизации временно недоступен"
        ) from exc
    except (ValueError, TypeError) as exc:
        raise IntegrationProviderError(
            "Сервис вернул неизвестный формат обновления авторизации"
        ) from exc
