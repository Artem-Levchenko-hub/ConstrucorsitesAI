"""Small, secret-safe client for the official MAX Bot API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://platform-api2.max.ru"
_TIMEOUT = httpx.Timeout(15.0, connect=8.0)


class MaxClientError(RuntimeError):
    pass


class MaxTokenInvalid(MaxClientError):
    pass


class MaxApiUnavailable(MaxClientError):
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


async def _request(
    method: str,
    path: str,
    token: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=_TIMEOUT) as client:
            response = await client.request(
                method,
                path,
                headers=_headers(token),
                json=json,
                params=params,
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
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
