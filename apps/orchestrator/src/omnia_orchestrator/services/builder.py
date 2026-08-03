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
import os
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

import structlog

from omnia_orchestrator.core import docker_client, postgres_admin
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.event_publisher import publish_project_event
from omnia_orchestrator.core.stack_registry import get_stack
from omnia_orchestrator.services import deploy_state, nginx_writer
from omnia_orchestrator.services.port_allocator import get_prod_port_allocator
from omnia_orchestrator.services.provisioner import _template_source_dir

log = structlog.get_logger("omnia_orchestrator.builder")

# App files overlaid from the live dev container on top of the template seed.
# Best-effort: missing paths are skipped. Covers everything the AI realistically
# edits in the nextjs-postgres-drizzle template (pages live under src/).
_OVERLAY_PATHS = [
    "src",
    # Base44-style nextjs-entities stack: the AI's data model is entities/*.json
    # read at runtime by the engine. Without this the prod image has ZERO
    # entities and every /api/entities/* call 404s (dev works, prod doesn't).
    "entities",
    "public",
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "index.html",
    "vite.config.ts",
    "next.config.ts",
    "tsconfig.json",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "drizzle.config.ts",
    "drizzle",
    "components.json",
    "postcss.config.mjs",
    "tailwind.config.ts",
    "scripts",
]

# These production boot files are platform-owned. A long-running dev container
# can carry an older copy, and overlaying its whole scripts/ directory must not
# downgrade the runner that executes against the production database.
_TEMPLATE_OWNED_PROD_PATHS = ("scripts/apply-migrations.mjs",)


def _is_app_owned_snapshot_path(relative: str) -> bool:
    return any(relative == root or relative.startswith(f"{root}/") for root in _OVERLAY_PATHS)


def _reset_app_owned_paths(build_dir: Path) -> None:
    """Remove template app files before laying down one immutable snapshot.

    Omissions in a Git tree represent deletions.  Overlaying without this reset
    would resurrect starter files that the selected version had removed.
    """
    for relative in _OVERLAY_PATHS:
        target = build_dir / relative
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)


def _materialize_snapshot(build_dir: Path, source_files: dict[str, str]) -> None:
    """Write bounded app-owned text files without permitting path escape.

    Production Dockerfiles/configs remain template-owned.  The API repository
    may contain them, but generated code must never replace that trusted build
    boundary.
    """
    _reset_app_owned_paths(build_dir)
    resolved_root = build_dir.resolve()
    for relative, content in sorted(source_files.items()):
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or path.as_posix() != relative
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise OrchestratorError(
                code="validation_failed",
                message=f"unsafe source path: {relative!r}",
                status_code=403,
            )
        if not _is_app_owned_snapshot_path(relative):
            continue
        destination = build_dir.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_destination = destination.resolve()
        if resolved_root not in resolved_destination.parents:
            raise OrchestratorError(
                code="validation_failed",
                message=f"source path escapes build root: {relative!r}",
                status_code=403,
            )
        destination.write_text(content, encoding="utf-8")


def _restore_template_owned_prod_files(template_dir: Path, build_dir: Path) -> None:
    for relative in _TEMPLATE_OWNED_PROD_PATHS:
        source = template_dir / relative
        if not source.is_file():
            continue
        destination = build_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


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


# Default/fallback template. The actual template per deploy is recovered from
# the dev container's image (omnia-template-<t>:dev) in `_run`, so an entities
# project seeds its prod build context from the entities template, not this one.
_DEFAULT_TEMPLATE = "nextjs-postgres-drizzle"


def _is_next_template(template: str) -> bool:
    return template.startswith("nextjs-") or template == "max-miniapp-nextjs"


# Prod build config the orchestrator forces into every deploy. AI-generated
# code very often has TS/ESLint errors that `next dev` (the live preview)
# tolerates but `next build` rejects — without this a single stray type error
# fails the whole deploy. We mirror dev's tolerance and guarantee the
# standalone output that Dockerfile.prod copies. Written AFTER the container
# overlay so it always wins.
_PROD_NEXT_CONFIG = """\
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
  // nextjs-entities: the engine reads entities/*.json from disk at runtime; the
  // tracer can't see fs reads, so force them into the standalone bundle or prod
  // 404s on every /api/entities/* call. No-op for templates with no entities/.
  outputFileTracingIncludes: {
    "/api/entities/**": ["./entities/**/*.json"],
  },
};

export default nextConfig;
"""

# Keep background task references alive until completion.
_bg_tasks: set[asyncio.Task[None]] = set()
_project_tasks: dict[str, asyncio.Task[None]] = {}


async def start_deploy(
    project_id: str,
    slug: str | None = None,
    commit_sha: str | None = None,
    template: str | None = None,
    source_files: dict[str, str] | None = None,
    target: dict[str, object] | None = None,
    domains: list[str] | None = None,
    idempotency_key: str | None = None,
    runtime_env: dict[str, str] | None = None,
) -> deploy_state.DeployRecord:
    """Resolve the project's dev container and launch a background deploy.

    Idempotent while a deploy is active: returns the in-flight record instead of
    starting a second build. `target` (BYO-VPS) = {host, port, user, auth_type,
    secret}: when set, the built image is deployed to the user's own VPS over
    SSH instead of run locally on our host. None = наш хостинг (текущий путь).
    """
    exact_snapshot = template is not None and source_files is not None and slug is not None
    dev_name: str | None = None
    if not exact_snapshot:
        dev_name = await docker_client.find_project_container(project_id, kind="dev")
        if dev_name is None and slug:
            dev_name = f"omnia-dev-{slug}"
    if dev_name is None and not exact_snapshot:
        raise OrchestratorError(
            code="not_found",
            message="no dev container for this project — provision/start it first",
            status_code=404,
        )
    resolved_slug = slug or (dev_name.removeprefix("omnia-dev-") if dev_name else "")

    active = deploy_state.get(project_id)
    if active is not None and deploy_state.is_active(project_id):
        if commit_sha is not None and active.commit_sha != commit_sha:
            raise OrchestratorError(
                code="conflict",
                message="another revision is already deploying",
                status_code=409,
            )
        # The first task owns this project until the persisted active record
        # reaches a terminal phase. Replaying the same exact revision must not
        # start a second build/push/swap coroutine.
        return active

    try:
        rec = deploy_state.start(
            project_id,
            idempotency_key=idempotency_key,
            commit_sha=commit_sha,
            target_label=str(target.get("label")) if target and target.get("label") else None,
            target_id=str(target.get("id")) if target and target.get("id") else None,
        )
    except deploy_state.DeployRevisionConflict as exc:
        raise OrchestratorError(
            code="conflict",
            message=str(exc),
            status_code=409,
        ) from exc
    if rec.phase not in ("building", "queued"):
        return rec
    # Optimistic public URL — deterministic, shown before the build completes.
    # For a remote target we don't know the URL until the container is up.
    rec.prod_url = None if target else nginx_writer.prod_url(resolved_slug)

    task = asyncio.create_task(
        _run(
            project_id,
            resolved_slug,
            dev_name,
            template,
            dict(source_files) if source_files is not None else None,
            target,
            domains,
            rec.run_id,
            dict(runtime_env or {}),
        )
    )
    _bg_tasks.add(task)
    _project_tasks[project_id] = task
    task.add_done_callback(_bg_tasks.discard)
    task.add_done_callback(lambda _: _project_tasks.pop(project_id, None))
    return rec


async def cancel_deploy(project_id: str) -> deploy_state.DeployRecord | None:
    """Cancel one active deployment; cleanup is handled by its pipeline."""
    rec = deploy_state.get(project_id)
    task = _project_tasks.get(project_id)
    if rec is None or task is None or task.done():
        return rec
    deploy_state.update(project_id, phase="cancelling", detail="Останавливаем деплой…")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    deploy_state.update(
        project_id,
        phase="cancelled",
        detail="Деплой остановлен пользователем.",
        error=None,
        finished_at=deploy_state.now_iso(),
    )
    return deploy_state.get(project_id)


def _remote_port(slug: str) -> int:
    """Детерминированный host-порт на чужой машине из slug (30000–49999).

    Без Date/random (они недоступны и ломают воспроизводимость): стабильный
    хеш slug. Достаточно, чтобы разные проекты не толкались на одном порту.
    """
    h = 0
    for ch in slug:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return 30000 + (h % 20000)


async def _deploy_remote(
    project_id: str,
    slug: str,
    tag: str,
    target: dict[str, object],
    domains: list[str] | None = None,
    run_id: str = "",
    template: str = _DEFAULT_TEMPLATE,
    runtime_env: dict[str, str] | None = None,
) -> None:
    """Развернуть уже собранный образ `tag` на чужом VPS (BYO-VPS).

    Если у проекта подключён домен — приложение открывается по https://<домен>
    (edge/Caddy на машине юзера выпускает SSL сам); иначе — по http://host:port.
    """
    from omnia_orchestrator.services import remote_deploy

    host_port = _remote_port(slug)
    needs_database = get_stack(template).needs_database
    db_dump: str | None = None
    db_schema: str | None = None
    if needs_database:
        db_dump, db_schema = await _export_project_database(project_id)
        deploy_state.append_log(project_id, "Подготовлена миграция данных проекта")
    auth_secret = _load_or_create_auth_secret_safe(project_id)
    app_url = f"https://{domains[0]}" if domains else f"http://{target['host']}:{host_port}"
    env = {
        "NODE_ENV": "production",
        "PORT": "3000",
        "HOSTNAME": "0.0.0.0",
        "OMNIA_PROJECT_ID": project_id,
        "OMNIA_PLATFORM_API_URL": os.getenv(
            "OMNIA_PLATFORM_API_URL", "https://constructor.lead-generator.ru"
        ),
        "AUTH_SECRET": auth_secret,
        "AUTH_URL": app_url,
        "AUTH_TRUST_HOST": "true",
        **(runtime_env or {}),
    }
    result = await remote_deploy.deploy_to_target(
        creds=target,
        image_tag=tag,
        project_id=project_id,
        run_id=run_id,
        slug=slug,
        host_port=host_port,
        container_port=3000,
        env=env,
        domains=domains,
        needs_database=needs_database,
        db_dump=db_dump,
        db_schema=db_schema,
        progress=lambda message: deploy_state.append_log(project_id, message),
    )
    if result.get("ok"):
        deploy_state.update(
            project_id,
            phase="done",
            prod_url=result.get("url"),
            detail=str(result.get("detail") or ""),
            finished_at=deploy_state.now_iso(),
        )
        log.info("deploy.remote_done", project_id=project_id, url=result.get("url"))
        await publish_project_event(
            project_id,
            "deploy.done",
            {
                "phase": "done",
                "slug": slug,
                "prod_url": result.get("url"),
                "image_tag": tag,
                "detail": result.get("detail"),
            },
        )
    else:
        deploy_state.update(
            project_id,
            phase="failed",
            error=str(result.get("detail")),
            finished_at=deploy_state.now_iso(),
        )
        await publish_project_event(
            project_id,
            "deploy.failed",
            {"phase": "failed", "slug": slug, "error": result.get("detail")},
        )


def _load_or_create_auth_secret_safe(project_id: str) -> str:
    from omnia_orchestrator.services.provisioner import _load_or_create_auth_secret

    return _load_or_create_auth_secret(project_id)


async def _export_project_database(project_id: str) -> tuple[str, str]:
    """Create a SQL snapshot with a client matching the managed database major."""
    from omnia_orchestrator.services.remote_deploy import POSTGRES_IMAGE

    dsn = _resolve_runtime_dsn(project_id)
    if dsn == _DB_PLACEHOLDER:
        raise RuntimeError("У проекта нет доступной базы данных для переноса.")
    project_url = urlparse(dsn)
    admin_url = urlparse(
        get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    options = parse_qs(project_url.query).get("options", [""])[0]
    marker = "search_path="
    schema = unquote(options).split(marker, 1)[-1].split(",", 1)[0].strip()
    if not schema or not schema.replace("_", "").isalnum():
        raise RuntimeError("Не удалось определить схему базы проекта.")
    process = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "-e",
        "PGHOST",
        "-e",
        "PGPORT",
        "-e",
        "PGUSER",
        "-e",
        "PGPASSWORD",
        "-e",
        "PGDATABASE",
        POSTGRES_IMAGE,
        "pg_dump",
        "--no-owner",
        "--no-acl",
        "--schema",
        schema,
        "--format",
        "plain",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PGHOST": admin_url.hostname or "127.0.0.1",
            "PGPORT": str(admin_url.port or 5432),
            "PGUSER": project_url.username or "",
            "PGPASSWORD": unquote(project_url.password or ""),
            "PGDATABASE": project_url.path.lstrip("/"),
        },
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    if process.returncode != 0:
        raise RuntimeError(
            "Не удалось перенести данные проекта: " + stderr.decode("utf-8", "replace")[-300:]
        )
    return stdout.decode("utf-8", "replace"), schema


async def _run(
    project_id: str,
    slug: str,
    dev_name: str | None,
    requested_template: str | None = None,
    source_files: dict[str, str] | None = None,
    target: dict[str, object] | None = None,
    domains: list[str] | None = None,
    run_id: str = "",
    runtime_env: dict[str, str] | None = None,
) -> None:
    build_dir = Path(tempfile.mkdtemp(prefix=f"omnia-build-{slug}-"))
    try:
        log.info(
            "deploy.start",
            project_id=project_id,
            slug=slug,
            exact_snapshot=source_files is not None,
        )
        deploy_state.update(project_id, phase="building")
        deploy_state.append_log(project_id, "Собираем production-образ")
        await publish_project_event(
            project_id, "deploy.progress", {"phase": "building", "slug": slug}
        )

        # 1. Seed the build context from the PROJECT's template (recovered from
        # the dev container's image), falling back to the default. This keeps an
        # entities project on the entities template's Dockerfile.prod/configs.
        detected_template = (
            await docker_client.container_image_template(dev_name) if dev_name is not None else None
        )
        template = requested_template or detected_template or _DEFAULT_TEMPLATE
        log.info("deploy.template", project_id=project_id, template=template)
        stack = get_stack(template)
        is_next_template = _is_next_template(template)
        if stack.production_dockerfile is None:
            raise OrchestratorError(
                code="unsupported_stack",
                message=(
                    f"template {template!r} has no production deployment recipe; "
                    "choose a supported stack or add Dockerfile.prod"
                ),
                status_code=422,
            )
        template_dir = _template_source_dir(template)
        shutil.copytree(
            template_dir,
            build_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("node_modules", ".next", ".git", "__pycache__"),
        )

        # 2. Materialize the exact attested repository tree.  Legacy internal
        # callers without a snapshot keep the old live-container fallback.
        if source_files is not None:
            _materialize_snapshot(build_dir, source_files)
        else:
            if dev_name is None:
                raise OrchestratorError(
                    code="not_found",
                    message="deploy source is unavailable",
                    status_code=404,
                )
            await docker_client.unpause_container(dev_name)
            for rel in _OVERLAY_PATHS:
                await docker_client.copy_path_from_container(
                    dev_name, f"/app/{rel}", str(build_dir)
                )
        _restore_template_owned_prod_files(template_dir, build_dir)

        # 2b. Force a prod-safe next.config (tolerate AI type/lint errors +
        # standalone output). Overwrites any config the overlay brought in.
        if is_next_template:
            for stale in ("next.config.js", "next.config.mjs"):
                (build_dir / stale).unlink(missing_ok=True)
            (build_dir / "next.config.ts").write_text(_PROD_NEXT_CONFIG, encoding="utf-8")

        # 2c. Build-time DATABASE_URL. The template's db module throws at import
        # if it's unset, and `next build` imports every route module during
        # page-data collection. `next build` reads .env.production; the
        # standalone runtime does NOT read .env files (it uses the container env
        # we inject), so this placeholder never reaches production.
        if is_next_template:
            (build_dir / ".env.production").write_text(
                f"DATABASE_URL={_DB_PLACEHOLDER}\n", encoding="utf-8"
            )

        # 2d. Dockerfile.prod has `COPY /app/public ./public`; the template
        # ships no public/ and a generated project may lack one too — ensure it
        # exists so the image build doesn't fail on a missing COPY source.
        if is_next_template:
            public_dir = build_dir / "public"
            public_dir.mkdir(exist_ok=True)
            (public_dir / ".gitkeep").touch()
            # Some projects have no generated SQL yet. The prod Dockerfile still
            # copies this directory so the migration runner has a stable contract.
            (build_dir / "drizzle").mkdir(exist_ok=True)

        dockerfile = stack.production_dockerfile
        if not (build_dir / dockerfile).exists():
            raise OrchestratorError(
                code="container_failure",
                message=f"build context missing {dockerfile}",
                status_code=500,
            )

        # 3. Build the prod image.
        tag = f"omnia-app-{slug}:{int(time.time())}"
        await docker_client.build_image(str(build_dir), dockerfile, tag)
        deploy_state.update(project_id, image_tag=tag, phase="swapping")
        await publish_project_event(
            project_id,
            "deploy.progress",
            {"phase": "swapping", "slug": slug, "image_tag": tag},
        )

        # BYO-VPS: если у проекта выбран свой сервер — переносим готовый образ
        # туда и запускаем на его машине (не на нашем хосте). Локальный путь
        # (шаги 4–7) при этом не выполняется — поведение нашего хостинга не
        # меняется, ветка живёт только когда target задан явно.
        if target is not None:
            deploy_state.update(project_id, phase="pushing")
            deploy_state.append_log(project_id, "Передаём образ на выбранный VPS")
            await publish_project_event(
                project_id, "deploy.progress", {"phase": "pushing", "slug": slug}
            )
            await _deploy_remote(
                project_id,
                slug,
                tag,
                target,
                domains,
                run_id,
                template,
                runtime_env,
            )
            return

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
                "OMNIA_PROJECT_ID": project_id,
                "OMNIA_PLATFORM_API_URL": os.getenv(
                    "OMNIA_PLATFORM_API_URL", "https://constructor.lead-generator.ru"
                ),
                "DATABASE_URL": _resolve_runtime_dsn(project_id),
                "AUTH_SECRET": auth_secret,
                "AUTH_URL": prod_origin,
                "AUTH_TRUST_HOST": "true",
                **(runtime_env or {}),
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
        await publish_project_event(
            project_id,
            "deploy.done",
            {"phase": "done", "slug": slug, "prod_url": prod_url, "image_tag": tag},
        )

        # 7. GC old prod image revisions for this slug. Best-effort: a failure
        # here does NOT roll back the deploy (image accumulation is cosmetic).
        try:
            await docker_client.prune_old_app_images(slug, keep=3)
        except Exception as exc:
            # Best-effort: a failed prune is cosmetic, never invalidates a deploy.
            log.warning("deploy.prune_failed", project_id=project_id, err=str(exc))
    except asyncio.CancelledError:
        deploy_state.update(
            project_id,
            phase="cancelled",
            detail="Деплой остановлен пользователем.",
            error=None,
            finished_at=deploy_state.now_iso(),
        )
        await publish_project_event(
            project_id,
            "deploy.failed",
            {"phase": "cancelled", "slug": slug, "error": None},
        )
        raise
    except Exception as exc:
        msg = exc.message if isinstance(exc, OrchestratorError) else str(exc)
        log.warning("deploy.failed", project_id=project_id, err=msg)
        deploy_state.update(
            project_id, phase="failed", error=msg, finished_at=deploy_state.now_iso()
        )
        await publish_project_event(
            project_id,
            "deploy.failed",
            {"phase": "failed", "slug": slug, "error": msg},
        )
    finally:
        if target is not None:
            target.clear()
        if runtime_env is not None:
            runtime_env.clear()
        if source_files is not None:
            source_files.clear()
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
