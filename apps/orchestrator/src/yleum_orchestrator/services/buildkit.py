"""Isolated production builds: `buildctl` against a rootless buildkitd.

Why: the production image of a user project is built from an UNTRUSTED
Dockerfile + context (the coding agent writes both). `docker build` hands that
context to the host's root daemon, so every RUN step executes as real root
behind a single kernel boundary. A rootless buildkitd (see
`infra/max-k3s/cells/50-buildkit-rootless.sh`) has no Docker socket, runs as an
unprivileged uid and executes RUN steps inside a nested user namespace with its
own PID namespace — two boundaries between the agent's Dockerfile and the host.

Contract mirrors `docker_client.build_image`: the built image lands in the local
daemon under `tag` (BuildKit's docker-archive output streamed into `docker load`)
and the immutable `sha256:` image id is returned; one total deadline covers a
single retry; timeout/cancellation terminates the CLI processes before control
returns; failures surface as `OrchestratorError(container_failure)` carrying the
tail of the build log. `build_and_push` is the registry variant
(`type=image,push=true`) for runtimes that pull from `image_registry` instead
of the local daemon.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import structlog

from yleum_orchestrator.core import docker_client
from yleum_orchestrator.core.config import get_settings
from yleum_orchestrator.core.errors import OrchestratorError

log = structlog.get_logger("yleum_orchestrator.buildkit")

_BUILD_ERROR_DETAIL_CHARS = 3_000
_DIGEST_HEX_LENGTH = 64

Process = asyncio.subprocess.Process


class _BuildTimeout(Exception):
    """The attempt hit the total deadline — terminal, never retried."""


@dataclass(frozen=True, slots=True)
class BuildKitInvocation:
    """Everything one attempt needs, resolved once before the retry loop."""

    build_argv: tuple[str, ...]
    env: dict[str, str]
    # `docker load` reading BuildKit's docker-archive output; None for a push.
    load_argv: tuple[str, ...] | None = None


# ----------------------------------------------------------------- argv


def buildctl_argv(
    *,
    buildctl: str,
    socket_path: str,
    context_dir: str,
    dockerfile: str,
    output: str,
    metadata_file: str | None = None,
) -> list[str]:
    """`buildctl build` for a Dockerfile inside `context_dir`.

    The context and the Dockerfile are sent to buildkitd over the session by the
    client process, so the daemon never needs filesystem access to the
    orchestrator's private build directory.
    """
    dockerfile_path = Path(context_dir) / dockerfile
    argv = [
        buildctl,
        "--addr",
        f"unix://{socket_path}",
        "build",
        "--frontend",
        "dockerfile.v0",
        "--local",
        f"context={context_dir}",
        "--local",
        f"dockerfile={dockerfile_path.parent}",
        "--opt",
        f"filename={dockerfile_path.name}",
        "--progress",
        "plain",
        "--output",
        output,
    ]
    if metadata_file:
        argv += ["--metadata-file", metadata_file]
    return argv


def docker_output(tag: str) -> str:
    """Docker-archive on stdout, tagged so `docker load` registers `tag`."""
    return f"type=docker,name={tag}"


def registry_output(reference: str) -> str:
    return f"type=image,name={reference},push=true"


def docker_load_argv() -> list[str]:
    return ["docker", "load"]


def split_reference(reference: str) -> tuple[str, str]:
    """`registry/repo:tag` → (`registry/repo`, `tag`); a registry port is not a tag."""
    repository, _slash, last = reference.rpartition("/")
    name, separator, tag = last.partition(":")
    if not separator or not tag or not name:
        raise ValueError(f"image reference must carry an explicit tag: {reference!r}")
    return (f"{repository}/{name}" if repository else name), tag


def registry_host(repository: str) -> str:
    return repository.split("/", 1)[0]


# ------------------------------------------------------------ environment


def _build_env(docker_config_dir: Path) -> dict[str, str]:
    """`DOCKER_CONFIG` pinned to a writable dir: the systemd sandbox keeps $HOME
    read-only, and both `buildctl` (registry auth provider) and `docker load`
    consult it."""
    docker_config_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DOCKER_CONFIG"] = str(docker_config_dir)
    return env


def _write_registry_config(docker_config_dir: Path, host: str, auth: dict[str, str]) -> None:
    """A private `config.json` so buildctl's auth provider can push to `host`.

    Lives only for the duration of the build inside a 0700 scratch dir.
    """
    token = base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode()
    docker_config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = docker_config_dir / "config.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"auths": {host: {"auth": token}}}, handle)


# ------------------------------------------------------------- processes


async def _spawn(
    argv: tuple[str, ...],
    *,
    env: dict[str, str],
    stdin: int | None = None,
    stdout: int,
    stderr: int,
) -> Process:
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=env,
        )
    except OSError as exc:
        raise OrchestratorError(
            code="container_failure",
            message=f"prod build failed: could not start {Path(argv[0]).name}: {exc}",
            status_code=500,
        ) from exc


async def _terminate(*processes: Process) -> None:
    for process in processes:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()


def _failure(output: bytes, fallback: str) -> OrchestratorError:
    detail = output.decode("utf-8", errors="replace").strip()[-_BUILD_ERROR_DETAIL_CHARS:]
    return OrchestratorError(
        code="container_failure",
        message=f"prod build failed: {detail}" if detail else f"prod build failed: {fallback}",
        status_code=500,
    )


def _last_line(output: bytes) -> str:
    lines = output.decode("utf-8", errors="replace").strip().splitlines()
    return lines[-1][:200] if lines else ""


async def _attempt_docker_load(
    invocation: BuildKitInvocation, remaining: float
) -> tuple[bytes, bytes]:
    """One build whose docker-archive output is piped straight into `docker load`.

    Returns (build log, docker load output). The pipe is a kernel pipe between
    the two children — the orchestrator never buffers the image tarball.
    """
    assert invocation.load_argv is not None
    read_fd, write_fd = os.pipe()
    build: Process | None = None
    load: Process | None = None
    try:
        build = await _spawn(
            invocation.build_argv,
            env=invocation.env,
            stdout=write_fd,
            stderr=asyncio.subprocess.PIPE,
        )
        os.close(write_fd)
        write_fd = -1
        load = await _spawn(
            invocation.load_argv,
            env=invocation.env,
            stdin=read_fd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        os.close(read_fd)
        read_fd = -1
    except BaseException:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)
        await _terminate(*(process for process in (build, load) if process is not None))
        raise

    try:
        results = await asyncio.wait_for(
            asyncio.gather(build.communicate(), load.communicate()),
            timeout=remaining,
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        await _terminate(build, load)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise _BuildTimeout() from exc
    build_log = results[0][1] or b""
    load_output = results[1][0] or b""
    if build.returncode != 0:
        raise _failure(build_log, f"buildctl exited with code {build.returncode}")
    if load.returncode != 0:
        raise _failure(load_output, f"docker load exited with code {load.returncode}")
    return build_log, load_output


async def _attempt_push(invocation: BuildKitInvocation, remaining: float) -> bytes:
    """One build+push; returns the build log."""
    build = await _spawn(
        invocation.build_argv,
        env=invocation.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(build.communicate(), timeout=remaining)
    except (TimeoutError, asyncio.CancelledError) as exc:
        await _terminate(build)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise _BuildTimeout() from exc
    build_log = output or b""
    if build.returncode != 0:
        raise _failure(build_log, f"buildctl exited with code {build.returncode}")
    return build_log


# ---------------------------------------------------------------- retry


async def _run_attempts[T](
    attempt: Callable[[float], Awaitable[T]],
    *,
    tag: str,
    timeout_sec: float,
    max_attempts: int,
    retry_delay_sec: float,
) -> T:
    """Same policy as the Docker backend: one total deadline, one retry for a
    failed attempt, timeouts are terminal."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec

    def _timeout_error() -> OrchestratorError:
        return OrchestratorError(
            code="container_failure",
            message=f"prod build timed out after {timeout_sec:g}s total",
            status_code=504,
        )

    for attempt_number in range(1, max_attempts + 1):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise _timeout_error()
        try:
            return await attempt(remaining)
        except _BuildTimeout as exc:
            raise _timeout_error() from exc
        except OrchestratorError as exc:
            failure = exc
        if attempt_number >= max_attempts:
            raise failure
        log.warning(
            "buildkit.build_image_retry",
            tag=tag,
            attempt=attempt_number,
            max_attempts=max_attempts,
            err=failure.message[:500],
        )
        if retry_delay_sec > 0:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _timeout_error() from failure
            try:
                await asyncio.wait_for(asyncio.sleep(retry_delay_sec), timeout=remaining)
            except TimeoutError as timeout_exc:
                raise _timeout_error() from timeout_exc
    raise RuntimeError("unreachable build retry state")


# --------------------------------------------------------------- public


async def build_image(
    context_dir: str,
    dockerfile: str,
    tag: str,
    *,
    socket_path: str,
    buildctl: str = "buildctl",
    timeout_sec: float = 840,
    max_attempts: int = 2,
    retry_delay_sec: float = 2.0,
) -> str:
    """Build `tag` on the rootless buildkitd and load it into the local daemon.

    Returns the immutable image id, exactly like `docker_client.build_image`, so
    the deploy pipeline (inventory probe, container start, BYO transfer, prune)
    is unchanged whichever backend produced the image.
    """
    log.info(
        "buildkit.build_image",
        tag=tag,
        context=context_dir,
        dockerfile=dockerfile,
        socket=socket_path,
    )
    env = await asyncio.to_thread(_build_env, Path(get_settings().docker_cli_config_dir))
    invocation = BuildKitInvocation(
        build_argv=tuple(
            buildctl_argv(
                buildctl=buildctl,
                socket_path=socket_path,
                context_dir=context_dir,
                dockerfile=dockerfile,
                output=docker_output(tag),
            )
        ),
        env=env,
        load_argv=tuple(docker_load_argv()),
    )
    started = time.monotonic()

    async def attempt(remaining: float) -> tuple[bytes, bytes]:
        return await _attempt_docker_load(invocation, remaining)

    build_log, load_output = await _run_attempts(
        attempt,
        tag=tag,
        timeout_sec=timeout_sec,
        max_attempts=max_attempts,
        retry_delay_sec=retry_delay_sec,
    )
    image_id = await asyncio.to_thread(docker_client.image_id_for_tag, tag)
    log.info(
        "buildkit.build_image_done",
        tag=tag,
        image_id=image_id,
        elapsed_sec=round(time.monotonic() - started, 1),
        log_bytes=len(build_log),
        loaded=_last_line(load_output),
    )
    return image_id


def _read_pushed_digest(metadata_file: Path) -> str:
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OrchestratorError(
            code="container_failure",
            message=f"prod build image identity unavailable: {exc}",
            status_code=500,
        ) from exc
    digest = metadata.get("containerimage.digest") if isinstance(metadata, dict) else None
    prefix, separator, hex_digest = str(digest or "").partition(":")
    if (
        prefix != "sha256"
        or separator != ":"
        or len(hex_digest) != _DIGEST_HEX_LENGTH
        or any(char not in "0123456789abcdef" for char in hex_digest)
    ):
        raise OrchestratorError(
            code="container_failure",
            message="prod build returned an invalid image digest",
            status_code=500,
        )
    return f"{prefix}:{hex_digest}"


async def build_and_push(
    context_dir: str,
    dockerfile: str,
    reference: str,
    *,
    socket_path: str,
    registry_auth: dict[str, str] | None = None,
    buildctl: str = "buildctl",
    timeout_sec: float = 840,
    max_attempts: int = 2,
    retry_delay_sec: float = 2.0,
) -> str:
    """Build and push `reference` (`registry/repo:tag`) without touching the
    local daemon; returns the pinned `registry/repo@sha256:…` reference.

    `registry_auth` = {"username", "password"} (see `KubernetesPlacement.
    registry_auth`); it is written to a private scratch `DOCKER_CONFIG` for the
    duration of the build and removed afterwards.
    """
    repository, _tag = split_reference(reference)
    log.info(
        "buildkit.build_and_push",
        reference=reference,
        context=context_dir,
        dockerfile=dockerfile,
        socket=socket_path,
    )
    workdir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="omnia-buildkit-"))
    try:
        if registry_auth:
            config_dir = workdir / "docker"
            await asyncio.to_thread(
                _write_registry_config, config_dir, registry_host(repository), registry_auth
            )
        else:
            config_dir = Path(get_settings().docker_cli_config_dir)
        env = await asyncio.to_thread(_build_env, config_dir)
        metadata_file = workdir / "metadata.json"
        invocation = BuildKitInvocation(
            build_argv=tuple(
                buildctl_argv(
                    buildctl=buildctl,
                    socket_path=socket_path,
                    context_dir=context_dir,
                    dockerfile=dockerfile,
                    output=registry_output(reference),
                    metadata_file=str(metadata_file),
                )
            ),
            env=env,
        )
        started = time.monotonic()

        async def attempt(remaining: float) -> bytes:
            return await _attempt_push(invocation, remaining)

        build_log = await _run_attempts(
            attempt,
            tag=reference,
            timeout_sec=timeout_sec,
            max_attempts=max_attempts,
            retry_delay_sec=retry_delay_sec,
        )
        digest = await asyncio.to_thread(_read_pushed_digest, metadata_file)
        log.info(
            "buildkit.build_and_push_done",
            reference=reference,
            digest=digest,
            elapsed_sec=round(time.monotonic() - started, 1),
            log_bytes=len(build_log),
        )
        return f"{repository}@{digest}"
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, True)


__all__ = [
    "BuildKitInvocation",
    "build_and_push",
    "build_image",
    "buildctl_argv",
    "docker_load_argv",
    "docker_output",
    "registry_host",
    "registry_output",
    "split_reference",
]
