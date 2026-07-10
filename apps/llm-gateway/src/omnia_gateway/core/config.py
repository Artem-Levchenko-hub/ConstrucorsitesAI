"""Centralized environment-driven configuration.

R-02 (hide what changes): all env access goes through `get_settings()`. If we
later swap pydantic-settings for vault / SSM, only this module changes.

Provider model: ONE upstream — **oneprovider.dev** — serves the whole product,
exactly as its docs describe (https://oneprovider.dev/llms.txt):

  * Anthropic-native surface  `https://api.oneprovider.dev`      → `/v1/messages`
    (the native tool-use agent, `x-api-key`, thinking + signatures preserved).
  * OpenAI-compatible surface `https://api.oneprovider.dev/v1`   → `/v1/chat/completions`
    (chat + streaming + image generation, `Authorization: Bearer`).

The same `ONEPROVIDER_API_KEY` authenticates both surfaces. `proxyapi.ru` remains
ONLY for the two capabilities oneprovider does not serve: speech-to-text
(whisper) and the optional `gpt-image-1` image model.
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

    # --- oneprovider.dev — the single LLM upstream (Claude models + flux images) ---
    # Key authenticates BOTH documented surfaces below. Flows in via env
    # ONEPROVIDER_API_KEY, never committed.
    oneprovider_api_key: SecretStr | None = None
    # Anthropic-native surface — the native tool-use agent posts RAW `/v1/messages`
    # here (routers/messages_native.py). The `anthropic` contract appends
    # `/v1/messages`, so this base carries NO `/v1`.
    oneprovider_base_url: str = "https://api.oneprovider.dev"
    # OpenAI-compatible surface — chat completions, streaming, and image generation
    # (providers/oneprovider.py, routers/images.py). The base carries `/v1`.
    oneprovider_openai_base_url: str = "https://api.oneprovider.dev/v1"

    # --- proxyapi.ru — ONLY for what oneprovider does not serve ---
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

    # Read timeout for one completion. oneprovider's Anthropic surface can spend up
    # to ~30-70s when it thinks; 240s tolerates the spike while staying under the api
    # llm_client's 300s read timeout so a genuine hang still surfaces cleanly.
    # Env: REQUEST_TIMEOUT_SECONDS.
    request_timeout_seconds: int = 240

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
