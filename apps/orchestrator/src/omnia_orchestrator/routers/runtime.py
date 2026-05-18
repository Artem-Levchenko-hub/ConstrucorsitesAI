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
    """Clone template, allocate port, provision Postgres schema, write nginx site,
    start dev container. Returns the dev URL once the container is healthy.

    TODO sprint A1: implement in `services/provisioner.py`. Steps:
      1. `port_allocator.acquire()` → free port in [3001, 3999].
      2. `git clone apps/orchestrator/templates/{template}` → `/opt/omnia-runtime/projects/{id}/`.
      3. `postgres_admin.create_schema(project_id)` → schema + role + creds in /secrets.
      4. `docker_client.start_container(spec)` with HMR volumes + per-project network.
      5. `nginx_writer.write_site(slug, port, dev=True)` + `nginx -s reload`.
      6. Health-poll GET http://127.0.0.1:port/ until 200 (timeout = wake_timeout_seconds).
      7. Return ProvisionResponse.
    """
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message=f"provision not yet implemented (sprint A1): {payload.project_id}",
        status_code=501,
    )


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
    x_internal_token: Annotated[str | None, Header()] = None,
) -> WakeResponse:
    """Force-hibernate. Pro tier passes pause=True, free passes pause=False.

    Normally hibernate happens via the idle timer in services/hibernate.py.
    This endpoint is for explicit "stop now" from the UI.
    """
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message="stop not yet implemented (sprint A1)",
        status_code=501,
    )


@router.post("/hot-reload")
async def hot_reload(
    payload: HotReloadRequest,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Copy AI-generated files into the running dev container; Next.js HMR
    picks up changes without restart. Same file-payload shape as apps/api
    file_extractor output.

    TODO sprint A1: `docker.containers.get(name).put_archive(path, tar_stream)`.
    """
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message="hot_reload not yet implemented (sprint A1)",
        status_code=501,
    )


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
    x_internal_token: Annotated[str | None, Header()] = None,
) -> StatusResponse:
    """Container state + activity metrics + signed log URL."""
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message=f"status not yet implemented (sprint A1): {project_id}",
        status_code=501,
    )


@router.post("/{project_id}/destroy")
async def destroy(
    project_id: str,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Full cleanup: stop container, drop Postgres schema + role, remove nginx
    site, delete /opt/omnia-runtime/projects/<id>/. Called when user deletes
    a project from the web UI.

    Irreversible — orchestrator does NOT keep tombstones.
    """
    _verify_token(x_internal_token)
    raise OrchestratorError(
        code="internal_error",
        message=f"destroy not yet implemented (sprint A1): {project_id}",
        status_code=501,
    )
