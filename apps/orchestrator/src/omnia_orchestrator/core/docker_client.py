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
    kind: str = "dev"  # `omnia.kind` label — "dev" or "prod"
    restart_policy_name: str = "no"  # "unless-stopped" for deployed prod


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
                restart_policy={"Name": spec.restart_policy_name},
                labels={
                    "omnia.project_id": spec.project_id,
                    "omnia.kind": spec.kind,
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


async def find_project_container(project_id: str, *, kind: str = "dev") -> str | None:
    """Return the container name for a project by label, or None if absent.

    Containers are labeled `omnia.project_id` + `omnia.kind` at creation (see
    `start_container`). Resolving by label lets stop/status/deploy work from
    `project_id` alone — no slug→name guessing and no slug query-param coupling
    (the source of the pause-never-stops and status-422 bugs).
    """
    log.info("docker.find_project_container", project_id=project_id, kind=kind)

    def _do() -> str | None:
        client = _get_client()
        containers = client.containers.list(
            all=True,
            filters={"label": [f"omnia.project_id={project_id}", f"omnia.kind={kind}"]},
        )
        return str(containers[0].name) if containers else None

    return await asyncio.to_thread(_do)


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


async def write_files(name: str, files: dict[str, str], *, dest_root: str = "/app") -> dict[str, str]:
    """Stream a set of AI-generated files into a running container via
    `docker cp` semantics (put_archive). Paths in `files` are container-relative
    to `dest_root` (default `/app`, matching Next.js workdir in the template).

    Returns a small summary {written: int, total_bytes: int, dropped: list-of-paths}.

    Safety: refuses any path with `..`, leading `/`, or escaping `dest_root`.
    Empty content (`""`) means "delete this file" — we write a zero-length
    file rather than removing; the file_extractor on the api side already
    treats empty as delete-intent, but here we keep a sentinel so a future
    audit (`docker exec ls -la`) shows the intent without a separate exec.

    Missing container = explicit OrchestratorError (caller should handle).
    """
    import io
    import tarfile
    import time
    import posixpath

    log.info("docker.write_files", name=name, files=len(files), dest_root=dest_root)

    def _do() -> dict[str, object]:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound as exc:
            raise OrchestratorError(
                code="not_found",
                message=f"container not found: {name}",
                status_code=404,
            ) from exc
        if c.status not in ("running", "paused"):
            raise OrchestratorError(
                code="container_failure",
                message=f"container {name} state={c.status}; can't write files into a stopped container",
                status_code=409,
            )

        dropped: list[str] = []
        written = 0
        total_bytes = 0

        # Build one tar in memory containing every file with its directory entries.
        # Docker SDK's put_archive needs a tar stream and a target directory.
        buf = io.BytesIO()
        ts = int(time.time())
        with tarfile.open(fileobj=buf, mode="w") as tar:
            seen_dirs: set[str] = set()
            for raw_path, content in files.items():
                # Sanitize: no .., no absolute, must stay under dest_root.
                norm = posixpath.normpath(raw_path)
                if norm.startswith("/") or norm.startswith(".."):
                    dropped.append(raw_path)
                    continue
                # Prevent escape via well-crafted normpath edge cases.
                joined = posixpath.normpath(posixpath.join(dest_root, norm))
                if not (joined == dest_root or joined.startswith(dest_root + "/")):
                    dropped.append(raw_path)
                    continue

                # Add missing parent dirs as tar entries so put_archive
                # can write into nested paths the very first time.
                parts = norm.split("/")
                for i in range(1, len(parts)):
                    d = "/".join(parts[:i])
                    if d and d not in seen_dirs:
                        di = tarfile.TarInfo(name=d)
                        di.type = tarfile.DIRTYPE
                        di.mode = 0o755
                        di.uid = 1000
                        di.gid = 1000
                        di.mtime = ts
                        tar.addfile(di)
                        seen_dirs.add(d)

                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=norm)
                info.size = len(data)
                info.mode = 0o644
                info.uid = 1000
                info.gid = 1000
                info.mtime = ts
                tar.addfile(info, io.BytesIO(data))
                written += 1
                total_bytes += len(data)

        buf.seek(0)
        try:
            ok = c.put_archive(path=dest_root, data=buf.getvalue())
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"put_archive failed for {name}: {exc}",
                status_code=500,
            ) from exc
        if not ok:
            raise OrchestratorError(
                code="container_failure",
                message=f"put_archive returned False for {name}",
                status_code=500,
            )

        return {"written": written, "total_bytes": total_bytes, "dropped": dropped}

    raw = await asyncio.to_thread(_do)
    # Coerce types for the response (mypy: dict[str,object] → dict[str,str|int|list]).
    return {
        "written": str(raw["written"]),
        "total_bytes": str(raw["total_bytes"]),
        "dropped": ",".join(raw["dropped"]) if raw["dropped"] else "",  # type: ignore[arg-type]
    }


async def exec_cmd(
    name: str,
    cmd: list[str],
    *,
    workdir: str | None = None,
    user: str = "1000:1000",
    timeout_sec: int = 120,
) -> dict[str, str]:
    """Run a command inside a container, return {exit_code, stdout, stderr}.

    Used for follow-up actions after `write_files`: notably `drizzle-kit push`
    when the AI changed `src/lib/db/schema.ts`. Idempotent for the caller —
    a non-zero exit is returned in the dict, not raised, so the api layer
    can decide whether to surface it.
    """
    log.info("docker.exec_cmd", name=name, cmd=cmd, workdir=workdir)

    def _do() -> dict[str, str]:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound as exc:
            raise OrchestratorError(
                code="not_found",
                message=f"container not found: {name}",
                status_code=404,
            ) from exc
        # demux=True splits stdout/stderr — the SDK signature is awkward but
        # gives us back two bytes-streams in a tuple.
        result = c.exec_run(
            cmd=cmd,
            workdir=workdir or "/app",
            user=user,
            demux=True,
        )
        out_bytes, err_bytes = result.output if isinstance(result.output, tuple) else (result.output, b"")
        return {
            "exit_code": str(result.exit_code),
            "stdout": (out_bytes or b"").decode("utf-8", errors="replace")[:8000],
            "stderr": (err_bytes or b"").decode("utf-8", errors="replace")[:8000],
        }

    # exec_run does not honor an explicit timeout; wrap in asyncio.wait_for.
    try:
        return await asyncio.wait_for(asyncio.to_thread(_do), timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        raise OrchestratorError(
            code="container_failure",
            message=f"exec {cmd[0]} on {name} timed out after {timeout_sec}s",
            status_code=504,
        ) from exc


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


async def unpause_container(name: str) -> None:
    """Unpause a paused container so its filesystem can be read. No-op otherwise."""
    log.info("docker.unpause_container", name=name)

    def _do() -> None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return
        if c.status == "paused":
            try:
                c.unpause()
            except docker.errors.APIError:
                pass

    await asyncio.to_thread(_do)


async def copy_path_from_container(
    name: str, container_path: str, dest_dir: str
) -> bool:
    """Extract `container_path` from a container into `dest_dir` on the host.

    Used to assemble a prod build context from the live dev container. Returns
    False if the path is absent (best-effort overlay) so the caller can layer
    optional paths without each one being fatal.
    """
    import io
    import tarfile

    log.info("docker.copy_from_container", name=name, path=container_path)

    def _do() -> bool:
        client = _get_client()
        c = client.containers.get(name)
        try:
            bits, _stat = c.get_archive(container_path)
        except docker.errors.NotFound:
            return False
        raw = b"".join(bits)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(dest_dir, filter="data")  # filter blocks path traversal
        return True

    return await asyncio.to_thread(_do)


async def build_image(
    context_dir: str, dockerfile: str, tag: str, *, timeout_sec: int = 900
) -> None:
    """`docker build` a prod image. Raises OrchestratorError (with a log tail)
    on failure. Blocking — call from a background task, not a request handler.
    """
    log.info("docker.build_image", tag=tag, context=context_dir, dockerfile=dockerfile)

    def _do() -> None:
        client = _get_client()
        try:
            client.images.build(
                path=context_dir,
                dockerfile=dockerfile,
                tag=tag,
                rm=True,
                forcerm=True,
                pull=False,
            )
        except docker.errors.BuildError as exc:
            tail: list[str] = []
            for chunk in getattr(exc, "build_log", None) or []:
                if isinstance(chunk, dict) and chunk.get("stream"):
                    tail.append(str(chunk["stream"]))
            detail = ("".join(tail))[-1500:] or str(exc)
            raise OrchestratorError(
                code="container_failure",
                message=f"prod build failed: {detail}",
                status_code=500,
            ) from exc
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"docker build error: {exc}",
                status_code=500,
            ) from exc

    try:
        await asyncio.wait_for(asyncio.to_thread(_do), timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        raise OrchestratorError(
            code="container_failure",
            message=f"prod build timed out after {timeout_sec}s",
            status_code=504,
        ) from exc
