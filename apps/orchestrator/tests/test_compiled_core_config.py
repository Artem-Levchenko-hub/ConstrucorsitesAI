"""Compiled MAX core config is data, applied atomically without source writes."""
import json
from types import SimpleNamespace

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.machine_business_config import apply_core_config
from tests.test_machine_core_recovery import FileCore


class CompiledCore(FileCore):
    def __init__(self):
        super().__init__()
        self.attrs = {"Config": {"Labels": {"omnia.max-core.protocol": "1"}}}
        self.renames = 0
        self.fail_rename = False

    def exec_run(self, command, **_):
        assert command == ["mv", "-f", "--", "/app/.omnia-business-config.next.json",
                           "/app/omnia-business-config.json"]
        if self.fail_rename:
            return SimpleNamespace(exit_code=1)
        self.files[command[-1]] = self.files.pop(command[-2])
        self.renames += 1
        return SimpleNamespace(exit_code=0)


def fake_readback(monkeypatch, config):
    class Connection:
        def __init__(self, *_a, **_kw):
            pass

        def request(self, *_a, **_kw):
            pass

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: json.dumps(config).encode())

        def close(self):
            pass

    monkeypatch.setattr("http.client.HTTPConnection", Connection)


def test_compiled_core_metadata_is_atomic_and_does_not_rewrite_code(monkeypatch):
    core = CompiledCore()
    config = {"app_name": "Original"}
    fake_readback(monkeypatch, config)
    apply_core_config(core, "127.0.0.1", config)
    apply_core_config(core, "127.0.0.1", config)
    assert core.renames == 1
    config["app_name"] = "Changed"
    apply_core_config(core, "127.0.0.1", config)
    assert core.renames == 2
    assert core.files == {"/app/omnia-business-config.json": json.dumps(config).encode()}
    assert all(name.endswith(".json") for name in core.writes)


def test_failed_atomic_replace_keeps_last_valid_metadata(monkeypatch):
    core = CompiledCore()
    config = {"app_name": "Original"}
    fake_readback(monkeypatch, config)
    apply_core_config(core, "127.0.0.1", config)
    core.fail_rename = True
    config["app_name"] = "Changed"
    with pytest.raises(CellResourceError, match="atomic"):
        apply_core_config(core, "127.0.0.1", config)
    assert json.loads(core.files["/app/omnia-business-config.json"])["app_name"] == "Original"
