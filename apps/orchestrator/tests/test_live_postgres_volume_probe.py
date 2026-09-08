from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import docker  # type: ignore[import-untyped]
import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend

pytestmark = pytest.mark.skipif(
    os.environ.get("OMNIA_P01_LIVE_DOCKER") != "1",
    reason="set OMNIA_P01_LIVE_DOCKER=1 for the disposable PostgreSQL volume probe",
)


class _MeasuredBackend(DockerPyCellBackend):
    def __post_init__(self) -> None:
        super().__post_init__()
        self.probe_output_bytes: list[int] = []
        self.owner_output_bytes: list[int] = []

    def _exec_checked(
        self,
        container: Any,
        command: list[str],
        label: str,
        environment: dict[str, str] | None = None,
        user: str | None = None,
    ) -> bytes:
        output = super()._exec_checked(container, command, label, environment, user)
        if label.startswith("probe postgres volume "):
            self.probe_output_bytes.append(len(output))
        elif label.startswith("inspect postgres volume owner "):
            self.owner_output_bytes.append(len(output))
        return output


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"{name} is required for the live PostgreSQL volume probe")
    return value


def _labels() -> dict[str, str]:
    return {
        "omnia.managed": "true",
        "omnia.project_cell": "true",
        "omnia.workspace_id": str(uuid4()),
        "omnia.project_id": str(uuid4()),
        "omnia.owner_id": str(uuid4()),
        "omnia.provider": "docker_owner_canary",
        "omnia.profile_version": "docker-owner-cell-p01-v1",
        "omnia.resource_kind": "postgres",
        "omnia.p01.disposable": "true",
    }


@contextmanager
def _disposable_volume(client: Any) -> Iterator[str]:
    name = f"omnia-p01-postgres-probe-{uuid4().hex}"
    volume = client.volumes.create(name=name, driver="local", labels=_labels())
    try:
        yield name
    finally:
        volume.remove(force=True)


def _run_volume_helper(
    client: Any,
    image: str,
    volume_name: str,
    script: str,
    *,
    cap_add: list[str] | None = None,
    user: str = "0:0",
    memory_limit_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    output = client.containers.run(
        image,
        ["sh", "-eu", "-c", script],
        name=f"omnia-p01-fixture-{uuid4().hex}",
        remove=True,
        network="none",
        volumes={volume_name: {"bind": "/volume", "mode": "rw"}},
        user=user,
        cap_add=[] if cap_add is None else cap_add,
        cap_drop=["ALL"],
        read_only=True,
        privileged=False,
        security_opt=["no-new-privileges:true"],
        pids_limit=32,
        mem_limit=memory_limit_bytes,
        tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=8m"},
    )
    return bytes(output)


@pytest.fixture
def live_backend() -> Iterator[tuple[Any, _MeasuredBackend, str]]:
    client = docker.from_env()
    client.ping()
    image = _required_env("OMNIA_P01_HELPER_IMAGE")
    backend = _MeasuredBackend(
        docker_host=str(client.api.base_url),
        helper_image=image,
        client_factory=lambda _host: client,
    )
    try:
        yield client, backend, image
    finally:
        client.close()


@pytest.mark.asyncio
async def test_live_probe_handles_empty_and_exact_legacy_cleanup(
    live_backend: tuple[Any, _MeasuredBackend, str],
) -> None:
    client, backend, image = live_backend
    with _disposable_volume(client) as empty_volume:
        assert await backend.probe_postgres_volume_after_legacy_cleanup(empty_volume) is False

    with _disposable_volume(client) as legacy_volume:
        _run_volume_helper(
            client,
            image,
            legacy_volume,
            "mkdir -p /volume/PGDATA; printf legacy > /volume/PGDATA/postgres-password.txt",
        )
        assert await backend.probe_postgres_volume_after_legacy_cleanup(legacy_volume) is False
        assert (
            _run_volume_helper(
                client,
                image,
                legacy_volume,
                "test ! -e /volume/PGDATA/postgres-password.txt; printf cleaned",
            )
            == b"cleaned"
        )


@pytest.mark.asyncio
async def test_live_probe_is_size_independent_and_preserves_fixture_digest(
    live_backend: tuple[Any, _MeasuredBackend, str],
) -> None:
    client, backend, image = live_backend
    measurements: dict[str, float | int | str] = {}

    with _disposable_volume(client) as small_volume:
        _run_volume_helper(
            client,
            image,
            small_volume,
            "mkdir -p /volume/PGDATA; printf '16\\n' > /volume/PGDATA/PG_VERSION",
        )
        before = time.perf_counter()
        assert await backend.probe_postgres_volume_after_legacy_cleanup(small_volume) is True
        measurements["small_elapsed_seconds"] = round(time.perf_counter() - before, 6)

    with _disposable_volume(client) as comparison_volume:
        _run_volume_helper(
            client,
            image,
            comparison_volume,
            "mkdir -p /volume/PGDATA/base/1; "
            "dd if=/dev/zero of=/volume/PGDATA/base/1/comparison.bin "
            "bs=1M count=16 2>/dev/null",
        )
        digest_script = "sha256sum /volume/PGDATA/base/1/comparison.bin"
        digest_before = _run_volume_helper(client, image, comparison_volume, digest_script)
        before = time.perf_counter()
        archived_files = await backend.read_volume_files(comparison_volume)
        measurements["legacy_read_16m_elapsed_seconds"] = round(time.perf_counter() - before, 6)
        measurements["legacy_read_payload_bytes"] = sum(map(len, archived_files.values()))
        assert measurements["legacy_read_payload_bytes"] == 16 * 1024 * 1024
        before = time.perf_counter()
        assert await backend.probe_postgres_volume_after_legacy_cleanup(comparison_volume) is True
        measurements["probe_16m_elapsed_seconds"] = round(time.perf_counter() - before, 6)
        assert _run_volume_helper(client, image, comparison_volume, digest_script) == digest_before

    with _disposable_volume(client) as large_volume:
        _run_volume_helper(
            client,
            image,
            large_volume,
            "mkdir -p /volume/PGDATA/base/1; "
            "dd if=/dev/zero of=/volume/PGDATA/base/1/large.bin bs=1M count=129 2>/dev/null",
            # Budget large-fixture preparation separately from the actual probe.
            memory_limit_bytes=256 * 1024 * 1024,
        )
        digest_script = "sha256sum /volume/PGDATA/base/1/large.bin"
        digest_before = _run_volume_helper(client, image, large_volume, digest_script)
        before = time.perf_counter()
        assert await backend.probe_postgres_volume_after_legacy_cleanup(large_volume) is True
        measurements["large_elapsed_seconds"] = round(time.perf_counter() - before, 6)
        digest_after = _run_volume_helper(client, image, large_volume, digest_script)
        assert digest_after == digest_before
        measurements["large_bytes"] = 129 * 1024 * 1024
        measurements["large_sha256"] = digest_after.split(maxsplit=1)[0].decode()

    assert backend.probe_output_bytes == [9, 9, 9]
    assert backend.owner_output_bytes == [4, 4, 4]
    measurements["max_probe_output_bytes"] = max(backend.probe_output_bytes)
    measurements["max_control_output_bytes"] = max(
        owner + result
        for owner, result in zip(
            backend.owner_output_bytes, backend.probe_output_bytes, strict=True
        )
    )
    print("P01_MEASUREMENT=" + json.dumps(measurements, sort_keys=True))


@pytest.mark.asyncio
async def test_live_probe_reads_realistic_postgres_owned_pgdata_without_extra_caps(
    live_backend: tuple[Any, _MeasuredBackend, str],
) -> None:
    client, backend, image = live_backend
    with _disposable_volume(client) as volume_name:
        _run_volume_helper(
            client,
            image,
            volume_name,
            "mkdir -p /volume/PGDATA/base/1; "
            "printf '16\\n' > /volume/PGDATA/PG_VERSION; "
            "printf tuple > /volume/PGDATA/base/1/42; "
            "printf legacy > /volume/PGDATA/postgres-password.txt; "
            "chmod 0755 /volume; chmod 0700 /volume/PGDATA; "
            "chmod 0600 /volume/PGDATA/PG_VERSION /volume/PGDATA/base/1/42; "
            "chown -R 70:70 /volume",
            cap_add=["CHOWN"],
        )

        assert await backend.probe_postgres_volume_after_legacy_cleanup(volume_name) is True
        assert (
            _run_volume_helper(
                client,
                image,
                volume_name,
                "test ! -e /volume/PGDATA/postgres-password.txt; "
                "test \"$(cat /volume/PGDATA/base/1/42)\" = tuple; printf retained",
                user="70:70",
            )
            == b"retained"
        )
        assert (
            client.containers.list(
                all=True,
                filters={"label": "omnia.resource_kind=postgres-volume-probe"},
            )
            == []
        )


@pytest.mark.asyncio
async def test_live_probe_never_interprets_unreadable_directory_as_empty(
    live_backend: tuple[Any, _MeasuredBackend, str],
) -> None:
    client, backend, image = live_backend
    with _disposable_volume(client) as volume_name:
        _run_volume_helper(
            client,
            image,
            volume_name,
            "mkdir /volume/blocked; chmod 000 /volume/blocked; chown 70:70 /volume/blocked",
            cap_add=["CHOWN"],
        )
        with pytest.raises(CellResourceError, match="exit code 44"):
            await backend.probe_postgres_volume_after_legacy_cleanup(volume_name)


@pytest.mark.asyncio
async def test_live_probe_rejects_legacy_symlink_without_following_or_removing_it(
    live_backend: tuple[Any, _MeasuredBackend, str],
) -> None:
    client, backend, image = live_backend
    with _disposable_volume(client) as volume_name:
        _run_volume_helper(
            client,
            image,
            volume_name,
            "mkdir -p /volume/PGDATA; ln -s /etc/passwd /volume/PGDATA/postgres-password.txt",
        )
        with pytest.raises(CellResourceError, match="exit code 42"):
            await backend.probe_postgres_volume_after_legacy_cleanup(volume_name)
        assert (
            _run_volume_helper(
                client,
                image,
                volume_name,
                "test -L /volume/PGDATA/postgres-password.txt; printf retained",
            )
            == b"retained"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "target_digest_command"),
    [
        (
            "alias-target",
            "sha256sum /volume/alias-target/postgres-password.txt",
        ),
        (
            "/etc",
            "sha256sum /etc/passwd",
        ),
    ],
)
async def test_live_probe_rejects_symlinked_pgdata_before_touching_target(
    live_backend: tuple[Any, _MeasuredBackend, str],
    target: str,
    target_digest_command: str,
) -> None:
    client, backend, image = live_backend
    with _disposable_volume(client) as volume_name:
        if target == "alias-target":
            setup = (
                "mkdir -p /volume/alias-target; "
                "printf must-stay > /volume/alias-target/postgres-password.txt; "
                "ln -s alias-target /volume/PGDATA"
            )
        else:
            setup = "ln -s /etc /volume/PGDATA"
        _run_volume_helper(client, image, volume_name, setup)
        digest_before = _run_volume_helper(
            client,
            image,
            volume_name,
            target_digest_command,
        )

        with pytest.raises(CellResourceError, match="exit code 41"):
            await backend.probe_postgres_volume_after_legacy_cleanup(volume_name)

        digest_after = _run_volume_helper(
            client,
            image,
            volume_name,
            target_digest_command,
        )
        assert digest_after == digest_before
        assert (
            _run_volume_helper(
                client,
                image,
                volume_name,
                "test -L /volume/PGDATA; printf retained",
            )
            == b"retained"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_command",
    [
        "ln -s /etc/passwd /volume/unexpected",
        "mkfifo /volume/unexpected",
    ],
)
async def test_live_probe_rejects_non_regular_only_volume(
    live_backend: tuple[Any, _MeasuredBackend, str],
    fixture_command: str,
) -> None:
    client, backend, image = live_backend
    with _disposable_volume(client) as volume_name:
        _run_volume_helper(client, image, volume_name, fixture_command)
        with pytest.raises(CellResourceError, match="exit code 43"):
            await backend.probe_postgres_volume_after_legacy_cleanup(volume_name)
