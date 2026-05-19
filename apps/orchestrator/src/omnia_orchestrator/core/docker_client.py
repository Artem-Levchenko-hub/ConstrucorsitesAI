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
    """Create + start a container. Returns container id.

    Idempotent: if a container with the same name exists, restart it if
    stopped and return the existing id without recreating. This matters
    because `provision` and `wake` may race on a fresh project.

    Sprint A1 will add per-project networks (--network=proj-<id>), read-only
    rootfs with tmpfs for /tmp, healthcheck wiring, and HMR volume mounts.
    For PoC this is sufficient: defaults still cap-drop ALL and run non-root.
    """
    log.info("docker.start_container", name=spec.name, image=spec.image, port=spec.port)

    def _do() -> str:
        client = _get_client()
        try:
            existing = client.containers.get(spec.name)
            if existing.status != "running":
                existing.start()
            return str(existing.id)
        except docker.errors.NotFound:
            pass

        try:
            container = client.containers.run(
                image=spec.image,
                name=spec.name,
                detach=True,
                ports={"3000/tcp": ("127.0.0.1", spec.port)},
                environment=spec.env,
                mem_limit=f"{spec.memory_mb}m",
                cpu_quota=int(spec.cpu_quota * 100_000),
                cpu_period=100_000,
                cap_drop=["ALL"],
                cap_add=["NET_BIND_SERVICE"],
                user="1000:1000",
                restart_policy={"Name": "no"},
                labels={
                    "omnia.project_id": spec.project_id,
                    "omnia.kind": "dev",
                },
            )
        except docker.errors.ImageNotFound as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"image not found: {spec.image} — build it first",
                status_code=409,
            ) from exc
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"docker refused start: {exc}",
                status_code=500,
            ) from exc
        return str(container.id)


    return await asyncio.to_thread(_do)


async def stop_container(name: str, *, pause: bool = False) -> None:
    """Stop or pause a container.

    `pause=True` keeps memory (1-3 sec wake) — Pro tier hibernate.
    `pause=False` frees memory (30-60 sec cold start) — Free tier hibernate.
    Missing container is a no-op (idempotent).
    """
    log.info("docker.stop_container", name=name, pause=pause)

    def _do() -> None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return
        try:
            if pause:
                c.pause()
            else:
                c.stop(timeout=10)
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"stop failed for {name}: {exc}",
                status_code=500,
            ) from exc

    await asyncio.to_thread(_do)


async def container_status(name: str) -> dict[str, str]:
    """Return {state, id, port} where state ∈ {running, paused, stopped, not_found}."""
    log.info("docker.container_status", name=name)

    def _do() -> dict[str, str]:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return {"state": "not_found", "id": "", "port": ""}
        ports = c.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        host_port = ""
        for bindings in ports.values():
            if bindings:
                host_port = str(bindings[0].get("HostPort", ""))
                break
        return {"state": c.status, "id": c.id, "port": host_port}

    return await asyncio.to_thread(_do)


async def destroy_container(name: str) -> None:
    """Full removal: stop + rm. Missing container is a no-op."""
    log.info("docker.destroy_container", name=name)

    def _do() -> None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return
        try:
            c.stop(timeout=5)
        except docker.errors.APIError:
            pass  # may already be stopped
        try:
            c.remove(v=True, force=True)
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"remove failed for {name}: {exc}",
                status_code=500,
            ) from exc

    await asyncio.to_thread(_do)
