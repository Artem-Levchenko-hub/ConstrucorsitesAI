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

import shutil
from pathlib import Path

import structlog

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import ContainerSpec, start_container
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.schemas.runtime import (
    ProvisionRequest,
    ProvisionResponse,
)
from omnia_orchestrator.services import nginx_writer
from omnia_orchestrator.services.port_allocator import get_port_allocator

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

    # Sprint A1 will inject a real DATABASE_URL pointing at the per-project
    # schema. For PoC we hand the template a syntactically-valid placeholder
    # so the Pool constructor doesn't throw at import — the landing page
    # renders without touching the DB.
    env = {
        "DATABASE_URL": "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder",
        "NODE_ENV": "development",
        **req.initial_env,
    }

    # Next.js 15 + Turbopack peaks at ~1.5 GB during the first compile of a
    # cold project; once warm it settles around 500-800 MB. 512 MB will be
    # OOM-killed mid-compile. Sprint A1 will pick limits per tier (free 1 GB,
    # pro 2 GB, business 4 GB); PoC picks the pro-tier ceiling for everyone.
    spec = ContainerSpec(
        name=container_name,
        image=image_tag,
        port=port,
        project_id=str(req.project_id),
        env=env,
        cpu_quota=1.0,
        memory_mb=2048,
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

    return ProvisionResponse(
        project_id=req.project_id,
        container_name=container_name,
        port=port,
        dev_url=dev_url,
        state="running",
    )
