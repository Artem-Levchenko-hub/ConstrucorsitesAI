"""Internal API for project runtime lifecycle.

All endpoints below are gated by `X-Internal-Token` header verified against
`Settings.internal_token`. They are meant for apps/api (the public-facing
FastAPI service) to call; web clients never touch this surface.

Implementation is currently a scaffold — every handler returns 501 with a
clear "implement in sprint A1" message. The contracts (request/response
schemas) are stable and consumed by apps/api today.
"""

from __future__ import annotations

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
    StatusResponse,
    StopRequest,
    WakeRequest,
    WakeResponse,
)
from omnia_orchestrator.services import builder, deploy_state, nginx_writer
from omnia_orchestrator.services.compile_status import parse_next_compile_error
from omnia_orchestrator.services.hibernate import record_activity
from omnia_orchestrator.services.port_allocator import (
    get_port_allocator,
    get_prod_port_allocator,
)
from omnia_orchestrator.services.provisioner import provision as provision_svc

router = APIRouter(prefix="/internal/projects", tags=["runtime"])


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
    container_name = f"omnia-dev-{slug}"

    write_result = await write_files(container_name, payload.files)

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
                cmd=["npx", "--yes", "drizzle-kit", "push", "--config=drizzle.config.ts"],
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
    }
    if drizzle_result is not None:
        response["drizzle_exit_code"] = drizzle_result["exit_code"]
        response["drizzle_stderr_tail"] = drizzle_result["stderr"][-500:]
    return response


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
    return StatusResponse(
        project_id=UUID(project_id),
        state=state_map.get(info["state"], "stopped"),
        container_name=name,
        port=int(info["port"]) if info["port"] else None,
        dev_url=nginx_writer.dev_url(derived_slug) if derived_slug else None,
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
