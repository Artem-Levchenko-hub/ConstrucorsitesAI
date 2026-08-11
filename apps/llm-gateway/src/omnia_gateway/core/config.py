"""Centralized environment-driven configuration.

R-02 (hide what changes): all env access goes through `get_settings()`. If we
later swap pydantic-settings for vault / SSM, only this module changes.

General text generation runs through **llmgw.ru**. MAX Studio's Sonnet 5 route
uses AITunnel's OpenAI-compatible chat endpoint so provider credentials fail
independently. The native-agent endpoint adapts Anthropic-shaped tool turns to
the selected provider's OpenAI tool-calling contract.

AITunnel also serves optional image/video generation. `proxyapi.ru` remains only
for speech-to-text (whisper) and the optional `gpt-image-1` model.
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

    # --- llmgw.ru — text, vision, and tool-calling LLM upstream ---
    llmgw_api_key: SecretStr | None = None
    llmgw_base_url: str = "https://api.llmgw.ru/v1"

    # --- aitunnel.ru — optional image/video upstream ---
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

    # Native-agent financial fuse. It is independent from the restored bounded
    # Sonnet loop and applies to free runs too: ``free`` means the customer
    # wallet is not debited, not that provider spend is unlimited.
    # A complete MAX build can legitimately need several long composition and
    # repair calls. Keep a generous per-run envelope; the independent request
    # count remains the absolute emergency brake against an infinite loop.
    native_run_max_cost_rub: float = 5000.0
    native_run_max_provider_cost_usd: float = 10.0
    native_run_max_requests: int = 160

    # Read timeout for one long agentic pass, which can spend tens of
    # seconds; 240s tolerates the spike while staying under the api llm_client's
    # 300s read timeout so a genuine hang still surfaces cleanly.
    # Env: REQUEST_TIMEOUT_SECONDS.
    request_timeout_seconds: int = 240

    # Native tool responses are consumed as SSE inside the gateway. This is an
    # idle-between-chunks timeout, not a whole-response deadline; the API caller
    # has a strictly larger read window so the gateway can always return a
    # classified terminal error instead of losing the socket first.
    native_response_idle_timeout_seconds: int = 600
    native_response_total_timeout_seconds: int = 1200
    native_turn_cache_ttl_seconds: int = 86400

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
