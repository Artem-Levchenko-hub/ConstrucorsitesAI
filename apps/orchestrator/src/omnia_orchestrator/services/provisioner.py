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

import asyncio
import os
import secrets as _secrets
import shutil
from pathlib import Path

import structlog

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import (
    ContainerSpec,
    copy_path_from_container,
    ensure_template_image_fresh,
    exec_cmd,
    find_project_container,
    start_container,
    unpause_container,
    write_files,
)
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.event_publisher import publish_project_event
from omnia_orchestrator.core.stack_registry import get_stack
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
_MAX_RUNTIME_OVERLAY_PATHS = (
    "src",
    "public",
    "package.json",
    "pnpm-lock.yaml",
    "next.config.ts",
    "tsconfig.json",
    "drizzle.config.ts",
    "drizzle",
    "components.json",
    "postcss.config.mjs",
    "tailwind.config.ts",
    "scripts",
)
_MAX_PLATFORM_CORE_DIRS = (
    "src/app/api/max",
    "src/app/api/omnia",
    "src/lib/max",
    "src/lib/omnia",
    "src/app/legal",
    "src/app/support",
)
_MAX_PLATFORM_CORE_FILES = (
    "next.config.ts",
    "drizzle.config.ts",
    "postcss.config.mjs",
    "docker-entrypoint.sh",
    "Dockerfile.dev",
    "Dockerfile.prod",
    "scripts/apply-migrations.mjs",
    "src/app/layout.tsx",
    "src/components/MaxAppProvider.tsx",
    "src/components/OmniaCompliance.tsx",
    "src/lib/db/index.ts",
)


def _integration_env(template: str | None = None) -> dict[str, str]:
    """Env for the Base44-style "Core" integrations injected into every user
    container. Containers reach MinIO + the LLM gateway CONTAINER-TO-CONTAINER
    over the runtime network (their host binds are 127.0.0.1-only, unreachable
    from a container). Values come from the orchestrator env with prod-shaped
    defaults; the MinIO secret + public URL must be set in the orchestrator env
    for UploadFile to work (see docs/08-vps-setup.md). SMTP is opt-in — absent →
    SendEmail stubs. LLM_GATEWAY_URL is injected now for the later InvokeLLM/
    GenerateImage pass.
    """
    # MAX reaches providers/storage through the scoped platform integration
    # broker. Injecting the shared MinIO/SMTP/Gateway credentials into its
    # generated runtime made any custom API route a cross-tenant secret escape.
    if template == "max-miniapp-nextjs":
        return {}

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


def _egress_env() -> dict[str, str]:
    """Proxy env that forces container egress through the allowlisting proxy when
    configured (Phase 1). Empty proxy → empty dict → direct egress (current
    behaviour). Both upper- and lower-case variants are set because libraries
    disagree on which they read."""
    s = get_settings()
    proxy = (s.container_egress_proxy or "").strip()
    if not proxy:
        return {}
    nop = s.container_egress_no_proxy
    return {
        "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "NO_PROXY": nop,
        "http_proxy": proxy, "https_proxy": proxy, "no_proxy": nop,
    }


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


def load_existing_auth_secret(project_id: str) -> str | None:
    """Return a previously provisioned project's auth secret, if present.

    Unlike :func:`_load_or_create_auth_secret`, this read-only helper must not
    create a directory or a secret. It is used by capabilities that may only
    operate on an already-provisioned project.
    """
    secret_file = Path(get_settings().secrets_root) / project_id / "auth.secret"
    try:
        value = secret_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None

log = structlog.get_logger("omnia_orchestrator.provisioner")

# A cold image rebuild is shared by template, but the rest of provisioning is
# project-specific (container start, nginx publication and runtime.started).
# Serialise duplicate starts for the same project so multiple open tabs cannot
# race those side effects while the first cold start is still in progress.
_PROVISION_LOCKS: dict[str, asyncio.Lock] = {}


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
    """Seed missing template files without overwriting the project workspace."""
    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in {"node_modules", ".next", ".git", "__pycache__"}]

    if not dest.exists():
        shutil.copytree(src, dest, ignore=_ignore)
        return
    skipped = {"node_modules", ".next", ".git", "__pycache__"}
    for source in src.rglob("*"):
        rel = source.relative_to(src)
        if any(part in skipped for part in rel.parts) or source.is_symlink():
            continue
        target = dest / rel
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _workspace_text_files(root: Path) -> dict[str, str]:
    """Bounded source overlay used to restore a re-profiled MAX runtime."""
    out: dict[str, str] = {}
    total = 0
    skipped = {"node_modules", ".next", ".git", "__pycache__", "dist", "build", ".venv"}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in skipped for part in rel.parts):
            continue
        lowered = path.name.lower()
        if lowered == ".env" or lowered.startswith(".env.") or lowered.startswith("secrets."):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        encoded = content.encode("utf-8")
        if len(encoded) > 8 * 1024 * 1024:
            continue
        out[rel.as_posix()] = content
        total += len(encoded)
        if len(out) > 5_000 or total > 64 * 1024 * 1024:
            raise OrchestratorError(
                code="validation_failed",
                message="project workspace exceeds MAX runtime restore quota",
                status_code=413,
            )
    return out


def _is_max_platform_core_path(rel: Path) -> bool:
    posix = rel.as_posix()
    return posix in _MAX_PLATFORM_CORE_FILES or any(
        posix == prefix or posix.startswith(prefix + "/")
        for prefix in _MAX_PLATFORM_CORE_DIRS
    )


def _collect_max_runtime_overlay(root: Path) -> dict[str, str]:
    """Return only user-owned MAX files that should survive a reprovision."""
    return {
        path: content
        for path, content in _workspace_text_files(root).items()
        if not _is_max_platform_core_path(Path(path))
    }


def _restore_max_platform_core(workspace_root: Path, template_root: Path) -> None:
    """Re-seed platform-owned MAX files from the current template version."""

    def _restore(relative: str) -> None:
        target = workspace_root / relative
        source = template_root / relative
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.is_dir():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    for relative in _MAX_PLATFORM_CORE_DIRS:
        _restore(relative)
    for relative in _MAX_PLATFORM_CORE_FILES:
        _restore(relative)


async def _sync_max_runtime_workspace(container_name: str, project_dir: Path) -> None:
    """Overlay current core + user source and materialise project dependencies."""
    workspace_files = await asyncio.to_thread(_workspace_text_files, project_dir)
    if workspace_files:
        await write_files(container_name, workspace_files)
    if "package.json" not in workspace_files:
        return
    install = await exec_cmd(
        container_name,
        cmd=["pnpm", "install", "--no-frozen-lockfile", "--ignore-scripts"],
        workdir="/app",
        timeout_sec=240,
        max_output=24_000,
    )
    if install["exit_code"] != "0":
        detail = (install["stderr"] or install["stdout"])[-1_500:]
        raise OrchestratorError(
            code="container_failure",
            message=f"MAX dependency sync failed: {detail}",
            status_code=409,
        )
    lock_result = await exec_cmd(
        container_name,
        cmd=["cat", "--", "pnpm-lock.yaml"],
        workdir="/app",
        max_output=2 * 1024 * 1024 + 1,
    )
    lock_content = lock_result["stdout"]
    if (
        lock_result["exit_code"] != "0"
        or len(lock_content.encode("utf-8")) > 2 * 1024 * 1024
    ):
        raise OrchestratorError(
            code="container_failure",
            message="MAX generated pnpm-lock.yaml is missing or exceeds the file quota",
            status_code=409,
        )
    await asyncio.to_thread(
        (project_dir / "pnpm-lock.yaml").write_text,
        lock_content,
        encoding="utf-8",
    )


async def provision(req: ProvisionRequest) -> ProvisionResponse:
    lock = _PROVISION_LOCKS.setdefault(str(req.project_id), asyncio.Lock())
    async with lock:
        return await _provision_once(req)


async def _provision_once(req: ProvisionRequest) -> ProvisionResponse:
    settings = get_settings()
    log.info(
        "provision.start",
        project_id=str(req.project_id),
        slug=req.slug,
        template=req.template,
        tier=req.tier,
    )

    stack = get_stack(req.template)
    src = _template_source_dir(stack.template_dir)

    project_dir = Path(settings.projects_root) / str(req.project_id)
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    _copy_template(src, project_dir)
    log.info("provision.template_copied", dest=str(project_dir))

    port = await get_port_allocator().acquire(req.project_id)
    log.info("provision.port_acquired", port=port)

    container_name = f"omnia-dev-{req.slug}"
    image_tag = stack.image_tag

    # Always serve the LATEST template: rebuild the baked image if its source was
    # edited since the image was built (dev containers run from the image, not a
    # mount — a template edit is invisible otherwise). Staleness-gated + fail-soft
    # → normally a no-op; after a template edit the first provision rebuilds
    # (layer cache → fast) and the client sees the change.
    await ensure_template_image_fresh(src, image_tag)

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
        "OMNIA_PROJECT_ID": str(req.project_id),
        "OMNIA_PLATFORM_API_URL": os.getenv(
            "OMNIA_PLATFORM_API_URL", "https://constructor.lead-generator.ru"
        ),
        "AUTH_SECRET": auth_secret,
        "AUTH_URL": dev_origin,
        "AUTH_TRUST_HOST": "true",
        **_integration_env(stack.template_dir),
        **_egress_env(),
        **req.initial_env,
    }

    # Area C (DARK): when the orchestrator runs with OMNIA_GATE_SEED=1, ask the
    # template's init-db to seed a login-able operator account so the composition
    # gate can render the authenticated cabinet. Off by default → normal apps get
    # no seed account.
    if os.getenv("OMNIA_GATE_SEED") == "1":
        env["OMNIA_GATE_SEED"] = "1"
        env["OMNIA_GATE_SEED_EMAIL"] = os.getenv(
            "OMNIA_GATE_SEED_EMAIL", "gate@omnia.local"
        )

    # Next.js 15 + Turbopack peaks well past 2 GB during the first compile of a
    # heavy entity/fullstack app (many routes); once warm it settles around
    # 500-800 MB. A 2 GB ceiling OOM-killed those mid-compile, so the memory
    # limit is config-driven (default 4 GB — a ceiling, not a reservation).
    #
    # restart_policy `unless-stopped` makes a crashed dev server (non-zero exit)
    # self-heal: docker re-runs it automatically. Hibernation is unaffected —
    # docker only restarts containers that exited on their own, never ones the
    # daemon API stopped/paused, so an idle-sweep `stop` stays down until /wake.
    # Per-project network isolation (Phase 1) — own bridge net per project when
    # enabled, else None → docker_client uses the shared runtime net (current).
    max_project_owner = bool(
        settings.agent_sandbox_enabled
        and stack.template_dir == "max-miniapp-nextjs"
    )
    if max_project_owner:
        existing_runtime = await find_project_container(str(req.project_id), kind="dev")
        if existing_runtime:
            await unpause_container(existing_runtime)
            for relative in _MAX_RUNTIME_OVERLAY_PATHS:
                await copy_path_from_container(
                    existing_runtime,
                    f"/app/{relative}",
                    str(project_dir),
                    max_archive_bytes=80 * 1024 * 1024,
                )
        await asyncio.to_thread(_restore_max_platform_core, project_dir, src)
    network_name = (
        f"omnia-proj-{req.project_id}"
        if max_project_owner or settings.isolate_project_network
        else None
    )

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
        container_port=stack.container_port,
        network_name=network_name,
        # Sandbox hardening (Phase 1) — the agent runs arbitrary bash in this
        # dev container, so it is the untrusted boundary. All knobs default to
        # OFF (current behaviour); enable per-env once the host is prepared.
        runtime=settings.container_runtime,
        harden=max_project_owner or settings.container_harden,
        pids_limit=(
            max(64, settings.container_pids_limit)
            if max_project_owner
            else settings.container_pids_limit
        ),
        sandbox_profile="max-runtime-v1" if max_project_owner else "",
        recreate_on_profile_change=max_project_owner,
        include_host_gateway=not max_project_owner,
        network_service_names=(
            (settings.runtime_db_container_name,) if max_project_owner else ()
        ),
    )

    container_id = await start_container(spec)
    if max_project_owner:
        # Re-profiling an existing MAX container removes the legacy instance so
        # Docker can change its network/env/security flags.  The durable mirrored
        # workspace is then overlaid onto the fresh image, preserving the user's
        # product while dropping shared platform credentials.
        # Apply the complete mirrored source, not just user files: same-tag
        # containers are intentionally reused, so this is also how a managed
        # core upgrade reaches an already-running runtime.
        await _sync_max_runtime_workspace(container_name, project_dir)
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
