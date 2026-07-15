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

import os
from typing import Annotated

from fastapi import APIRouter, Header

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import (
    container_logs,
    destroy_container,
    exec_cmd,
    find_project_container,
    stop_container,
    wake_container,
    write_files,
)
from omnia_orchestrator.core.docker_client import (
    container_status as docker_container_status,
)
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.event_publisher import publish_project_event
from omnia_orchestrator.schemas.runtime import (
    CompileStatusResponse,
    DeployRequest,
    DeployResponse,
    HotReloadRequest,
    LogsResponse,
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
from omnia_orchestrator.services.hibernate import record_activity
from omnia_orchestrator.services.port_allocator import (
    get_port_allocator,
    get_prod_port_allocator,
)
from omnia_orchestrator.services.provisioner import provision as provision_svc
from omnia_orchestrator.services.runtime_probe import probe_runtime_error

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


def _safe_app_path(path: str) -> str:
    """Validate an agent-supplied path stays inside /app and return it relative.

    Rejects absolute paths, ``~``, NUL, and any ``..`` segment (traversal). The
    container already runs non-root + cap-dropped; this is defense-in-depth so a
    tool call can never escape the project tree.
    """
    p = (path or "").strip()
    if (
        not p
        or p.startswith("/")
        or p.startswith("~")
        or "\x00" in p
        or ".." in p.split("/")
    ):
        raise OrchestratorError(
            code="validation_failed",
            message=f"unsafe path: {path!r}",
            status_code=403,
        )
    return p


def _verify_token(token: str | None) -> None:
    expected = get_settings().internal_token.get_secret_value()
    if not token or token != expected:
        raise OrchestratorError(
            code="unauthorized",
            message="missing or invalid X-Internal-Token",
            status_code=401,
        )


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
                "dev_url": (
                    nginx_writer.dev_url(derived_slug) if derived_slug else None
                ),
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
        return WakeResponse(
            project_id=payload.project_id, state="stopped", ready_in_seconds=0
        )
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

    # Seed PUBLIC entity catalogs with demo rows so the first browse screen
    # isn't an empty-state (NORTH STAR pillars 1 & 4). Idempotent (only fills
    # empty catalogs) and fail-soft (never raises) — see demo_seed_writer.
    seeded = await demo_seed_writer.seed_demo_data(
        payload.project_id, payload.files, niche=slug
    )

    # If the AI touched the DB schema or migrations, push it to Postgres now.
    schema_touched = any(
        p == "src/lib/db/schema.ts" or p.startswith("src/lib/db/migrations/")
        for p in payload.files
    )
    drizzle_result: dict[str, str] | None = None
    if schema_touched:
        try:
            drizzle_result = await exec_cmd(
                container_name,
                # --force: non-interactive. Without it drizzle-kit prompts
                # "Yes/No, abort" on any change it deems risky and the exec
                # stalls/aborts, so the model's new tables never get created
                # and its DB-backed pages 500 at runtime.
                cmd=["npx", "--yes", "drizzle-kit", "push", "--force", "--config=drizzle.config.ts"],
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
    return response


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
            container_name, cmd=["cat", "--", rel],
            workdir="/app", max_output=_AGENT_MAX_READ,
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
            container_name, cmd=["ls", "-la", "--", rel],
            workdir="/app", max_output=_AGENT_MAX_LIST,
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
            code="validation_failed", message="empty pattern", status_code=400,
        )
    container_name = f"omnia-dev-{slug}"
    try:
        # argv (no shell) → no injection; `--` ends options so a pattern that
        # starts with `-` can't become a flag.
        result = await exec_cmd(
            container_name, cmd=["grep", "-rnI", "--", pattern, rel],
            workdir="/app", max_output=_AGENT_MAX_GREP,
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
            container_name, cmd=["cat", "--", "package.json"],
            workdir="/app", max_output=_AGENT_MAX_READ,
        )
        if pj["exit_code"] != "0":
            return ""
        imports = await exec_cmd(
            container_name,
            cmd=["sh", "-lc", 'grep -rhsE "(from|import|require)" src 2>/dev/null || true'],
            # Generous cap: import lines across a whole src/ tree already exceed the
            # 16 KB grep cap on the default nextjs-entities template (~28 KB), which
            # would silently drop packages past the cut and leave them uninstalled.
            workdir="/app", max_output=_AGENT_MAX_READ,
        )
        missing = dep_doctor.plan_installs(pj["stdout"], imports["stdout"])
        if not missing:
            return ""
        # Names passed the allowlist AND a strict package-name regex, so they
        # carry no shell metacharacters — safe to interpolate into `pnpm add`.
        res = await exec_cmd(
            container_name,
            cmd=["sh", "-lc", f"cd /app && pnpm add {' '.join(missing)}"],
            workdir="/app", timeout_sec=120, max_output=_AGENT_MAX_BUILD,
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
            code="validation_failed", message="empty cmd", status_code=400,
        )
    if any(bad in low for bad in _EXEC_DENY):
        return {"ok": False, "detail": "command blocked by safety denylist"}
    container_name = f"omnia-dev-{slug}"
    try:
        result = await exec_cmd(
            container_name, cmd=["sh", "-lc", cmd],
            workdir="/app", timeout_sec=180, max_output=_AGENT_MAX_BUILD,
        )
    except OrchestratorError as exc:
        if exc.code == "container_not_running":
            raise
        return {"ok": False, "detail": exc.message}
    ok = result["exit_code"] == "0"
    out = (result["stdout"] + "\n" + result["stderr"]).strip()
    return {
        "ok": ok,
        "exit_code": result["exit_code"],
        "detail": out[:_AGENT_MAX_BUILD] or ("ok" if ok else "non-zero exit"),
    }


def _deploy_record_to_response(rec: deploy_state.DeployRecord) -> DeployResponse:
    from uuid import UUID

    return DeployResponse(
        project_id=UUID(rec.project_id),
        phase=rec.phase,  # type: ignore[arg-type]  # validated by DeployPhase
        prod_url=rec.prod_url,
        image_tag=rec.image_tag,
        error=rec.error,
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
    rec = await builder.start_deploy(str(payload.project_id), slug)
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
    if name is None and slug:
        name = f"omnia-dev-{slug}"
    if name is None:
        return StatusResponse(project_id=UUID(project_id), state="stopped")

    info = await docker_container_status(name)
    if info["state"] == "not_found":
        return StatusResponse(project_id=UUID(project_id), state="stopped")

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
    return CompileStatusResponse(
        project_id=UUID(project_id), ok=ok, error=error, file=file
    )


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
