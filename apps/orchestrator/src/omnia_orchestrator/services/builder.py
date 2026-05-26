"""Deploy pipeline — build a prod image from the LIVE dev container and serve it.

R-01 (deep module): the surface is `start_deploy(project_id, slug)`. It kicks
off the slow build+run+publish in a background task and returns the in-flight
`DeployRecord` immediately (phase=building) so the apps/api request returns
within its timeout. Progress lands in `deploy_state`; the public prod URL is
deterministic so it can be shown before the build finishes.

Why build from the container, not git: hot-reload writes AI files straight
into the dev container (`docker cp`), so the container — not any git tree — is
the source of truth for "what the user sees". We seed the build context from
the template (Dockerfile.prod + configs) and overlay the live app files.

R-10 (stability): every step has a timeout (build, health-poll, cert) and the
whole run is wrapped so a failure records phase=failed with a reason rather
than crashing the orchestrator.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from uuid import UUID

import structlog

from omnia_orchestrator.core import docker_client, postgres_admin
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services import deploy_state, nginx_writer
from omnia_orchestrator.services.port_allocator import get_prod_port_allocator
from omnia_orchestrator.services.provisioner import _template_source_dir

log = structlog.get_logger("omnia_orchestrator.builder")

# App files overlaid from the live dev container on top of the template seed.
# Best-effort: missing paths are skipped. Covers everything the AI realistically
# edits in the nextjs-postgres-drizzle template (pages live under src/).
_OVERLAY_PATHS = [
    "src",
    "public",
    "package.json",
    "next.config.ts",
    "tsconfig.json",
    "drizzle.config.ts",
    "drizzle",
    "components.json",
    "postcss.config.mjs",
    "tailwind.config.ts",
]

# Build-time placeholder. `next build` collects route data and imports the db
# module — without DATABASE_URL the import throws. The `output: standalone`
# runtime never reads `.env.production`, so this string never reaches a live
# request. Runtime DSN is resolved from the per-project secrets file (see
# `_resolve_runtime_dsn`) and overrides this placeholder in the container env.
_DB_PLACEHOLDER = "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder"


def _resolve_runtime_dsn(project_id: str) -> str:
    """Use the dev container's persisted DSN for prod — same schema, same role.
    Falls back to the placeholder so a project provisioned in degraded mode
    still deploys (DB-backed routes will surface the underlying issue at first
    query, same UX as dev)."""
    from uuid import UUID

    dsn = postgres_admin.load_existing_dsn(UUID(project_id))
    return dsn or _DB_PLACEHOLDER

_TEMPLATE = "nextjs-postgres-drizzle"

# Prod build config the orchestrator forces into every deploy. AI-generated
# code very often has TS/ESLint errors that `next dev` (the live preview)
# tolerates but `next build` rejects — without this a single stray type error
# fails the whole deploy. We mirror dev's tolerance and guarantee the
# standalone output that Dockerfile.prod copies. Written AFTER the container
# overlay so it always wins.
_PROD_NEXT_CONFIG = '''\
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
'''

# Keep background task references alive until completion.
_bg_tasks: set[asyncio.Task[None]] = set()


async def start_deploy(project_id: str, slug: str | None = None) -> deploy_state.DeployRecord:
    """Resolve the project's dev container and launch a background deploy.

    Idempotent while a deploy is active: returns the in-flight record instead of
    starting a second build.
    """
    dev_name = await docker_client.find_project_container(project_id, kind="dev")
    if dev_name is None and slug:
        dev_name = f"omnia-dev-{slug}"
    if dev_name is None:
        raise OrchestratorError(
            code="not_found",
            message="no dev container for this project — provision/start it first",
            status_code=404,
        )
    resolved_slug = slug or dev_name.removeprefix("omnia-dev-")

    active = deploy_state.get(project_id)
    if active is not None and deploy_state.is_active(project_id):
        return active

    rec = deploy_state.start(project_id)
    # Optimistic public URL — deterministic, shown before the build completes.
    rec.prod_url = nginx_writer.prod_url(resolved_slug)

    task = asyncio.create_task(_run(project_id, resolved_slug, dev_name))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return rec


async def _run(project_id: str, slug: str, dev_name: str) -> None:
    build_dir = Path(tempfile.mkdtemp(prefix=f"omnia-build-{slug}-"))
    try:
        log.info("deploy.start", project_id=project_id, slug=slug, dev=dev_name)
        deploy_state.update(project_id, phase="building")

        # 1. Seed the build context from the template (Dockerfile.prod + configs).
        template_dir = _template_source_dir(_TEMPLATE)
        shutil.copytree(
            template_dir,
            build_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("node_modules", ".next", ".git", "__pycache__"),
        )

        # 2. Overlay the live app files from the dev container.
        await docker_client.unpause_container(dev_name)
        for rel in _OVERLAY_PATHS:
            await docker_client.copy_path_from_container(
                dev_name, f"/app/{rel}", str(build_dir)
            )

        # 2b. Force a prod-safe next.config (tolerate AI type/lint errors +
        # standalone output). Overwrites any config the overlay brought in.
        for stale in ("next.config.js", "next.config.mjs"):
            (build_dir / stale).unlink(missing_ok=True)
        (build_dir / "next.config.ts").write_text(_PROD_NEXT_CONFIG, encoding="utf-8")

        # 2c. Build-time DATABASE_URL. The template's db module throws at import
        # if it's unset, and `next build` imports every route module during
        # page-data collection. `next build` reads .env.production; the
        # standalone runtime does NOT read .env files (it uses the container env
        # we inject), so this placeholder never reaches production.
        (build_dir / ".env.production").write_text(
            f"DATABASE_URL={_DB_PLACEHOLDER}\n", encoding="utf-8"
        )

        # 2d. Dockerfile.prod has `COPY /app/public ./public`; the template
        # ships no public/ and a generated project may lack one too — ensure it
        # exists so the image build doesn't fail on a missing COPY source.
        public_dir = build_dir / "public"
        public_dir.mkdir(exist_ok=True)
        (public_dir / ".gitkeep").touch()

        if not (build_dir / "Dockerfile.prod").exists():
            raise OrchestratorError(
                code="container_failure",
                message="build context missing Dockerfile.prod",
                status_code=500,
            )

        # 3. Build the prod image.
        tag = f"omnia-app-{slug}:{int(time.time())}"
        await docker_client.build_image(str(build_dir), "Dockerfile.prod", tag)
        deploy_state.update(project_id, image_tag=tag, phase="swapping")

        # 4. Run the new prod container, replacing any previous one.
        prod_name = f"omnia-app-{slug}"
        prod_port = await get_prod_port_allocator().acquire(UUID(project_id))
        await docker_client.destroy_container(prod_name)
        # Auth.js v5 envs — same secret as dev so a deploy doesn't log every
        # user out. The dev container's `secrets_root/<id>/auth.secret` is
        # the canonical source; `_load_auth_secret` mirrors what provisioner
        # does (load or create) so a fresh deploy without a prior dev session
        # still works.
        from omnia_orchestrator.services.provisioner import (
            _load_or_create_auth_secret,
        )

        auth_secret = _load_or_create_auth_secret(project_id)
        prod_origin = nginx_writer.prod_url(slug)

        spec = docker_client.ContainerSpec(
            name=prod_name,
            image=tag,
            port=prod_port,
            project_id=project_id,
            env={
                "NODE_ENV": "production",
                "PORT": "3000",
                "HOSTNAME": "0.0.0.0",  # standalone server must bind all ifaces
                "DATABASE_URL": _resolve_runtime_dsn(project_id),
                "AUTH_SECRET": auth_secret,
                "AUTH_URL": prod_origin,
                "AUTH_TRUST_HOST": "true",
            },
            cpu_quota=1.0,
            memory_mb=1024,
            kind="prod",
            restart_policy_name="unless-stopped",
        )
        await docker_client.start_container(spec)

        # 5. Health-poll before swapping traffic.
        if not await _healthy(prod_port):
            raise OrchestratorError(
                code="container_failure",
                message="prod container did not become healthy in time",
                status_code=504,
            )

        # 6. Publish nginx (HTTP + TLS — we're already in the background task).
        prod_url = await nginx_writer.publish(nginx_writer.prod_host(slug), prod_port)
        deploy_state.update(
            project_id,
            phase="done",
            prod_url=prod_url,
            finished_at=deploy_state.now_iso(),
        )
        log.info("deploy.done", project_id=project_id, url=prod_url)

        # 7. GC old prod image revisions for this slug. Best-effort: a failure
        # here does NOT roll back the deploy (image accumulation is cosmetic).
        try:
            await docker_client.prune_old_app_images(slug, keep=3)
        except Exception as exc:
            # Best-effort: a failed prune is cosmetic, never invalidates a deploy.
            log.warning("deploy.prune_failed", project_id=project_id, err=str(exc))
    except Exception as exc:
        msg = exc.message if isinstance(exc, OrchestratorError) else str(exc)
        log.warning("deploy.failed", project_id=project_id, err=msg)
        deploy_state.update(
            project_id, phase="failed", error=msg, finished_at=deploy_state.now_iso()
        )
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


async def _healthy(port: int, *, tries: int = 60, delay: float = 3.0) -> bool:
    """Poll http://127.0.0.1:<port>/ until it answers (<500) or we give up.

    Budget: 60 tries x 3 s = 3 min. Next.js 15 + Turbopack cold compile of a
    real user project can peak at ~90 s on the free tier; the previous 90 s
    ceiling was flaky for larger generated pages. 3 min stays well under the
    api request budget because deploy runs in a background task anyway.
    """
    import httpx

    url = f"http://127.0.0.1:{port}/"
    async with httpx.AsyncClient(timeout=4.0) as client:
        for _ in range(tries):
            try:
                resp = await client.get(url)
                if resp.status_code < 500:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(delay)
    return False
