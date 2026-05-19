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

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import (
    container_status as docker_container_status,
)
from omnia_orchestrator.core.docker_client import (
    destroy_container,
    exec_cmd,
    stop_container,
    write_files,
)
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.schemas.runtime import (
    DeployRequest,
    DeployResponse,
    HotReloadRequest,
    ProvisionRequest,
    ProvisionResponse,
    StatusResponse,
    StopRequest,
    WakeRequest,
    WakeResponse,
)
from omnia_orchestrator.services.port_allocator import get_port_allocator
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
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WakeResponse:
    """Resume a hibernated container. `pause` → unpause (fast). `stopped` →
    docker start (slow cold). Returns expected ready-in-seconds.

    Idempotent: if container is already running, returns state=running, ready=0.
    """
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message="wake not yet implemented (sprint A1)",
        status_code=501,
    )


@router.post("/stop", response_model=WakeResponse)
async def stop(
    payload: StopRequest,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WakeResponse:
    """Force-hibernate via docker pause/stop.

    PoC: looks up container by `omnia-dev-<slug>`. Sprint A1 will hold the
    container_name in the orchestrator's own state table.
    """
    _verify_token(x_internal_token)
    await stop_container(f"omnia-dev-{slug}", pause=payload.pause)
    new_state = "paused" if payload.pause else "stopped"
    return WakeResponse(
        project_id=payload.project_id,
        state=new_state,        ready_in_seconds=0,
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


@router.post("/deploy", response_model=DeployResponse)
async def deploy(
    payload: DeployRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> DeployResponse:
    """Build prod image from current commit, push to local registry, switch
    nginx to new prod container. Emits progress events that apps/api forwards
    to the web client via WebSocket `deploy.progress`.

    TODO sprint A1: implement in `services/builder.py`. Steps:
      1. Checkout commit_sha into a temp dir.
      2. `docker build -t {registry}/proj-{id}:{sha} -f Dockerfile.prod .`
      3. `docker push {registry}/proj-{id}:{sha}`.
      4. `docker run -d --restart=unless-stopped --name proj-{id}-prod ...`.
      5. Health-poll new container.
      6. `nginx_writer.write_site(slug, port, dev=False)` + reload.
      7. Stop the previous prod container (zero-downtime swap).
    """
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message="deploy not yet implemented (sprint A1)",
        status_code=501,
    )


@router.get("/{project_id}/status", response_model=StatusResponse)
async def status(
    project_id: str,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> StatusResponse:
    """Container state derived from Docker inspect.

    PoC: slug is required as a query param because we name containers
    `omnia-dev-<slug>` — the lookup table for project_id → container_name will
    move into Postgres in sprint A1. Today the caller (apps/api) already
    knows both the id and the slug from its own project record.
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    info = await docker_container_status(f"omnia-dev-{slug}")
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
    return StatusResponse(
        project_id=UUID(project_id),
        state=state_map.get(info["state"], "stopped"),        container_name=f"omnia-dev-{slug}",
        port=int(info["port"]) if info["port"] else None,
        dev_url=f"http://127.0.0.1:{info['port']}" if info["port"] else None,
    )


@router.post("/{project_id}/destroy")
async def destroy(
    project_id: str,
    slug: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Full cleanup: stop + remove container, release port.

    PoC: doesn't drop Postgres schema or remove nginx site (sprint A1).
    `slug` query param same rationale as `status`.
    """
    _verify_token(x_internal_token)
    from uuid import UUID

    await destroy_container(f"omnia-dev-{slug}")
    await get_port_allocator().release(UUID(project_id))
    return {"state": "destroyed"}
