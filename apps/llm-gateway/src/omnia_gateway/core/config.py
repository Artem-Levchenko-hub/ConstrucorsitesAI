"""Centralized environment-driven configuration.

R-02 (hide what changes): all env access goes through `get_settings()`. If we
later swap pydantic-settings for vault / SSM, only this module changes.

Provider model: ONE upstream — **aitunnel.ru** — serves the whole product,
exactly as its docs describe (https://docs.aitunnel.ru/). Everything lives under
`https://api.aitunnel.ru/v1`:

  * `/v1/chat/completions` — OpenAI-compatible chat + streaming,
  * `/v1/messages`         — Anthropic-native surface (the native tool-use agent,
    thinking + signatures preserved),
  * `/v1/images/generations` — image generation (flux).

The same `AITUNNEL_API_KEY` authenticates every endpoint, and BOTH surfaces use
`Authorization: Bearer` (the native one rejects `x-api-key`). `proxyapi.ru`
remains ONLY for speech-to-text (whisper) and the optional `gpt-image-1` model.
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

    # --- aitunnel.ru — the single LLM upstream (Claude models + flux images) ---
    # ONE key + ONE base for every endpoint: chat (`/chat/completions`), native
    # Anthropic (`/messages`), images (`/images/generations`). Flows in via env
    # AITUNNEL_API_KEY, never committed.
    aitunnel_api_key: SecretStr | None = None
    aitunnel_base_url: str = "https://api.aitunnel.ru/v1"

    # --- proxyapi.ru — ONLY for whisper speech-to-text (+ legacy gpt-image) ---
    # Speech-to-text (whisper) + the optional gpt-image-1 image model. Same key +
    # balance for both. The openai contract expects the `/v1` already on the base.
    proxyapi_api_key: SecretStr | None = None
    proxyapi_openai_base_url: str = "https://api.proxyapi.ru/openai/v1"

    database_url: str = "postgresql://omnia:omnia@localhost:5432/omnia"
    redis_url: str = "redis://localhost:6379/1"

    # Speech-to-text (voice prompt dictation) upstream model on proxyapi's OpenAI
    # surface. whisper-1 is the cheap, RU-capable default; swap to
    # gpt-4o-mini-transcribe for higher quality. Env: TRANSCRIBE_MODEL.
    transcribe_model: str = "whisper-1"

    safety_filter_enabled: bool = True
    cache_ttl_seconds: int = 3600
    min_balance_rub: float = 5.0

    # Read timeout for one completion. A thinking-heavy Opus pass can spend tens of
    # seconds; 240s tolerates the spike while staying under the api llm_client's
    # 300s read timeout so a genuine hang still surfaces cleanly.
    # Env: REQUEST_TIMEOUT_SECONDS.
    request_timeout_seconds: int = 240

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
