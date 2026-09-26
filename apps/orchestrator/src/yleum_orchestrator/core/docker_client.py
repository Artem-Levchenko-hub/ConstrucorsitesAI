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
import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import docker  # type: ignore[import-untyped]
import requests  # docker SDK transport — its timeouts surface as requests errors
import structlog

from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.env import rebrand_env
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.core.labels import with_both
from yleum_orchestrator.core.template_materialization import (
    materialized_template,
    shared_public_files,
)

log = structlog.get_logger("yleum_orchestrator.docker")

# Docker network that hosts `omnia-postgres-users` (the per-project Postgres).
# User containers join it so they reach the DB container-to-container by name
# (the host bind is 127.0.0.1-only — unreachable from a container). Override via
# env if the compose project/network is renamed.
_RUNTIME_NETWORK = rebrand_env("RUNTIME_NETWORK", "omnia-runtime_default")
_SANDBOX_ARCHIVE_MAX_BYTES = 80 * 1024 * 1024
_SANDBOX_BOOTSTRAP_FAILURE_EXIT_CODE = "190"
_SANDBOX_EXPORT_FAILURE_EXIT_CODE = "191"
_BUILD_ERROR_DETAIL_CHARS = 3_000
_SANDBOX_EXPORT_EXCLUDES = (
    "./node_modules",
    "*/node_modules",
    "./.next",
    "*/.next",
    "./.git",
    "*/.git",
    "./__pycache__",
    "*/__pycache__",
    "./dist",
    "*/dist",
    "./build",
    "*/build",
    "./.venv",
    "*/.venv",
    "./vendor",
    "*/vendor",
)


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """Declarative spec for a dev container. Hides Docker SDK kwargs."""

    name: str
    image: str
    port: int  # host port bound to the container's internal listen port
    project_id: str
    env: dict[str, str]
    cpu_quota: float = 0.5  # default for free tier — 50% of 1 core
    memory_mb: int = 512
    network_name: str | None = None  # `proj-<id>` for per-project isolation
    kind: str = "dev"  # `omnia.kind` label — "dev" or "prod"
    restart_policy_name: str = "no"  # "unless-stopped" for deployed prod
    tier: str = "free"  # `omnia.tier` label — drives hibernate pause/stop policy
    container_port: int = 3000  # internal port the app listens on (StackSpec-driven)
    # ── Sandbox hardening (Phase 1) — all default to current behaviour ───────
    runtime: str = ""        # docker --runtime, e.g. "runsc" (gVisor); "" = daemon default (runc)
    harden: bool = False     # add no-new-privileges + a PID ceiling (safe for non-root images)
    pids_limit: int = 0      # PID ceiling applied only when `harden` is on (0 = unset)
    sandbox_profile: str = ""  # versioned security profile stamped into labels
    recreate_on_profile_change: bool = False
    include_host_gateway: bool = True
    network_internal: bool = False
    # Shared services explicitly attached to a per-project network. MAX uses
    # only its schema-scoped Postgres service; no gateway/MinIO/control plane.
    network_service_names: tuple[str, ...] = ()


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


# ── Template image freshness ─────────────────────────────────────────────────
# Dev containers run from the BAKED image `omnia-template-<dir>:dev`; the project
# src is NOT bind-mounted, so a template EDIT never reaches a client's build until
# the image is rebuilt (2026-07-09: a designed realtime template shipped as the
# OLD bare baseline because the image was stale). This makes every provision
# self-heal that: if the template source is newer than the baked image, rebuild
# it first (Docker layer cache → a no-dep-change rebuild is ~COPY-only, fast).

_TEMPLATE_BUILD_LOCKS: dict[str, asyncio.Lock] = {}
_IMG_IGNORE_DIRS = {"node_modules", ".next", ".git", "__pycache__"}
_IMG_IGNORE_FILE_SUFFIX = (".tsbuildinfo",)
_IMG_IGNORE_FILE_NAMES = {"next-env.d.ts"}


def _newest_source_mtime(template_dir: Path) -> float:
    """Newest mtime under the template dir, skipping build artifacts / vendored
    dirs (whose volatile mtimes would force a rebuild every provision)."""
    newest = 0.0
    for p in template_dir.rglob("*"):
        if _IMG_IGNORE_DIRS & set(p.parts):
            continue
        name = p.name
        if name in _IMG_IGNORE_FILE_NAMES or name.endswith(_IMG_IGNORE_FILE_SUFFIX):
            continue
        try:
            if p.is_file():
                newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    shared = shared_public_files(template_dir)
    if shared:
        inputs = [
            *shared.values(), template_dir.resolve().parent / "shared-public/manifest.json",
        ]
        newest = max(newest, *(path.stat().st_mtime for path in inputs))
    return newest


def _image_created_epoch(tag: str) -> float | None:
    """Epoch seconds the image `tag` was built, or None if it doesn't exist."""
    try:
        img = _get_client().images.get(tag)
    except docker.errors.ImageNotFound:
        return None
    except Exception:
        return None
    created = (img.attrs or {}).get("Created")
    if not isinstance(created, str) or not created:
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def ensure_template_image_fresh(template_dir: Path | str, image_tag: str) -> bool:
    """Rebuild the template dev image iff its source is newer than the baked image
    (or the image is missing) — so a template edit ALWAYS reaches the next build.

    Staleness-gated (skips when unchanged → zero overhead on the hot path),
    fail-soft (a build failure falls back to the existing image, never blocks
    provisioning), per-tag locked (concurrent provisions don't double-build).
    Returns True if it rebuilt. The orchestrator runs on the host with the docker
    CLI + socket, so a subprocess `docker build` (BuildKit, layer cache) is used.
    """
    template_dir = Path(template_dir)
    dockerfile = template_dir / "Dockerfile.dev"
    if not dockerfile.exists():
        return False
    created = _image_created_epoch(image_tag)
    # +2s slack: a just-built image has created≈now ≥ src mtimes; avoid a loop.
    if created is not None and _newest_source_mtime(template_dir) <= created + 2:
        return False

    lock = _TEMPLATE_BUILD_LOCKS.setdefault(image_tag, asyncio.Lock())
    async with lock:
        created = _image_created_epoch(image_tag)  # re-check under the lock
        if created is not None and _newest_source_mtime(template_dir) <= created + 2:
            return False
        log.info("template.image_stale_rebuild", tag=image_tag, dir=str(template_dir))

        def _build() -> tuple[int, str]:
            # Fixed argv (no shell), host docker CLI — the orchestrator runs on
            # the host with the docker socket + BuildKit layer cache. Its systemd
            # sandbox makes $HOME read-only, while Buildx writes activity metadata
            # under $DOCKER_CONFIG. Pin that state to the writable runtime root.
            docker_config_dir = Path(get_settings().docker_cli_config_dir)
            docker_config_dir.mkdir(parents=True, exist_ok=True)
            build_env = os.environ.copy()
            build_env["DOCKER_CONFIG"] = str(docker_config_dir)
            with materialized_template(template_dir) as context:
                proc = subprocess.run(
                    ["docker", "build", "-f", str(context / dockerfile.name),
                     "-t", image_tag, str(context)],
                    capture_output=True,
                    text=True,
                    timeout=900,
                    env=build_env,
                )
            return proc.returncode, (proc.stderr or proc.stdout or "")[-500:]

        try:
            rc, tail = await asyncio.to_thread(_build)
        except Exception as exc:  # timeout / docker missing — never block provision
            log.warning("template.image_rebuild_error", tag=image_tag, err=str(exc))
            return False
        if rc == 0:
            log.info("template.image_rebuilt", tag=image_tag)
            return True
        log.warning("template.image_rebuild_failed", tag=image_tag, rc=rc, tail=tail)
        return False


async def start_container(spec: ContainerSpec) -> str:
    """Create + start a container. Returns container id.

    Idempotent: if a container with the same name exists, restart it if
    stopped and return the existing id without recreating. This matters
    because `provision` and `wake` may race on a fresh project.

    Exception — image change: if the existing container runs a DIFFERENT image
    than `spec.image`, it is stale and must be replaced. This happens on a stack
    switch (e.g. auto-stack-routing flips a project drizzle→nextjs-entities and
    re-provisions): reusing the old container would serve generated code against
    the wrong template's component kit (`@/components/ui/*` 404 → 500). We
    compare by the image *tag* string, so a same-tag rebuild does NOT force a
    recreate — running containers keep serving until their stack actually
    changes.

    Sprint A1 will add per-project networks (--network=proj-<id>), read-only
    rootfs with tmpfs for /tmp, healthcheck wiring, and HMR volume mounts.
    For PoC this is sufficient: defaults still cap-drop ALL and run non-root.
    """
    log.info("docker.start_container", name=spec.name, image=spec.image, port=spec.port)

    def _do() -> str:
        client = _get_client()
        # Per-project network isolation (Phase 1): when the spec names a network
        # other than the shared runtime net, ensure it exists before the run.
        # Idempotent + suppressed so a concurrent provision can't race-fail here.
        # No-op on the default path (network_name None → shared net).
        project_network = None
        if spec.network_name and spec.network_name != _RUNTIME_NETWORK:
            try:
                project_network = client.networks.get(spec.network_name)
            except docker.errors.NotFound:
                try:
                    network_pool = get_settings().cell_network_pool
                    if network_pool:
                        from yleum_orchestrator.services.machine_network_allocation import (
                            create_pool_network,
                        )

                        project_network = create_pool_network(
                            client,
                            network_pool,
                            spec.network_name,
                            driver="bridge",
                            check_duplicate=True,
                            internal=spec.network_internal,
                        )
                    else:
                        project_network = client.networks.create(
                            spec.network_name,
                            driver="bridge",
                            check_duplicate=True,
                            internal=spec.network_internal,
                        )
                except (docker.errors.APIError, ValueError) as exc:
                    # A concurrent provision may have created the deterministic
                    # network after our first lookup. Recover only when it now
                    # exists; otherwise keep the real allocation error instead
                    # of replacing it with a misleading follow-up 404.
                    try:
                        project_network = client.networks.get(spec.network_name)
                    except docker.errors.NotFound:
                        raise OrchestratorError(
                            code="container_failure",
                            message=f"project network allocation failed: {exc}",
                            status_code=503,
                        ) from exc
            if project_network is None:
                project_network = client.networks.get(spec.network_name)
            for service_name in spec.network_service_names:
                try:
                    project_network.connect(service_name, aliases=[service_name])
                except docker.errors.APIError as exc:
                    if "already exists" not in str(exc).lower():
                        raise
        try:
            existing = client.containers.get(spec.name)
        except docker.errors.NotFound:
            existing = None

        if existing is not None:
            existing.reload()
            current_image = (existing.attrs.get("Config") or {}).get("Image")
            current_labels = (existing.attrs.get("Config") or {}).get("Labels") or {}
            current_profile = str(current_labels.get("omnia.sandbox_profile") or "")
            profile_changed = bool(
                spec.recreate_on_profile_change
                and spec.sandbox_profile
                and current_profile != spec.sandbox_profile
            )
            if (current_image and current_image != spec.image) or profile_changed:
                # Stack switched — drop the stale container and recreate below.
                log.info(
                    "docker.recreate_on_image_change",
                    name=spec.name,
                    old_image=current_image,
                    new_image=spec.image,
                    old_profile=current_profile,
                    new_profile=spec.sandbox_profile,
                )
                with suppress(docker.errors.APIError, docker.errors.NotFound):
                    existing.remove(force=True)
            else:
                if existing.status == "paused":
                    existing.unpause()  # can't .start() a frozen container
                elif existing.status != "running":
                    existing.start()
                return str(existing.id)

        # Sandbox hardening (Phase 1) — every entry is OFF by default, so when
        # the spec carries no overrides the run kwargs are byte-identical to
        # before. `runtime` selects gVisor (runsc) when registered on the
        # daemon; `harden` adds no-new-privileges + a PID ceiling. Building a
        # dict and splatting it keeps the default call path untouched (R-10).
        security_kwargs: dict[str, object] = {}
        if spec.runtime:
            security_kwargs["runtime"] = spec.runtime
        if spec.harden:
            security_kwargs["security_opt"] = ["no-new-privileges:true"]
            if spec.pids_limit and spec.pids_limit > 0:
                security_kwargs["pids_limit"] = spec.pids_limit

        labels = {
            "omnia.project_id": spec.project_id,
            "omnia.kind": spec.kind,
            "omnia.tier": spec.tier,
        }
        if spec.sandbox_profile:
            labels["omnia.sandbox_profile"] = spec.sandbox_profile
        connectivity_kwargs: dict[str, object] = {}
        if spec.include_host_gateway:
            connectivity_kwargs["extra_hosts"] = {
                "host.docker.internal": "host-gateway"
            }

        try:
            container = client.containers.run(
                image=spec.image,
                name=spec.name,
                detach=True,
                ports={f"{spec.container_port}/tcp": ("127.0.0.1", spec.port)},
                environment=spec.env,
                mem_limit=f"{spec.memory_mb}m",
                cpu_quota=int(spec.cpu_quota * 100_000),
                cpu_period=100_000,
                cap_drop=["ALL"],
                cap_add=["NET_BIND_SERVICE"],
                user="1000:1000",
                # User containers reach `omnia-postgres-users` on the host via
                # `host.docker.internal`. On Linux this resolves only when the
                # container is started with this extra_hosts entry; on Docker
                # Desktop it already does. Matches the DSN built by
                # `postgres_admin._user_facing_host`.
                **connectivity_kwargs,
                # Join the runtime network so the container resolves and reaches
                # `omnia-postgres-users` (and the DSN built by postgres_admin) by
                # name. Without this the dev/prod app cannot reach its database.
                network=spec.network_name or _RUNTIME_NETWORK,
                restart_policy={"Name": spec.restart_policy_name},
                labels=with_both(labels),
                **security_kwargs,
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
    """Stop or pause a container — fully idempotent.

    `pause=True` keeps memory (1-3 sec wake) — Pro tier hibernate.
    `pause=False` frees memory (30-60 sec cold start) — Free tier hibernate.

    No-ops cleanly when the container is missing OR already in the target
    state: pausing an already-paused container (or stopping a stopped one)
    must NOT error — the UI fires repeat clicks, and Docker raises 500 on
    `pause` of a paused container. We check status first and also swallow the
    idempotency races.
    """
    log.info("docker.stop_container", name=name, pause=pause)

    def _do() -> None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return
        c.reload()
        status = c.status
        try:
            if pause:
                if status == "running":
                    c.pause()
                # paused / exited / created → already not running, no-op
            else:
                if status == "paused":
                    c.unpause()  # can't stop a frozen container — thaw first
                    c.stop(timeout=10)
                elif status in ("running", "restarting"):
                    c.stop(timeout=10)
                # exited / created → already stopped, no-op
        except docker.errors.APIError as exc:
            msg = str(exc).lower()
            if any(
                token in msg
                for token in (
                    "already paused",
                    "not running",
                    "is not paused",
                    "already stopped",
                    "304",
                )
            ):
                return  # idempotency race — treat as success
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
    """Return {state, id, port, project_id} where state ∈ {running, paused,
    stopped, not_found}. `project_id` is the `omnia.project_id` label ("" when
    absent) — the wake-on-request ingress needs it to reset the idle timer
    without a second docker round-trip."""
    log.info("docker.container_status", name=name)

    def _do() -> dict[str, str]:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return {"state": "not_found", "id": "", "port": "", "project_id": ""}
        ports = c.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        host_port = ""
        for bindings in ports.values():
            if bindings:
                host_port = str(bindings[0].get("HostPort", ""))
                break
        project_id = (c.labels or {}).get("omnia.project_id", "")
        return {
            "state": c.status,
            "id": c.id,
            "port": host_port,
            "project_id": project_id,
        }

    return await asyncio.to_thread(_do)


async def container_image_template(name: str) -> str | None:
    """Best-effort: recover the orchestrator template name from a container's
    image. Dev containers run `omnia-template-<template>:dev`, so deploy can seed
    the prod build context from the RIGHT template without the api threading it
    through. Returns None when it can't be parsed (caller falls back to default).
    """
    log.info("docker.container_image_template", name=name)

    def _do() -> str | None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return None
        refs: list[str] = []
        img_ref = (c.attrs.get("Config", {}) or {}).get("Image", "") or ""
        if img_ref:
            refs.append(img_ref)
        try:
            refs.extend(c.image.tags or [])
        except Exception:
            pass
        for ref in refs:
            base = ref.rsplit("/", 1)[-1]  # drop any registry/host prefix
            if base.startswith("omnia-template-"):
                template = base[len("omnia-template-") :].split(":", 1)[0]
                if template:
                    return template
        return None

    return await asyncio.to_thread(_do)


async def container_image_name(name: str) -> str | None:
    """Return the concrete image reference a container was created from."""
    log.info("docker.container_image_name", name=name)

    def _do() -> str | None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return None
        img_ref = (c.attrs.get("Config", {}) or {}).get("Image", "") or ""
        return str(img_ref) or None

    return await asyncio.to_thread(_do)


def _read_bounded_archive(
    chunks: Iterable[bytes | bytearray], *, max_bytes: int, label: str
) -> bytes:
    """Collect a Docker archive without allowing an untrusted memory spike."""
    parts: list[bytes] = []
    total = 0
    for chunk in chunks:
        if not isinstance(chunk, (bytes, bytearray)):
            raise OrchestratorError(
                code="container_failure",
                message=f"{label} returned an invalid archive chunk",
                status_code=500,
            )
        data = bytes(chunk)
        total += len(data)
        if total > max_bytes:
            raise OrchestratorError(
                code="validation_failed",
                message=f"{label} archive exceeds {max_bytes} bytes",
                status_code=413,
            )
        parts.append(data)
    return b"".join(parts)


def _replace_workspace_from_sandbox_archive(raw: bytes, workspace_dir: Path) -> None:
    """Replace a temporary host workspace from a validated Docker archive.

    Only directories and regular UTF-8 candidates are materialised here;
    symlinks, hardlinks, devices, FIFOs and traversal paths are rejected.  The
    caller's later text collector applies the stricter UTF-8/file-count payload
    contract.  A 64 MiB extraction ceiling is intentionally much smaller than
    the tmpfs ceiling so dependency/cache output can never become an API diff.
    """
    import io
    import posixpath
    import tarfile

    max_files = 5_000
    max_members = 10_000
    max_bytes = 64 * 1024 * 1024
    seen_members = 0
    extracted_files = 0
    extracted_bytes = 0
    staging = workspace_dir.parent / f"{workspace_dir.name}-collected"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                seen_members += 1
                if seen_members > max_members:
                    raise OrchestratorError(
                        code="validation_failed",
                        message="sandbox archive contains too many members",
                        status_code=413,
                    )
                normalized = posixpath.normpath(member.name).lstrip("/")
                parts = [part for part in normalized.split("/") if part not in {"", "."}]
                if parts and parts[0] == "workspace":
                    parts = parts[1:]
                if not parts:
                    continue
                if any(part == ".." for part in parts):
                    raise OrchestratorError(
                        code="validation_failed",
                        message="sandbox archive contains a traversal path",
                        status_code=400,
                    )
                # Generated dependency/build trees are disposable artifacts, not
                # project source and can be hundreds of megabytes.
                if any(
                    part
                    in {
                        "node_modules",
                        ".next",
                        ".git",
                        "__pycache__",
                        "dist",
                        "build",
                        ".venv",
                        "vendor",
                    }
                    for part in parts
                ):
                    continue
                target = staging.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    # Most importantly: never materialise a symlink created by
                    # untrusted code and later let host-side Path.read_text()
                    # follow it into /opt/omnia or /proc.
                    continue
                extracted_files += 1
                extracted_bytes += max(0, int(member.size))
                if extracted_files > max_files or extracted_bytes > max_bytes:
                    raise OrchestratorError(
                        code="validation_failed",
                        message="sandbox result exceeds the source collection quota",
                        status_code=413,
                    )
                source = archive.extractfile(member)
                if source is None:
                    continue
                data = source.read(max(0, int(member.size)) + 1)
                if len(data) > member.size:
                    raise OrchestratorError(
                        code="validation_failed",
                        message="sandbox archive member exceeds its declared size",
                        status_code=400,
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

        shutil.rmtree(workspace_dir, ignore_errors=True)
        staging.replace(workspace_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


async def write_files(
    name: str,
    files: dict[str, str],
    *,
    dest_root: str = "/app",
    empty_files: Iterable[str] = (),
) -> dict[str, str]:
    """Stream a set of AI-generated files into a running container via
    `docker cp` semantics (put_archive). Paths in `files` are container-relative
    to `dest_root` (default `/app`, matching Next.js workdir in the template).

    Returns a small summary {written: int, total_bytes: int, dropped: list-of-paths}.

    Safety: refuses any path with `..`, leading `/`, or escaping `dest_root`.
    Empty content (`""`) keeps the legacy delete semantics unless the path also
    appears in `empty_files`, which explicitly preserves a legitimate zero-byte file.
    This lets Project Cell mirror an empty file exactly while older callers still
    delete by sending `""`.

    Missing container = explicit OrchestratorError (caller should handle).
    """
    import io
    import posixpath
    import tarfile

    log.info("docker.write_files", name=name, files=len(files), dest_root=dest_root)

    def _do() -> dict[str, object]:
        def _normalize_target(raw_path: str) -> tuple[str, str] | None:
            norm = posixpath.normpath(raw_path)
            if norm.startswith("/") or norm.startswith(".."):
                return None
            joined = posixpath.normpath(posixpath.join(dest_root, norm))
            if not (joined == dest_root or joined.startswith(dest_root + "/")):
                return None
            return norm, joined

        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound as exc:
            raise OrchestratorError(
                code="not_found",
                message=f"container not found: {name}",
                status_code=404,
            ) from exc
        # Wake a hibernated container instead of failing the write (same
        # mid-build rescue as exec_cmd; see _wake_if_stopped).
        _wake_if_stopped(c, name)
        if c.status not in ("running", "paused"):
            raise OrchestratorError(
                code="container_failure",
                message=(
                    f"container {name} state={c.status}; "
                    "can't write files into a stopped container"
                ),
                status_code=409,
            )

        dropped: list[str] = []
        to_delete: list[str] = []
        explicit_empty_paths: set[str] = set()
        written = 0
        total_bytes = 0

        for raw_path in empty_files:
            if not isinstance(raw_path, str):
                continue
            normalized = _normalize_target(raw_path)
            if normalized is None:
                continue
            norm, _joined = normalized
            explicit_empty_paths.add(norm)

        # Build one tar in memory containing every file with its directory entries.
        # Docker SDK's put_archive needs a tar stream and a target directory.
        buf = io.BytesIO()
        ts = int(time.time())
        with tarfile.open(fileobj=buf, mode="w") as tar:
            seen_dirs: set[str] = set()
            for raw_path, content in files.items():
                normalized = _normalize_target(raw_path)
                if normalized is None:
                    dropped.append(raw_path)
                    continue
                norm, joined = normalized

                if content == "" and norm not in explicit_empty_paths:
                    to_delete.append(joined)
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
                # Runtime entrypoints must survive a source hot-reload and a
                # later cold wake. Repository snapshots store text, not Unix
                # executable bits; writing this file as the old blanket 0644
                # made Node's base-image entrypoint treat the shell script as
                # JavaScript after docker stop/start. Other generated files
                # remain non-executable by default.
                info.mode = 0o755 if norm == "docker-entrypoint.sh" else 0o644
                info.uid = 1000
                info.gid = 1000
                info.mtime = ts
                tar.addfile(info, io.BytesIO(data))
                written += 1
                total_bytes += len(data)

        # Only push an archive when there's something to write — an all-deletes
        # batch produces an empty tar that put_archive would reject.
        if written > 0:
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

        # Apply deletes (empty-content paths). Best-effort: a delete failure must
        # not fail the whole hot-reload — the files that were written still land.
        deleted = 0
        if to_delete:
            try:
                res = c.exec_run(["rm", "-f", *to_delete], user="1000:1000")
                if getattr(res, "exit_code", 0) in (0, None):
                    deleted = len(to_delete)
                else:
                    log.warning(
                        "docker.write_files.delete_nonzero",
                        name=name, paths=to_delete, exit=res.exit_code,
                    )
            except docker.errors.APIError as exc:
                log.warning("docker.write_files.delete_failed", name=name, err=str(exc))

        return {
            "written": written,
            "total_bytes": total_bytes,
            "dropped": dropped,
            "deleted": deleted,
        }

    raw = await asyncio.to_thread(_do)
    # Coerce types for the response (mypy: dict[str,object] → dict[str,str|int|list]).
    return {
        "written": str(raw["written"]),
        "total_bytes": str(raw["total_bytes"]),
        "deleted": str(raw["deleted"]),
        "dropped": ",".join(raw["dropped"]) if raw["dropped"] else "",  # type: ignore[arg-type]
    }


def _wake_if_stopped(c: object, name: str) -> None:
    """Wake a hibernated container in-line before an exec/write (sync, thread ctx).

    The hibernate sweeper counts only PREVIEW traffic as activity, so it can
    docker-stop a dev container while the build agent is mid-loop (2026-07-08
    incident: a realtime build died this way — every subsequent agent op became
    a generic 500 for 40 minutes while the agent ground on). An agent op IS
    proof the project is active — wake the container exactly like the ingress
    wake-on-request path instead of failing: paused → unpause, exited/created →
    start, bounded wait until running. Raises a STRUCTURED 409
    ``container_not_running`` if the wake doesn't take (callers translate it
    into an in-band observation instead of the old unhandled-APIError 500).
    """
    c.reload()  # type: ignore[attr-defined]
    status = c.status  # type: ignore[attr-defined]
    if status == "running":
        return
    log.info("docker.wake_on_agent_op", name=name, was=status)
    try:
        if status == "paused":
            c.unpause()  # type: ignore[attr-defined]
        elif status in ("exited", "created"):
            c.start()  # type: ignore[attr-defined]
    except docker.errors.APIError as exc:
        msg = str(exc).lower()
        if not any(t in msg for t in ("not paused", "already", "304")):
            raise OrchestratorError(
                code="container_not_running",
                message=f"container {name} is {status} and wake failed: {exc}",
                status_code=409,
            ) from exc
    for _ in range(20):  # up to ~10s; exec needs only PID 1, not the dev server
        c.reload()  # type: ignore[attr-defined]
        if c.status == "running":  # type: ignore[attr-defined]
            return
        time.sleep(0.5)
    raise OrchestratorError(
        code="container_not_running",
        message=f"container {name} did not reach running state (now {c.status})",  # type: ignore[attr-defined]
        status_code=409,
    )


async def exec_cmd(
    name: str,
    cmd: list[str],
    *,
    workdir: str | None = None,
    user: str = "1000:1000",
    timeout_sec: int = 120,
    max_output: int = 8_000,
) -> dict[str, str]:
    """Run a command inside a container, return {exit_code, stdout, stderr}.

    Used for follow-up actions after `write_files`: notably `drizzle-kit push`
    when the AI changed `src/lib/db/schema.ts`. Idempotent for the caller —
    a non-zero exit is returned in the dict, not raised, so the api layer
    can decide whether to surface it.

    ``max_output`` bounds each stream (chars) to keep command-log dumps small.
    Callers that read a whole file (e.g. ``read-file`` cat-ing ``globals.css``,
    which exceeds the default cap) MUST raise it, else the content is silently
    truncated mid-line — a truncated ``globals.css`` then breaks the CSS build.
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
        # A hibernated container is WOKEN, not failed (2026-07-08 incident:
        # the sweeper stopped a container mid-build → every agent op 500'd).
        _wake_if_stopped(c, name)
        # demux=True splits stdout/stderr — the SDK signature is awkward but
        # gives us back two bytes-streams in a tuple.
        try:
            result = c.exec_run(
                cmd=cmd,
                workdir=workdir or "/app",
                user=user,
                demux=True,
            )
        except docker.errors.APIError as exc:
            # Stop race (hibernate fired between the wake and the exec) or any
            # daemon-level refusal: structured, not the generic unhandled 500.
            msg = str(exc).lower()
            not_running = "is not running" in msg or "is paused" in msg
            raise OrchestratorError(
                code="container_not_running" if not_running else "container_failure",
                message=f"exec on {name} failed: {exc}",
                status_code=409 if not_running else 500,
            ) from exc
        out_bytes, err_bytes = (
            result.output if isinstance(result.output, tuple) else (result.output, b"")
        )
        return {
            "exit_code": str(result.exit_code),
            "stdout": (out_bytes or b"").decode("utf-8", errors="replace")[:max_output],
            "stderr": (err_bytes or b"").decode("utf-8", errors="replace")[:max_output],
        }

    # exec_run does not honor an explicit timeout; wrap in asyncio.wait_for.
    try:
        return await asyncio.wait_for(asyncio.to_thread(_do), timeout=timeout_sec)
    except TimeoutError as exc:
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
        except (docker.errors.APIError, requests.exceptions.Timeout):
            pass  # already stopped, or daemon busy — the force-remove handles it
        try:
            c.remove(v=True, force=True)
        except docker.errors.NotFound:
            return  # already gone — idempotent
        except requests.exceptions.Timeout:
            # The force-remove (SIGKILL + rm) was dispatched, but under heavy
            # daemon load the SDK's 60s read can elapse before the daemon
            # answers — the removal still completes in the background. Treat the
            # timeout as best-effort success so a slow daemon never blocks the
            # user's «удалить»: the teardown is idempotent, so any leftover is a
            # no-op on the next pass / reaped by hibernate. Owner bug — projects
            # with a live container 503'd forever because this ReadTimeout (NOT a
            # docker.errors.APIError) escaped and failed the whole deletion.
            log.warning("docker.destroy_container.remove_timeout", name=name)
            return
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"remove failed for {name}: {exc}",
                status_code=500,
            ) from exc

    await asyncio.to_thread(_do)


async def destroy_project_network(
    project_id: str,
    *,
    service_names: tuple[str, ...] = (),
) -> None:
    """Remove one deterministic legacy project network after its runtimes.

    Shared services are detached explicitly. Any other remaining endpoint
    fails closed so teardown never disconnects an unrelated live container.
    """
    name = f"omnia-proj-{project_id}"
    log.info("docker.destroy_project_network", name=name)

    def _do() -> None:
        client = _get_client()
        try:
            network = client.networks.get(name)
        except docker.errors.NotFound:
            return

        try:
            network.reload()
            containers = (network.attrs or {}).get("Containers") or {}
            allowed = set(service_names)
            for container_id, metadata in list(containers.items()):
                container_name = str((metadata or {}).get("Name") or "")
                if container_name in allowed:
                    network.disconnect(container_id, force=True)

            network.reload()
            remaining = (network.attrs or {}).get("Containers") or {}
            if remaining:
                names = sorted(
                    str((metadata or {}).get("Name") or container_id)
                    for container_id, metadata in remaining.items()
                )
                raise OrchestratorError(
                    code="container_failure",
                    message=f"project network still has endpoints: {', '.join(names)}",
                    status_code=409,
                )
            network.remove()
        except docker.errors.NotFound:
            return
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"project network removal failed: {exc}",
                status_code=500,
            ) from exc

    await asyncio.to_thread(_do)


async def wake_container(name: str) -> None:
    """Resume a hibernated dev container.

    Handles every reachable state: paused → unpause (instant), exited /
    created → start (cold boot, 30-60 s on Next.js), running → no-op.
    The original `containers.run(...)` config (env, mounts, ports, network)
    is preserved by Docker across stop, so a plain `.start()` brings the
    project back identically.

    Raises 404 only when the container truly doesn't exist — the caller
    should re-provision rather than wake.
    """
    log.info("docker.wake_container", name=name)

    def _do() -> None:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound as exc:
            raise OrchestratorError(
                code="not_found",
                message=f"container not found: {name}",
                status_code=404,
            ) from exc
        c.reload()
        status = c.status
        try:
            if status == "paused":
                c.unpause()
            elif status in ("exited", "created"):
                c.start()
            # running → no-op
        except docker.errors.APIError as exc:
            msg = str(exc).lower()
            if any(t in msg for t in ("not paused", "already", "304")):
                return  # idempotency race
            raise OrchestratorError(
                code="container_failure",
                message=f"wake failed for {name}: {exc}",
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
    name: str,
    container_path: str,
    dest_dir: str,
    *,
    max_archive_bytes: int | None = None,
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
        raw = (
            _read_bounded_archive(
                bits,
                max_bytes=max_archive_bytes,
                label=f"container path {container_path}",
            )
            if max_archive_bytes is not None
            else b"".join(bits)
        )
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(dest_dir, filter="data")  # filter blocks path traversal
        return True

    return await asyncio.to_thread(_do)


_ARTIFACT_INVENTORY_EXCLUDED_PARTS = frozenset({".git", ".next", "node_modules"})
_ARTIFACT_INVENTORY_SENSITIVE_NAMES = frozenset(
    {".env", ".env.local", ".env.production", "secrets"}
)


def _extract_archive_with_inventory(raw: bytes, dest_dir: str | None) -> dict[str, str]:
    """Extract regular, non-sensitive files and return their value-free digests."""
    import io
    import tarfile

    inventory: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        allowed: list[tarfile.TarInfo] = []
        for member in archive.getmembers():
            name = member.name.replace("\\", "/")
            while name.startswith("./"):
                name = name[2:]
            path = PurePosixPath(name)
            if not name or path.is_absolute() or ".." in path.parts:
                raise OrchestratorError(
                    code="invalid_path",
                    message="container archive contains an invalid path",
                    status_code=400,
                )
            if any(
                part in _ARTIFACT_INVENTORY_EXCLUDED_PARTS
                or part in _ARTIFACT_INVENTORY_SENSITIVE_NAMES
                or part.startswith(".env.")
                for part in path.parts
            ):
                continue
            if member.isdir():
                allowed.append(member)
                continue
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                raise OrchestratorError(
                    code="container_failure",
                    message=f"container archive file cannot be read: {path.as_posix()}",
                    status_code=500,
                )
            inventory[path.as_posix()] = hashlib.sha256(handle.read()).hexdigest()
            allowed.append(member)
        if dest_dir is not None:
            archive.extractall(dest_dir, members=allowed, filter="data")
    return inventory


async def copy_path_from_container_with_inventory(
    name: str,
    container_path: str,
    dest_dir: str,
    *,
    max_archive_bytes: int = 80 * 1024 * 1024,
) -> dict[str, str] | None:
    """Atomically copy one path and attest the copied source archive.

    ``None`` means the source path does not exist.  The digest map never includes
    generated trees, Git metadata or environment/secret files.
    """
    log.info("docker.copy_from_container_inventory", name=name, path=container_path)

    def _do() -> dict[str, str] | None:
        client = _get_client()
        container = client.containers.get(name)
        try:
            bits, _stat = container.get_archive(container_path)
        except docker.errors.NotFound:
            return None
        raw = _read_bounded_archive(
            bits,
            max_bytes=max_archive_bytes,
            label=f"container path {container_path}",
        )
        return _extract_archive_with_inventory(raw, dest_dir)

    return await asyncio.to_thread(_do)


async def image_path_inventory(
    image_id: str,
    container_paths: tuple[str, ...],
    *,
    max_archive_bytes: int = 80 * 1024 * 1024,
) -> dict[str, str]:
    """Read a value-free file manifest from an exact, never-started image.

    Docker's archive API works on stopped containers, so the probe gets no
    network, environment or runtime secrets.  Its cleanup is part of the same
    blocking operation and runs on every success/failure path.
    """
    log.info("docker.image_path_inventory", image=image_id, paths=container_paths)

    def _do() -> dict[str, str]:
        client = _get_client()
        probe = None
        result: dict[str, str] = {}
        failure: Exception | None = None
        try:
            probe = client.containers.create(
                image=image_id,
                network_disabled=True,
                command=["true"],
            )
            for container_path in container_paths:
                try:
                    bits, _stat = probe.get_archive(container_path)
                except docker.errors.NotFound:
                    continue
                raw = _read_bounded_archive(
                    bits,
                    max_bytes=max_archive_bytes,
                    label=f"built image path {container_path}",
                )
                captured = _extract_archive_with_inventory(raw, None)
                for path, digest in captured.items():
                    previous = result.get(path)
                    if previous is not None and previous != digest:
                        raise OrchestratorError(
                            code="container_failure",
                            message=f"built image inventory conflicts for {path}",
                            status_code=500,
                        )
                    result[path] = digest
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, OrchestratorError)
                else OrchestratorError(
                    code="container_failure",
                    message=f"built image inventory failed: {exc}",
                    status_code=500,
                )
            )
        finally:
            if probe is not None:
                try:
                    probe.remove(force=True)
                except Exception as exc:
                    if failure is None:
                        failure = OrchestratorError(
                            code="container_failure",
                            message=f"built image inventory cleanup failed: {exc}",
                            status_code=500,
                        )
        if failure is not None:
            raise failure
        return result

    return await asyncio.to_thread(_do)


def image_id_for_tag(tag: str) -> str:
    """Blocking: the immutable `sha256:<64 hex>` id the local daemon holds for `tag`.

    Shared by every build backend: whatever produced the image (root `docker
    build` or a rootless buildkitd + `docker load`), the deploy pipeline pins
    containers, inventories and pushes to this identity, never to the mutable tag.
    """
    try:
        image_id = str(_get_client().images.get(tag).id)
    except Exception as exc:
        raise OrchestratorError(
            code="container_failure",
            message=f"prod build image identity unavailable: {exc}",
            status_code=500,
        ) from exc
    prefix, separator, digest = image_id.partition(":")
    if (
        prefix != "sha256"
        or separator != ":"
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise OrchestratorError(
            code="container_failure",
            message="prod build returned an invalid immutable image id",
            status_code=500,
        )
    return image_id


async def build_image(
    context_dir: str,
    dockerfile: str,
    tag: str,
    *,
    timeout_sec: float = 840,
    max_attempts: int = 2,
    retry_delay_sec: float = 2.0,
) -> str:
    """Build a prod image with a cancellable Docker CLI subprocess.

    One retry absorbs transient daemon/resource failures. A single total deadline
    covers both attempts, and timeout/cancellation terminates the CLI process before
    control returns, so a later publish cannot overlap an orphaned build.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    log.info("docker.build_image", tag=tag, context=context_dir, dockerfile=dockerfile)

    docker_config_dir = Path(get_settings().docker_cli_config_dir)
    await asyncio.to_thread(docker_config_dir.mkdir, parents=True, exist_ok=True)
    build_env = os.environ.copy()
    build_env["DOCKER_CONFIG"] = str(docker_config_dir)
    dockerfile_path = str(Path(context_dir) / dockerfile)

    async def _start_build() -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                "docker",
                "build",
                "--force-rm",
                "--file",
                dockerfile_path,
                "--tag",
                tag,
                context_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=build_env,
            )
        except OSError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"prod build failed: could not start docker CLI: {exc}",
                status_code=500,
            ) from exc

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec

    def _timeout_error() -> OrchestratorError:
        return OrchestratorError(
            code="container_failure",
            message=f"prod build timed out after {timeout_sec:g}s total",
            status_code=504,
        )

    for attempt in range(1, max_attempts + 1):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise _timeout_error()
        try:
            process = await _start_build()
        except OrchestratorError as exc:
            failure = exc
        else:
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=remaining)
            except (TimeoutError, asyncio.CancelledError) as exc:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise _timeout_error() from exc
            if process.returncode == 0:
                return await asyncio.to_thread(image_id_for_tag, tag)
            detail = (output or b"").decode("utf-8", errors="replace").strip()
            detail = detail[-_BUILD_ERROR_DETAIL_CHARS:]
            failure = OrchestratorError(
                code="container_failure",
                message=(
                    f"prod build failed: {detail}"
                    if detail
                    else f"prod build failed: docker exited with code {process.returncode}"
                ),
                status_code=500,
            )

        if attempt >= max_attempts:
            raise failure
        log.warning(
            "docker.build_image_retry",
            tag=tag,
            attempt=attempt,
            max_attempts=max_attempts,
            err=failure.message[:500],
        )
        if retry_delay_sec > 0:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _timeout_error() from failure
            try:
                await asyncio.wait_for(
                    asyncio.sleep(retry_delay_sec),
                    timeout=remaining,
                )
            except TimeoutError as timeout_exc:
                raise _timeout_error() from timeout_exc
    raise RuntimeError("unreachable build retry state")


async def container_logs(
    name: str, *, tail: int = 200, kind: str = "dev"
) -> dict[str, str]:
    """Tail recent stdout+stderr from a container. UTF-8 decoded, no follow.

    Returns ``{"logs": "<text>", "tail": "<n>"}``. The frontend renders this
    in a scrollable panel; truncating at 200 lines by default keeps the
    payload under ~50 KB for typical Next.js / FastAPI startup logs.

    Missing container is NOT a hard error — we return empty logs so the UI
    can show "No logs yet" without surfacing a 404 on freshly-provisioned
    projects (race between container start and first user log click).
    """
    log.info("docker.container_logs", name=name, tail=tail, kind=kind)

    def _do() -> dict[str, str]:
        client = _get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return {"logs": "", "tail": str(tail)}
        try:
            raw = c.logs(tail=tail, timestamps=False, stdout=True, stderr=True)
        except docker.errors.APIError as exc:
            raise OrchestratorError(
                code="container_failure",
                message=f"docker logs failed for {name}: {exc}",
                status_code=500,
            ) from exc
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        return {"logs": text, "tail": str(tail)}

    return await asyncio.to_thread(_do)


async def prune_old_app_images(slug: str, *, keep: int = 3) -> None:
    """Remove old `omnia-app-<slug>:*` tags, keeping the `keep` most recent.

    Called after a successful deploy so the VPS doesn't accumulate image
    layers (prod was sitting on 9 dangling revisions of one project before
    this was wired). Idempotent and best-effort: API errors are logged, not
    raised — a deploy is not invalidated by a failed prune.
    """
    log.info("docker.prune_old_app_images", slug=slug, keep=keep)

    def _do() -> None:
        client = _get_client()
        prefix = f"omnia-app-{slug}"
        try:
            images = client.images.list(name=prefix)
        except docker.errors.APIError as exc:
            log.warning("docker.prune_list_failed", slug=slug, err=str(exc))
            return

        tagged: list[tuple[int, str]] = []
        for img in images:
            for tag in img.tags or []:
                if not tag.startswith(prefix + ":"):
                    continue
                ts_str = tag.split(":", 1)[1]
                try:
                    tagged.append((int(ts_str), tag))
                except ValueError:
                    continue  # non-timestamp tag — leave alone

        tagged.sort(reverse=True)
        for _ts, tag in tagged[keep:]:
            try:
                client.images.remove(image=tag, force=True)
                log.info("docker.image_pruned", tag=tag)
            except docker.errors.APIError as exc:
                log.warning("docker.prune_failed", tag=tag, err=str(exc))

    await asyncio.to_thread(_do)
