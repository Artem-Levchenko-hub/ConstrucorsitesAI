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

    # Native tool-use agent (/v1/messages) upstream (owner 2026-07-01): vsegpt —
    # same ~3s no-thinking Opus as the /v1/chat/completions path. vsegpt has no
    # native Anthropic endpoint, so providers/vsegpt_native.py adapts the shapes
    # (Anthropic Messages ⇄ OpenAI chat). Kill switch NATIVE_VIA_VSEGPT=false
    # reverts to the raw oneprovider passthrough (forced thinking, ~71s/call).
    native_via_vsegpt: bool = True

    # Master kill switch for the Opus-4.8→vsegpt route (owner 2026-07-02). Default
    # True = the fast ~3s vsegpt path (both /v1/chat/completions AND the native
    # /v1/messages agent go through `is_vsegpt_model`). Set OPUS_VIA_VSEGPT=false to
    # pin Opus back to oneprovider (~71s/call, forced thinking) WITHOUT a rebuild —
    # the reversible failover for when the vsegpt balance runs dry (every call →
    # HTTP 400 "out of budget", which aborts the build as «Сборка прервана»). Flip
    # back to true once the balance is topped up; env-only, no code change.
    opus_via_vsegpt: bool = True

    database_url: str = "postgresql://omnia:omnia@localhost:5432/omnia"
    redis_url: str = "redis://localhost:6379/1"

    # Speech-to-text (voice prompt dictation). proxyapi's OpenAI surface exposes
    # whisper-1 + gpt-4o-(mini-)transcribe, reachable from the RU prod box. whisper-1
    # is the cheap, battle-tested RU-capable default; swap to gpt-4o-mini-transcribe
    # for higher quality. Routed direct (not LiteLLM), like /v1/images/generations.
    transcribe_model: str = "whisper-1"
    safety_filter_enabled: bool = True
    cache_ttl_seconds: int = 3600
    min_balance_rub: float = 5.0
    # LiteLLM Router timeout for one completion. oneprovider.dev (the current Opus
    # provider) forces extended thinking → up to ~67s/call, which blew the old 60s
    # and killed the build planner ("Timeout on reading data from socket" → empty
    # plan). 240s tolerates the spike while staying under the api llm_client's 300s
    # read timeout so a genuine hang still surfaces cleanly. Env: REQUEST_TIMEOUT_SECONDS.
    request_timeout_seconds: int = 240

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
