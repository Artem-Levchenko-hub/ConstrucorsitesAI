"""Opt-in real-Docker concurrency proof using one disposable, labelled volume.

Run from apps/orchestrator with its locked environment. Requires an already
installed digest-pinned helper image; never pulls images or touches project data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import threading
from typing import Any, cast
from uuid import uuid4

import docker  # type: ignore[import-untyped]

from omnia_orchestrator.services.docker_cell_resources import DockerVolumeRecord
from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend


class ConcurrentReader(DockerPyCellBackend):
    def __init__(self, *, client: Any, host: str, image: str, readers: int) -> None:
        super().__init__(host, image, client_factory=lambda _host: client)
        self.barrier = threading.Barrier(readers)
        self.names: list[str] = []

    def _get_archive_bytes(self, container: Any, path: str, label: str) -> bytes:
        self.names.append(str(container.name))
        self.barrier.wait(timeout=30)
        return super()._get_archive_bytes(container, path, label)


async def run(host: str, image: str) -> None:
    client = docker.DockerClient(base_url=host, timeout=60)
    client.ping()
    client.images.get(image)  # Fail before allocation if the pinned image is absent.
    workspace_id = uuid4()
    name = f"omnia-cell-smoke-{workspace_id.hex}"
    labels = {
        "omnia.managed": "true",
        "omnia.project_cell": "true",
        "omnia.workspace_id": str(workspace_id),
        "omnia.project_id": str(uuid4()),
        "omnia.owner_id": str(uuid4()),
        "omnia.provider": "docker_owner_canary",
        "omnia.profile_version": "docker-owner-cell-resources-v1",
        "omnia.resource_kind": "workspace",
        "omnia.disposable_helper_smoke": "true",
    }
    backend = DockerPyCellBackend(host, image, client_factory=lambda _host: client)
    volume = None
    result: dict[str, object] = {}
    try:
        volume = client.volumes.create(name=name, driver="local", labels=labels)
        if volume.name != name or volume.attrs.get("Labels") != labels:
            volume = None  # Do not write to or remove a resource we cannot identify.
            raise RuntimeError("disposable volume identity mismatch")
        expected = {"proof.txt": b"retained-through-concurrent-reads"}
        await backend.write_volume_files(name, expected)
        reader = ConcurrentReader(client=client, host=host, image=image, readers=6)
        for _round in range(3):
            results = await asyncio.gather(
                *(reader.read_volume_files(name) for _ in range(6)),
                *(backend.get_volume(name) for _ in range(6)),
            )
            read_results = cast(list[dict[str, bytes]], list(results[:6]))
            metadata_results = cast(list[DockerVolumeRecord | None], list(results[6:]))
            assert read_results == [expected] * 6
            assert all(record is not None and record.files == {} for record in metadata_results)
        assert len(reader.names) == len(set(reader.names)) == 18
        assert await backend.read_volume_files(name) == expected
        helpers = client.containers.list(
            all=True, filters={"label": f"omnia.workspace_id={workspace_id}"}
        )
        assert not helpers, "temporary helpers leaked"
        abandoned = await backend._start_helper_container(
            name=backend._helper_name("volume-read", name),
            labels=backend._helper_labels(labels, "volume-read"),
            volumes={name: {"bind": "/volume", "mode": "ro"}},
        )
        assert abandoned.attrs["HostConfig"]["AutoRemove"] is True
        assert abandoned.attrs["Config"]["Cmd"] == ["sleep", "300"]
        abandoned.kill()  # Simulate helper expiry: Docker must reclaim it itself.
        for _attempt in range(50):
            try:
                client.containers.get(abandoned.id)
            except docker.errors.NotFound:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("daemon-side auto-removal did not complete")
        assert await backend.read_volume_files(name) == expected
        result = {"parallel_reads": 18, "metadata_reads": 18, "data_intact": True}
    finally:
        # Only this invocation's random identity is eligible for cleanup.
        for container in client.containers.list(
            all=True, filters={"label": f"omnia.workspace_id={workspace_id}"}
        ):
            if (
                container.labels.get("omnia.disposable_helper_smoke") != "true"
                or container.labels.get("omnia.owner_id") != labels["omnia.owner_id"]
            ):
                raise RuntimeError("refusing cleanup of an unowned container")
            container.remove(force=True)
        if volume is not None:
            if volume.name != name:
                raise RuntimeError("refusing cleanup of an unowned volume")
            volume.remove()
        client.close()
    print(json.dumps({**result, "cleanup": "ok"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-disposable-docker", action="store_true", required=True)
    parser.add_argument("--docker-host", default="unix:///var/run/docker.sock")
    parser.add_argument("--helper-image", required=True)
    arguments = parser.parse_args()
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", arguments.helper_image):
        parser.error("--helper-image must be digest-pinned")
    asyncio.run(run(arguments.docker_host, arguments.helper_image))
