"""Sber GigaChat provider — OAuth token exchange + OpenAI-shaped chat completion.

GigaChat needs a two-step auth flow:
    1. POST /api/v2/oauth with `Authorization: Basic <auth_key>`, `RqUID: <uuid>`,
       body `scope=GIGACHAT_API_PERS` → returns {access_token, expires_at_ms}.
    2. Use Bearer token on /api/v1/chat/completions.

Token is short-lived (~30 min); we cache in-process with a 30-second safety margin
and a single-flight asyncio.Lock so concurrent requests don't stampede the OAuth
endpoint.

R-01 (deep module): callers see only `acompletion()` + `is_sber_model()`. The
OAuth dance and TLS quirks live entirely inside.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import httpx

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.errors import UpstreamProviderError, ValidationFailedError

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
COMPLETION_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

# Omnia model ID → GigaChat's own model name.
_GIGACHAT_MODEL_NAME: dict[str, str] = {
    "gigachat-2": "GigaChat",
    "gigachat-2-pro": "GigaChat-Pro",
    "gigachat-2-max": "GigaChat-Max",
}

# In-process token cache: (access_token, expires_at_unix_seconds)
_token_cache: tuple[str, float] | None = None
_token_lock = asyncio.Lock()


def is_sber_model(model_id: str) -> bool:
    return model_id in _GIGACHAT_MODEL_NAME


def reset_token_cache() -> None:
    """Test helper — clears the cached OAuth token."""
    global _token_cache
    _token_cache = None


def _client_kwargs(timeout: float) -> dict[str, Any]:
    """Build httpx.AsyncClient kwargs honoring the verify flag.

    Sber's OAuth endpoint (ngw.devices.sberbank.ru:9443) regularly takes 15-25s
    for the TLS handshake from RU VPSes — observed empirically. We use a
    generous connect timeout and a moderate read timeout.
    """
    return {
        "timeout": httpx.Timeout(timeout, connect=30.0, read=timeout, write=15.0),
        "verify": get_settings().gigachat_verify_ssl,
    }


def _fetch_token_sync(auth_key: str, scope: str, verify_ssl: bool) -> dict[str, Any]:
    """Synchronous OAuth call.

    Empirically `httpx.AsyncClient` inside the long-running uvicorn process
    keeps timing out the TCP+TLS handshake to ngw.devices.sberbank.ru:9443
    (some interaction with the event loop / DNS resolver), while a sync
    httpx.Client in a fresh thread works in ~250ms. Run this on a thread.
    """
    with httpx.Client(
        timeout=httpx.Timeout(45.0, connect=30.0),
        verify=verify_ssl,
    ) as client:
        resp = client.post(
            OAUTH_URL,
            headers={
                "Authorization": f"Basic {auth_key}",
                "RqUID": str(uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": scope},
        )
        resp.raise_for_status()
        return resp.json()


async def _get_token() -> str:
    global _token_cache
    async with _token_lock:
        now = time.time()
        if _token_cache is not None and _token_cache[1] > now + 30:
            return _token_cache[0]

        settings = get_settings()
        if not settings.gigachat_auth_key:
            raise UpstreamProviderError("GIGACHAT_AUTH_KEY not configured")

        try:
            data = await asyncio.to_thread(
                _fetch_token_sync,
                settings.gigachat_auth_key.get_secret_value(),
                settings.gigachat_scope,
                settings.gigachat_verify_ssl,
            )
        except httpx.HTTPStatusError as exc:
            print(
                f"[SBER] OAuth HTTP {exc.response.status_code}: {exc.response.text[:300]!r}",
                flush=True,
            )
            raise UpstreamProviderError(
                f"GigaChat OAuth HTTP {exc.response.status_code}",
                details={"body": exc.response.text[:500]},
            ) from exc
        except httpx.HTTPError as exc:
            import traceback as _tb
            print(
                f"[SBER] OAuth transport error type={type(exc).__name__} repr={exc!r}\n{_tb.format_exc()}",
                flush=True,
            )
            raise UpstreamProviderError(
                f"GigaChat OAuth transport error: {type(exc).__name__}: {exc}"
            ) from exc

        token = data.get("access_token")
        expires_ms = data.get("expires_at")
        if not token or not expires_ms:
            raise UpstreamProviderError(
                "GigaChat OAuth: malformed response", details={"body": str(data)[:500]}
            )
        _token_cache = (token, float(expires_ms) / 1000.0)
        return token


def _to_gigachat_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    role_map = {"system": "system", "user": "user", "assistant": "assistant"}
    out: list[dict[str, str]] = []
    for m in messages:
        role = role_map.get(m["role"])
        if role is None:
            raise ValidationFailedError(f"unsupported role: {m['role']}")
        out.append({"role": role, "content": m["content"]})
    return out


async def acompletion(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    timeout: float = 60.0,  # noqa: ASYNC109 — passed to httpx.AsyncClient
) -> dict[str, Any]:
    """Call GigaChat and return an OpenAI-shaped completion dict."""
    gigachat_name = _GIGACHAT_MODEL_NAME.get(model)
    if gigachat_name is None:
        raise ValidationFailedError(f"unsupported Sber model: {model}")

    token = await _get_token()
    payload: dict[str, Any] = {
        "model": gigachat_name,
        "messages": _to_gigachat_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def _completion_sync() -> dict[str, Any]:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=30.0),
            verify=get_settings().gigachat_verify_ssl,
        ) as client:
            r = client.post(COMPLETION_URL, json=payload, headers=headers)
            r.raise_for_status()
            return r.json()

    try:
        data = await asyncio.to_thread(_completion_sync)
    except httpx.HTTPStatusError as exc:
        print(
            f"[SBER] completion HTTP {exc.response.status_code}: {exc.response.text[:300]!r}",
            flush=True,
        )
        raise UpstreamProviderError(
            f"GigaChat HTTP {exc.response.status_code}",
            details={"body": exc.response.text[:500]},
        ) from exc
    except httpx.HTTPError as exc:
        import traceback as _tb
        print(
            f"[SBER] completion transport error {type(exc).__name__} {exc!r}\n{_tb.format_exc()}",
            flush=True,
        )
        raise UpstreamProviderError(
            f"GigaChat transport error: {type(exc).__name__}: {exc}"
        ) from exc

    # GigaChat already returns OpenAI-shaped {choices, usage}; just normalize the
    # outward-facing fields and remap `model` to our omnia ID for billing.
    data["model"] = model
    data.setdefault("id", f"gigachat-{uuid4()}")
    data.setdefault("object", "chat.completion")
    data.setdefault("created", int(time.time()))
    return data
