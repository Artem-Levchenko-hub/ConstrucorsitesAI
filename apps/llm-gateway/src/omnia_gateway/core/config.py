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

    # proxyapi.ru — Russian proxy that fronts native Anthropic Messages API.
    # The same balance covers all proxyapi-routed models; per-model routing is
    # declared in _PROXY_ROUTES in services/litellm_router.py.
    proxyapi_api_key: SecretStr | None = None
    # Anthropic provider in LiteLLM appends /v1/messages itself; do not include /v1 here.
    proxyapi_base_url: str = "https://api.proxyapi.ru/anthropic"

    # Sber GigaChat — auth key is base64(client_id:client_secret) from Sber developer cabinet.
    # Sber's API uses the Russian Trusted Root CA, which most Python builds don't trust by
    # default — set GIGACHAT_VERIFY_SSL=false locally if you don't have the cert installed.
    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"  # or GIGACHAT_API_CORP
    gigachat_verify_ssl: bool = False

    database_url: str = "postgresql://omnia:omnia@localhost:5432/omnia"
    redis_url: str = "redis://localhost:6379/1"

    default_model: str = "claude-sonnet-4-6"
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
