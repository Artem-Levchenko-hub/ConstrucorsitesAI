import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.machine_adapter import MachineAdapter
from omnia_orchestrator.services.machine_business_config import apply_core_config, config_source


def test_configuration_is_json_data_and_only_trusted_core_files_are_written(monkeypatch):
    config = {"app_name": '"; throw new Error("injection"); //', "content": []}
    source = config_source(config)
    assert (
        json.loads(source.removeprefix("export const omniaMaxConfig = ").removesuffix(";\n"))
        == config
    )
    response = SimpleNamespace(
        status=200,
        read=lambda _: json.dumps(config).encode(),
    )
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr("http.client.HTTPConnection", lambda *a, **kw: connection)
    core = Mock()
    core.put_archive.return_value = True
    apply_core_config(core, "127.0.0.1", config)
    target, payload = core.put_archive.call_args.args
    assert target == "/app"
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        assert set(archive.getnames()) == {
            "src/lib/omnia/max-config.ts",
            "src/app/layout.tsx",
            "src/app/api/omnia/config/route.ts",
        }
    assert connection.request.call_count == 4  # config readback and all legal pages


@pytest.mark.asyncio
async def test_saved_metadata_replays_without_touching_product_or_generation(tmp_path, monkeypatch):
    path = tmp_path / "machine.json"
    state = SimpleNamespace(project_id=uuid4(), owner_id=uuid4(), fencing_epoch=5)
    manifest = {"version": 1, "tasks": [], "services": [], "routes": []}
    from omnia_orchestrator.services.machine_defaults import next_machine_manifest

    manifest = next_machine_manifest().model_dump(mode="json")
    machine = SimpleNamespace(path=path, state=lambda: {"manifest": manifest})
    runtime = MachineAdapter(SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(runtime, "parts", lambda _: (machine, object()))
    monkeypatch.setattr(runtime, "preview", lambda _: ("running", "127.0.0.1"))
    start = Mock()
    monkeypatch.setattr(runtime, "_start_boundary", start)
    for _ in range(2):
        await runtime.apply_owner_business_config(state, version=1, config={"app_name": "Saved"})
    assert start.call_count == 1
    saved = json.loads((tmp_path / "business-config.json").read_text())
    assert saved["applied"] is True
    with pytest.raises(CellResourceError, match="stale"):
        await runtime.apply_owner_business_config(
            state, version=1, config={"app_name": "Different"}
        )
    state.owner_id = uuid4()
    with pytest.raises(CellResourceError, match="ownership"):
        await runtime.apply_owner_business_config(state, version=2, config={"app_name": "Saved"})
    assert start.call_count == 1
