"""Rootless BuildKit build backend — argv contract, process lifecycle, dispatch.

No real buildkitd or docker: `asyncio.create_subprocess_exec` is replaced with
fakes that record argv/kwargs and play back exit codes, so the tests pin the
exact command lines the orchestrator issues and how it behaves when they fail,
hang or get cancelled.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from omnia_orchestrator.core import docker_client
from omnia_orchestrator.core.config import Settings
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services import builder, buildkit

IMAGE_ID = "sha256:" + "a" * 64
DIGEST = "sha256:" + "b" * 64
SOCKET = "/run/omnia-buildkit/buildkitd.sock"


class FakeProcess:
    """Enough of `asyncio.subprocess.Process` for the backend's lifecycle."""

    def __init__(
        self,
        returncode: int,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        delay: float = 0.0,
        on_start: Any = None,
    ) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._delay = delay
        self._on_start = on_start
        self.returncode: int | None = None
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._on_start is not None:
            self._on_start()
        if self._delay:
            await asyncio.sleep(self._delay)
        self.returncode = self._returncode
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        if self.returncode is None:
            self.returncode = -9
        return self.returncode


class Spawner:
    """Plays back a queue of processes and records every spawn."""

    def __init__(self, *processes: FakeProcess | BaseException) -> None:
        self.queue = list(processes)
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    async def __call__(self, *argv: str, **kwargs: Any) -> FakeProcess:
        self.calls.append((argv, kwargs))
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def argv(self, index: int) -> list[str]:
        return list(self.calls[index][0])


@pytest.fixture
def cli_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config_dir = tmp_path / "docker-cli"
    monkeypatch.setattr(
        buildkit, "get_settings", lambda: SimpleNamespace(docker_cli_config_dir=str(config_dir))
    )
    monkeypatch.setattr(docker_client, "image_id_for_tag", lambda _tag: IMAGE_ID)
    return config_dir


def _install(monkeypatch: pytest.MonkeyPatch, spawner: Spawner) -> None:
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawner)


# ------------------------------------------------------------ argv contract


def test_buildctl_argv_sends_context_and_dockerfile_over_the_session() -> None:
    argv = buildkit.buildctl_argv(
        buildctl="/usr/local/bin/buildctl",
        socket_path=SOCKET,
        context_dir="/tmp/omnia-build-x",
        dockerfile="Dockerfile.prod",
        output=buildkit.docker_output("omnia-app-x:1"),
    )

    assert argv == [
        "/usr/local/bin/buildctl",
        "--addr",
        f"unix://{SOCKET}",
        "build",
        "--frontend",
        "dockerfile.v0",
        "--local",
        "context=/tmp/omnia-build-x",
        "--local",
        "dockerfile=/tmp/omnia-build-x",
        "--opt",
        "filename=Dockerfile.prod",
        "--progress",
        "plain",
        "--output",
        "type=docker,name=omnia-app-x:1",
    ]


def test_buildctl_argv_supports_nested_dockerfile_and_metadata_file() -> None:
    argv = buildkit.buildctl_argv(
        buildctl="buildctl",
        socket_path=SOCKET,
        context_dir="/ctx",
        dockerfile="deploy/Dockerfile.prod",
        output=buildkit.registry_output("registry.yleum.ru/max-app/p:r1"),
        metadata_file="/tmp/meta.json",
    )

    assert "dockerfile=/ctx/deploy" in argv
    assert "filename=Dockerfile.prod" in argv
    assert "type=image,name=registry.yleum.ru/max-app/p:r1,push=true" in argv
    assert argv[-2:] == ["--metadata-file", "/tmp/meta.json"]


def test_split_reference_keeps_registry_ports_out_of_the_tag() -> None:
    assert buildkit.split_reference("registry.yleum.ru/max-app/p:r1") == (
        "registry.yleum.ru/max-app/p",
        "r1",
    )
    assert buildkit.split_reference("127.0.0.1:5000/max-app/p:r1") == (
        "127.0.0.1:5000/max-app/p",
        "r1",
    )
    assert buildkit.split_reference("omnia-app-x:17") == ("omnia-app-x", "17")
    assert buildkit.registry_host("127.0.0.1:5000/max-app/p") == "127.0.0.1:5000"
    with pytest.raises(ValueError, match="explicit tag"):
        buildkit.split_reference("registry.yleum.ru/max-app/p")


# --------------------------------------------------------- docker-load path


async def test_build_image_pipes_archive_into_docker_load_and_returns_image_id(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    build = FakeProcess(0, stderr=b"#1 [internal] load build definition\n#9 DONE 12.3s\n")
    load = FakeProcess(0, stdout=b"Loaded image: omnia-app-x:1\n")
    spawner = Spawner(build, load)
    _install(monkeypatch, spawner)

    image_id = await buildkit.build_image(
        "/tmp/ctx",
        "Dockerfile.prod",
        "omnia-app-x:1",
        socket_path=SOCKET,
        buildctl="/usr/local/bin/buildctl",
        retry_delay_sec=0,
    )

    assert image_id == IMAGE_ID
    assert len(spawner.calls) == 2
    build_argv, build_kwargs = spawner.calls[0]
    load_argv, load_kwargs = spawner.calls[1]
    assert build_argv == tuple(
        buildkit.buildctl_argv(
            buildctl="/usr/local/bin/buildctl",
            socket_path=SOCKET,
            context_dir="/tmp/ctx",
            dockerfile="Dockerfile.prod",
            output="type=docker,name=omnia-app-x:1",
        )
    )
    assert load_argv == ("docker", "load")
    # The image tarball flows child → child through one kernel pipe.
    write_end = build_kwargs["stdout"]
    read_end = load_kwargs["stdin"]
    assert isinstance(write_end, int) and isinstance(read_end, int)
    assert build_kwargs["stderr"] == asyncio.subprocess.PIPE
    assert load_kwargs["stdout"] == asyncio.subprocess.PIPE
    assert load_kwargs["stderr"] == asyncio.subprocess.STDOUT
    for fd in (write_end, read_end):  # the parent keeps no copy of either end
        with pytest.raises(OSError):
            os.fstat(fd)
    for kwargs in (build_kwargs, load_kwargs):
        assert kwargs["env"]["DOCKER_CONFIG"] == str(cli_config)
    assert stat.S_ISDIR(os.stat(cli_config).st_mode)  # created for the CLI, as the docker path does


async def test_build_image_retries_one_failed_attempt(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    spawner = Spawner(
        FakeProcess(1, stderr=b"error: failed to solve: rpc error: transient"),
        FakeProcess(1, stdout=b"open /var/lib/docker/tmp: no such file"),
        FakeProcess(0, stderr=b"#9 DONE"),
        FakeProcess(0, stdout=b"Loaded image: omnia-app-x:1"),
    )
    _install(monkeypatch, spawner)

    image_id = await buildkit.build_image(
        "/tmp/ctx", "Dockerfile.prod", "omnia-app-x:1", socket_path=SOCKET, retry_delay_sec=0
    )

    assert image_id == IMAGE_ID
    assert len(spawner.calls) == 4


async def test_build_image_reports_buildctl_log_tail_after_retry(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    spawner = Spawner(
        FakeProcess(1, stderr=b'#7 ERROR: process "/bin/sh -c pnpm build" exited with code 1'),
        FakeProcess(1),
        FakeProcess(1, stderr=b'#7 ERROR: process "/bin/sh -c pnpm build" exited with code 1'),
        FakeProcess(1),
    )
    _install(monkeypatch, spawner)

    with pytest.raises(OrchestratorError, match=r"prod build failed: .*pnpm build") as info:
        await buildkit.build_image(
            "/tmp/ctx", "Dockerfile.prod", "omnia-app-x:1", socket_path=SOCKET, retry_delay_sec=0
        )

    assert info.value.code == "container_failure"
    assert info.value.status_code == 500
    assert len(spawner.calls) == 4


async def test_build_image_reports_docker_load_failure(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    spawner = Spawner(
        FakeProcess(0, stderr=b"#9 DONE"),
        FakeProcess(1, stdout=b"invalid tar header"),
    )
    _install(monkeypatch, spawner)

    with pytest.raises(OrchestratorError, match="prod build failed: invalid tar header"):
        await buildkit.build_image(
            "/tmp/ctx",
            "Dockerfile.prod",
            "omnia-app-x:1",
            socket_path=SOCKET,
            max_attempts=1,
        )


async def test_build_image_uses_exit_code_when_log_is_empty(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    spawner = Spawner(FakeProcess(137), FakeProcess(1))
    _install(monkeypatch, spawner)

    with pytest.raises(OrchestratorError, match="buildctl exited with code 137"):
        await buildkit.build_image(
            "/tmp/ctx",
            "Dockerfile.prod",
            "omnia-app-x:1",
            socket_path=SOCKET,
            max_attempts=1,
        )


async def test_build_image_times_out_and_kills_both_processes(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    build = FakeProcess(0, delay=1.0)
    load = FakeProcess(0, delay=1.0)
    spawner = Spawner(build, load)
    _install(monkeypatch, spawner)

    with pytest.raises(OrchestratorError, match=r"timed out after 0\.05s total") as info:
        await buildkit.build_image(
            "/tmp/ctx", "Dockerfile.prod", "omnia-app-x:1", socket_path=SOCKET, timeout_sec=0.05
        )

    assert info.value.status_code == 504
    assert build.killed and build.waited
    assert load.killed and load.waited
    assert len(spawner.calls) == 2  # a timeout is terminal, never retried


async def test_build_image_kills_both_processes_on_cancellation(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    started = asyncio.Event()
    build = FakeProcess(0, delay=10.0, on_start=started.set)
    load = FakeProcess(0, delay=10.0)
    _install(monkeypatch, Spawner(build, load))

    task = asyncio.create_task(
        buildkit.build_image("/tmp/ctx", "Dockerfile.prod", "omnia-app-x:1", socket_path=SOCKET)
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert build.killed and load.killed


async def test_build_image_missing_buildctl_is_a_build_failure_without_fd_leak(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    pipes: list[tuple[int, int]] = []
    real_pipe = os.pipe

    def recording_pipe() -> tuple[int, int]:
        fds = real_pipe()
        pipes.append(fds)
        return fds

    monkeypatch.setattr(buildkit.os, "pipe", recording_pipe)
    spawner = Spawner(FileNotFoundError(2, "No such file or directory", "buildctl"))
    _install(monkeypatch, spawner)

    with pytest.raises(OrchestratorError, match="could not start buildctl"):
        await buildkit.build_image(
            "/tmp/ctx",
            "Dockerfile.prod",
            "omnia-app-x:1",
            socket_path=SOCKET,
            max_attempts=1,
        )

    assert len(pipes) == 1
    for fd in pipes[0]:
        with pytest.raises(OSError):
            os.fstat(fd)


async def test_build_image_kills_buildctl_when_docker_load_cannot_start(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    build = FakeProcess(0, delay=10.0)
    spawner = Spawner(build, FileNotFoundError(2, "No such file or directory", "docker"))
    _install(monkeypatch, spawner)

    with pytest.raises(OrchestratorError, match="could not start docker"):
        await buildkit.build_image(
            "/tmp/ctx",
            "Dockerfile.prod",
            "omnia-app-x:1",
            socket_path=SOCKET,
            max_attempts=1,
        )

    assert build.killed and build.waited


async def test_build_image_identity_failure_is_terminal(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    spawner = Spawner(FakeProcess(0), FakeProcess(0))
    _install(monkeypatch, spawner)

    def missing(_tag: str) -> str:
        raise OrchestratorError(
            code="container_failure",
            message="prod build image identity unavailable: not found",
            status_code=500,
        )

    monkeypatch.setattr(docker_client, "image_id_for_tag", missing)

    with pytest.raises(OrchestratorError, match="identity unavailable"):
        await buildkit.build_image(
            "/tmp/ctx", "Dockerfile.prod", "omnia-app-x:1", socket_path=SOCKET, retry_delay_sec=0
        )

    assert len(spawner.calls) == 2  # the build itself succeeded — no rebuild


def test_image_id_for_tag_validates_the_immutable_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def client_with(image_id: str) -> SimpleNamespace:
        return SimpleNamespace(images=SimpleNamespace(get=lambda _t: SimpleNamespace(id=image_id)))

    monkeypatch.setattr(docker_client, "_get_client", lambda: client_with(IMAGE_ID))
    assert docker_client.image_id_for_tag("omnia-app-x:1") == IMAGE_ID

    monkeypatch.setattr(docker_client, "_get_client", lambda: client_with("x:1"))
    with pytest.raises(OrchestratorError, match="invalid immutable image id"):
        docker_client.image_id_for_tag("omnia-app-x:1")


# ------------------------------------------------------------ registry path


def _metadata_writer(digest: str) -> Any:
    """A spawner hook that writes buildctl's metadata file named in argv."""

    class Writer(Spawner):
        async def __call__(self, *argv: str, **kwargs: Any) -> FakeProcess:
            path = argv[argv.index("--metadata-file") + 1]
            with open(path, "w", encoding="utf-8") as handle:  # noqa: ASYNC230 — test fake
                json.dump({"containerimage.digest": digest}, handle)
            return await super().__call__(*argv, **kwargs)

    return Writer


async def test_build_and_push_uses_registry_output_and_a_private_auth_config(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(buildkit.tempfile, "tempdir", str(tmp_path))  # scratch dir under tmp_path
    seen_config: dict[str, Any] = {}
    original_spawn = _metadata_writer(DIGEST)(FakeProcess(0, stdout=b"#10 pushing layers\n"))

    async def spawn(*argv: str, **kwargs: Any) -> FakeProcess:
        config_dir = Path(kwargs["env"]["DOCKER_CONFIG"])
        seen_config["dir"] = config_dir
        seen_config["json"] = json.loads((config_dir / "config.json").read_text())
        seen_config["mode"] = (config_dir / "config.json").stat().st_mode & 0o777
        return await original_spawn(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    reference = await buildkit.build_and_push(
        "/tmp/ctx",
        "Dockerfile.prod",
        "registry.yleum.ru/max-app/p:r1",
        socket_path=SOCKET,
        registry_auth={"username": "max", "password": "s3cret"},
        retry_delay_sec=0,
    )

    assert reference == f"registry.yleum.ru/max-app/p@{DIGEST}"
    argv = original_spawn.argv(0)
    assert "type=image,name=registry.yleum.ru/max-app/p:r1,push=true" in argv
    assert "--metadata-file" in argv
    token = base64.b64encode(b"max:s3cret").decode()
    assert seen_config["json"] == {"auths": {"registry.yleum.ru": {"auth": token}}}
    assert seen_config["mode"] == 0o600
    assert seen_config["dir"] != cli_config
    assert not seen_config["dir"].exists()  # scratch credentials never outlive the build


async def test_build_and_push_without_auth_uses_the_orchestrator_cli_config(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    spawner = _metadata_writer(DIGEST)(FakeProcess(0))
    _install(monkeypatch, spawner)

    reference = await buildkit.build_and_push(
        "/tmp/ctx", "Dockerfile.prod", "127.0.0.1:5000/max-app/p:r1", socket_path=SOCKET
    )

    assert reference == f"127.0.0.1:5000/max-app/p@{DIGEST}"
    assert spawner.calls[0][1]["env"]["DOCKER_CONFIG"] == str(cli_config)


async def test_build_and_push_rejects_an_unpinned_result(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    _install(monkeypatch, _metadata_writer("sha256:short")(FakeProcess(0)))

    with pytest.raises(OrchestratorError, match="invalid image digest"):
        await buildkit.build_and_push(
            "/tmp/ctx", "Dockerfile.prod", "registry.yleum.ru/max-app/p:r1", socket_path=SOCKET
        )


async def test_build_and_push_reports_push_failure_and_kills_on_timeout(
    monkeypatch: pytest.MonkeyPatch, cli_config: Path
) -> None:
    denied = FakeProcess(1, stdout=b"error: failed to push: 401 Unauthorized")
    _install(monkeypatch, Spawner(denied))
    with pytest.raises(OrchestratorError, match="401 Unauthorized"):
        await buildkit.build_and_push(
            "/tmp/ctx",
            "Dockerfile.prod",
            "registry.yleum.ru/max-app/p:r1",
            socket_path=SOCKET,
            max_attempts=1,
        )

    slow = FakeProcess(0, delay=1.0)
    _install(monkeypatch, Spawner(slow))
    with pytest.raises(OrchestratorError, match=r"timed out after 0\.05s total"):
        await buildkit.build_and_push(
            "/tmp/ctx",
            "Dockerfile.prod",
            "registry.yleum.ru/max-app/p:r1",
            socket_path=SOCKET,
            timeout_sec=0.05,
        )
    assert slow.killed


# ------------------------------------------------------- dispatch + config


async def test_builder_dispatches_to_buildkit_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    via_buildkit = AsyncMock(return_value=IMAGE_ID)
    via_docker = AsyncMock(return_value=IMAGE_ID)
    monkeypatch.setattr(builder.buildkit, "build_image", via_buildkit)
    monkeypatch.setattr(builder.docker_client, "build_image", via_docker)

    monkeypatch.setattr(
        builder,
        "get_settings",
        lambda: SimpleNamespace(
            build_backend="buildkit",
            buildkit_socket=SOCKET,
            buildctl_binary="/usr/local/bin/buildctl",
        ),
    )
    assert await builder._build_prod_image("/tmp/ctx", "Dockerfile.prod", "t:1") == IMAGE_ID
    via_buildkit.assert_awaited_once_with(
        "/tmp/ctx",
        "Dockerfile.prod",
        "t:1",
        socket_path=SOCKET,
        buildctl="/usr/local/bin/buildctl",
    )
    via_docker.assert_not_awaited()

    monkeypatch.setattr(builder, "get_settings", lambda: SimpleNamespace(build_backend="docker"))
    assert await builder._build_prod_image("/tmp/ctx", "Dockerfile.prod", "t:2") == IMAGE_ID
    via_docker.assert_awaited_once_with("/tmp/ctx", "Dockerfile.prod", "t:2")
    assert via_buildkit.await_count == 1


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:5432/test",
        internal_token="test-internal-token-not-a-real-secret",
        **cast(dict[str, Any], overrides),
    )


def test_build_backend_defaults_to_docker_so_prod_is_unchanged() -> None:
    settings = _settings()

    assert settings.build_backend == "docker"
    assert settings.buildkit_socket == "/run/omnia-buildkit/buildkitd.sock"
    assert settings.buildctl_binary == "buildctl"


def test_build_backend_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_BACKEND", "buildkit")
    monkeypatch.setenv("BUILDKIT_SOCKET", "/run/custom/buildkitd.sock")

    settings = _settings()

    assert settings.build_backend == "buildkit"
    assert settings.buildkit_socket == "/run/custom/buildkitd.sock"


def test_build_backend_rejects_unknown_values_and_empty_socket() -> None:
    with pytest.raises(ValidationError):
        _settings(build_backend="podman")
    with pytest.raises(ValidationError, match="requires buildkit_socket"):
        _settings(build_backend="buildkit", buildkit_socket=" ")
    with pytest.raises(ValidationError, match="requires buildctl_binary"):
        _settings(build_backend="buildkit", buildctl_binary="")
