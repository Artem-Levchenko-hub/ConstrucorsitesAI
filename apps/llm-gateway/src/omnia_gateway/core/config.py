"""Centralized environment-driven configuration.

R-02 (hide what changes): all env access goes through `get_settings()`. If we
later swap pydantic-settings for vault / SSM, only this module changes.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    yandex_api_key: SecretStr | None = None
    yandex_folder_id: str | None = None
    openrouter_api_key: SecretStr | None = None
    # Google Gemini API key from Google AI Studio (https://aistudio.google.com/apikey).
    # Free tier covers gemini-2.5-pro (low RPM/RPD) and gemini-2.5-flash (higher quota);
    # the same key transparently bills the paid tier once a billing project is attached.
    gemini_api_key: SecretStr | None = None

    # VseGPT (vsegpt.ru) — Russian OpenAI-compatible aggregator. Keys look like
    # `sk-or-vv-…`. Fronts many models incl. deepseek/deepseek-v4-flash-thinking;
    # routed as an OpenAI endpoint via _PROXY_ROUTES in litellm_router.
    vsegpt_api_key: SecretStr | None = None
    vsegpt_base_url: str = "https://api.vsegpt.ru/v1"

    # proxyapi.ru — Russian proxy that fronts both Anthropic Messages API and
    # OpenAI's chat-completions surface. The same key + same balance cover
    # every proxyapi-routed model (Claude Haiku, GPT-5 family, etc.) —
    # per-model routing is declared in `_PROXY_ROUTES` in
    # `services/litellm_router.py`.
    proxyapi_api_key: SecretStr | None = None
    # Anthropic provider in LiteLLM appends /v1/messages itself; do not include /v1 here.
    proxyapi_base_url: str = "https://api.proxyapi.ru/anthropic"
    # OpenAI-compatible surface on proxyapi: GPT-5 family + GPT-4o family.
    # LiteLLM's openai provider expects the `/v1` suffix already on the base.
    proxyapi_openai_base_url: str = "https://api.proxyapi.ru/openai/v1"
    # DeepSeek (deepseek-chat = V3, deepseek-reasoner = R1) via proxyapi's
    # OpenAI-compatible surface — same key + balance as Anthropic/OpenAI above.
    # LiteLLM's openai provider appends the path itself, so the base carries /v1.
    # Verify the exact path against the proxyapi dashboard before prod billing.
    proxyapi_deepseek_base_url: str = "https://api.proxyapi.ru/deepseek/v1"

    # oneprovider.dev — native Anthropic-Messages endpoint serving claude-opus-4-8
    # (owner key, 2026-06-30). Tested: HTTP 200 + prompt caching (cache_read /
    # cache_creation tokens). LiteLLM's `anthropic` provider appends /v1/messages,
    # so the base carries NO /v1. Routed in `_PROXY_ROUTES` (litellm_router.py);
    # the key flows in via env ONEPROVIDER_API_KEY, never committed.
    oneprovider_api_key: SecretStr | None = None
    oneprovider_base_url: str = "https://api.oneprovider.dev"

    # Warmup keep-alive loop (services/warmup.py) exists ONLY to defeat
    # proxyapi.ru's ~5-min idle cold-start (Haiku/GPT-5-nano returning near-empty
    # on the first call after idle). proxyapi is retired — every role now routes
    # via vsegpt, which opens a FRESH sync httpx.Client per call, so there is no
    # warm upstream session to keep alive and the loop just pings dead routes
    # every 4 min. Default OFF; set ENABLE_WARMUP=true only if a proxyapi-backed
    # model is reactivated. `run_warmup_loop` also hard-skips when no proxyapi key
    # is configured, so this flag is a belt-and-suspenders explicit control.
    enable_warmup: bool = False

    # Sber GigaChat — auth key is base64(client_id:client_secret) from Sber developer cabinet.
    # Sber's API uses the Russian Trusted Root CA, which most Python builds don't trust by
    # default — set GIGACHAT_VERIFY_SSL=false locally if you don't have the cert installed.
    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"  # or GIGACHAT_API_CORP
    gigachat_verify_ssl: bool = False

    database_url: str = "postgresql://omnia:omnia@localhost:5432/omnia"
    redis_url: str = "redis://localhost:6379/1"

    default_model: str = "claude-sonnet-4-6"
    # Speech-to-text (voice prompt dictation). proxyapi's OpenAI surface exposes
    # whisper-1 + gpt-4o-(mini-)transcribe, reachable from the RU prod box. whisper-1
    # is the cheap, battle-tested RU-capable default; swap to gpt-4o-mini-transcribe
    # for higher quality. Routed direct (not LiteLLM), like /v1/images/generations.
    transcribe_model: str = "whisper-1"
    safety_filter_enabled: bool = True
    cache_ttl_seconds: int = 3600
    min_balance_rub: float = 5.0
    request_timeout_seconds: int = 60

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
