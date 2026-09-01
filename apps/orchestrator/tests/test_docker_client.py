"""Unit tests for the docker_client surface that doesn't talk to Docker.

The thick parts (start_container body, exec_cmd) require a real docker
daemon and live in integration tests on the VPS. Here we cover the
deterministic helpers + dataclass guarantees + module-level constants
that regress under refactors: `ContainerSpec` defaults, label assembly,
the label-set we stamp onto containers.
"""

from __future__ import annotations

import asyncio
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import docker  # type: ignore[import-untyped]
import pytest

from omnia_orchestrator.core import docker_client
from omnia_orchestrator.core.docker_client import ContainerSpec
from omnia_orchestrator.core.errors import OrchestratorError


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_container_spec_defaults_match_brief() -> None:
    """Free-tier defaults: 0.5 CPU, 512 MB, kind=dev, restart=no, tier=free.
    These are the security/quota numbers AGENT-D-ORCHESTRATOR.md promises —
    any change should be intentional and approved."""
    spec = ContainerSpec(
        name="omnia-dev-x",
        image="omnia-template-x:dev",
        port=3200,
        project_id="00000000-0000-0000-0000-000000000001",
        env={},
    )
    assert spec.cpu_quota == 0.5
    assert spec.memory_mb == 512
    assert spec.kind == "dev"
    assert spec.restart_policy_name == "no"
    assert spec.tier == "free"
    assert spec.network_name is None


def test_container_spec_is_frozen() -> None:
    """Frozen dataclass — mutating raises FrozenInstanceError. Catching
    accidental in-place changes (e.g. in test fixtures) keeps caller
    invariants honest."""
    import dataclasses

    spec = ContainerSpec(
        name="x",
        image="y",
        port=1,
        project_id="p",
        env={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.port = 2  # type: ignore[misc]


def test_container_spec_carries_tier_for_hibernate() -> None:
    """`tier` flows from ProvisionRequest → ContainerSpec → omnia.tier
    label → hibernate sweeper. The whole policy hangs on this field
    being respected — no implicit defaults that would silently grant
    pro privileges."""
    free = ContainerSpec(
        name="x",
        image="y",
        port=1,
        project_id="p",
        env={},
        tier="free",
    )
    pro = ContainerSpec(
        name="x",
        image="y",
        port=1,
        project_id="p",
        env={},
        tier="pro",
    )
    assert free.tier == "free"
    assert pro.tier == "pro"
    # Defaults to free explicitly so a partial spec construction never
    # accidentally lands in a pro tier.
    bare = ContainerSpec(name="x", image="y", port=1, project_id="p", env={})
    assert bare.tier == "free"


class _FakeContainer:
    def __init__(
        self,
        cid: str,
        image: str,
        status: str = "running",
        *,
        sandbox_profile: str = "",
    ) -> None:
        self.id = cid
        self.status = status
        self.attrs = {
            "Config": {
                "Image": image,
                "Labels": {"omnia.sandbox_profile": sandbox_profile},
            }
        }
        self.removed = False
        self.started = False
        self.unpause_calls = 0
        self.stop_timeouts: list[int] = []
        self.archives: list[tuple[str, bytes]] = []
        self.exec_runs: list[tuple[list[str], str]] = []

    def reload(self) -> None:
        pass

    def start(self) -> None:
        self.started = True
        self.status = "running"

    def unpause(self) -> None:
        self.unpause_calls += 1
        self.status = "running"

    def pause(self) -> None:
        self.status = "paused"

    def stop(self, timeout: int) -> None:
        self.stop_timeouts.append(timeout)
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True

    def put_archive(self, *, path: str, data: bytes) -> bool:
        self.archives.append((path, data))
        return True

    def exec_run(self, cmd: list[str], user: str) -> object:
        self.exec_runs.append((list(cmd), user))
        return SimpleNamespace(exit_code=0)


class _FakeContainers:
    def __init__(self, existing: _FakeContainer | None) -> None:
        self._existing = existing
        self.run_image: str | None = None
        self.run_called = False
        self.run_kwargs: dict[str, Any] = {}

    def get(self, name: str) -> _FakeContainer:
        if self._existing is None:
            raise docker.errors.NotFound(name)
        return self._existing

    def run(self, *, image: str, **kwargs: Any) -> _FakeContainer:
        self.run_called = True
        self.run_image = image
        self.run_kwargs = kwargs
        return _FakeContainer("new-container-id", image)


class _FakeNetwork:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connections: list[tuple[str, tuple[str, ...]]] = []

    def connect(self, service: str, *, aliases: list[str]) -> None:
        self.connections.append((service, tuple(aliases)))


class _FakeNetworks:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.created: list[str] = []
        self.create_kwargs: dict[str, dict[str, Any]] = {}
        self.items: dict[str, _FakeNetwork] = {name: _FakeNetwork(name) for name in self.existing}

    def get(self, name: str) -> _FakeNetwork:
        if name not in self.existing:
            raise docker.errors.NotFound(name)
        return self.items[name]

    def create(self, name: str, **kwargs: Any) -> _FakeNetwork:
        self.created.append(name)
        self.create_kwargs[name] = kwargs
        self.existing.add(name)
        network = _FakeNetwork(name)
        self.items[name] = network
        return network


class _FakeClient:
    def __init__(self, existing: _FakeContainer | None) -> None:
        self.containers = _FakeContainers(existing)
        self.networks = _FakeNetworks()


def _spec(image: str) -> ContainerSpec:
    return ContainerSpec(
        name="omnia-dev-x",
        image=image,
        port=3200,
        project_id="00000000-0000-0000-0000-000000000001",
        env={},
    )


async def test_start_container_recreates_on_image_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stack switch (drizzle→nextjs-entities) re-provisions with a new image.
    The stale container must be removed and a fresh one created from the new
    image — otherwise generated entity code runs against the wrong template's
    kit and 500s on `@/components/ui/*`. Regression for the stack-switch bug."""
    stale = _FakeContainer("old-id", "omnia-template-nextjs-postgres-drizzle:dev")
    client = _FakeClient(stale)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    cid = await docker_client.start_container(_spec("omnia-template-nextjs-entities:dev"))

    assert stale.removed is True, "stale container must be removed on image change"
    assert client.containers.run_called is True
    assert client.containers.run_image == "omnia-template-nextjs-entities:dev"
    assert cid == "new-container-id"


async def test_start_container_reuses_when_image_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same image tag (incl. a same-tag rebuild) → reuse the running container,
    never recreate. A rebuild must not disturb live apps mid-session."""
    same = _FakeContainer("live-id", "omnia-template-nextjs-entities:dev", status="running")
    client = _FakeClient(same)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    cid = await docker_client.start_container(_spec("omnia-template-nextjs-entities:dev"))

    assert same.removed is False
    assert client.containers.run_called is False
    assert cid == "live-id"


async def test_stop_container_releases_memory_from_legacy_paused_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paused = _FakeContainer(
        "paused-id",
        "omnia-template-nextjs-entities:dev",
        status="paused",
    )
    client = _FakeClient(paused)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.stop_container("omnia-dev-x", pause=False)

    assert paused.unpause_calls == 1
    assert paused.stop_timeouts == [10]
    assert paused.status == "exited"


async def test_stop_container_releases_memory_from_restarting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarting = _FakeContainer(
        "restarting-id",
        "omnia-template-nextjs-entities:dev",
        status="restarting",
    )
    client = _FakeClient(restarting)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.stop_container("omnia-dev-x", pause=False)

    assert restarting.stop_timeouts == [10]
    assert restarting.status == "exited"


async def test_stop_container_is_idempotent_when_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exited = _FakeContainer(
        "exited-id",
        "omnia-template-nextjs-entities:dev",
        status="exited",
    )
    client = _FakeClient(exited)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.stop_container("omnia-dev-x", pause=False)

    assert exited.unpause_calls == 0
    assert exited.stop_timeouts == []
    assert exited.status == "exited"


async def test_write_files_preserves_only_root_runtime_entrypoint_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _FakeContainer(
        "running-id",
        "omnia-template-nextjs-entities:dev",
        status="running",
    )
    client = _FakeClient(running)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    result = await docker_client.write_files(
        "omnia-dev-x",
        {
            "docker-entrypoint.sh": "#!/bin/sh\nexec npm run dev\n",
            "src/app.ts": "export const ok = true;\n",
            "nested/docker-entrypoint.sh": "#!/bin/sh\nexit 0\n",
        },
    )

    assert result["written"] == "3"
    assert len(running.archives) == 1
    archive_root, archive_bytes = running.archives[0]
    assert archive_root == "/app"
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        assert archive.getmember("docker-entrypoint.sh").mode == 0o755
        assert archive.getmember("src/app.ts").mode == 0o644
        assert archive.getmember("nested/docker-entrypoint.sh").mode == 0o644


async def test_write_files_preserves_explicit_empty_file_and_deletes_legacy_empty_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = _FakeContainer(
        "running-id",
        "omnia-template-nextjs-entities:dev",
        status="running",
    )
    client = _FakeClient(running)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    result = await docker_client.write_files(
        "omnia-dev-x",
        {
            "blank.txt": "",
            "removed.txt": "",
        },
        empty_files=("blank.txt",),
    )

    assert result["written"] == "1"
    assert result["deleted"] == "1"
    assert len(running.archives) == 1
    archive_root, archive_bytes = running.archives[0]
    assert archive_root == "/app"
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        blank = archive.getmember("blank.txt")
        assert blank.size == 0
        with pytest.raises(KeyError):
            archive.getmember("removed.txt")
    assert running.exec_runs == [(["rm", "-f", "/app/removed.txt"], "1000:1000")]


def test_dev_templates_invoke_entrypoint_through_shell() -> None:
    templates = Path(__file__).resolve().parents[1] / "templates"
    for template in (
        "bare-nextjs",
        "max-miniapp-nextjs",
        "nextjs-entities",
        "nextjs-postgres-drizzle",
        "nextjs-realtime",
    ):
        dockerfile = (templates / template / "Dockerfile.dev").read_text(encoding="utf-8")
        assert 'CMD ["sh", "./docker-entrypoint.sh"]' in dockerfile


def test_module_exposes_expected_public_api() -> None:
    """Smoke test: the public surface AGENT-D-ORCHESTRATOR.md promises +
    what's documented in `routers/runtime.py` imports actually exists."""
    expected = {
        "ContainerSpec",
        "start_container",
        "stop_container",
        "container_status",
        "destroy_container",
        "find_project_container",
        "wake_container",
        "unpause_container",
        "write_files",
        "exec_cmd",
        "copy_path_from_container",
        "build_image",
        "prune_old_app_images",
        "container_logs",
        "container_image_name",
        "run_sandbox_command",
    }
    for name in expected:
        assert hasattr(docker_client, name), f"missing public symbol: {name}"


async def test_build_image_retries_one_transient_build_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    class Process:
        def __init__(self, returncode: int, output: bytes) -> None:
            self.returncode = returncode
            self.output = output

        async def communicate(self) -> tuple[bytes, None]:
            return self.output, None

    async def start(*_args: object, **_kwargs: object) -> Process:
        nonlocal calls
        calls += 1
        return Process(137, b"command returned code 137") if calls == 1 else Process(0, b"")

    settings = type("Settings", (), {"docker_cli_config_dir": str(tmp_path)})()
    monkeypatch.setattr(docker_client, "get_settings", lambda: settings)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    await docker_client.build_image(
        ".",
        "Dockerfile.prod",
        "omnia-app-test:1",
        timeout_sec=2,
        retry_delay_sec=0,
    )

    assert calls == 2


async def test_build_image_reports_terminal_error_after_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    class Process:
        returncode = 1

        async def communicate(self) -> tuple[bytes, None]:
            return b"next build exited with code 1", None

    async def start(*_args: object, **_kwargs: object) -> Process:
        nonlocal calls
        calls += 1
        return Process()

    settings = type("Settings", (), {"docker_cli_config_dir": str(tmp_path)})()
    monkeypatch.setattr(docker_client, "get_settings", lambda: settings)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    with pytest.raises(OrchestratorError, match="next build exited with code 1"):
        await docker_client.build_image(
            ".",
            "Dockerfile.prod",
            "omnia-app-test:1",
            timeout_sec=2,
            retry_delay_sec=0,
        )

    assert calls == 2


async def test_build_image_uses_one_total_deadline_across_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    class Process:
        returncode = 1

        async def communicate(self) -> tuple[bytes, None]:
            return b"transient daemon failure", None

    async def start(*_args: object, **_kwargs: object) -> Process:
        nonlocal calls
        calls += 1
        return Process()

    settings = type("Settings", (), {"docker_cli_config_dir": str(tmp_path)})()
    monkeypatch.setattr(docker_client, "get_settings", lambda: settings)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    with pytest.raises(OrchestratorError, match=r"timed out after 0\.05s total"):
        await docker_client.build_image(
            ".",
            "Dockerfile.prod",
            "omnia-app-test:1",
            timeout_sec=0.05,
            retry_delay_sec=0.1,
        )

    assert calls == 1


async def test_build_image_kills_cli_process_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Process:
        returncode = None
        killed = False

        async def communicate(self) -> tuple[bytes, None]:
            await asyncio.sleep(1)
            return b"", None

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.returncode = -9
            return self.returncode

    process = Process()

    async def start(*_args: object, **_kwargs: object) -> Process:
        return process

    settings = type("Settings", (), {"docker_cli_config_dir": str(tmp_path)})()
    monkeypatch.setattr(docker_client, "get_settings", lambda: settings)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    with pytest.raises(OrchestratorError, match=r"timed out after 0\.05s total"):
        await docker_client.build_image(
            ".",
            "Dockerfile.prod",
            "omnia-app-test:1",
            timeout_sec=0.05,
        )

    assert process.killed is True


async def test_build_image_kills_cli_process_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False

        async def communicate(self) -> tuple[bytes, None]:
            started.set()
            await asyncio.sleep(10)
            return b"", None

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.returncode = -9
            return self.returncode

    process = Process()

    async def start(*_args: object, **_kwargs: object) -> Process:
        return process

    settings = type("Settings", (), {"docker_cli_config_dir": str(tmp_path)})()
    monkeypatch.setattr(docker_client, "get_settings", lambda: settings)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)

    task = asyncio.create_task(
        docker_client.build_image(".", "Dockerfile.prod", "omnia-app-test:1")
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True


# ── Sandbox hardening (Phase 1) ─────────────────────────────────────────────


def test_container_spec_hardening_defaults_off() -> None:
    """Hardening knobs default to OFF so a bare spec produces today's exact
    run kwargs — the feature ships dark (R-10). A change here means prod
    container behaviour changed and must be intentional."""
    spec = ContainerSpec(name="x", image="y", port=1, project_id="p", env={})
    assert spec.runtime == ""
    assert spec.harden is False
    assert spec.pids_limit == 0


async def test_docker_runtime_facts_attests_requested_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = type(
        "Client",
        (),
        {
            "info": lambda self: {
                "DefaultRuntime": "runc",
                "Runtimes": {"runc": {}, "runsc": {}},
            }
        },
    )()
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    result = await docker_client.docker_runtime_facts("runsc")

    assert result == {
        "ready": True,
        "missing": [],
        "checks": {"registered": True},
        "configured": True,
        "runtime": "runsc",
        "default_runtime": "runc",
    }


async def test_docker_runtime_facts_fails_closed_when_runtime_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = type(
        "Client",
        (),
        {
            "info": lambda self: {
                "DefaultRuntime": "runc",
                "Runtimes": {"runc": {}},
            }
        },
    )()
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    result = await docker_client.docker_runtime_facts("runsc")

    assert result["ready"] is False
    assert result["missing"] == ["runtime_unavailable"]
    assert result["runtime"] == "runsc"
    assert result["default_runtime"] == "runc"


async def test_start_container_default_adds_no_security_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With default spec, start_container passes NO runtime/security_opt/
    pids_limit — byte-identical to pre-Phase-1 so OFF is a true no-op."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.start_container(_spec("omnia-template-x:dev"))

    assert client.containers.run_called is True
    assert "runtime" not in client.containers.run_kwargs
    assert "security_opt" not in client.containers.run_kwargs
    assert "pids_limit" not in client.containers.run_kwargs


async def test_start_container_applies_hardening_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the spec carries gVisor runtime + harden, those land on the
    docker run kwargs: runtime=runsc, no-new-privileges, PID ceiling."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    spec = ContainerSpec(
        name="omnia-dev-x",
        image="omnia-template-x:dev",
        port=3200,
        project_id="00000000-0000-0000-0000-000000000001",
        env={},
        runtime="runsc",
        harden=True,
        pids_limit=512,
    )
    await docker_client.start_container(spec)

    kw = client.containers.run_kwargs
    assert kw.get("runtime") == "runsc"
    assert kw.get("security_opt") == ["no-new-privileges:true"]
    assert kw.get("pids_limit") == 512


async def test_start_container_harden_without_pids_limit_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """harden on but pids_limit=0 → no-new-privileges applies, pids_limit is
    omitted (0 must not become a real, accidental ceiling)."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    spec = ContainerSpec(
        name="omnia-dev-x",
        image="omnia-template-x:dev",
        port=3200,
        project_id="00000000-0000-0000-0000-000000000001",
        env={},
        harden=True,
        pids_limit=0,
    )
    await docker_client.start_container(spec)

    kw = client.containers.run_kwargs
    assert kw.get("security_opt") == ["no-new-privileges:true"]
    assert "pids_limit" not in kw


async def test_container_image_name_reads_container_config_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _FakeContainer("live-id", "ghcr.io/acme/omnia-template-nextjs-entities:dev")
    client = _FakeClient(live)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    image = await docker_client.container_image_name("omnia-dev-x")

    assert image == "ghcr.io/acme/omnia-template-nextjs-entities:dev"


def _tar_bytes(
    files: dict[str, bytes],
    *,
    symlinks: dict[str, str] | None = None,
    directories: list[str] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name=name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
        for name in directories or []:
            info = tarfile.TarInfo(name=name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
    return buf.getvalue()


async def test_run_sandbox_command_uses_read_only_workspace_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed", encoding="utf-8")
    archive_bytes = _tar_bytes(
        {
            "workspace/seed.txt": b"seed",
            "workspace/src/result.ts": b"export const ok = true;\n",
        }
    )

    class _SandboxContainer:
        def __init__(self) -> None:
            self.removed_force: bool | None = None
            self.wait_timeout: int | None = None

        def wait(self, timeout: int) -> dict[str, int]:
            self.wait_timeout = timeout
            return {"StatusCode": 0}

        def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
            assert stdout is True
            assert stderr is True
            return b"pnpm typecheck clean"

        def remove(self, force: bool = False) -> None:
            self.removed_force = force

    class _SandboxContainers:
        def __init__(self) -> None:
            self.run_kwargs: dict[str, Any] = {}
            self.container = _SandboxContainer()

        def run(self, *, image: str, **kwargs: Any) -> _SandboxContainer:
            self.run_kwargs = {"image": image, **kwargs}
            result_archive = workspace.parent / "sandbox-output" / "workspace.tar"
            output_file = Path(kwargs["volumes"][str(result_archive)]["bind"])
            assert output_file == Path("/sandbox-output.tar")
            result_archive.write_bytes(archive_bytes)
            return self.container

    client = type("C", (), {"containers": _SandboxContainers()})()
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    result = await docker_client.run_sandbox_command(
        image="omnia-template-max-miniapp-nextjs:dev",
        workspace_dir=workspace,
        project_id="00000000-0000-0000-0000-000000000001",
        cmd="pnpm typecheck",
        network_name="omnia-proj-0001",
        runtime="runsc",
        harden=True,
        pids_limit=64,
        timeout_sec=12,
        max_output=64,
    )

    kw = client.containers.run_kwargs
    assert result == {"exit_code": "0", "stdout": "pnpm typecheck clean", "stderr": ""}
    assert kw["image"] == "omnia-template-max-miniapp-nextjs:dev"
    assert kw["command"][0:2] == ["sh", "-lc"]
    assert "cd /workspace" in kw["command"][2]
    assert 'sh -lc "$OMNIA_SANDBOX_COMMAND"' in kw["command"][2]
    assert "pnpm typecheck" not in kw["command"][2]
    assert "sandbox bootstrap failed: seed mount unavailable" in kw["command"][2]
    assert "if ! cp -R /seed/. /workspace/" in kw["command"][2]
    assert "ulimit -Sf 163840" in kw["command"][2]
    assert "kill -KILL $survivor_pids" in kw["command"][2]
    assert "background cleanup failed" in kw["command"][2]
    assert "tar --exclude='./node_modules'" in kw["command"][2]
    assert "--exclude='*/node_modules'" in kw["command"][2]
    assert "--exclude='./.next'" in kw["command"][2]
    assert " -C /workspace -cf /sandbox-output.tar ." in kw["command"][2]
    assert "cp -a /app/node_modules /workspace/node_modules" in kw["command"][2]
    assert "cp -a /seed/. /workspace/ 2>/dev/null || true" not in kw["command"][2]
    assert "ln -s" not in kw["command"][2]
    assert kw["working_dir"] == "/workspace"
    result_archive = workspace.parent / "sandbox-output" / "workspace.tar"
    assert kw["volumes"] == {
        str(workspace): {"bind": "/seed", "mode": "ro"},
        str(result_archive): {"bind": "/sandbox-output.tar", "mode": "rw"},
    }
    assert kw["read_only"] is True
    assert kw["tmpfs"] == {
        "/workspace": "rw,size=2147483648,mode=1777,uid=1000,gid=1000",
        "/tmp": "rw,size=268435456,mode=1777,uid=1000,gid=1000",
    }
    assert kw["runtime"] == "runsc"
    assert kw["security_opt"] == ["no-new-privileges:true"]
    assert kw["pids_limit"] == 64
    assert kw["network"] == "none"
    assert "extra_hosts" not in kw
    assert kw["restart_policy"] == {"Name": "no"}
    assert kw["cap_drop"] == ["ALL"]
    assert kw["user"] == "1000:1000"
    assert kw["init"] is True
    assert kw["environment"]["OMNIA_PROJECT_ID"] == "00000000-0000-0000-0000-000000000001"
    assert kw["environment"]["OMNIA_SANDBOX_COMMAND"] == "pnpm typecheck"
    assert set(kw["environment"]) == {
        "CI",
        "HOME",
        "NODE_ENV",
        "OMNIA_PROJECT_ID",
        "OMNIA_SANDBOX_COMMAND",
        "npm_config_cache",
    }
    assert kw["labels"]["omnia.kind"] == "sandbox"
    assert client.containers.container.wait_timeout == 12
    assert client.containers.container.removed_force is True
    assert (workspace / "src" / "result.ts").read_text(encoding="utf-8") == (
        "export const ok = true;\n"
    )
    assert not list(workspace.glob(".omnia-sandbox-seed-*"))


async def test_run_sandbox_command_fails_closed_when_seed_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")

    class _BootstrapFailureContainer:
        def __init__(self) -> None:
            self.archive_requested = False
            self.removed_force: bool | None = None

        def wait(self, timeout: int) -> dict[str, int]:
            return {"StatusCode": int(docker_client._SANDBOX_BOOTSTRAP_FAILURE_EXIT_CODE)}

        def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
            return b"sandbox bootstrap failed: seed mount unavailable"

        def get_archive(self, path: str) -> tuple[list[bytes], dict[str, object]]:
            self.archive_requested = True
            raise AssertionError("bootstrap failures must not collect or replace the workspace")

        def remove(self, force: bool = False) -> None:
            self.removed_force = force

    class _BootstrapFailureContainers:
        def __init__(self) -> None:
            self.container = _BootstrapFailureContainer()

        def run(self, *, image: str, **kwargs: Any) -> _BootstrapFailureContainer:
            return self.container

    containers = _BootstrapFailureContainers()
    client = type("C", (), {"containers": containers})()
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    with pytest.raises(OrchestratorError, match="sandbox seed bootstrap failed"):
        await docker_client.run_sandbox_command(
            image="omnia-template-max-miniapp-nextjs:dev",
            workspace_dir=workspace,
            project_id="00000000-0000-0000-0000-000000000001",
            cmd="true",
        )

    assert containers.container.archive_requested is False
    assert containers.container.removed_force is True
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not list(workspace.glob(".omnia-sandbox-seed-*"))
    assert not (workspace.parent / "sandbox-output").exists()


async def test_run_sandbox_command_fails_closed_when_workspace_export_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")

    class _ExportFailureContainer:
        def __init__(self) -> None:
            self.removed_force: bool | None = None

        def wait(self, timeout: int) -> dict[str, int]:
            return {"StatusCode": int(docker_client._SANDBOX_EXPORT_FAILURE_EXIT_CODE)}

        def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
            return b"sandbox export failed: workspace tar failed"

        def remove(self, force: bool = False) -> None:
            self.removed_force = force

    class _ExportFailureContainers:
        def __init__(self) -> None:
            self.container = _ExportFailureContainer()

        def run(self, *, image: str, **kwargs: Any) -> _ExportFailureContainer:
            return self.container

    containers = _ExportFailureContainers()
    client = type("C", (), {"containers": containers})()
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    with pytest.raises(OrchestratorError, match="sandbox result export failed"):
        await docker_client.run_sandbox_command(
            image="omnia-template-max-miniapp-nextjs:dev",
            workspace_dir=workspace,
            project_id="00000000-0000-0000-0000-000000000001",
            cmd="true",
        )

    assert containers.container.removed_force is True
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not list(workspace.glob(".omnia-sandbox-seed-*"))
    assert not (workspace.parent / "sandbox-output").exists()


async def test_run_sandbox_command_rejects_signal_exit_before_reading_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")

    class _SignalledContainer:
        def __init__(self) -> None:
            self.removed_force: bool | None = None

        def wait(self, timeout: int) -> dict[str, int]:
            return {"StatusCode": 137}

        def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
            return b""

        def remove(self, force: bool = False) -> None:
            self.removed_force = force

    class _SignalledContainers:
        def __init__(self) -> None:
            self.container = _SignalledContainer()

        def run(self, *, image: str, **kwargs: Any) -> _SignalledContainer:
            result_archive = workspace.parent / "sandbox-output" / "workspace.tar"
            result_archive.write_bytes(_tar_bytes({"workspace/pwned.txt": b"crafted"}))
            return self.container

    containers = _SignalledContainers()
    client = type("C", (), {"containers": containers})()
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    with pytest.raises(OrchestratorError, match="terminated before a trusted export"):
        await docker_client.run_sandbox_command(
            image="omnia-template-max-miniapp-nextjs:dev",
            workspace_dir=workspace,
            project_id="00000000-0000-0000-0000-000000000001",
            cmd="kill -9 $PPID",
        )

    assert containers.container.removed_force is True
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (workspace / "pwned.txt").exists()
    assert not (workspace.parent / "sandbox-output").exists()


def test_sandbox_archive_counts_directories_and_excluded_members(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = _tar_bytes(
        {},
        directories=[f"workspace/node_modules/pkg-{index}/" for index in range(10_001)],
    )

    with pytest.raises(OrchestratorError, match="too many members"):
        docker_client._replace_workspace_from_sandbox_archive(raw, workspace)

    assert workspace.is_dir()


def test_read_bounded_archive_rejects_oversized_stream() -> None:
    with pytest.raises(OrchestratorError, match="archive exceeds"):
        docker_client._read_bounded_archive(
            [b"1234", b"5678"], max_bytes=7, label="sandbox workspace"
        )


def test_sandbox_archive_rejects_traversal_without_replacing_workspace(
    tmp_path: Path,
) -> None:
    from omnia_orchestrator.core.errors import OrchestratorError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")
    raw = _tar_bytes({"workspace/../../escape.txt": b"escape"})

    with pytest.raises(OrchestratorError, match="traversal path") as exc:
        docker_client._replace_workspace_from_sandbox_archive(raw, workspace)

    assert exc.value.status_code == 400
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "escape.txt").exists()


def test_sandbox_archive_ignores_links_and_generated_trees(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = _tar_bytes(
        {
            "workspace/src/page.tsx": b"export default function Page() {}\n",
            "workspace/node_modules/pkg/index.js": b"vendored",
            "workspace/.next/cache/data": b"generated",
        },
        symlinks={"workspace/src/host-secret": "/etc/passwd"},
    )

    docker_client._replace_workspace_from_sandbox_archive(raw, workspace)

    assert (workspace / "src" / "page.tsx").is_file()
    assert not (workspace / "src" / "host-secret").exists()
    assert not (workspace / "node_modules").exists()
    assert not (workspace / ".next").exists()


async def test_start_container_default_creates_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default spec (network_name None) → shared runtime net, NO per-project
    network created — byte-identical to pre-Phase-1."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.start_container(_spec("omnia-template-x:dev"))

    assert client.networks.created == []


async def test_start_container_creates_per_project_network_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the spec names a per-project network, start_container ensures it
    exists (idempotent) and runs the container on it (isolation)."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    spec = ContainerSpec(
        name="omnia-dev-x",
        image="omnia-template-x:dev",
        port=3200,
        project_id="00000000-0000-0000-0000-000000000001",
        env={},
        network_name="omnia-proj-1",
    )
    await docker_client.start_container(spec)

    assert "omnia-proj-1" in client.networks.created
    assert client.containers.run_kwargs.get("network") == "omnia-proj-1"


async def test_start_container_recreates_legacy_runtime_for_new_sandbox_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _FakeContainer(
        "legacy-id",
        "omnia-template-max-miniapp-nextjs:dev",
        sandbox_profile="",
    )
    client = _FakeClient(legacy)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)
    spec = ContainerSpec(
        name="omnia-dev-max-app",
        image="omnia-template-max-miniapp-nextjs:dev",
        port=3200,
        project_id="00000000-0000-0000-0000-000000000001",
        env={},
        sandbox_profile="max-runtime-v1",
        recreate_on_profile_change=True,
    )

    result = await docker_client.start_container(spec)

    assert legacy.removed is True
    assert client.containers.run_called is True
    assert result == "new-container-id"
    assert client.containers.run_kwargs["labels"]["omnia.sandbox_profile"] == ("max-runtime-v1")


async def test_start_container_max_profile_has_db_only_project_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)
    project_id = "00000000-0000-0000-0000-000000000001"
    network_name = f"omnia-proj-{project_id}"
    spec = ContainerSpec(
        name="omnia-dev-max-app",
        image="omnia-template-max-miniapp-nextjs:dev",
        port=3200,
        project_id=project_id,
        env={"DATABASE_URL": "postgresql://scoped-role@omnia-postgres-users/app"},
        cpu_quota=1.0,
        memory_mb=4096,
        network_name=network_name,
        harden=True,
        pids_limit=64,
        sandbox_profile="max-runtime-v1",
        include_host_gateway=False,
        network_service_names=("omnia-postgres-users",),
    )

    await docker_client.start_container(spec)

    kwargs = client.containers.run_kwargs
    assert kwargs["network"] == network_name
    assert "extra_hosts" not in kwargs
    assert kwargs["labels"]["omnia.sandbox_profile"] == "max-runtime-v1"
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["pids_limit"] == 64
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["user"] == "1000:1000"
    assert client.networks.items[network_name].connections == [
        ("omnia-postgres-users", ("omnia-postgres-users",))
    ]
    assert client.networks.create_kwargs[network_name] == {
        "driver": "bridge",
        "internal": False,
    }


async def test_container_security_facts_attests_value_free_max_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "00000000-0000-0000-0000-000000000001"
    container = _FakeContainer(
        "max-id",
        "omnia-template-max-miniapp-nextjs:dev",
        sandbox_profile="max-runtime-v1",
    )
    container.attrs = {
        "Config": {
            "Image": "omnia-template-max-miniapp-nextjs:dev",
            "User": "1000:1000",
            "Env": [
                "DATABASE_URL=postgresql://project:secret@omnia-postgres-users/app",
                "AUTH_SECRET=per-project-secret",
            ],
            "Labels": {
                "omnia.project_id": project_id,
                "omnia.sandbox_profile": "max-runtime-v1",
            },
        },
        "HostConfig": {
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64,
            "Memory": 4 * 1024 * 1024,
            "CpuQuota": 100_000,
            "ExtraHosts": [],
            "Runtime": "runsc",
        },
        "NetworkSettings": {"Networks": {f"omnia-proj-{project_id}": {}}},
        "Mounts": [],
    }
    client = _FakeClient(container)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    facts = await docker_client.container_security_facts("omnia-dev-max", project_id)

    assert facts["ready"] is True
    assert facts["missing"] == []
    assert facts["profile"] == "max-runtime-v1"
    assert facts["runtime"] == "runsc"
    serialized = repr(facts)
    assert "per-project-secret" not in serialized
    assert "postgresql://" not in serialized


async def test_container_security_facts_fails_closed_on_shared_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "00000000-0000-0000-0000-000000000001"
    container = _FakeContainer("legacy-id", "image")
    container.attrs = {
        "Config": {
            "User": "1000:1000",
            "Env": ["MINIO_SECRET_KEY=shared-secret"],
            "Labels": {
                "omnia.project_id": project_id,
                "omnia.sandbox_profile": "max-runtime-v1",
            },
        },
        "HostConfig": {
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64,
            "Memory": 1024,
            "CpuQuota": 100_000,
            "ExtraHosts": ["host.docker.internal:host-gateway"],
        },
        "NetworkSettings": {
            "Networks": {
                f"omnia-proj-{project_id}": {},
                "omnia-runtime_default": {},
            }
        },
        "Mounts": [{"Source": "/var/run/docker.sock"}],
    }
    client = _FakeClient(container)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    facts = await docker_client.container_security_facts("omnia-dev-max", project_id)

    assert facts["ready"] is False
    assert set(facts["missing"]) >= {
        "project_network",
        "host_gateway_absent",
        "platform_secrets_absent",
        "docker_socket_absent",
    }
    assert "shared-secret" not in repr(facts)


# ── _wake_if_stopped (wake-on-agent-op; 2026-07-08 hibernate-mid-build fix) ──


class _WakeFakeContainer:
    """Container double for the wake helper: status transitions on start/unpause."""

    def __init__(self, status: str = "exited", *, fail_start: bool = False) -> None:
        self.status = status
        self._fail_start = fail_start
        self.start_calls = 0
        self.unpause_calls = 0

    def reload(self) -> None:  # status is already current on the fake
        pass

    def start(self) -> None:
        self.start_calls += 1
        if self._fail_start:
            raise docker.errors.APIError("boom")
        self.status = "running"

    def unpause(self) -> None:
        self.unpause_calls += 1
        self.status = "running"


def test_wake_if_stopped_starts_exited_container() -> None:
    c = _WakeFakeContainer(status="exited")
    docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert c.start_calls == 1
    assert c.status == "running"


def test_wake_if_stopped_unpauses_paused_container() -> None:
    c = _WakeFakeContainer(status="paused")
    docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert c.unpause_calls == 1
    assert c.status == "running"


def test_wake_if_stopped_running_is_noop() -> None:
    c = _WakeFakeContainer(status="running")
    docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert c.start_calls == 0
    assert c.unpause_calls == 0


def test_wake_if_stopped_raises_structured_409_when_wake_fails() -> None:
    from omnia_orchestrator.core.errors import OrchestratorError

    c = _WakeFakeContainer(status="exited", fail_start=True)
    with pytest.raises(OrchestratorError) as ei:
        docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert ei.value.code == "container_not_running"
    assert ei.value.status_code == 409


# ── template image freshness (2026-07-09: template edits must reach the build) ──


def test_newest_source_mtime_ignores_vendored_and_artifacts(tmp_path) -> None:
    import os

    (tmp_path / "src" / "app").mkdir(parents=True)
    real = tmp_path / "src" / "app" / "globals.css"
    real.write_text("body{}")
    os.utime(real, (1000, 1000))  # old real source file

    # vendored + build artifacts with a MUCH newer mtime — must be ignored, else
    # the staleness check would rebuild the image on every provision.
    (tmp_path / "node_modules").mkdir()
    nm = tmp_path / "node_modules" / "big.js"
    nm.write_text("x")
    os.utime(nm, (9_000_000, 9_000_000))
    tsb = tmp_path / "tsconfig.tsbuildinfo"
    tsb.write_text("{}")
    os.utime(tsb, (9_000_000, 9_000_000))
    nenv = tmp_path / "next-env.d.ts"
    nenv.write_text("// gen")
    os.utime(nenv, (9_000_000, 9_000_000))

    newest = docker_client._newest_source_mtime(tmp_path)
    assert newest == 1000.0  # only the real source file counted


def test_newest_source_mtime_tracks_a_real_edit(tmp_path) -> None:
    import os

    f = tmp_path / "src" / "layout.tsx"
    f.parent.mkdir(parents=True)
    f.write_text("export default function L(){}")
    os.utime(f, (5000, 5000))
    assert docker_client._newest_source_mtime(tmp_path) == 5000.0


async def test_template_build_uses_writable_docker_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Buildx must not write under the read-only systemd `$HOME`.

    A brand-new stack has no cached image, so this path is the difference
    between a provisioned container and a silent `container not found`.
    """
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    (template_dir / "Dockerfile.dev").write_text("FROM scratch\n")
    docker_config_dir = tmp_path / "docker-cli"
    monkeypatch.setenv("DOCKER_CLI_CONFIG_DIR", str(docker_config_dir))

    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(docker_client, "_image_created_epoch", lambda _tag: None)
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(docker_client.subprocess, "run", fake_run)

    rebuilt = await docker_client.ensure_template_image_fresh(
        template_dir, "omnia-template-new:dev"
    )

    assert rebuilt is True
    assert docker_config_dir.is_dir()
    assert captured["env"]["DOCKER_CONFIG"] == str(docker_config_dir)
    assert captured["argv"][:2] == ["docker", "build"]
