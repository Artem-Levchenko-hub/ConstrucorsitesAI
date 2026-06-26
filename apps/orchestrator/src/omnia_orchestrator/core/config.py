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

    # ── Sandbox hardening (Phase 1, security) ───────────────────────────────
    # The agent (USE_AGENTIC_BUILDER) runs ARBITRARY code + bash inside dev
    # containers, so a dev container is an UNTRUSTED boundary, not a convenience.
    # These knobs tighten that boundary WITHOUT a code deploy. Every default
    # preserves today's behaviour byte-for-byte, so the feature ships dark
    # (R-10 instant rollback); flip per-env once the host is prepared.
    #
    # `container_runtime` — passed to `docker run --runtime`. Set to "runsc"
    # (gVisor) to run user containers under a userspace kernel that intercepts
    # syscalls, so a container escape never reaches the host kernel. The daemon
    # must have the runtime registered (/etc/docker/daemon.json) FIRST; until
    # then leave empty = the daemon default (runc, current behaviour).
    container_runtime: str = Field(default="")
    # `container_harden` — host-free, in-container hardening safe for our
    # non-root Node/Python images: `no-new-privileges` (a setuid binary cannot
    # re-grant the caps we dropped) + a PID ceiling (fork-bomb guard). Off by
    # default so prod is unchanged until validated on a live container.
    container_harden: bool = Field(default=False)
    # PID ceiling applied only when `container_harden` is on. 512 is generous
    # for a Next.js/Turbopack dev server plus the agent's occasional pnpm/bash,
    # while still stopping a runaway fork bomb from exhausting host PIDs.
    container_pids_limit: int = Field(default=512)

    # `container_egress_proxy` — when set (e.g. "http://omnia-egress:3128") every
    # user container gets HTTP(S)_PROXY + NO_PROXY injected, so ALL outbound
    # traffic is forced through an allowlisting proxy (run a squid/tinyproxy with
    # a host whitelist — see the Phase-1 runbook). Empty = direct egress
    # (current behaviour). This is the real egress allowlist: the agent's bash
    # can no longer exfiltrate to or call arbitrary hosts.
    container_egress_proxy: str = Field(default="")
    # Hosts that bypass the egress proxy — the internal services a container must
    # still reach directly (DB / gateway / MinIO / loopback). Only consulted when
    # `container_egress_proxy` is set.
    container_egress_no_proxy: str = Field(
        default=(
            "localhost,127.0.0.1,host.docker.internal,"
            "omnia-postgres-users,omnia-prod-gw,omnia-prod-minio"
        )
    )
    # `isolate_project_network` — when True each dev container joins its OWN
    # `omnia-proj-<id>` bridge network instead of the shared runtime network, so
    # a compromised container can't reach OTHER projects' containers laterally.
    # Requires the shared services be reachable via host.docker.internal + the
    # egress proxy (runbook) — until then leave False (shared net, current
    # behaviour).
    isolate_project_network: bool = Field(default=False)

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

    # Where the per-project nginx vhost forwards a 502 (dead/hibernated
    # upstream) so the wake-on-request interstitial can boot the container and
    # serve a "waking up" page instead of a raw Bad Gateway. This is the
    # orchestrator's own HTTP bind, reached over loopback from the shared nginx.
    orchestrator_wake_target: str = Field(default="127.0.0.1:8003")

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
