from __future__ import annotations

import io
import json
import tarfile
from types import SimpleNamespace
from typing import Any

import pytest

from omnia_orchestrator.core import docker_client


@pytest.mark.asyncio
async def test_write_files_stages_then_atomically_replaces_live_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_calls: list[list[str]] = []
    archives: list[tuple[str, bytes]] = []

    class FakeContainer:
        status = "running"

        def reload(self) -> None:
            self.status = "running"

        def exec_run(self, command: list[str], **_kwargs: Any) -> SimpleNamespace:
            exec_calls.append(command)
            return SimpleNamespace(exit_code=0, output=b"")

        def put_archive(self, *, path: str, data: bytes) -> bool:
            archives.append((path, data))
            return True

    container = FakeContainer()
    client = SimpleNamespace(
        containers=SimpleNamespace(get=lambda _name: container),
    )
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    result = await docker_client.write_files(
        "omnia-dev-proof",
        {
            "src/app/globals.css": "body{color:white}",
            "src/components/product/ProductApp.tsx": "export default function App(){}",
            "src/components/product/obsolete.tsx": "",
            ".": "must not replace the app root",
        },
    )

    assert result["written"] == "2"
    assert result["deleted"] == "1"
    assert result["dropped"] == "."
    assert len(archives) == 1
    stage_root, archive = archives[0]
    assert stage_root.startswith("/app/.omnia-hot-reload-")
    assert stage_root != "/app"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = {member.name for member in tar.getmembers() if member.isfile()}
    assert members == {
        "src/app/globals.css",
        "src/components/product/ProductApp.tsx",
    }

    assert exec_calls[0] == ["mkdir", "-p", stage_root]
    atomic = exec_calls[1]
    assert atomic[:2] == ["node", "-e"]
    assert "renameSync" in atomic[2]
    payload = json.loads(atomic[3])
    assert payload == {
        "stage": stage_root,
        "dest": "/app",
        "files": [
            "src/app/globals.css",
            "src/components/product/ProductApp.tsx",
        ],
    }
    assert exec_calls[2] == [
        "rm",
        "-f",
        "/app/src/components/product/obsolete.tsx",
    ]
