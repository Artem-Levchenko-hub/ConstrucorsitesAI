"""Minimum-viable provisioner for V2 PoC.

What it does today:
  - allocate a host port via `port_allocator`
  - copy the template tree into `{projects_root}/<project_id>/` (so the AI
    can later write files there for HMR)
  - start a Docker container from the prebuilt `omnia-template-<template>:dev`
    image, bound to `127.0.0.1:<port>`
  - return a ProvisionResponse with a dev URL

What it deliberately skips (sprint A1 territory):
  - Postgres schema + per-project role + DATABASE_URL injection
  - nginx site generation + reload (PoC reaches the container via IP:port)
  - per-project Docker network
  - health-poll until the container is "Ready"
  - secrets keystore wiring

Keeping the contract identical to the production version (ProvisionRequest →
ProvisionResponse) means apps/api can call this today and stays unchanged when
sprint A1 swaps the body.
"""

from __future__ import annotations

import os
import secrets as _secrets
import shutil
from pathlib import Path

import structlog

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import ContainerSpec, start_container
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.event_publisher import publish_project_event
from omnia_orchestrator.schemas.runtime import (
    ProvisionRequest,
    ProvisionResponse,
)
from omnia_orchestrator.services import nginx_writer
from omnia_orchestrator.services.port_allocator import get_port_allocator

# Fallback DSN — syntactically valid, points nowhere. Used only when Postgres
# schema provisioning fails (degraded mode): the template's db module still
# imports cleanly, the static landing page still renders, and the failure
# surfaces only when AI-generated code actually queries the DB.
_DB_FALLBACK = "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"


def _integration_env() -> dict[str, str]:
    """Env for the Base44-style "Core" integrations injected into every user
    container. Containers reach MinIO + the LLM gateway CONTAINER-TO-CONTAINER
    over the runtime network (their host binds are 127.0.0.1-only, unreachable
    from a container). Values come from the orchestrator env with prod-shaped
    defaults; the MinIO secret + public URL must be set in the orchestrator env
    for UploadFile to work (see docs/08-vps-setup.md). SMTP is opt-in — absent →
    SendEmail stubs. LLM_GATEWAY_URL is injected now for the later InvokeLLM/
    GenerateImage pass.
    """
    out: dict[str, str] = {
        "MINIO_ENDPOINT": os.getenv("OMNIA_MINIO_ENDPOINT", "omnia-prod-minio:9000"),
        "MINIO_ACCESS_KEY": os.getenv("OMNIA_MINIO_ACCESS_KEY", "omnia"),
        "MINIO_BUCKET": os.getenv("OMNIA_MINIO_UPLOAD_BUCKET", "omnia-user-uploads"),
        "MINIO_SECURE": os.getenv("OMNIA_MINIO_SECURE", "false"),
        "MINIO_PUBLIC_URL": os.getenv("OMNIA_MINIO_PUBLIC_URL", ""),
        "LLM_GATEWAY_URL": os.getenv("OMNIA_LLM_GATEWAY_URL", "http://omnia-prod-gw:8001"),
    }
    secret = os.getenv("OMNIA_MINIO_SECRET_KEY")
    if secret:
        out["MINIO_SECRET_KEY"] = secret
    for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"):
        val = os.getenv(f"OMNIA_{key}")
        if val:
            out[key] = val
    return out


def _load_or_create_auth_secret(project_id: str) -> str:
    """Auth.js v5 `AUTH_SECRET` — per-project, persisted under
    ``secrets_root/<project_id>/auth.secret`` so re-provisions reuse the
    same value and existing sessions survive a container restart.

    Rotating this secret invalidates every active session for that
    project's app — intentional fallback if a secret leaks.
    """
    secrets_dir = Path(get_settings().secrets_root) / project_id
    secret_file = secrets_dir / "auth.secret"
    if secret_file.exists():
        content = secret_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = _secrets.token_urlsafe(48)
    secret_file.write_text(value, encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass  # Windows dev path
    return value

log = structlog.get_logger("omnia_orchestrator.provisioner")


def _template_source_dir(template: str) -> Path:
    """Resolve the template directory inside the orchestrator source tree.

    Layout: apps/orchestrator/templates/<template>/. The orchestrator source
    is installed at /opt/omnia-runtime/source/apps/orchestrator (see
    docs/08-vps-setup.md), so `__file__` is two parents below the templates
    directory.
    """
    here = Path(__file__).resolve()
    # services/provisioner.py → omnia_orchestrator/ → src/ → apps/orchestrator/
    candidate = here.parents[3] / "templates" / template
    if not candidate.is_dir():
        raise OrchestratorError(
            code="not_found",
            message=f"template not found: {template} (looked at {candidate})",
            status_code=404,
        )
    return candidate


def _copy_template(src: Path, dest: Path) -> None:
    """Copy template tree, skipping node_modules / .next / .git / __pycache__."""
    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in {"node_modules", ".next", ".git", "__pycache__"}]

    shutil.copytree(src, dest, ignore=_ignore, dirs_exist_ok=True)


async def provision(req: ProvisionRequest) -> ProvisionResponse:
    settings = get_settings()
    log.info(
        "provision.start",
        project_id=str(req.project_id),
        slug=req.slug,
        template=req.template,
        tier=req.tier,
    )

    src = _template_source_dir(req.template)

    project_dir = Path(settings.projects_root) / str(req.project_id)
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    _copy_template(src, project_dir)
    log.info("provision.template_copied", dest=str(project_dir))

    port = await get_port_allocator().acquire(req.project_id)
    log.info("provision.port_acquired", port=port)

    container_name = f"omnia-dev-{req.slug}"
    image_tag = f"omnia-template-{req.template}:dev"

    # Real per-project DSN — reuse persisted creds on re-provision, otherwise
    # create a fresh schema + role on `omnia-postgres-users`. Fail-soft: if
    # schema provisioning errors out we still hand the template a syntactically
    # valid placeholder so the Pool constructor doesn't throw at import. The
    # static landing renders either way; the DB-backed routes break only when
    # AI generates them on top of a degraded provision.
    database_url = postgres_admin.load_existing_dsn(req.project_id)
    if database_url is None:
        try:
            creds = await postgres_admin.create_schema(req.project_id)
            database_url = creds.dsn
        except Exception as exc:
            log.warning(
                "provision.db_fallback",
                project_id=str(req.project_id),
                err=str(exc),
            )
            database_url = _DB_FALLBACK

    # AUTH_SECRET — Auth.js v5 cookie/token signing key. Stable per-project
    # so a container restart doesn't log every user out. AUTH_URL helps
    # Auth.js build absolute callback URLs when running behind our nginx
    # proxy (it can't infer the public origin from x-forwarded headers in
    # all paths). AUTH_TRUST_HOST is required when the host header doesn't
    # match a known-safe domain — our preview/prod URLs are dynamic so
    # we trust the host explicitly.
    auth_secret = _load_or_create_auth_secret(str(req.project_id))
    dev_origin = nginx_writer.dev_url(req.slug)

    env = {
        "DATABASE_URL": database_url,
        "NODE_ENV": "development",
        "AUTH_SECRET": auth_secret,
        "AUTH_URL": dev_origin,
        "AUTH_TRUST_HOST": "true",
        **_integration_env(),
        **req.initial_env,
    }

    # Next.js 15 + Turbopack peaks well past 2 GB during the first compile of a
    # heavy entity/fullstack app (many routes); once warm it settles around
    # 500-800 MB. A 2 GB ceiling OOM-killed those mid-compile, so the memory
    # limit is config-driven (default 4 GB — a ceiling, not a reservation).
    #
    # restart_policy `unless-stopped` makes a crashed dev server (non-zero exit)
    # self-heal: docker re-runs it automatically. Hibernation is unaffected —
    # docker only restarts containers that exited on their own, never ones the
    # daemon API stopped/paused, so an idle-sweep `stop` stays down until /wake.
    spec = ContainerSpec(
        name=container_name,
        image=image_tag,
        port=port,
        project_id=str(req.project_id),
        env=env,
        cpu_quota=1.0,
        memory_mb=settings.dev_container_memory_mb,
        restart_policy_name="unless-stopped",
        tier=req.tier,
    )

    container_id = await start_container(spec)
    log.info("provision.container_started", id=container_id[:12], name=container_name)

    # Expose the dev container at a browser-reachable host via nginx.
    # 127.0.0.1:<port> is the VPS loopback — unreachable from the user's
    # browser (that was the "connection refused" preview). publish_http is
    # fast (~1-2s); the TLS upgrade runs in the background so provision stays
    # within the api call budget. Fail-soft: on nginx failure fall back to the
    # loopback URL so provision still succeeds.
    host = nginx_writer.dev_host(req.slug)
    try:
        await nginx_writer.publish_http(host, port)
        nginx_writer.publish_tls_in_background(host, port)
        dev_url = nginx_writer.dev_url(req.slug)
    except OrchestratorError as exc:
        log.warning("provision.nginx_failed", host=host, err=exc.message)
        dev_url = f"http://127.0.0.1:{port}"

    response = ProvisionResponse(
        project_id=req.project_id,
        container_name=container_name,
        port=port,
        dev_url=dev_url,
        state="running",
    )

    # Live UI: tell the workspace the container is up. Frontend's
    # `usePromptStream` flips ["runtime", projectId] react-query cache from
    # state=provisioning → running on this, so the iframe swaps from the
    # startup spinner to the live dev URL without polling.
    await publish_project_event(
        str(req.project_id),
        "runtime.started",
        {
            "runtime": {
                "project_id": str(req.project_id),
                "state": "running",
                "container_name": container_name,
                "port": port,
                "dev_url": dev_url,
            },
        },
    )

    return response
