from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.routers import messages
from omnia_api.services.max_project_kit import _template_file
from tests.test_project_cell_executor import _prepare_executor

SDK_PATH = "src/lib/omnia/integration-client.ts"
PROVIDER_PATH = "src/components/MaxAppProvider.tsx"


@pytest.mark.parametrize("fail_delivery", [False, True])
async def test_generation_delivers_sdk_before_reading_agent_seed(monkeypatch, fail_delivery):
    tree = {
        SDK_PATH: "old SDK",
        PROVIDER_PATH: "old session bootstrap",
        "src/app/page.tsx": "existing product",
        ".omnia/cell.json": '{"project":"existing"}',
    }
    writes = []
    seed_reads = []

    async def stage(files, deletes):
        assert not seed_reads
        if fail_delivery:
            raise RuntimeError("delivery failed")
        writes.append((files, deletes))
        tree.update(files)

    async def read(*args):
        seed_reads.append(args)
        assert tree[SDK_PATH] == _template_file(SDK_PATH)
        assert tree[PROVIDER_PATH] == _template_file(PROVIDER_PATH)
        return None

    handle = SimpleNamespace(
        is_portable=lambda: True,
        snapshot_files=AsyncMock(side_effect=lambda: dict(tree)),
        stage_patch=stage,
    )
    monkeypatch.setattr(messages, "_project_cell_list_dir", read)
    monkeypatch.setattr(messages, "_project_cell_read_file", read)
    if fail_delivery:
        with pytest.raises(RuntimeError, match="delivery failed"):
            await messages._build_agent_seed_parts(
                uuid4(), "existing", project_cell_handle=handle, refresh_managed_sdk=True,
            )
        assert not seed_reads
        assert tree[SDK_PATH] == "old SDK"
        assert tree[PROVIDER_PATH] == "old session bootstrap"
        return
    for _ in range(2):
        await messages._build_agent_seed_parts(
            uuid4(), "existing", project_cell_handle=handle, refresh_managed_sdk=True,
        )
    assert writes == [({path: _template_file(path) for path in (SDK_PATH, PROVIDER_PATH)}, ())]
    assert tree["src/app/page.tsx"] == "existing product"
    assert tree[".omnia/cell.json"] == '{"project":"existing"}'


async def test_sdk_delivery_uses_current_generation_revision_and_is_exported(
    monkeypatch, db_session, test_engine,
):
    from omnia_api.services import project_cell_executor
    from omnia_api.services.max_managed_generation import refresh_integration_sdk

    original = {
        SDK_PATH: "old SDK",
        PROVIDER_PATH: "old session bootstrap",
        "src/app/page.tsx": "existing product",
        ".omnia/cell.json": '{"project":"existing"}',
        "package.json": '{"custom":true}',
    }
    harness = await _prepare_executor(
        monkeypatch, db_session, test_engine, snapshot_files=original,
        capabilities={"portable_machine": True},
    )
    handle = harness.handle
    assert handle.is_portable()
    await refresh_integration_sdk(handle)
    await refresh_integration_sdk(handle)
    canonical = {path: _template_file(path) for path in (SDK_PATH, PROVIDER_PATH)}
    assert await handle.snapshot_files() == {**original, **canonical}
    assert await handle.export_files() == canonical
    assert harness.write_calls == [{
        "generation_run_id": harness.run_id,
        "fencing_epoch": 1,
        "expected_revision": f"{1:064x}",
        "files": canonical,
        "deletes": [],
    }]
    assert harness.hot_reload_calls == []
    monkeypatch.setattr(
        project_cell_executor.get_settings(), "use_max_finalization_coordinator", False,
    )
    await handle.sync_preview()
    assert harness.hot_reload_calls[0] == canonical
