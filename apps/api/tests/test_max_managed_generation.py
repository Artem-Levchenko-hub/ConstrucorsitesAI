from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_project_cell_executor import _prepare_executor
from yleum_api.services.generation import runtime
from yleum_api.services.max_project_kit import _template_file

SDK_PATH = "src/lib/omnia/integration-client.ts"
LEGACY_CLIENT_PATH = "src/lib/omnia/client.ts"
ACTIVITY_PATH = "src/lib/omnia/activity.ts"
CATALOG_SERVER_PATH = "src/lib/omnia/catalog-server.ts"
CATALOG_ACTIONS_PATH = "src/lib/omnia/catalog-actions.ts"
CATALOG_CLIENT_PATH = "src/lib/omnia/catalog-client.ts"
PROVIDER_PATH = "src/components/MaxAppProvider.tsx"
RENEWAL_PATH = "src/lib/max/owner-preview-renewal.ts"
FOOTER_PATH = "src/components/YleumCompliance.tsx"
SUPPORT_PATH = "src/app/support/page.tsx"
CONFIG_PATH = "src/lib/omnia/max-config.ts"


@pytest.mark.parametrize("fail_delivery", [False, True])
async def test_generation_delivers_sdk_before_reading_agent_seed(monkeypatch, fail_delivery):
    tree = {
        SDK_PATH: "old SDK",
        LEGACY_CLIENT_PATH: "old legacy SDK",
        PROVIDER_PATH: "old session bootstrap",
        SUPPORT_PATH: "old support without contact",
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
        assert tree[LEGACY_CLIENT_PATH] == _template_file(LEGACY_CLIENT_PATH)
        assert tree[PROVIDER_PATH] == _template_file(PROVIDER_PATH)
        assert tree.get(RENEWAL_PATH) == _template_file(RENEWAL_PATH)
        assert tree[SUPPORT_PATH] == _template_file(SUPPORT_PATH)
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
            await runtime._build_agent_seed_parts(handle, refresh_managed_sdk=True)
        assert not seed_reads
        assert tree[SDK_PATH] == "old SDK"
        assert tree[LEGACY_CLIENT_PATH] == "old legacy SDK"
        assert tree[PROVIDER_PATH] == "old session bootstrap"
        assert RENEWAL_PATH not in tree
        return
    for _ in range(2):
        await runtime._build_agent_seed_parts(handle, refresh_managed_sdk=True)
    canonical = {
        path: _template_file(path)
        for path in (
            SDK_PATH, LEGACY_CLIENT_PATH, ACTIVITY_PATH,
            CATALOG_SERVER_PATH, CATALOG_ACTIONS_PATH, CATALOG_CLIENT_PATH,
            PROVIDER_PATH, RENEWAL_PATH, FOOTER_PATH, SUPPORT_PATH
        )
    }
    assert writes == [(canonical, ())]
    assert tree["src/app/page.tsx"] == "existing product"
    assert tree[".omnia/cell.json"] == '{"project":"existing"}'


async def test_generation_retires_encrypted_crud_files_from_existing_projects():
    from yleum_api.services.max_managed_generation import refresh_integration_sdk
    from yleum_api.services.max_project_kit import MAX_RETIRED_MANAGED_FILES

    canonical = {
        path: _template_file(path)
        for path in (
            SDK_PATH, LEGACY_CLIENT_PATH, ACTIVITY_PATH,
            CATALOG_SERVER_PATH, CATALOG_ACTIONS_PATH, CATALOG_CLIENT_PATH,
            PROVIDER_PATH, RENEWAL_PATH, FOOTER_PATH, SUPPORT_PATH
        )
    }
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


@pytest.mark.parametrize("protocol", [None, 1, 2, True])
async def test_catalog_manifest_upgrade_is_explicit_atomic_and_preserves_product(protocol):
    import json

    from yleum_api.services.max_managed_generation import refresh_integration_sdk

    marker = {"version": 1, "services": [{"name": "app", "port": 3000}],
              "routes": [{"path": "/", "service": "app"}], "custom": "preserved"}
    if protocol is not None:
        marker["app_config_protocol"] = protocol
    original = json.dumps(marker)
    tree = {".omnia/cell.json": original, "src/app/page.tsx": "owner product"}
    writes = []
    async def stage(files, deletes):
        writes.append((files, deletes))
        tree.update(files)
    handle = SimpleNamespace(snapshot_files=AsyncMock(side_effect=lambda: dict(tree)),
                             stage_patch=stage)
    if protocol == 2 or protocol is True:
        with pytest.raises(ValueError, match="Unsupported"):
            await refresh_integration_sdk(handle, catalog_runtime=True)
        assert writes == [] and tree[".omnia/cell.json"] == original
        return
    await refresh_integration_sdk(handle, catalog_runtime=True)
    await refresh_integration_sdk(handle, catalog_runtime=True)
    assert len(writes) == 1
    upgraded = json.loads(tree[".omnia/cell.json"])
    assert type(upgraded.pop("app_config_protocol")) is int
    assert upgraded == {key: value for key, value in marker.items() if key != "app_config_protocol"}
    assert tree["src/app/page.tsx"] == "owner product"
    if protocol == 1:
        assert tree[".omnia/cell.json"] == original


async def test_sdk_delivery_uses_current_generation_revision_and_is_exported(
    monkeypatch,
    db_session,
    test_engine,
):
    from yleum_api.services import project_cell_executor
    from yleum_api.services.max_managed_generation import refresh_integration_sdk

    original = {
        SDK_PATH: "old SDK",
        LEGACY_CLIENT_PATH: "old legacy SDK",
        PROVIDER_PATH: "old session bootstrap",
        SUPPORT_PATH: "old support without contact",
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
    canonical = {
        path: _template_file(path)
        for path in (
            SDK_PATH, LEGACY_CLIENT_PATH, ACTIVITY_PATH,
            CATALOG_SERVER_PATH, CATALOG_ACTIONS_PATH, CATALOG_CLIENT_PATH,
            PROVIDER_PATH, RENEWAL_PATH, FOOTER_PATH, SUPPORT_PATH
        )
    }
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


@pytest.mark.parametrize("manifest", [
    '{"version":1,"services":[],"routes":[]}',
    '{"version":1,"services":[],"routes":[],"app_config_protocol":true}',
    "not json",
])
async def test_catalog_policy_survives_new_handle_without_stack_preparation(
    monkeypatch, db_session, test_engine, manifest,
):
    from sqlalchemy import select

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services import project_cell_executor

    harness = await _prepare_executor(monkeypatch, db_session, test_engine,
        snapshot_files={".omnia/cell.json": manifest})
    handle = harness.handle
    await handle.seed_business_config(0, {"content": []})
    db_session.expire_all()
    run = await db_session.scalar(select(GenerationRun).where(GenerationRun.id == harness.run_id))
    assert run.agent_state["max_catalog_runtime_protocol"] == 1
    # A cached entry bypasses prepare_stack_prompt. A new worker must still
    # restore policy from platform state, even with damaged agent source.
    user_id = run.user_id
    await db_session.rollback()
    resumed = await project_cell_executor.maybe_create_project_cell_executor(
        project_id=harness.project_id, project_slug=harness.project_slug,
        project_template="max_miniapp", user_id=user_id, generation_run_id=harness.run_id,
        docs_media_execute=AsyncMock(),
    )
    assert resumed is not None
    with pytest.raises(
        project_cell_executor.ProjectCellExecutorUnavailable, match="app_config_protocol",
    ):
        await resumed.run_role(
            project_cell_executor.ProjectCellCommandRole.FULL_BUILD, uuid4(),
        )
    assert harness.exec_calls == []
