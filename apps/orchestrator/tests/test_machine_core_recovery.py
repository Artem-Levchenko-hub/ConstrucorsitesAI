"""Reconciliation must not invalidate a warm trusted core's unchanged sources."""
import io
import json
import tarfile
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.machine_business_config import (
    apply_core_config,
    apply_public_core_overlay,
)


class FileCore:
    """Docker archive boundary with actual file bytes and write generations."""

    def __init__(self):
        self.files = {}
        self.writes = {}
        self.fail_upload = False

    def get_archive(self, path):
        if path not in self.files:
            raise NotFound("file absent")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as output:
            entry = tarfile.TarInfo(PurePosixPath(path).name)
            entry.size = len(self.files[path])
            output.addfile(entry, io.BytesIO(self.files[path]))
        return iter([archive.getvalue()]), {"size": entry.size}

    def put_archive(self, destination, data):
        if self.fail_upload:
            return False
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for entry in archive:
                path = str(PurePosixPath(destination) / entry.name)
                self.files[path] = archive.extractfile(entry).read()
                self.writes[path] = self.writes.get(path, 0) + 1
        return True


@pytest.fixture
def config_readback(monkeypatch):
    current = {"app_name": "Original"}

    class Connection:
        def __init__(self, *_, **__):
            pass

        def request(self, *_):
            pass

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: json.dumps(current).encode())

        def close(self):
            pass

    monkeypatch.setattr("http.client.HTTPConnection", Connection)
    return current


def test_recovery_preserves_unchanged_core_files_across_new_container_handles(config_readback):
    core = FileCore()
    apply_public_core_overlay(core)
    apply_core_config(core, "127.0.0.1", config_readback)
    writes = dict(core.writes)
    # The controller creates new Docker handles on every recovery / process restart.
    recovered = FileCore()
    recovered.files, recovered.writes = core.files, core.writes
    apply_public_core_overlay(recovered)
    apply_core_config(recovered, "127.0.0.1", config_readback)
    assert recovered.writes == writes


def test_metadata_change_rewrites_only_config_and_repairs_missing_file(config_readback):
    core = FileCore()
    apply_core_config(core, "127.0.0.1", config_readback)
    config_readback["app_name"] = "Updated"
    apply_core_config(core, "127.0.0.1", config_readback)
    assert core.writes == {
        "/app/src/lib/omnia/max-config.ts": 2,
        "/app/src/app/layout.tsx": 1,
        "/app/src/app/api/omnia/config/route.ts": 1,
    }
    del core.files["/app/src/app/layout.tsx"]
    apply_core_config(core, "127.0.0.1", config_readback)
    assert core.writes["/app/src/app/layout.tsx"] == 2
    assert core.writes["/app/src/lib/omnia/max-config.ts"] == 2


def test_failed_upload_is_not_remembered_as_applied(config_readback):
    core = FileCore()
    core.fail_upload = True
    with pytest.raises(CellResourceError, match="upload failed"):
        apply_core_config(core, "127.0.0.1", config_readback)
    core.fail_upload = False
    apply_core_config(core, "127.0.0.1", config_readback)
    assert len(core.files) == 3


def test_corrupted_overlay_is_repaired_but_current_overlay_is_not_rewritten():
    core = FileCore()
    apply_public_core_overlay(core)
    name, expected = next(iter(core.files.items()))
    core.files[name] = b"damaged"
    apply_public_core_overlay(core)
    apply_public_core_overlay(core)
    assert core.files[name] == expected
    assert core.writes[name] == 2
