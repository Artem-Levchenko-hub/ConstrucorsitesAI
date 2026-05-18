"""Orchestrator config — env-driven via pydantic-settings.

R-02 (hide what changes): one Settings class is the only file that knows
how config reaches code. Swap pydantic-settings for vault/SSM later by
editing this module only.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="dev")
    log_level: str = Field(default="INFO")

    # Shared Postgres for *user* projects (NOT omnia-mvp app DB).
    database_url: str

    # Docker daemon socket. On prod Linux: unix:///var/run/docker.sock.
    docker_host: str = Field(default="unix:///var/run/docker.sock")

    # Filesystem layout on the VPS — see docs/08-vps-setup.md.
    projects_root: str = Field(default="/opt/omnia-runtime/projects")
    nginx_sites_dir: str = Field(default="/opt/omnia-runtime/nginx/sites-enabled")
    secrets_root: str = Field(default="/opt/omnia-runtime/secrets")

    # Local Docker registry for prod images.
    registry_url: str = Field(default="127.0.0.1:5000")

    # Wildcard cert domain — `<slug>.${base_domain}` for prod, `<slug>-dev.${base_domain}` for dev.
    base_domain: str = Field(default="omnia.app")

    # Dev container port pool. nginx maps slug → port from here.
    port_range_min: int = Field(default=3001)
    port_range_max: int = Field(default=3999)

    # Hibernate policy (minutes of inactivity before pause/stop).
    hibernate_free_tier_minutes: int = Field(default=15)
    hibernate_pro_tier_minutes: int = Field(default=60)
    wake_timeout_seconds: int = Field(default=60)

    # Shared secret with apps/api. Validated against X-Internal-Token header.
    internal_token: SecretStr

    # Sentry — leave empty to disable.
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=0.1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
