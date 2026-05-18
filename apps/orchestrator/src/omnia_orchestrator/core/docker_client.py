"""Thin wrapper around docker SDK with async + structured errors.

R-01 (deep module): callers see `start_container(spec)` / `stop_container(name)`
methods that take dataclass specs. They never touch raw `docker.client.from_env()`
or handle `docker.errors.APIError`. This makes mocking trivial in tests and
keeps the rest of the codebase free of Docker SDK idioms.

TODO sprint A1:
  - implement spec → container_create with --read-only, --cap-drop=ALL, etc.
  - port binding via PortAllocator
  - --network proj-<id> per project
  - log streaming → /var/log/omnia-runtime/projects/<id>/
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import docker  # type: ignore[import-untyped]
import structlog

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError

log = structlog.get_logger("omnia_orchestrator.docker")


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """Declarative spec for a dev container. Hides Docker SDK kwargs."""

    name: str
    image: str
    port: int  # host port → container's :3000 (Next.js default)
    project_id: str
    env: dict[str, str]
    cpu_quota: float = 0.5  # default for free tier — 50% of 1 core
    memory_mb: int = 512
    network_name: str | None = None  # `proj-<id>` for per-project isolation


_client: docker.DockerClient | None = None


def _get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        try:
            _client = docker.DockerClient(base_url=get_settings().docker_host)
            _client.ping()
        except Exception as exc:  # docker.errors.* or socket failures
            raise OrchestratorError(
                code="docker_unavailable",
                message=f"cannot reach docker daemon: {exc}",
                status_code=503,
            ) from exc
    return _client


async def start_container(spec: ContainerSpec) -> str:
    """Create + start a container. Returns container_id. Idempotent: if a
    container with the same name exists, returns its id and starts if stopped.

    TODO sprint A1:
      - apply security defaults: --cap-drop=ALL, --read-only, tmpfs /tmp, --user 1000:1000
      - per-project network: docker.networks.create(spec.network_name)
      - volume mount: /opt/omnia-runtime/projects/<id>:/app:rw
      - HMR-friendly: bind src/ as volume for live edits
    """
    log.info("docker.start_container", name=spec.name, image=spec.image, port=spec.port)
    # Stub: real impl will be done by Agent D in sprint A1.
    return await asyncio.to_thread(_start_stub, spec)


def _start_stub(spec: ContainerSpec) -> str:
    raise OrchestratorError(
        code="internal_error",
        message=f"start_container not yet implemented (TODO sprint A1): {spec.name}",
        status_code=501,
    )


async def stop_container(name: str, *, pause: bool = False) -> None:
    """Stop or pause a container. Pause keeps memory, stop frees it.

    Pro tier → pause (1-3 sec wake). Free tier → stop (30-60 sec cold start).
    """
    log.info("docker.stop_container", name=name, pause=pause)
    # Stub
    raise OrchestratorError(
        code="internal_error",
        message=f"stop_container not yet implemented (TODO sprint A1): {name}",
        status_code=501,
    )


async def container_status(name: str) -> dict[str, str]:
    """Return {state: running|paused|stopped, port, last_seen}.

    TODO A1: docker.containers.get(name) → inspect → State.Status,
    HostConfig.PortBindings, plus our own last_activity_at lookup.
    """
    log.info("docker.container_status", name=name)
    raise OrchestratorError(
        code="internal_error",
        message=f"container_status not yet implemented (TODO sprint A1): {name}",
        status_code=501,
    )


async def destroy_container(name: str) -> None:
    """Full removal — stop + rm + cleanup volume. For project deletion."""
    log.info("docker.destroy_container", name=name)
    raise OrchestratorError(
        code="internal_error",
        message=f"destroy_container not yet implemented (TODO sprint A1): {name}",
        status_code=501,
    )
