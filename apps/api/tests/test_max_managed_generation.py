from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_project_cell_executor import _prepare_executor
from yleum_api.services.generation import runtime
from yleum_api.services.max_project_kit import _template_file

SDK_PATH = "src/lib/omnia/integration-client.ts"
PROVIDER_PATH = "src/components/MaxAppProvider.tsx"
FOOTER_PATH = "src/components/YleumCompliance.tsx"
CONFIG_PATH = "src/lib/omnia/max-config.ts"


@pytest.mark.parametrize("fail_delivery", [False, True])
async def test_legacy_generation_restores_saved_config_after_provisioning(
    monkeypatch,
    fail_delivery,
):
    source = 'export const omniaMaxConfig = {app_name: "Saved", content: [{id: "tea"}]};\n'
    tree = {CONFIG_PATH: "template defaults", "src/app/page.tsx": "existing product"}
    seed_reads = []

    async def reload(project_id, slug, files):
        assert not seed_reads
        if fail_delivery:
            raise RuntimeError("delivery failed")
        tree.update(files)
        return {"ok": True}

    async def read(*args):
        seed_reads.append(args)
        assert tree[CONFIG_PATH] == source
        assert tree[SDK_PATH] == _template_file(SDK_PATH)
        return ""

    monkeypatch.setattr(runtime.orchestrator_client, "hot_reload", reload)
    monkeypatch.setattr(runtime.orchestrator_client, "agent_list_dir", read)
    monkeypatch.setattr(runtime.orchestrator_client, "agent_read_file", read)
    if fail_delivery:
        with pytest.raises(RuntimeError, match="delivery failed"):
            await runtime._build_agent_seed_parts(
                uuid4(),
                "legacy",
                refresh_managed_sdk=True,
                max_config_source=source,
            )
        assert not seed_reads
    else:
        await runtime._build_agent_seed_parts(
            uuid4(),
            "legacy",
            refresh_managed_sdk=True,
            max_config_source=source,
        )
        assert seed_reads and tree[CONFIG_PATH] == source
        assert tree["src/app/page.tsx"] == "existing product"


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
    monkeypatch.setattr(runtime, "_project_cell_list_dir", read)
    monkeypatch.setattr(runtime, "_project_cell_read_file", read)
    if fail_delivery:
        with pytest.raises(RuntimeError, match="delivery failed"):
            await runtime._build_agent_seed_parts(
                uuid4(),
                "existing",
                project_cell_handle=handle,
                refresh_managed_sdk=True,
            )
        assert not seed_reads
        assert tree[SDK_PATH] == "old SDK"
        assert tree[PROVIDER_PATH] == "old session bootstrap"
        return
    for _ in range(2):
        await runtime._build_agent_seed_parts(
            uuid4(),
            "existing",
            project_cell_handle=handle,
            refresh_managed_sdk=True,
        )
    canonical = {path: _template_file(path) for path in (SDK_PATH, PROVIDER_PATH, FOOTER_PATH)}
    assert writes == [(canonical, ())]
    assert tree["src/app/page.tsx"] == "existing product"
    assert tree[".omnia/cell.json"] == '{"project":"existing"}'


async def test_generation_retires_encrypted_crud_files_from_existing_projects():
    from yleum_api.services.max_managed_generation import refresh_integration_sdk
    from yleum_api.services.max_project_kit import MAX_RETIRED_MANAGED_FILES

    canonical = {path: _template_file(path) for path in (SDK_PATH, PROVIDER_PATH, FOOTER_PATH)}
    tree = {
        **canonical,
        "src/lib/omnia/data-client.ts": "export function secureCollection() {}",
        "src/lib/secure-data/store.ts": "store",
        "drizzle/0003_secure_records.sql": "CREATE TABLE omnia_secure_records ();",
        "src/lib/secure-data-notes.ts": "product file with a similar name",
        "src/app/page.tsx": "existing product",
    }
    writes = []

    async def stage(files, deletes):
        writes.append((files, deletes))
        tree.update(files)
        for path in deletes:
            tree.pop(path)

    handle = SimpleNamespace(
        snapshot_files=AsyncMock(side_effect=lambda: dict(tree)), stage_patch=stage,
    )
    await refresh_integration_sdk(handle)
    await refresh_integration_sdk(handle)
    assert writes == [({}, (
        "drizzle/0003_secure_records.sql",
        "src/lib/omnia/data-client.ts",
        "src/lib/secure-data/store.ts",
    ))]
    assert not MAX_RETIRED_MANAGED_FILES & set(tree)
    assert tree["src/lib/secure-data-notes.ts"] == "product file with a similar name"
    assert tree["src/app/page.tsx"] == "existing product"


async def test_sdk_delivery_uses_current_generation_revision_and_is_exported(
    monkeypatch,
    db_session,
    test_engine,
):
    from yleum_api.services import project_cell_executor
    from yleum_api.services.max_managed_generation import refresh_integration_sdk

    original = {
        SDK_PATH: "old SDK",
        PROVIDER_PATH: "old session bootstrap",
        "src/app/page.tsx": "existing product",
        ".omnia/cell.json": '{"project":"existing"}',
        "package.json": '{"custom":true}',
    }
    harness = await _prepare_executor(
        monkeypatch,
        db_session,
        test_engine,
        snapshot_files=original,
        capabilities={"portable_machine": True},
    )
    handle = harness.handle
    assert handle.is_portable()
    saved_source = 'export const omniaMaxConfig = {app_name: "Saved owner title"};\n'
    await refresh_integration_sdk(handle, max_config_source=saved_source)
    await refresh_integration_sdk(handle, max_config_source=saved_source)
    canonical = {path: _template_file(path) for path in (SDK_PATH, PROVIDER_PATH, FOOTER_PATH)}
    canonical[CONFIG_PATH] = saved_source
    assert await handle.snapshot_files() == {**original, **canonical}
    assert await handle.export_files() == canonical
    assert harness.write_calls == [
        {
            "generation_run_id": harness.run_id,
            "fencing_epoch": 1,
            "expected_revision": f"{1:064x}",
            "files": canonical,
            "deletes": [],
        }
    ]
    assert harness.hot_reload_calls == []
    monkeypatch.setattr(
        project_cell_executor.get_settings(),
        "use_max_finalization_coordinator",
        False,
    )
    await handle.sync_preview()
    assert harness.hot_reload_calls[0] == canonical
