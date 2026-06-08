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

    # Wildcard pattern: `<slug>.preview.${base_domain}` for dev,
    # `<slug>.app.${base_domain}` for prod. Both wildcards need DNS + cert.
    base_domain: str = Field(default="omniadevelop.ru")

    # Public hostname suffix for user-facing dev/prod URLs. Default is the
    # sslip.io wildcard that resolves "<anything>.170-168-72-200.sslip.io" to
    # this VPS with ZERO registrar setup — used until proper wildcard DNS for
    # *.app.omniadevelop.ru exists. Switch to "app.omniadevelop.ru" then.
    #   dev preview → "{slug}-dev.{suffix}"   prod deploy → "{slug}.{suffix}"
    runtime_host_suffix: str = Field(default="170-168-72-200.sslip.io")

    # Per-host Let's Encrypt (HTTP-01 via webroot). No DNS API token needed
    # because sslip.io hosts already resolve to us. Fail-soft: if a cert can't
    # be issued the site stays HTTP-only rather than failing the whole flow.
    enable_tls: bool = Field(default=True)
    acme_email: str = Field(default="artem@omniadevelop.ru")
    # Webroot for ACME http-01 challenges — orchestrator-owned (no sudo to
    # write). nginx serves /.well-known/acme-challenge/ from here. Certs are
    # issued by acme.sh (the system certbot 2.1.0 is broken on this box).
    acme_webroot: str = Field(default="/opt/omnia-runtime/acme-webroot")
    # Where acme.sh installs issued certs (orchestrator-owned; nginx reads).
    acme_certs_dir: str = Field(default="/opt/omnia-runtime/certs")

    # Dev container port pool. 3001-3199 reserved for V1 + other tenants.
    port_range_min: int = Field(default=3200)
    port_range_max: int = Field(default=3999)

    # Prod (deployed) container port pool — separate range so a project's
    # dev and prod containers never collide on a host port.
    prod_port_range_min: int = Field(default=4000)
    prod_port_range_max: int = Field(default=4999)

    # Hibernate policy (minutes of inactivity before pause/stop).
    hibernate_free_tier_minutes: int = Field(default=15)
    hibernate_pro_tier_minutes: int = Field(default=60)
    wake_timeout_seconds: int = Field(default=60)

    # Memory ceiling for dev preview containers. Heavy entity/fullstack apps
    # (Next.js + Turbopack compiling many routes) blew past the old 2 GB limit
    # and were OOM-killed mid-compile. This is a ceiling, not a reservation —
    # a light project still settles around 500 MB; only a heavy compile climbs
    # toward it. Tune per host (or per tier later) via env without a code edit.
    dev_container_memory_mb: int = Field(default=4096)

    # Redis pub-sub channel for hibernate activity. Whoever fronts dev preview
    # traffic (apps/api proxy / nginx ingress) publishes `activity:<project_id>`
    # on every request; the hibernate loop subscribes to reset idle timers.
    # Default points at the shared `omnia-redis` (same instance apps/api uses).
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")

    # Shared secret with apps/api. Validated against X-Internal-Token header.
    internal_token: SecretStr

    # Sentry — leave empty to disable.
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=0.1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
