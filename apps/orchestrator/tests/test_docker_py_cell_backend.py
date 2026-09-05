from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tarfile
import threading
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import docker  # type: ignore[import-untyped]
import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.docker_cell_resources import DockerContainerSpec
from omnia_orchestrator.services.docker_py_cell_backend import (
    _WORKSPACE_SOURCE_ARCHIVE_EXCLUDES,
    DockerPyCellBackend,
)


def test_source_archive_ignores_compiler_outputs_but_keeps_application_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    entries = {
        "tsconfig.tsbuildinfo": "incremental compiler cache",
        "next-env.d.ts": "generated Next declarations",
        "nested/build.tsbuildinfo": "nested cache",
        "nested/next-env.d.ts": "nested generated declarations",
        "src/app/page.tsx": "export default function Page() {}",
        "src/env.d.ts": "declare const application: string",
    }
    for path, content in entries.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    archive_path = tmp_path / "source.tar"
    subprocess.run([
        "tar", "-C", str(root),
        *(f"--exclude={item}" for item in _WORKSPACE_SOURCE_ARCHIVE_EXCLUDES),
        "-cf", str(archive_path), ".",
    ], check=True, env={**os.environ, "COPYFILE_DISABLE": "1"})
    with tarfile.open(archive_path) as archive:
        names = {member.name.removeprefix("./") for member in archive if member.isfile()}
    # Next declarations must remain available in a cold restored workspace.
    assert names == {
        "src/app/page.tsx", "src/env.d.ts", "next-env.d.ts", "nested/next-env.d.ts",
    }


def _labels(kind: str) -> dict[str, str]:
    return {
        "omnia.managed": "true",
        "omnia.project_cell": "true",
        "omnia.workspace_id": "00000000-0000-0000-0000-000000000001",
        "omnia.project_id": "00000000-0000-0000-0000-000000000002",
        "omnia.owner_id": "00000000-0000-0000-0000-000000000003",
        "omnia.provider": "docker_owner_canary",
        "omnia.profile_version": "docker-owner-cell-resources-v1",
        "omnia.resource_kind": kind,
    }


def _archive(files: dict[str, bytes], *, root: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, payload in sorted(files.items()):
            info = tarfile.TarInfo(name=f"{root}/{path}")
            info.size = len(payload)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _extract(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            if member.isfile() is False:
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            files[PurePosixPath(member.name).as_posix()] = extracted.read()
    return files


def _sync_preserves_top_level(path: str) -> bool:
    top = PurePosixPath(path).parts[0]
    return top in {
        "node_modules",
        ".next",
        ".pnpm-store",
        ".git",
        "dist",
        "build",
        ".venv",
        "vendor",
        "__pycache__",
        ".env",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    } or top.startswith(".env.")


def _sync_excludes_path(path: str, script: str) -> bool:
    def _has_exclude(pattern: str) -> bool:
        return f"--exclude={pattern}" in script or f"--exclude='{pattern}'" in script

    pure = PurePosixPath(path)
    name = pure.name
    skipped_directories = {
        "node_modules",
        ".next",
        ".pnpm-store",
        ".git",
        "dist",
        "build",
        ".venv",
        "vendor",
        "__pycache__",
    }
    skipped = next((part for part in pure.parts if part in skipped_directories), None)
    if skipped is not None:
        return _has_exclude(f"./{skipped}") or _has_exclude(f"*/{skipped}")
    if name == ".env" or name.startswith(".env."):
        return _has_exclude("./.env") and _has_exclude("*/.env.*")
    if name in {"secrets.json", "secrets.yaml", "secrets.yml"}:
        return _has_exclude(f"./{name}") and _has_exclude(f"*/{name}")
    return False


class _FakeExecResult:
    def __init__(self, exit_code: int = 0, output: bytes = b"") -> None:
        self.exit_code = exit_code
        self.output = output


class _FakeVolume:
    def __init__(
        self,
        name: str,
        labels: dict[str, str],
        *,
        files: dict[str, bytes] | None = None,
    ) -> None:
        self.id = f"vol-{name}"
        self.name = name
        self.files = dict(files or {})
        self.attrs = {"Labels": dict(labels)}
        self.removed = False

    def remove(self, force: bool = False) -> None:
        _ = force
        self.removed = True


class _FakeVolumes:
    def __init__(self) -> None:
        self.items: dict[str, _FakeVolume] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any] | None] = []

    def get(self, name: str) -> _FakeVolume:
        if name not in self.items:
            raise docker.errors.NotFound(name)
        return self.items[name]

    def create(self, *, name: str, driver: str, labels: dict[str, str]) -> _FakeVolume:
        self.create_calls.append({"name": name, "driver": driver, "labels": dict(labels)})
        volume = _FakeVolume(name, labels)
        self.items[name] = volume
        return volume

    def list(self, *, filters: dict[str, str] | None = None) -> list[_FakeVolume]:
        self.list_calls.append(filters)
        if not filters or "label" not in filters:
            return list(self.items.values())
        key, expected = str(filters["label"]).split("=", 1)
        return [item for item in self.items.values() if item.attrs["Labels"].get(key) == expected]


class _FakeNetwork:
    def __init__(self, name: str, labels: dict[str, str], *, internal: bool) -> None:
        self.id = f"net-{name}"
        self.name = name
        self.attrs: dict[str, Any] = {"Labels": dict(labels), "Internal": internal}
        self.removed = False
        self.connections: list[str] = []
        self.event_log: list[str] | None = None

    def remove(self) -> None:
        self.removed = True

    def connect(self, container: Any) -> None:
        self.connections.append(str(getattr(container, "name", "")))
        event_log = getattr(self, "event_log", None)
        if isinstance(event_log, list):
            event_log.append(f"connect:{getattr(container, 'name', '')}")


class _FakeNetworks:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.items: dict[str, _FakeNetwork] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any] | None] = []

    def get(self, name: str) -> _FakeNetwork:
        if name not in self.items:
            raise docker.errors.NotFound(name)
        return self.items[name]

    def create(
        self,
        name: str,
        *,
        driver: str,
        check_duplicate: bool,
        internal: bool,
        labels: dict[str, str],
    ) -> _FakeNetwork:
        self.create_calls.append(
            {
                "name": name,
                "driver": driver,
                "check_duplicate": check_duplicate,
                "internal": internal,
                "labels": dict(labels),
            }
        )
        network = _FakeNetwork(name, labels, internal=internal)
        network.event_log = self.client.events
        self.items[name] = network
        return network

    def list(self, *, filters: dict[str, str] | None = None) -> list[_FakeNetwork]:
        self.list_calls.append(filters)
        if not filters or "label" not in filters:
            return list(self.items.values())
        key, expected = str(filters["label"]).split("=", 1)
        return [
            item
            for item in self.items.values()
            if cast(dict[str, str], item.attrs["Labels"]).get(key) == expected
        ]


class _FakeContainer:
    def __init__(
        self,
        manager: _FakeContainers,
        image: str,
        command: list[str] | None,
        kwargs: dict[str, Any],
    ) -> None:
        self.manager = manager
        self.id = f"ctr-{len(manager.items) + 1}"
        self.name = str(kwargs["name"])
        self.image = image
        self.command = command
        self.kwargs = kwargs
        self.labels = dict(kwargs["labels"])
        self.status = "created"
        self.wait_calls: list[int] = []
        self.stop_calls: list[int] = []
        self.remove_calls: list[bool] = []
        self.exec_calls: list[dict[str, Any]] = []
        self.get_archive_calls: list[str] = []
        self.put_archive_calls: list[tuple[str, bytes]] = []
        self.logs_output = b""
        self.temp_files: dict[str, bytes] = {}
        self.attrs = {
            "Config": {
                "Image": image,
                "Labels": dict(self.labels),
                "User": kwargs.get("user", ""),
                "Env": [
                    f"{key}={value}"
                    for key, value in (kwargs.get("environment") or {}).items()
                ],
            },
            "HostConfig": {
                "CapAdd": list(kwargs.get("cap_add") or []),
                "CapDrop": list(kwargs.get("cap_drop") or []),
                "ReadonlyRootfs": bool(kwargs.get("read_only", False)),
                "Privileged": bool(kwargs.get("privileged", False)),
                "SecurityOpt": list(kwargs.get("security_opt") or []),
                "PortBindings": dict(kwargs.get("ports") or {}),
                "Tmpfs": dict(kwargs.get("tmpfs") or {}),
                "PidsLimit": int(kwargs.get("pids_limit") or 0),
                "Memory": int(kwargs.get("mem_limit") or 0),
                "CpuQuota": int(kwargs.get("cpu_quota") or 0),
                "CpuPeriod": int(kwargs.get("cpu_period") or 0),
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": volume_name,
                    "Destination": mount["bind"],
                }
                for volume_name, mount in (kwargs.get("volumes") or {}).items()
            ],
            "NetworkSettings": {
                "Networks": ({str(kwargs["network"]): {}} if kwargs.get("network") else {})
            },
        }

    def reload(self) -> None:
        return

    def start(self) -> None:
        self.status = "running"
        self.manager.client.events.append(f"start:{self.name}")

    def unpause(self) -> None:
        self.status = "running"

    def wait(self, timeout: int) -> dict[str, int]:
        self.wait_calls.append(timeout)
        exit_code = self._simulate_workspace_command_status()
        self.status = "exited"
        return {"StatusCode": exit_code}

    def stop(self, timeout: int) -> None:
        self.stop_calls.append(timeout)
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.remove_calls.append(force)
        self.manager.items.pop(self.name, None)
        self.status = "removed"

    def logs(self, tail: int = 50) -> bytes:
        _ = tail
        return self.logs_output

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, object]]:
        self.get_archive_calls.append(path)
        if path == "/volume":
            mounted_name = next(iter((self.kwargs.get("volumes") or {}).keys()))
            payload = _archive(self.manager.client.volumes.items[mounted_name].files, root="volume")
            return [payload], {}
        payload = self.temp_files.get(path, b"")
        return [_archive({PurePosixPath(path).name: payload}, root=PurePosixPath(path).name)], {}

    def put_archive(self, *, path: str, data: bytes) -> bool:
        self.put_archive_calls.append((path, data))
        extracted = _extract(data)
        if path == "/volume":
            mounted_name = next(iter((self.kwargs.get("volumes") or {}).keys()))
            volume = self.manager.client.volumes.items[mounted_name]
            for raw_path, payload in extracted.items():
                volume.files[PurePosixPath(raw_path).as_posix()] = payload
            return True
        for raw_path, payload in extracted.items():
            self.temp_files[f"{path}/{PurePosixPath(raw_path).name}"] = payload
        return True

    def exec_run(
        self,
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        demux: bool = False,
    ) -> _FakeExecResult:
        self.exec_calls.append(
            {"command": list(command), "environment": environment, "demux": demux}
        )
        if command[:2] == ["pg_dump", "-Fc"]:
            return _FakeExecResult(output=b"pg-dump-bytes")
        if command[:1] == ["pg_restore"]:
            return _FakeExecResult()
        if command[:1] == ["psql"]:
            return _FakeExecResult(output=b"1\n")
        if command[:2] == ["rm", "-f"]:
            for path in command[2:]:
                self.temp_files.pop(path, None)
            return _FakeExecResult()
        if command[:3] == ["tar", "-C", "/volume"]:
            mounted_name = next(iter((self.kwargs.get("volumes") or {}).keys()))
            volume = self.manager.client.volumes.items[mounted_name]
            script = " ".join(command)
            source_files = {
                path: payload
                for path, payload in volume.files.items()
                if not _sync_excludes_path(path, script)
            }
            archive_path = command[command.index("-cf") + 1]
            self.temp_files[archive_path] = _archive(source_files, root=".")
            return _FakeExecResult()
        if command[:3] == ["stat", "-c", "%s"]:
            payload = self.temp_files.get(command[3], b"")
            return _FakeExecResult(output=f"{len(payload)}\n".encode())
        if command[:1] == ["cat"]:
            return _FakeExecResult(output=self.temp_files.get(command[1], b""))
        if command[:3] != ["sh", "-eu", "-c"]:
            return _FakeExecResult()
        script = command[3]
        mounted_name = next(iter((self.kwargs.get("volumes") or {}).keys()))
        volume = self.manager.client.volumes.items[mounted_name]
        if script.startswith("cd /volume && rm -rf -- "):
            raw_paths = script.removeprefix("cd /volume && rm -rf -- ").split(" ")
            for raw_path in raw_paths:
                path = raw_path.strip("'")
                volume.files.pop(path, None)
            return _FakeExecResult()
        if "mv --" in script:
            if "test -e " in script:
                stage = script.split("test -e ", 1)[1].split(" && ", 1)[0].strip("'")
                has_stage = any(
                    key == stage or key.startswith(f"{stage}/") for key in volume.files
                )
                if not has_stage:
                    return _FakeExecResult(exit_code=1, output=b"stage missing")
            if "test ! -e " in script:
                final_probe = script.split("test ! -e ", 1)[1].split(" && ", 1)[0].strip("'")
                has_final = any(
                    key == final_probe or key.startswith(f"{final_probe}/")
                    for key in volume.files
                )
                if has_final:
                    return _FakeExecResult(exit_code=1, output=b"final exists")
            stage = script.split("mv -- ", 1)[1].split(" ", 1)[0].strip("'")
            final = script.rsplit(" ", 1)[-1].strip("'")
            moved: dict[str, bytes] = {}
            for key, payload in list(volume.files.items()):
                if key == stage:
                    moved[final] = payload
                    del volume.files[key]
                    continue
                prefix = f"{stage}/"
                if key.startswith(prefix):
                    moved[f"{final}/{key.removeprefix(prefix)}"] = payload
                    del volume.files[key]
            volume.files.update(moved)
            return _FakeExecResult()
        if script.startswith("find /volume -mindepth 1"):
            volume.files.clear()
            return _FakeExecResult()
        return _FakeExecResult()

    def _simulate_workspace_command_status(self) -> int:
        if self.command is None or self.command[:3] != ["sh", "-eu", "-c"]:
            return 0
        script = self.command[3]
        environment = self.kwargs.get("environment") or {}
        command = str(environment.get("OMNIA_CELL_CMD", ""))
        mounted = self.kwargs.get("volumes") or {}
        if mounted:
            mounted_name = next(iter(mounted.keys()))
            volume = self.manager.client.volumes.items[mounted_name]
            self._simulate_workspace_sync(volume, script, command)
        if command == "false":
            return 1 if 'sh -lc "$OMNIA_CELL_CMD" || status=$?' in script else 0
        if command == "exit 7":
            return 7 if 'sh -lc "$OMNIA_CELL_CMD" || status=$?' in script else 0
        return 0

    def _simulate_workspace_sync(
        self,
        volume: _FakeVolume,
        script: str,
        command: str,
    ) -> None:
        if "sync_back" not in script:
            return
        run_root_files = dict(volume.files)
        if command == "simulate-sync":
            run_root_files.pop("stale.txt", None)
            run_root_files["app.txt"] = b"after"
            run_root_files["nested/.env.local"] = b"secret"
        source_files = dict(volume.files)
        if "find /workspace-src -mindepth 1 -exec rm -rf -- {} +" in script:
            source_files.clear()
        else:
            source_files = {
                path: payload
                for path, payload in source_files.items()
                if _sync_preserves_top_level(path)
            }
        for path, payload in run_root_files.items():
            if _sync_excludes_path(path, script):
                continue
            source_files[path] = payload
        volume.files = source_files


class _FakeContainers:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.items: dict[str, _FakeContainer] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def get(self, name: str) -> _FakeContainer:
        if name not in self.items:
            raise docker.errors.NotFound(name)
        return self.items[name]

    def create(self, image: str, command: list[str] | None = None, **kwargs: Any) -> _FakeContainer:
        self.create_calls.append({"image": image, "command": command, "kwargs": dict(kwargs)})
        container = _FakeContainer(self, image, command, kwargs)
        self.items[container.name] = container
        return container

    def list(self, *, all: bool, filters: dict[str, str] | None = None) -> list[_FakeContainer]:
        _ = all
        self.list_calls.append({"all": all, "filters": filters})
        if not filters or "label" not in filters:
            return list(self.items.values())
        key, expected = str(filters["label"]).split("=", 1)
        return [item for item in self.items.values() if item.labels.get(key) == expected]


class _FakeClient:
    def __init__(self) -> None:
        self.api = type("API", (), {"base_url": "unix:///var/run/docker.sock"})()
        self.ping_calls = 0
        self.events: list[str] = []
        self.volumes = _FakeVolumes()
        self.networks = _FakeNetworks(self)
        self.containers = _FakeContainers(self)

    def ping(self) -> None:
        self.ping_calls += 1

    def info(self) -> dict[str, object]:
        return {
            "ID": "daemon-id",
            "DockerRootDir": "/var/lib/docker",
            "Name": "daemon",
            "OperatingSystem": "Linux",
        }


def _backend(client: _FakeClient) -> DockerPyCellBackend:
    return DockerPyCellBackend(
        docker_host="unix:///var/run/docker.sock",
        helper_image="alpine@sha256:" + "9" * 64,
        client_factory=lambda _host: client,
    )


def _docker_api_error(status_code: int, explanation: str) -> docker.errors.APIError:
    response = SimpleNamespace(
        status_code=status_code,
        url="http+docker://localhost/v1.51/containers/helper",
        reason="Conflict" if status_code == 409 else "Not Found",
    )
    return docker.errors.APIError(
        explanation,
        response=response,
        explanation=explanation,
    )


@pytest.mark.asyncio
async def test_volume_inspection_reads_only_metadata() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol", _labels("workspace"), files={"large.db": b"private"}
    )
    record = await _backend(client).get_volume("workspace-vol")
    assert record is not None
    assert record.name == "workspace-vol"
    assert record.labels == _labels("workspace")
    assert record.files == {}
    assert client.containers.create_calls == []
    assert await _backend(client).get_volume("missing") is None


@pytest.mark.asyncio
async def test_parallel_volume_readers_have_independent_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol", _labels("workspace"), files={"proof.txt": b"retained"}
    )
    readers = 6
    barrier = threading.Barrier(readers)
    create_lock = threading.Lock()
    original_create = client.containers.create
    original_archive = _FakeContainer.get_archive

    def create(image: str, command: list[str] | None = None, **kwargs: Any) -> _FakeContainer:
        with create_lock:
            if kwargs["name"] in client.containers.items:
                raise _docker_api_error(409, "The container name is already in use")
            return original_create(image, command, **kwargs)

    def read(container: _FakeContainer, path: str) -> tuple[list[bytes], dict[str, object]]:
        barrier.wait(timeout=5)
        assert container.status == "running"
        return original_archive(container, path)

    monkeypatch.setattr(client.containers, "create", create)
    monkeypatch.setattr(_FakeContainer, "get_archive", read)
    # Separate backend objects also model requests handled by different workers.
    results = await asyncio.gather(
        *(_backend(client).read_volume_files("workspace-vol") for _ in range(readers))
    )
    assert results == [{"proof.txt": b"retained"}] * readers
    names = [call["kwargs"]["name"] for call in client.containers.create_calls]
    assert len(set(names)) == readers
    assert client.containers.items == {}


@pytest.mark.asyncio
async def test_volume_read_does_not_remove_another_requests_helper() -> None:
    client = _FakeClient()
    backend = _backend(client)
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    existing = client.containers.create(
        backend.helper_image,
        name="workspace-vol-volume-read",
        labels=backend._helper_labels(_labels("workspace"), "volume-read"),
    )
    existing.start()
    await backend.read_volume_files("workspace-vol")
    assert existing.status == "running"
    assert existing.remove_calls == []
    assert list(client.containers.items.values()) == [existing]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["start", "reload"])
async def test_volume_helper_startup_failure_cleans_only_its_container(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))

    def fail(_container: _FakeContainer) -> None:
        raise _docker_api_error(500, "startup failed")

    monkeypatch.setattr(_FakeContainer, stage, fail)
    with pytest.raises(docker.errors.APIError, match="startup failed"):
        await _backend(client).read_volume_files("workspace-vol")
    assert client.containers.items == {}
    assert client.volumes.items["workspace-vol"].removed is False


@pytest.mark.asyncio
async def test_helper_name_conflict_retries_allocation_without_replaying_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    original_create = client.containers.create
    attempted_names: list[str] = []

    def create(image: str, command: list[str] | None = None, **kwargs: Any) -> _FakeContainer:
        attempted_names.append(kwargs["name"])
        if len(attempted_names) < 3:
            raise _docker_api_error(409, "The container name is already in use")
        return original_create(image, command, **kwargs)

    monkeypatch.setattr(client.containers, "create", create)
    await _backend(client).write_volume_files("workspace-vol", {"proof.txt": b"once"})
    assert len(set(attempted_names)) == 3
    assert len(client.containers.create_calls) == 1
    assert client.volumes.items["workspace-vol"].files == {"proof.txt": b"once"}
    assert client.containers.items == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "detail", "expected_attempts"),
    [
        (409, "The container name is already in use", 3),
        (409, "container is paused by another operation", 1),
        (500, "daemon unavailable", 1),
    ],
)
async def test_helper_allocation_retry_is_bounded_and_specific(
    monkeypatch: pytest.MonkeyPatch, status: int, detail: str, expected_attempts: int,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    attempts = 0

    def fail(*_args: Any, **_kwargs: Any) -> _FakeContainer:
        nonlocal attempts
        attempts += 1
        raise _docker_api_error(status, detail)

    monkeypatch.setattr(client.containers, "create", fail)
    with pytest.raises(docker.errors.APIError, match=detail):
        await _backend(client).read_volume_files("workspace-vol")
    assert attempts == expected_attempts
    assert client.containers.items == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["create", "start", "reload"])
async def test_cancelled_helper_startup_waits_for_late_container_cleanup(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    entered = threading.Event()
    release = threading.Event()
    target = client.containers if stage == "create" else _FakeContainer
    original = getattr(target, stage)

    def delayed(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(target, stage, delayed)
    task = asyncio.create_task(_backend(client).read_volume_files("workspace-vol"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # Repeated disconnect/shutdown cancellation must not orphan it.
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert len(client.containers.create_calls) == 1
    assert client.containers.items == {}
    assert client.volumes.items["workspace-vol"].removed is False


@pytest.mark.asyncio
async def test_failed_volume_read_removes_helper_and_keeps_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol", _labels("workspace"), files={"proof.txt": b"keep"}
    )

    def fail(_container: _FakeContainer, _path: str) -> Any:
        raise _docker_api_error(500, "archive failed")

    monkeypatch.setattr(_FakeContainer, "get_archive", fail)
    with pytest.raises(docker.errors.APIError, match="archive failed"):
        await _backend(client).read_volume_files("workspace-vol")
    assert client.containers.items == {}
    assert client.volumes.items["workspace-vol"].files == {"proof.txt": b"keep"}


@pytest.mark.asyncio
async def test_helper_container_removal_is_idempotent_during_concurrent_cleanup() -> None:
    backend = _backend(_FakeClient())
    barrier = threading.Barrier(2)
    attempt_lock = threading.Lock()
    attempts = 0

    class ConcurrentlyRemovedContainer:
        def remove(self, *, force: bool) -> None:
            nonlocal attempts
            assert force is True
            with attempt_lock:
                attempts += 1
                attempt = attempts
            barrier.wait(timeout=2)
            if attempt == 2:
                raise _docker_api_error(
                    409,
                    "removal of container helper is already in progress",
                )

    removing = ConcurrentlyRemovedContainer()
    await asyncio.gather(
        backend._remove_container_object(removing),
        backend._remove_container_object(removing),
    )

    class MissingContainer:
        def remove(self, *, force: bool) -> None:
            assert force is True
            raise docker.errors.NotFound("helper is already gone")

    await backend._remove_container_object(MissingContainer())

    class ConflictedContainer:
        def remove(self, *, force: bool) -> None:
            assert force is True
            raise _docker_api_error(409, "container is paused by another operation")

    with pytest.raises(docker.errors.APIError, match="paused by another operation"):
        await backend._remove_container_object(ConflictedContainer())


@pytest.mark.asyncio
async def test_create_container_uses_fail_closed_kwargs_for_postgres_init() -> None:
    client = _FakeClient()
    client.volumes.items["pg-vol"] = _FakeVolume("pg-vol", _labels("postgres"))
    client.volumes.items["secret-vol"] = _FakeVolume("secret-vol", _labels("secret-staging"))
    backend = _backend(client)

    record = await backend.create_container(
        DockerContainerSpec(
            name="omnia-cell-test-init",
            image="postgres@sha256:" + "1" * 64,
            labels=_labels("postgres-init"),
            user="postgres",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=("pg-vol", "secret-vol"),
            mounts=("/run/secrets/postgres-password.txt",),
            network_names=(),
            helper=True,
            pids_limit=32,
            memory_limit_bytes=123_456_789,
            cpu_quota=0.5,
        )
    )

    create_call = client.containers.create_calls[0]
    kwargs = create_call["kwargs"]

    assert create_call["image"] == "postgres@sha256:" + "1" * 64
    assert kwargs["network"] == "none"
    assert kwargs["privileged"] is False
    assert kwargs["read_only"] is True
    assert kwargs["cap_add"] == []
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["ports"] == {}
    assert kwargs["environment"] == {"PGDATA": "/var/lib/postgresql/PGDATA"}
    assert kwargs["volumes"] == {
        "pg-vol": {"bind": "/var/lib/postgresql", "mode": "rw"},
        "secret-vol": {"bind": "/run/secrets", "mode": "ro"},
    }
    assert kwargs["tmpfs"]["/var/run/postgresql"].startswith("rw,")
    assert kwargs["cpu_period"] == 100_000
    assert kwargs["cpu_quota"] == 50_000
    init_command = " ".join(create_call["command"] or [])
    assert "initdb" in init_command
    assert "host all all samenet scram-sha-256" in init_command
    assert "grep -Fqx" in init_command
    assert "trust" not in init_command
    assert record.state == "exited"
    container = client.containers.items["omnia-cell-test-init"]
    assert container.wait_calls == [30]


@pytest.mark.asyncio
async def test_create_container_uses_loopback_bind_and_work_copy_for_draft_runtime() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    client.volumes.items["agent-home-vol"] = _FakeVolume("agent-home-vol", _labels("agent-home"))
    client.networks.items["omnia-cell-test-internal"] = _FakeNetwork(
        "omnia-cell-test-internal",
        _labels("internal-network"),
        internal=True,
    )
    backend = _backend(client)

    record = await backend.create_container(
        DockerContainerSpec(
            name="omnia-cell-test-draft",
            image="omnia-template-max-miniapp-nextjs:dev",
            labels=_labels("draft-runtime"),
            user="0:0",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={"3000/tcp": "127.0.0.1:34567"},
            env={
                "HOME": "/root",
                "CI": "1",
                "NODE_ENV": "development",
                "HOSTNAME": "0.0.0.0",
                "PORT": "3000",
                "OMNIA_PROJECT_ID": "00000000-0000-0000-0000-000000000002",
                "OMNIA_DRAFT_ENV_FILE": "/root/.omnia/draft-env.sh",
                "COREPACK_HOME": "/home/node/.cache/node/corepack",
                "COREPACK_ENABLE_NETWORK": "0",
            },
            volumes=("workspace-vol", "agent-home-vol"),
            mounts=(),
            network_names=("omnia-cell-test-internal",),
            helper=False,
            tmpfs=("/tmp", "/run", "/work"),
            pids_limit=128,
            memory_limit_bytes=512 * 1024 * 1024,
            cpu_quota=0.75,
        )
    )

    create_call = client.containers.create_calls[0]
    kwargs = create_call["kwargs"]
    command = create_call["command"][3]

    assert kwargs["network"] == "omnia-cell-test-internal"
    assert kwargs["ports"] == {"3000/tcp": ("127.0.0.1", 34567)}
    assert kwargs["volumes"] == {
        "workspace-vol": {"bind": "/workspace-src", "mode": "rw"},
        "agent-home-vol": {"bind": "/root", "mode": "rw"},
    }
    assert "/work" in kwargs["tmpfs"]
    assert '. "$env_file"' in command
    assert "node /app/node_modules/next/dist/bin/next dev " in command
    assert "--port 3000 --hostname 0.0.0.0 &" in command
    assert "--turbopack" not in command
    assert "pnpm dev &" not in command
    assert "cp -a /workspace-src/. /work/" in command
    assert "cp /work/pnpm-lock.yaml /workspace-src/pnpm-lock.yaml" in command
    assert record.ports == {"3000/tcp": "127.0.0.1:34567"}


@pytest.mark.asyncio
async def test_volume_helpers_use_named_volume_and_cleanup_temp_container() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol",
        _labels("workspace"),
        files={"stage/proof.txt": b"accepted", "stale.txt": b"old"},
    )
    backend = _backend(client)

    read_back = await backend.read_volume_files("workspace-vol")
    await backend.write_volume_files("workspace-vol", {"nested/new.txt": b"new"})
    await backend.delete_volume_paths("workspace-vol", ("stale.txt",))
    await backend.promote_volume_directory("workspace-vol", "stage", "accepted")
    await backend.clear_volume("workspace-vol")

    helper_calls = client.containers.create_calls

    assert read_back == {"stage/proof.txt": b"accepted", "stale.txt": b"old"}
    assert helper_calls[0]["kwargs"]["volumes"] == {
        "workspace-vol": {"bind": "/volume", "mode": "ro"}
    }
    assert helper_calls[0]["kwargs"]["network"] == "none"
    assert all(call["kwargs"]["auto_remove"] is True for call in helper_calls)
    assert all(call["command"] == ["sleep", "300"] for call in helper_calls)
    assert helper_calls[0]["kwargs"]["labels"]["omnia.resource_kind"] == "volume-read"
    assert helper_calls[1]["kwargs"]["labels"]["omnia.resource_kind"] == "volume-write"
    assert helper_calls[2]["kwargs"]["labels"]["omnia.resource_kind"] == "volume-delete"
    assert helper_calls[3]["kwargs"]["labels"]["omnia.resource_kind"] == "volume-promote"
    assert helper_calls[4]["kwargs"]["labels"]["omnia.resource_kind"] == "volume-clear"
    assert client.containers.items == {}
    assert client.volumes.items["workspace-vol"].files == {}


@pytest.mark.asyncio
async def test_workspace_source_read_excludes_runtime_caches_and_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol",
        _labels("workspace"),
        files={
            "package.json": b"{}",
            "src/app/page.tsx": b"export default 1",
            "node_modules/pkg/index.js": b"vendored",
            ".next/cache/data": b"generated",
            ".pnpm-store/v3/files/pkg": b"cached",
            "nested/node_modules/pkg/index.js": b"nested vendored",
            ".env.local": b"secret",
            "nested/secrets.json": b"secret",
        },
    )
    backend = _backend(client)
    commands: list[list[str]] = []
    original_exec_run = _FakeContainer.exec_run

    def capture_exec(
        container: _FakeContainer,
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        demux: bool = False,
    ) -> _FakeExecResult:
        commands.append(command)
        return original_exec_run(
            container,
            command,
            environment=environment,
            demux=demux,
        )

    monkeypatch.setattr(_FakeContainer, "exec_run", capture_exec)

    files = await backend.read_workspace_source_files("workspace-vol")

    assert files == {
        "package.json": b"{}",
        "src/app/page.tsx": b"export default 1",
    }
    helper = client.containers.create_calls[0]
    command = commands[0]
    assert "--exclude=./node_modules" in command
    assert "--exclude=*/node_modules" in command
    assert "--exclude=./.next" in command
    assert "--exclude=./.pnpm-store" in command
    assert helper["kwargs"]["labels"]["omnia.resource_kind"] == "workspace-source-read"
    assert client.containers.items == {}


@pytest.mark.asyncio
async def test_workspace_source_read_rejects_oversize_archive_and_cleans_helper() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol",
        _labels("workspace"),
        files={"src/app/page.tsx": b"x" * 2048},
    )
    backend = _backend(client)
    backend.archive_limit_bytes = 1024

    with pytest.raises(CellResourceError, match="source archive exceeds 1024 bytes"):
        await backend.read_workspace_source_files("workspace-vol")

    assert client.containers.items == {}


@pytest.mark.asyncio
async def test_promote_volume_directory_fails_closed_when_final_exists() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol",
        _labels("workspace"),
        files={
            "accepted/proof.txt": b"keep",
            "stage/proof.txt": b"new",
        },
    )
    backend = _backend(client)

    with pytest.raises(CellResourceError, match="final exists"):
        await backend.promote_volume_directory("workspace-vol", "stage", "accepted")

    assert client.containers.items == {}
    assert client.volumes.items["workspace-vol"].files == {
        "accepted/proof.txt": b"keep",
        "stage/proof.txt": b"new",
    }


@pytest.mark.asyncio
async def test_postgres_dump_restore_and_smoke_use_exec_and_archives() -> None:
    client = _FakeClient()
    client.volumes.items["pg-vol"] = _FakeVolume("pg-vol", _labels("postgres"))
    backend = _backend(client)
    await backend.create_container(
        DockerContainerSpec(
            name="omnia-cell-test-postgres",
            image="postgres@sha256:" + "1" * 64,
            labels=_labels("postgres"),
            user="postgres",
            cap_add=[],
            cap_drop=["ALL"],
            read_only=True,
            privileged=False,
            security_opt=["no-new-privileges:true"],
            ports={},
            env={},
            volumes=("pg-vol",),
            mounts=(),
            network_names=("omnia-cell-test-internal",),
            helper=False,
            pids_limit=128,
            memory_limit_bytes=512 * 1024 * 1024,
            cpu_quota=1.5,
        )
    )

    dump = await backend.postgres_dump("omnia-cell-test-postgres", "secret")
    await backend.postgres_restore("omnia-cell-test-postgres", b"restore-bytes", "secret")
    smoke = await backend.postgres_smoke_query("omnia-cell-test-postgres", "secret")
    container = client.containers.items["omnia-cell-test-postgres"]

    assert dump == b"pg-dump-bytes"
    assert smoke is True
    assert container.exec_calls[0]["command"] == [
        "pg_dump",
        "-Fc",
        "-U",
        "postgres",
        "-d",
        "postgres",
    ]
    assert container.exec_calls[0]["environment"] == {"PGPASSWORD": "secret"}
    assert container.put_archive_calls[0][0] == "/"
    uploaded = _extract(container.put_archive_calls[0][1])
    assert uploaded["project-cell.dump"] == b"restore-bytes"
    assert container.exec_calls[1]["command"][:5] == [
        "pg_restore",
        "--clean",
        "--if-exists",
        "-U",
        "postgres",
    ]
    assert container.exec_calls[1]["command"][-1] == "/project-cell.dump"
    assert container.exec_calls[1]["environment"] == {"PGPASSWORD": "secret"}
    assert container.exec_calls[3]["command"] == [
        "psql",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-tAc",
        "select 1",
    ]
    assert container.exec_calls[3]["environment"] == {"PGPASSWORD": "secret"}


@pytest.mark.asyncio
async def test_run_workspace_command_uses_cell_mounts_and_networks() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    client.volumes.items["agent-home-vol"] = _FakeVolume("agent-home-vol", _labels("agent-home"))
    internal = _FakeNetwork(
        "omnia-cell-test-internal",
        _labels("internal-network"),
        internal=True,
    )
    internal.event_log = client.events
    egress = _FakeNetwork(
        "omnia-cell-test-egress",
        _labels("egress-network"),
        internal=False,
    )
    egress.event_log = client.events
    client.networks.items["omnia-cell-test-internal"] = internal
    client.networks.items["omnia-cell-test-egress"] = egress
    backend = _backend(client)

    result = await backend.run_workspace_command(
        workspace_volume_name="workspace-vol",
        agent_home_volume_name="agent-home-vol",
        labels=_labels("workspace"),
        image="omnia-template-max-miniapp-nextjs:dev",
        command="pnpm typecheck",
        internal_network_name="omnia-cell-test-internal",
        egress_network_name="omnia-cell-test-egress",
        environment={"DATABASE_URL": "postgresql://postgres:secret@pg:5432/postgres"},
        timeout_seconds=180,
    )

    create_call = client.containers.create_calls[0]
    kwargs = create_call["kwargs"]

    assert result.exit_code == 0
    assert result.output == ""
    assert kwargs["network"] == "omnia-cell-test-internal"
    assert kwargs["read_only"] is False
    assert kwargs["user"] == "0:0"
    assert kwargs["mem_limit"] == 1024 * 1024 * 1024
    assert kwargs["memswap_limit"] == kwargs["mem_limit"]
    assert kwargs["cpu_period"] == 100_000
    assert kwargs["cpu_quota"] == 50_000
    assert kwargs["environment"]["OMNIA_CELL_CMD"] == "pnpm typecheck"
    assert kwargs["volumes"] == {
        "workspace-vol": {"bind": "/workspace-src", "mode": "rw"},
        "agent-home-vol": {"bind": "/root", "mode": "rw"},
    }
    assert client.events == [
        "connect:workspace-vol-agent-exec",
        "start:workspace-vol-agent-exec",
    ]
    assert client.networks.items["omnia-cell-test-egress"].connections == [
        "workspace-vol-agent-exec"
    ]
    assert client.containers.items == {}


@pytest.mark.asyncio
async def test_run_workspace_command_preserves_nonzero_exit_status() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume("workspace-vol", _labels("workspace"))
    client.volumes.items["agent-home-vol"] = _FakeVolume("agent-home-vol", _labels("agent-home"))
    client.networks.items["omnia-cell-test-internal"] = _FakeNetwork(
        "omnia-cell-test-internal",
        _labels("internal-network"),
        internal=True,
    )
    client.networks.items["omnia-cell-test-egress"] = _FakeNetwork(
        "omnia-cell-test-egress",
        _labels("egress-network"),
        internal=False,
    )
    backend = _backend(client)

    result = await backend.run_workspace_command(
        workspace_volume_name="workspace-vol",
        agent_home_volume_name="agent-home-vol",
        labels=_labels("workspace"),
        image="omnia-template-max-miniapp-nextjs:dev",
        command="exit 7",
        internal_network_name="omnia-cell-test-internal",
        egress_network_name="omnia-cell-test-egress",
        environment={},
        timeout_seconds=180,
    )

    create_call = client.containers.create_calls[0]
    assert result.exit_code == 7
    assert 'sh -lc "$OMNIA_CELL_CMD" || status=$?' in create_call["command"][3]


@pytest.mark.asyncio
async def test_run_workspace_command_preserves_git_and_drops_secret_sync_files() -> None:
    client = _FakeClient()
    client.volumes.items["workspace-vol"] = _FakeVolume(
        "workspace-vol",
        _labels("workspace"),
        files={
            ".git/HEAD": b"ref: refs/heads/main\n",
            ".env": b"host-secret",
            "app.txt": b"before",
            "stale.txt": b"old",
        },
    )
    client.volumes.items["agent-home-vol"] = _FakeVolume("agent-home-vol", _labels("agent-home"))
    client.networks.items["omnia-cell-test-internal"] = _FakeNetwork(
        "omnia-cell-test-internal",
        _labels("internal-network"),
        internal=True,
    )
    client.networks.items["omnia-cell-test-egress"] = _FakeNetwork(
        "omnia-cell-test-egress",
        _labels("egress-network"),
        internal=False,
    )
    backend = _backend(client)

    await backend.run_workspace_command(
        workspace_volume_name="workspace-vol",
        agent_home_volume_name="agent-home-vol",
        labels=_labels("workspace"),
        image="omnia-template-max-miniapp-nextjs:dev",
        command="simulate-sync",
        internal_network_name="omnia-cell-test-internal",
        egress_network_name="omnia-cell-test-egress",
        environment={},
        timeout_seconds=180,
    )

    assert client.volumes.items["workspace-vol"].files == {
        ".git/HEAD": b"ref: refs/heads/main\n",
        ".env": b"host-secret",
        "app.txt": b"after",
    }
