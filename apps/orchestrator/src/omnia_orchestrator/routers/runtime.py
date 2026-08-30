"""Internal API for project runtime lifecycle.

All endpoints below are gated by `X-Internal-Token` header verified against
`Settings.internal_token`. They are meant for apps/api (the public-facing
FastAPI service) to call; web clients never touch this surface.

These handlers are fully implemented and live in production: provision/wake/
stop/status, hot-reload, compile + runtime status, and a real prod deploy
(build image → run durable container → health-poll → nginx vhost + TLS). The
contracts (request/response schemas) are stable and consumed by apps/api today.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Header

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import (
    container_image_name,
    container_image_template,
    container_logs,
    container_security_facts,
    destroy_container,
    exec_cmd,
    find_project_container,
    run_sandbox_command,
    stop_container,
    wake_container,
    write_files,
)
from omnia_orchestrator.core.docker_client import (
    container_status as docker_container_status,
)
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.event_publisher import publish_project_event
from omnia_orchestrator.core.internal_auth import (
    verify_internal_token as _verify_token,
)
from omnia_orchestrator.schemas.runtime import (
    CompileStatusResponse,
    DeployRequest,
    DeployResponse,
    HotReloadRequest,
    KeepAliveRequest,
    KeepAliveResponse,
    LogsResponse,
    MaxPreviewSessionResponse,
    ProvisionRequest,
    ProvisionResponse,
    RuntimeStatusResponse,
    StatusResponse,
    StopRequest,
    WakeRequest,
    WakeResponse,
)
from omnia_orchestrator.services import (
    builder,
    demo_seed_writer,
    dep_doctor,
    deploy_state,
    nginx_writer,
)
from omnia_orchestrator.services.compile_status import parse_next_compile_error
from omnia_orchestrator.services.hibernate import (
    is_keep_alive_enabled,
    record_activity,
    set_keep_alive,
)
from omnia_orchestrator.services.port_allocator import (
    get_port_allocator,
    get_prod_port_allocator,
)
from omnia_orchestrator.services.provisioner import (
    load_existing_auth_secret,
)
from omnia_orchestrator.services.provisioner import (
    provision as provision_svc,
)
from omnia_orchestrator.services.runtime_probe import probe_runtime_error
from omnia_orchestrator.services.warm import warm_routes

router = APIRouter(prefix="/internal/projects", tags=["runtime"])

# Fixed template files (globals.css, the component kit, layout) are baked into
# the container image and never committed to the project git repo. The direct
# style-patch endpoint needs the current globals.css to append its managed
# override block, so we expose a narrow, read-only door — strictly whitelisted.
_READABLE_FILES = frozenset({"src/app/globals.css"})

# Agentic builder (Phase 0) caps — bound each observation so one fat result
# can't blow the agent's context window.
_AGENT_MAX_READ = 1_000_000
_AGENT_MAX_LIST = 16_000
_AGENT_MAX_GREP = 16_000
_AGENT_MAX_BUILD = 24_000
_SANDBOX_SYNC_MAX_FILES = 5_000
_SANDBOX_SYNC_MAX_FILE_BYTES = 8 * 1024 * 1024
_SANDBOX_SYNC_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SANDBOX_SKIP_NAMES = frozenset(
    {"node_modules", ".next", ".git", "__pycache__", "dist", "build", ".venv", "vendor"}
)

_MAX_PREVIEW_TEMPLATE = "max-miniapp-nextjs"
_MAX_PREVIEW_BOOTSTRAP_TTL = timedelta(seconds=120)
_MAX_PREVIEW_BOOTSTRAP_PATH = "/api/omnia/preview-session"


def _max_preview_bootstrap_message(project_id: str, expires: int) -> bytes:
    """Canonical, domain-separated signing input shared with the template."""
    return f"omnia:max-preview-session:v1\n{project_id}\n{expires}".encode("ascii")


def _max_preview_bootstrap_signature(secret: str, project_id: str, expires: int) -> str:
    digest = hmac_new(
        secret.encode("utf-8"),
        _max_preview_bootstrap_message(project_id, expires),
        sha256,
    ).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _safe_app_path(path: str) -> str:
    """Validate an agent-supplied path stays inside /app and return it relative.

    Rejects absolute paths, ``~``, NUL, and any ``..`` segment (traversal). The
    container already runs non-root + cap-dropped; this is defense-in-depth so a
    tool call can never escape the project tree.
    """
    p = (path or "").strip()
    if not p or p.startswith("/") or p.startswith("~") or "\x00" in p or ".." in p.split("/"):
        raise OrchestratorError(
            code="validation_failed",
            message=f"unsafe path: {path!r}",
            status_code=403,
        )
    return p


def _project_workspace_dir(project_id: str) -> Path:
    return Path(get_settings().projects_root) / project_id


def _sandbox_name_is_secret(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in {"secrets.json", "secrets.yaml", "secrets.yml"}
    )


def _copy_workspace(src: Path, dest: Path) -> None:
    def _ignore(directory: str, names: list[str]) -> list[str]:
        base = Path(directory)
        return [
            name
            for name in names
            if name in _SANDBOX_SKIP_NAMES
            or _sandbox_name_is_secret(name)
            or (base / name).is_symlink()
        ]

    shutil.copytree(src, dest, ignore=_ignore, dirs_exist_ok=True)


def _apply_workspace_files(project_id: str, files: dict[str, str]) -> None:
    root = _project_workspace_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    canonical_root = root.resolve()
    for raw_path, content in files.items():
        rel = _safe_app_path(raw_path)
        if _sandbox_name_is_secret(Path(rel).name):
            raise OrchestratorError(
                code="validation_failed",
                message=f"secret file is not allowed in the agent workspace: {rel}",
                status_code=403,
            )
        target = root / rel
        cursor = root
        for part in Path(rel).parts[:-1]:
            cursor /= part
            if cursor.is_symlink():
                raise OrchestratorError(
                    code="validation_failed",
                    message=f"workspace path crosses a symlink: {rel}",
                    status_code=403,
                )
        try:
            target.parent.resolve().relative_to(canonical_root)
        except (OSError, ValueError) as exc:
            raise OrchestratorError(
                code="validation_failed",
                message=f"workspace path escapes project root: {rel}",
                status_code=403,
            ) from exc
        if target.is_symlink():
            raise OrchestratorError(
                code="validation_failed",
                message=f"workspace target is a symlink: {rel}",
                status_code=403,
            )
        if content == "":
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _collect_workspace_text_files(root: Path) -> tuple[dict[str, str], set[str]]:
    files: dict[str, str] = {}
    dropped: set[str] = set()
    total_bytes = 0
    if not root.exists():
        return files, dropped
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                dropped.add(path.relative_to(root).as_posix())
            except ValueError:
                pass
            continue
        if not path.is_file():
            continue
        if any(part in _SANDBOX_SKIP_NAMES for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if _sandbox_name_is_secret(path.name):
            dropped.add(rel)
            continue
        try:
            size = path.stat().st_size
        except OSError:
            dropped.add(rel)
            continue
        if size > _SANDBOX_SYNC_MAX_FILE_BYTES:
            dropped.add(rel)
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            dropped.add(rel)
            continue
        files[rel] = content
        total_bytes += len(content.encode("utf-8"))
        if len(files) > _SANDBOX_SYNC_MAX_FILES:
            raise OrchestratorError(
                code="validation_failed",
                message=(
                    f"sandbox workspace exceeds sync file budget: {len(files)} > "
                    f"{_SANDBOX_SYNC_MAX_FILES}"
                ),
                status_code=413,
            )
        if total_bytes > _SANDBOX_SYNC_MAX_TOTAL_BYTES:
            raise OrchestratorError(
                code="validation_failed",
                message=(
                    "sandbox workspace exceeds sync payload budget: "
                    f"{total_bytes} > {_SANDBOX_SYNC_MAX_TOTAL_BYTES}"
                ),
                status_code=413,
            )
    return files, dropped


def _diff_workspace_files(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    changed = {path: content for path, content in after.items() if before.get(path) != content}
    for path in before.keys() - after.keys():
        changed[path] = ""
    if len(changed) > _SANDBOX_SYNC_MAX_FILES:
        raise OrchestratorError(
            code="validation_failed",
            message=(
                f"sandbox changed too many files: {len(changed)} > {_SANDBOX_SYNC_MAX_FILES}"
            ),
            status_code=413,
        )
    total_bytes = sum(len(content.encode("utf-8")) for content in changed.values() if content)
    if total_bytes > _SANDBOX_SYNC_MAX_TOTAL_BYTES:
        raise OrchestratorError(
            code="validation_failed",
            message=(
                "sandbox changed files exceed sync payload budget: "
                f"{total_bytes} > {_SANDBOX_SYNC_MAX_TOTAL_BYTES}"
            ),
            status_code=413,
        )
    return changed


@router.post("/provision", response_model=ProvisionResponse)
async def provision(
    payload: ProvisionRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> ProvisionResponse:
    """Clone template, allocate port, start dev container, return dev URL.

    PoC scope (today): port + template copy + container start. Sprint A1 will
    extend with Postgres schema, nginx site, per-project network, health-poll.
    """
    _verify_token(x_internal_token)
    return await provision_svc(payload)


@router.post("/{project_id}/max-preview-session", response_model=MaxPreviewSessionResponse)
async def create_max_preview_session(
    project_id: UUID,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> MaxPreviewSessionResponse:
    """Issue a short-lived, development-only MAX preview bootstrap URL.

    This deliberately never starts containers or creates secrets: callers can
    bootstrap only a running MAX template which was previously provisioned.
    """
    _verify_token(x_internal_token)
    canonical_project_id = str(project_id)

    container_name = await find_project_container(canonical_project_id, kind="dev")
    if container_name is None:
        raise OrchestratorError(
            code="not_found",
            message="no running MAX preview for this project",
            status_code=404,
        )
    status = await docker_container_status(container_name)
    if status["state"] != "running":
        raise OrchestratorError(
            code="container_not_running",
            message="MAX preview container is not running",
            status_code=409,
        )
    if await container_image_template(container_name) != _MAX_PREVIEW_TEMPLATE:
        raise OrchestratorError(
            code="unsupported_stack",
            message="project is not a MAX Mini App preview",
            status_code=409,
        )

    secret = load_existing_auth_secret(canonical_project_id)
    if secret is None:
        # A missing secret is not repaired here: doing so would make an unknown
        # project bootstrap-able and would hide an incomplete provision.
        raise OrchestratorError(
            code="not_found",
            message="MAX preview credentials are unavailable",
            status_code=404,
        )

    now = datetime.now(UTC)
    expires_at = now + _MAX_PREVIEW_BOOTSTRAP_TTL
    expires = int(expires_at.timestamp())
    signature = _max_preview_bootstrap_signature(secret, canonical_project_id, expires)
    slug = container_name.removeprefix("omnia-dev-")
    origin = nginx_writer.dev_url(slug)
    if not origin.startswith("https://"):
        raise OrchestratorError(
            code="container_failure",
            message="MAX preview requires an HTTPS development origin",
            status_code=503,
        )
    query = urlencode({"expires": expires, "signature": signature})
    bootstrap_url = f"{origin}{_MAX_PREVIEW_BOOTSTRAP_PATH}?{query}"
    return MaxPreviewSessionResponse(
        project_id=project_id,
        bootstrap_url=bootstrap_url,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
    )


@router.post("/wake", response_model=WakeResponse)
async def wake(
    payload: WakeRequest,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WakeResponse:
    """Resume a hibernated container. `paused` → unpause (~1-3 s, Pro tier).
    `exited` → docker start (~30-60 s cold, Free tier). Already-running is a
    no-op that returns ready=0.

    Resets the project's hibernate idle timer so the next sweep (60 s later)
    doesn't pause the container right back. Without this, a user clicking
    "wake" during an active session could see the preview die mid-edit when
    the sweeper read a stale `last_activity` from before the wake. The
    `slug` query param is an optional fallback for callers that don't yet
    label-resolve (same pattern as /stop and /status).
    """
    _verify_token(x_internal_token)

    name = await find_project_container(str(payload.project_id), kind="dev")
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        raise OrchestratorError(
            code="not_found",
            message="no dev container for this project — provision first",
            status_code=404,
        )

    info = await docker_container_status(name)
    state = info["state"]

    if state == "running":
        await record_activity(str(payload.project_id))
        return WakeResponse(
            project_id=payload.project_id,
            state="running",
            ready_in_seconds=0,
        )

    await wake_container(name)
    await record_activity(str(payload.project_id))

    # paused → unpause is near-instant; cold start ~30-60 s for Next.js dev
    # mode (first compile). Caller polls /status for the real readiness.
    ready = 2 if state == "paused" else 45

    # Live UI: the wake button doesn't need a poll-loop anymore — frontend's
    # runtime.started handler flips the cache on this event. ready_in_seconds
    # still lets the caller render an estimated wait.
    derived_slug = name.removeprefix("omnia-dev-")
    await publish_project_event(
        str(payload.project_id),
        "runtime.started",
        {
            "runtime": {
                "project_id": str(payload.project_id),
                "state": "running",
                "container_name": name,
                "dev_url": (nginx_writer.dev_url(derived_slug) if derived_slug else None),
            },
        },
    )

    return WakeResponse(
        project_id=payload.project_id,
        state="running",
        ready_in_seconds=ready,
    )


@router.post("/{project_id}/heartbeat")
async def heartbeat(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Reset the hibernate idle timer for a project — HTTP fallback for the
    Redis `activity:<project_id>` pub-sub channel.

    Steady-state production publishes activity from the ingress proxy to
    Redis (one less round-trip). This endpoint exists for environments
    without Redis (tests, bare-metal docker-compose) and for apps/api to
    use directly if its proxy already touches the orchestrator anyway.
    """
    _verify_token(x_internal_token)
    await record_activity(project_id)
    return {"state": "recorded"}


@router.post("/keep-alive", response_model=KeepAliveResponse)
async def keep_alive(
    payload: KeepAliveRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> KeepAliveResponse:
    """Enable or disable the durable no-hibernation mode for a project."""
    _verify_token(x_internal_token)
    await set_keep_alive(str(payload.project_id), payload.enabled)
    return KeepAliveResponse(project_id=payload.project_id, enabled=payload.enabled)


@router.post("/stop", response_model=WakeResponse)
async def stop(
    payload: StopRequest,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WakeResponse:
    """Force-hibernate via docker pause/stop.

    Resolves the container by the `omnia.project_id` label; `slug` is an
    optional fallback kept for backward-compat. This is the pause-never-stops
    fix: apps/api never sent the `slug` query param, so the old required-slug
    signature returned 422 and the container kept running.
    """
    _verify_token(x_internal_token)
    name = await find_project_container(str(payload.project_id), kind="dev")
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        # Nothing to stop — already gone. Idempotent.
        return WakeResponse(project_id=payload.project_id, state="stopped", ready_in_seconds=0)
    await stop_container(name, pause=payload.pause)
    new_state = "paused" if payload.pause else "stopped"

    # Live UI: api → ws_hub forwards this to the project's WebSocket clients,
    # which flip ["runtime", projectId] cache so the workspace's "Запустить"
    # button reappears and the iframe gracefully swaps to the startup panel
    # instead of staring at a dead live URL.
    await publish_project_event(
        str(payload.project_id),
        "runtime.stopped",
        {
            "runtime": {
                "project_id": str(payload.project_id),
                "state": new_state,
                "container_name": name,
            },
        },
    )

    return WakeResponse(
        project_id=payload.project_id,
        state=new_state,
        ready_in_seconds=0,
    )


@router.post("/hot-reload")
async def hot_reload(
    payload: HotReloadRequest,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Copy AI-generated files into the running dev container; Next.js HMR
    picks up changes without restart.

    Lookup is by `omnia-dev-<slug>` for the same reason `status` / `destroy`
    do it (PoC: no project-name registry yet — apps/api always knows the slug).
    `slug` is a query param to keep the JSON body matching `HotReloadRequest`
    exactly (which only carries project_id + files; slug-resolution is the
    orchestrator's internal concern).

    Side-effects beyond the file write:
      - If any file under `src/lib/db/schema.ts` or `src/lib/db/migrations/`
        changed, run `npm exec drizzle-kit push` in the container. This makes
        the new schema/migrations land in the project's Postgres without
        the user having to ask. Failure here is logged into the response but
        does NOT fail the whole hot-reload (drizzle errors are far more
        useful inside the dev preview than as a 5xx to the user).
    """
    _verify_token(x_internal_token)
    # A build writing files is activity too — keep hibernate off its back.
    await record_activity(str(payload.project_id))
    container_name = f"omnia-dev-{slug}"

    write_result = await write_files(container_name, payload.files)
    await asyncio.to_thread(_apply_workspace_files, str(payload.project_id), payload.files)

    # Seed PUBLIC entity catalogs with demo rows so the first browse screen
    # isn't an empty-state (NORTH STAR pillars 1 & 4). Idempotent (only fills
    # empty catalogs) and fail-soft (never raises) — see demo_seed_writer.
    seeded = await demo_seed_writer.seed_demo_data(payload.project_id, payload.files, niche=slug)

    # Dependency selection belongs to the project now. Lifecycle scripts already
    # ran (if needed) in the secretless disposable sandbox; the live preview only
    # materialises the resolved tree with scripts disabled, so a package cannot
    # read runtime DB/auth/bot credentials during install.
    package_touched = any(
        path in {"package.json", "pnpm-lock.yaml"} for path in payload.files
    )
    package_result: dict[str, str] | None = None
    if package_touched:
        try:
            package_result = await exec_cmd(
                container_name,
                cmd=["pnpm", "install", "--no-frozen-lockfile", "--ignore-scripts"],
                workdir="/app",
                timeout_sec=240,
                max_output=_AGENT_MAX_BUILD,
            )
        except OrchestratorError as exc:
            package_result = {
                "exit_code": "-1",
                "stdout": "",
                "stderr": f"orchestrator: {exc.message}",
            }

    # If the AI touched the DB schema or migrations, push it to Postgres now.
    schema_touched = any(
        p == "src/lib/db/schema.ts" or p.startswith("src/lib/db/migrations/") for p in payload.files
    )
    drizzle_result: dict[str, str] | None = None
    if schema_touched:
        try:
            drizzle_result = await exec_cmd(
                container_name,
                # Deliberately omit --force. Drizzle applies additive changes
                # directly but asks before statements it classifies as data
                # loss; the non-interactive exec then fails closed and the API
                # returns that failure to the agent for a safe rewrite.
                cmd=[
                    "npx",
                    "--yes",
                    "drizzle-kit",
                    "push",
                    "--config=drizzle.config.ts",
                ],
                workdir="/app",
                timeout_sec=90,
            )
        except OrchestratorError as exc:
            # Log as failure but don't propagate — see docstring.
            drizzle_result = {
                "exit_code": "-1",
                "stdout": "",
                "stderr": f"orchestrator: {exc.message}",
            }

    response: dict[str, str] = {
        "state": "hot_reloaded",
        "written": write_result.get("written", "0"),
        "total_bytes": write_result.get("total_bytes", "0"),
        "dropped": write_result.get("dropped", ""),
        "seeded": str(sum(seeded.values())),
    }
    if drizzle_result is not None:
        response["drizzle_exit_code"] = drizzle_result["exit_code"]
        response["drizzle_stderr_tail"] = drizzle_result["stderr"][-500:]
    if package_result is not None:
        response["package_exit_code"] = package_result["exit_code"]
        response["package_stderr_tail"] = package_result["stderr"][-500:]
    return response


@router.get("/{project_id}/agent/sandbox-capabilities")
async def agent_sandbox_capabilities(
    project_id: str,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Attest the concrete security contract behind the MAX shell tool.

    The response contains facts and feature names only—never environment values
    or credentials.  apps/api uses ``ready`` as a fail-closed gate before it
    even advertises ``bash`` to the model.
    """
    _verify_token(x_internal_token)
    settings = get_settings()
    workspace = _project_workspace_dir(project_id)
    runtime_name = f"omnia-dev-{slug}"
    image = await container_image_name(runtime_name)
    runtime_facts = await container_security_facts(runtime_name, project_id)
    missing: list[str] = []
    runtime_missing = runtime_facts.get("missing", [])
    if not isinstance(runtime_missing, list):
        runtime_missing = []
    if not settings.agent_sandbox_enabled:
        missing.append("agent_sandbox_disabled")
    if not workspace.is_dir():
        missing.append("workspace_missing")
    if not image:
        missing.append("template_image_missing")
    if not runtime_facts.get("ready"):
        missing.extend(f"runtime:{item}" for item in runtime_missing)
    return {
        "ready": not missing,
        "profile": "ephemeral-secretless-v1",
        "missing": missing,
        "capabilities": {
            "shell": True,
            "dependency_manifest_edit": True,
            "dependency_sync_without_lifecycle_scripts": True,
            "source_generators": True,
            "tests": True,
            "workspace_diff": True,
        },
        "isolation": {
            "ephemeral": True,
            "non_root": True,
            "read_only_rootfs": True,
            "tmpfs_workspace": True,
            "host_workspace_writable": False,
            "network": False,
            "runtime_network": False,
            "host_gateway": False,
            "docker_socket": False,
            "runtime_secrets": False,
            "cap_drop_all": True,
            "no_new_privileges": True,
            "pids_limit": max(1, settings.container_pids_limit or 256),
            "runtime": settings.container_runtime or "runc",
        },
        "runtime_attestation": runtime_facts,
    }


@router.post("/{project_id}/agent/exec-sandbox")
async def agent_exec_sandbox(
    project_id: str,
    slug: str,
    cmd: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Run one shell command in an isolated project sandbox and return a diff.

    The sandbox starts from the mirrored project workspace on disk, not the
    live preview container, so the caller can inspect and vet shell-produced
    file changes before syncing them into the running app.
    """
    _verify_token(x_internal_token)
    await record_activity(project_id)
    if not get_settings().agent_sandbox_enabled:
        raise OrchestratorError(
            code="container_failure",
            message="agent sandbox is disabled by the operator",
            status_code=503,
        )
    low = (cmd or "").strip()
    if not low:
        raise OrchestratorError(
            code="validation_failed",
            message="empty cmd",
            status_code=400,
        )
    if any(bad in low for bad in _EXEC_DENY):
        return {"ok": False, "detail": "command blocked by safety denylist"}
    if _command_exposes_environment(low):
        return {
            "ok": False,
            "detail": "command blocked: environment and secret enumeration is not allowed",
        }

    workspace = _project_workspace_dir(project_id)
    if not workspace.is_dir():
        raise OrchestratorError(
            code="not_found",
            message="project sandbox workspace not found",
            status_code=404,
        )
    image = await container_image_name(f"omnia-dev-{slug}")
    if not image:
        raise OrchestratorError(
            code="not_found",
            message="project dev container image not found",
            status_code=404,
        )

    before_files, before_dropped = await asyncio.to_thread(_collect_workspace_text_files, workspace)
    settings = get_settings()
    network_name = f"omnia-proj-{project_id}" if settings.isolate_project_network else None
    with tempfile.TemporaryDirectory(prefix=f"omnia-sandbox-{project_id}-") as tmp:
        sandbox_root = Path(tmp) / "workspace"
        await asyncio.to_thread(_copy_workspace, workspace, sandbox_root)
        try:
            result = await run_sandbox_command(
                image=image,
                workspace_dir=sandbox_root,
                project_id=project_id,
                cmd=cmd,
                network_name=network_name,
                runtime=settings.container_runtime,
                harden=settings.container_harden,
                pids_limit=settings.container_pids_limit,
                timeout_sec=180,
                max_output=_AGENT_MAX_BUILD,
            )
        except OrchestratorError as exc:
            if exc.code == "container_not_running":
                raise
            return {"ok": False, "detail": exc.message, "files": {}, "changed": "0", "dropped": ""}
        after_files, after_dropped = await asyncio.to_thread(
            _collect_workspace_text_files, sandbox_root
        )

    changed_files = _diff_workspace_files(before_files, after_files)
    ok = result["exit_code"] == "0"
    out = _redact_exec_output((result["stdout"] + "\n" + result["stderr"]).strip())
    detail = out[:_AGENT_MAX_BUILD] or ("ok" if ok else "non-zero exit")
    if changed_files:
        detail += f"\n\nSandbox prepared {len(changed_files)} file change(s)."
    new_dropped = sorted(after_dropped.difference(before_dropped))
    if new_dropped:
        preview = ", ".join(new_dropped[:10])
        suffix = "…" if len(new_dropped) > 10 else ""
        detail += f"\nDropped unsynced files: {preview}{suffix}"
    return {
        "ok": ok,
        "exit_code": result["exit_code"],
        "detail": detail,
        "files": changed_files,
        "changed": str(len(changed_files)),
        "dropped": ",".join(new_dropped),
    }


@router.get("/{project_id}/read-file")
async def read_file(
    project_id: str,
    slug: str,
    path: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Read a single whitelisted file from the running dev container.

    Only the fixed ``globals.css`` is exposed (see ``_READABLE_FILES``). Returns
    ``{found, content}``; a missing file / stopped container yields
    ``found=False`` rather than an error, so the caller can fall back cleanly.
    """
    _verify_token(x_internal_token)
    if path not in _READABLE_FILES:
        raise OrchestratorError(
            code="validation_failed",
            message=f"path not readable: {path}",
            status_code=403,
        )
    container_name = f"omnia-dev-{slug}"
    try:
        # Read the whole file: globals.css (~10 KB) exceeds exec_cmd's default
        # 8 KB log cap, which would truncate it mid-rule and break the CSS build.
        # 1 MB ceiling stays bounded (whitelist holds only small fixed files).
        result = await exec_cmd(
            container_name, cmd=["cat", path], workdir="/app", max_output=1_000_000
        )
    except OrchestratorError:
        # Container not running / not found — let the caller fall back.
        return {"found": False, "content": ""}
    found = result["exit_code"] == "0"
    return {"found": found, "content": result["stdout"] if found else ""}


# ── Agentic builder tools (Phase 0) ─────────────────────────────────────────
# Internal-token-gated capability surface the api-side agent loop calls to act
# on the live dev container: read any /app file, list, grep, and run a real
# typecheck/build. Separate from the whitelisted ``read-file`` above (used by
# style edits) so that path is untouched. exec_cmd already runs non-root
# (1000:1000) inside the cap-dropped container; `_safe_app_path` blocks escape.


@router.get("/{project_id}/agent/read-file")
async def agent_read_file(
    project_id: str,
    slug: str,
    path: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Read ANY file under /app from the running dev container (agent loop)."""
    _verify_token(x_internal_token)
    # An agent op IS activity: without this the hibernate sweeper sees a purely
    # reading build agent as idle and docker-stops the container MID-BUILD
    # (2026-07-08 incident). Same for every agent/* handler below.
    await record_activity(project_id)
    rel = _safe_app_path(path)
    container_name = f"omnia-dev-{slug}"
    try:
        result = await exec_cmd(
            container_name,
            cmd=["cat", "--", rel],
            workdir="/app",
            max_output=_AGENT_MAX_READ,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise  # structured 409 → apps/api circuit breaker aborts the build
        return {"found": False, "content": ""}
    found = result["exit_code"] == "0"
    return {
        "found": found,
        "content": result["stdout"] if found else "",
        "error": "" if found else (result["stderr"][:500] or "not found"),
    }


@router.get("/{project_id}/agent/list-dir")
async def agent_list_dir(
    project_id: str,
    slug: str,
    path: str = ".",
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """List a directory under /app (agent loop)."""
    _verify_token(x_internal_token)
    await record_activity(project_id)
    rel = _safe_app_path(path)
    container_name = f"omnia-dev-{slug}"
    try:
        result = await exec_cmd(
            container_name,
            cmd=["ls", "-la", "--", rel],
            workdir="/app",
            max_output=_AGENT_MAX_LIST,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise
        return {"ok": False, "detail": "container not running"}
    ok = result["exit_code"] == "0"
    return {"ok": ok, "detail": result["stdout"] if ok else result["stderr"]}


@router.get("/{project_id}/agent/grep")
async def agent_grep(
    project_id: str,
    slug: str,
    pattern: str,
    path: str = "src",
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Recursive text search under /app (agent loop). grep exit 1 = no match."""
    _verify_token(x_internal_token)
    await record_activity(project_id)
    rel = _safe_app_path(path)
    if not pattern:
        raise OrchestratorError(
            code="validation_failed",
            message="empty pattern",
            status_code=400,
        )
    container_name = f"omnia-dev-{slug}"
    try:
        # argv (no shell) → no injection; `--` ends options so a pattern that
        # starts with `-` can't become a flag.
        result = await exec_cmd(
            container_name,
            cmd=["grep", "-rnI", "--", pattern, rel],
            workdir="/app",
            max_output=_AGENT_MAX_GREP,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise
        return {"ok": False, "detail": "container not running"}
    out = result["stdout"]
    return {"ok": True, "detail": out if out else "(no matches)"}


async def _run_dep_doctor(container_name: str) -> str:
    """Install missing allowlisted deps BEFORE typecheck so a TS2307 "Cannot find
    module" (kit-file drift or a generated import of an undeclared package) heals
    instead of aborting the whole build — the agent edits source, but a baked
    ``node_modules`` is not a source file. Returns a short status line (empty when
    nothing was installed). Fail-soft: any error → "" and the typecheck then
    surfaces the real module error exactly as today (no regression)."""
    if not get_settings().use_dep_doctor:
        return ""
    try:
        pj = await exec_cmd(
            container_name,
            cmd=["cat", "--", "package.json"],
            workdir="/app",
            max_output=_AGENT_MAX_READ,
        )
        if pj["exit_code"] != "0":
            return ""
        imports = await exec_cmd(
            container_name,
            cmd=["sh", "-lc", 'grep -rhsE "(from|import|require)" src 2>/dev/null || true'],
            # Generous cap: import lines across a whole src/ tree already exceed the
            # 16 KB grep cap on the default nextjs-entities template (~28 KB), which
            # would silently drop packages past the cut and leave them uninstalled.
            workdir="/app",
            max_output=_AGENT_MAX_READ,
        )
        missing = dep_doctor.plan_installs(pj["stdout"], imports["stdout"])
        if not missing:
            return ""
        # Names passed the allowlist AND a strict package-name regex, so they
        # carry no shell metacharacters — safe to interpolate into `pnpm add`.
        res = await exec_cmd(
            container_name,
            cmd=["sh", "-lc", f"cd /app && pnpm add {' '.join(missing)}"],
            workdir="/app",
            timeout_sec=120,
            max_output=_AGENT_MAX_BUILD,
        )
        verb = "installed" if res["exit_code"] == "0" else "FAILED to install"
        note = f"[dep-doctor] {verb}: {' '.join(missing)}"
        print(note, flush=True)
        return note
    except OrchestratorError:
        return ""


@router.post("/{project_id}/agent/build")
async def agent_build(
    project_id: str,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Run the project's local TypeScript typecheck — a real, deterministic
    correctness signal independent of HMR timing. Non-zero exit returns the
    actual compiler errors so the agent can fix them. A dep-doctor pass first
    installs any missing allowlisted package (see ``_run_dep_doctor``)."""
    _verify_token(x_internal_token)
    await record_activity(project_id)
    container_name = f"omnia-dev-{slug}"
    dep_note = await _run_dep_doctor(container_name)
    # Next dev writes route validators for the source tree that existed at the
    # time of its last successful compile. Agent writes/rollback can replace or
    # remove a page faster than HMR refreshes those generated imports, which made
    # a valid restored tree fail on a ghost `.next/types/app/page.ts` reference.
    # Remove only regenerable route validators before the independent source
    # typecheck; keep routes.d.ts because next-env.d.ts references it directly.
    try:
        await exec_cmd(
            container_name,
            cmd=[
                "rm",
                "-rf",
                "--",
                "/app/.next/types/app",
                "/app/.next/types/validator.ts",
            ],
            workdir="/app",
            max_output=1_024,
        )
    except OrchestratorError:
        pass
    try:
        result = await exec_cmd(
            container_name,
            cmd=["/app/node_modules/.bin/tsc", "--noEmit", "-p", "/app/tsconfig.json"],
            workdir="/app",
            timeout_sec=180,
            max_output=_AGENT_MAX_BUILD,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise
        return {"ok": False, "error": exc.message}
    ok = result["exit_code"] == "0"
    detail = (result["stdout"] + "\n" + result["stderr"]).strip()
    body = "typecheck clean" if ok else detail[:_AGENT_MAX_BUILD]
    # Surface the dep-doctor action in the observation so the agent + operators
    # see "[dep-doctor] installed: sonner" instead of a silent self-heal.
    if dep_note:
        body = f"{dep_note}\n{body}"
    return {"ok": ok, "detail": body}


# Phase 1: a bounded shell tool for the agent. Runs an arbitrary command inside
# the project's dev container via `sh -lc`. Safe-by-construction: the container
# is cap-dropped (ALL), non-root (1000:1000), memory-capped, loopback-bound, on
# an isolated network, with a schema-scoped DB role — so the blast radius is the
# project's own container. Bounded by timeout + output cap. (Egress lockdown is
# a follow-up; today outbound is open.) A small denylist blocks the obvious
# foot-guns. Lets the agent run npm install / lint / tests / the dev server.
_EXEC_DENY = ("rm -rf /", ":(){", "mkfs", "dd if=", "/dev/sd", "shutdown", "reboot")
_EXEC_ENV_ENUM_RE = re.compile(
    r"""(?ix)
    \b(?:env|printenv)\b
    |
    (?:^|[;&|]\s*)(?:export|set|declare\s+-x)(?:\s*(?:$|[;&|]))
    |
    /proc/[^\s]*/environ
    |
    \b(?:process\.env|os\.environ|os\.getenv)\b
    |
    (?:^|[\s/])\.env(?:\.[\w.-]+)?(?:\s|$)
    |
    \$\{?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|PRIVATE_KEY|ACCESS_KEY|API_KEY|DATABASE_URL)[A-Z0-9_]*\}?
    """
)
_EXEC_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Z0-9_]*"
    r"(?:SECRET|TOKEN|PASSWORD|PASS|PRIVATE_KEY|ACCESS_KEY|API_KEY|DATABASE_URL)"
    r"[A-Z0-9_]*\s*=\s*)([^\r\n]*)$"
)
_EXEC_DSN_RE = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|redis|mongodb)"
    r"(?:\+[a-z0-9_]+)?://[^:\s/@]+:)([^@\s/]+)(@)"
)
_EXEC_AUTH_HEADER_RE = re.compile(
    r"(?im)^(\s*(?:authorization|x-api-key|x-max-bot-api-secret)\s*:\s*)"
    r"([^\r\n]+)$"
)
_EXEC_KNOWN_TOKEN_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b")
_EXEC_PRIVATE_KEY_RE = re.compile(
    r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"
)


def _redact_exec_output(value: str) -> str:
    redacted = _EXEC_SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", value)
    redacted = _EXEC_DSN_RE.sub(r"\1[REDACTED]\3", redacted)
    redacted = _EXEC_AUTH_HEADER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _EXEC_KNOWN_TOKEN_RE.sub("[REDACTED]", redacted)
    return _EXEC_PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", redacted)


def _command_exposes_environment(cmd: str) -> bool:
    return bool(_EXEC_ENV_ENUM_RE.search(cmd.strip()))


@router.post("/{project_id}/agent/exec")
async def agent_exec(
    project_id: str,
    slug: str,
    cmd: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Run a shell command in the project's dev container (agent `bash` tool)."""
    _verify_token(x_internal_token)
    await record_activity(project_id)
    low = (cmd or "").strip()
    if not low:
        raise OrchestratorError(
            code="validation_failed",
            message="empty cmd",
            status_code=400,
        )
    if any(bad in low for bad in _EXEC_DENY):
        return {"ok": False, "detail": "command blocked by safety denylist"}
    if _command_exposes_environment(low):
        return {
            "ok": False,
            "detail": "command blocked: environment and secret enumeration is not allowed",
        }
    container_name = f"omnia-dev-{slug}"
    try:
        result = await exec_cmd(
            container_name,
            cmd=["sh", "-lc", cmd],
            workdir="/app",
            timeout_sec=180,
            max_output=_AGENT_MAX_BUILD,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise
        return {"ok": False, "detail": exc.message}
    ok = result["exit_code"] == "0"
    out = _redact_exec_output((result["stdout"] + "\n" + result["stderr"]).strip())
    return {
        "ok": ok,
        "exit_code": result["exit_code"],
        "detail": out[:_AGENT_MAX_BUILD] or ("ok" if ok else "non-zero exit"),
    }


def _deploy_record_to_response(rec: deploy_state.DeployRecord) -> DeployResponse:
    from uuid import UUID

    return DeployResponse(
        project_id=UUID(rec.project_id),
        run_id=rec.run_id,
        phase=rec.phase,
        prod_url=rec.prod_url,
        image_tag=rec.image_tag,
        error=rec.error,
        detail=rec.detail,
        target_label=rec.target_label,
        target_id=rec.target_id,
        can_cancel=rec.can_cancel,
        logs=rec.logs,
        started_at=rec.started_at,
        finished_at=rec.finished_at,
    )


@router.post("/deploy", response_model=DeployResponse)
async def deploy(
    payload: DeployRequest,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> DeployResponse:
    """Build a prod image from the LIVE dev container, run it, swap nginx.

    Async: returns immediately with phase=building and the deterministic prod
    URL; progress is tracked server-side and read via GET .../deploy. `slug` is
    optional — the dev container is resolved by the `omnia.project_id` label.
    """
    _verify_token(x_internal_token)
    target = payload.target.model_dump() if payload.target else None
    rec = await builder.start_deploy(
        str(payload.project_id),
        slug,
        target,
        payload.domains,
        payload.idempotency_key,
        payload.runtime_env,
    )
    return _deploy_record_to_response(rec)


@router.post("/{project_id}/deploy/cancel", response_model=DeployResponse)
async def cancel_deploy(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> DeployResponse:
    """Cancel the active build/transfer and keep the previous version live."""
    _verify_token(x_internal_token)
    from uuid import UUID

    rec = await builder.cancel_deploy(project_id)
    if rec is None:
        return DeployResponse(project_id=UUID(project_id), phase="cancelled")
    return _deploy_record_to_response(rec)


@router.get("/{project_id}/deploy", response_model=DeployResponse)
async def get_deploy(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> DeployResponse:
    """Last deploy state for a project (phase / prod_url / image_tag / error)."""
    _verify_token(x_internal_token)
    from uuid import UUID

    rec = deploy_state.get(project_id)
    if rec is None:
        return DeployResponse(project_id=UUID(project_id), phase="queued")
    return _deploy_record_to_response(rec)


@router.get("/{project_id}/deploy/history", response_model=list[DeployResponse])
async def get_deploy_history(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> list[DeployResponse]:
    _verify_token(x_internal_token)
    return [
        _deploy_record_to_response(record) for record in reversed(deploy_state.history(project_id))
    ]


@router.get("/{project_id}/status", response_model=StatusResponse)
async def status(
    project_id: str,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> StatusResponse:
    """Container state derived from Docker inspect.

    Resolves the dev container by the `omnia.project_id` label (`slug` is an
    optional fallback). Returns the browser-reachable nginx dev URL — not the
    `127.0.0.1:<port>` loopback, which was the "connection refused" preview.
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    name = await find_project_container(project_id, kind="dev")
    keep_alive = is_keep_alive_enabled(project_id)
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        return StatusResponse(
            project_id=UUID(project_id),
            state="stopped",
            keep_alive=keep_alive,
        )

    info = await docker_container_status(name)
    if info["state"] == "not_found":
        return StatusResponse(
            project_id=UUID(project_id),
            state="stopped",
            keep_alive=keep_alive,
        )

    state_map = {
        "running": "running",
        "paused": "paused",
        "exited": "stopped",
        "created": "provisioning",
        "restarting": "provisioning",
        "dead": "failed",
    }
    derived_slug = name.removeprefix("omnia-dev-")

    # Area C (DARK): expose the per-project AUTH_SECRET so the gate worker can
    # re-derive the seed operator's password and drive a real login. Populated
    # ONLY when OMNIA_GATE_SEED=1; null otherwise → contract unchanged. The
    # secret comes from _load_or_create_auth_secret, which is idempotent and
    # read-only once the per-project secret file exists.
    gate_seed: dict[str, str] | None = None
    if os.getenv("OMNIA_GATE_SEED") == "1":
        from omnia_orchestrator.services.provisioner import (
            _load_or_create_auth_secret,
        )

        gate_seed = {
            "email": os.getenv("OMNIA_GATE_SEED_EMAIL", "gate@omnia.local"),
            "auth_secret": _load_or_create_auth_secret(project_id),
        }

    return StatusResponse(
        project_id=UUID(project_id),
        state=state_map.get(info["state"], "stopped"),
        container_name=name,
        port=int(info["port"]) if info["port"] else None,
        dev_url=nginx_writer.dev_url(derived_slug) if derived_slug else None,
        keep_alive=keep_alive,
        gate_seed=gate_seed,
    )


@router.get("/{project_id}/logs", response_model=LogsResponse)
async def logs(
    project_id: str,
    slug: str | None = None,
    tail: int = 200,
    kind: str = "dev",
    x_internal_token: Annotated[str | None, Header()] = None,
) -> LogsResponse:
    """Tail recent stdout/stderr from the project's container.

    Reads via `docker logs --tail N` (`docker_client.container_logs`). No
    follow stream yet — frontend polls every 3 s for live updates. Caller
    must pick `kind="dev"` (default) or `"prod"`; we resolve the container
    name via the same label-lookup pattern used by /status and /stop.

    Missing container returns 200 with empty `logs` — UI shows "No logs"
    instead of a confusing 404 when the project has been hibernated.
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    name = await find_project_container(project_id, kind=kind)
    if name is None and slug:
        name = f"omnia-{kind}-{slug}"
    if name is None:
        return LogsResponse(
            project_id=UUID(project_id),
            container_name=None,
            tail=tail,
            logs="",
        )

    if tail < 1:
        tail = 1
    elif tail > 5000:
        tail = 5000  # cap to keep payloads bounded

    result = await container_logs(name, tail=tail, kind=kind)
    return LogsResponse(
        project_id=UUID(project_id),
        container_name=name,
        tail=tail,
        logs=result["logs"],
    )


@router.post("/{project_id}/warm")
async def warm(
    project_id: str,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, int | str]:
    """Pre-warm the dev app's static routes so a demo hits WARM pages.

    `next dev` compiles each route lazily on first request (~30-90 s cold), so a
    reviewer eats that per page. apps/api calls this fire-and-forget right after a
    successful build to force those first requests itself. Best-effort: a missing
    container or any warm failure returns a benign summary, never an error — the
    app just falls back to the normal cold-first-hit behaviour.
    """
    _verify_token(x_internal_token)

    name = await find_project_container(project_id, kind="dev")
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        return {"warmed": 0, "note": "no container"}
    return await warm_routes(name)


@router.get("/{project_id}/compile-status", response_model=CompileStatusResponse)
async def compile_status(
    project_id: str,
    slug: str | None = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> CompileStatusResponse:
    """Whether the dev container's Next.js/Turbopack build currently fails.

    apps/api polls this right after a hot-reload so the chat can surface a
    compile error as a card instead of leaving the user on a broken preview.
    Reads recent dev logs and parses them (see ``services.compile_status``).

    Missing container → ``ok=True`` (no app, nothing to report) — same
    fail-soft posture as ``/logs``: never raise a 404 the caller would have to
    special-case.
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    name = await find_project_container(project_id, kind="dev")
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        return CompileStatusResponse(project_id=UUID(project_id), ok=True)

    result = await container_logs(name, tail=250, kind="dev")
    ok, error, file = parse_next_compile_error(result["logs"])
    return CompileStatusResponse(project_id=UUID(project_id), ok=ok, error=error, file=file)


@router.get("/{project_id}/runtime-status", response_model=RuntimeStatusResponse)
async def runtime_status(
    project_id: str,
    slug: str | None = None,
    path: str = "/",
    x_internal_token: Annotated[str | None, Header()] = None,
) -> RuntimeStatusResponse:
    """Whether the running dev app currently 5xx's on render.

    A compile-clean app can still throw a 500 when a route is actually rendered
    (server components / data fetching run lazily, per-route). apps/api polls
    this right after a build so a broken-on-load preview surfaces as a card
    instead of leaving the user staring at a Next.js error overlay.

    Missing / paused container → ``ok=True`` (nothing to probe) — same fail-soft
    posture as ``/compile-status``: never raise a 404 the caller must special-case.
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    name = await find_project_container(project_id, kind="dev")
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        return RuntimeStatusResponse(project_id=UUID(project_id), ok=True)

    probe = await probe_runtime_error(name, path=path)
    return RuntimeStatusResponse(
        project_id=UUID(project_id),
        ok=probe.ok,
        status_code=probe.status_code,
        error=probe.error,
        file=probe.file,
    )


@router.post("/{project_id}/destroy")
async def destroy(
    project_id: str,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Full teardown of a project's runtime. Mirrors :func:`provision` in reverse.

    Removes the dev + prod containers, releases both ports, archives the
    per-project Postgres schema (soft-delete — rule 5: user data is kept for a
    grace window, not hard-dropped), and removes the dev + prod nginx vhosts.

    Idempotent (R-10): every step is a no-op when its resource is already gone,
    so apps/api can safely retry after a partial failure. `slug` query param has
    the same rationale as `status`/`hot-reload` (no project_id↔name registry).
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    pid = UUID(project_id)

    # 1. Containers — dev + prod. Missing is a no-op.
    await destroy_container(f"omnia-dev-{slug}")
    await destroy_container(f"omnia-app-{slug}")

    # 2. Ports — dev + prod pools.
    await get_port_allocator().release(pid)
    await get_prod_port_allocator().release(pid)

    # 3. Per-project Postgres — soft archive (rename aside), keep data recoverable.
    await postgres_admin.archive_schema(pid)

    # 4. nginx vhosts — dev + prod. Missing site is a no-op.
    await nginx_writer.unpublish(nginx_writer.dev_host(slug))
    await nginx_writer.unpublish(nginx_writer.prod_host(slug))

    return {"state": "destroyed"}
