import json
import re
from pathlib import Path
from subprocess import run
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.test_project_cell_executor import _prepare_executor
from yleum_api.services.generation import runtime
from yleum_api.services.max_project_kit import MAX_MANAGEMENT_RUNTIME_FILES, _template_file

SDK_PATH = "src/lib/omnia/integration-client.ts"
LEGACY_CLIENT_PATH = "src/lib/omnia/client.ts"
ACTIVITY_PATH = "src/lib/omnia/activity.ts"
ANALYTICS_PATH = "src/lib/omnia/analytics.ts"
TELEMETRY_PATH = "src/app/api/omnia/telemetry/route.ts"
CATALOG_SERVER_PATH = "src/lib/omnia/catalog-server.ts"
CATALOG_ACTIONS_PATH = "src/lib/omnia/catalog-actions.ts"
CATALOG_CLIENT_PATH = "src/lib/omnia/catalog-client.ts"
PROVIDER_PATH = "src/components/MaxAppProvider.tsx"
RENEWAL_PATH = "src/lib/max/owner-preview-renewal.ts"
FOOTER_PATH = "src/components/YleumCompliance.tsx"
SUPPORT_PATH = "src/app/support/page.tsx"
CONFIG_PATH = "src/lib/omnia/max-config.ts"
WEB_NODE_MODULES = Path(__file__).resolve().parents[2] / "web/node_modules"


def legacy_analytics(project_expression):
    return (
        "export async function forwardMaxAnalytics(\n"
        '  actor: string, eventId: string, kind: "open" | "action" | "event",\n'
        "): Promise<boolean> {\n"
        f"  const project = {project_expression};\n"
        "  return Boolean(project);\n}\n"
    )


@pytest.mark.parametrize("binding", ["missing", "environment", "literal", "unbound"])
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["lf", "crlf"])
async def test_sdk_refresh_upgrades_analytics_without_losing_project_binding(binding, line_ending):
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_managed_generation import refresh_integration_sdk
    from yleum_api.services.max_project_kit import render_max_managed_files

    project_id = uuid4()
    expected = _template_file(ANALYTICS_PATH)
    tree = {"src/app/page.tsx": "owner product"}
    if binding in {"literal", "unbound"}:
        project_id = project_id if binding == "literal" else None
        expected = render_max_managed_files(
            MaxProjectConfigPayload.default_for("Owner app"), project_id,
        )[ANALYTICS_PATH]
        tree[ANALYTICS_PATH] = legacy_analytics(json.dumps(str(project_id) if project_id else ""))
    elif binding == "environment":
        tree[ANALYTICS_PATH] = legacy_analytics(
            'process.env.OMNIA_PROJECT_ID || process.env.YLEUM_OWNER_PROJECT_ID || ""',
        )
    if ANALYTICS_PATH in tree:
        tree[ANALYTICS_PATH] = tree[ANALYTICS_PATH].replace("\n", line_ending)
    writes = []

    async def stage(files, deletes):
        writes.append((files, deletes))
        tree.update(files)

    handle = SimpleNamespace(snapshot_files=AsyncMock(side_effect=lambda: dict(tree)),
                             stage_patch=stage)
    await refresh_integration_sdk(handle)
    await refresh_integration_sdk(handle)
    assert tree[ANALYTICS_PATH] == expected
    assert tree[ACTIVITY_PATH] == _template_file(ACTIVITY_PATH)
    assert tree["src/lib/omnia/business-server.ts"] == _template_file(
        "src/lib/omnia/business-server.ts",
    )
    assert tree["src/app/page.tsx"] == "owner product"
    assert len(writes) == 1 and writes[0][1] == ()


@pytest.mark.parametrize("source", [
    legacy_analytics('"not-a-project-uuid"'),
    legacy_analytics("unknownProject()"),
    legacy_analytics('"00000000-0000-4000-8000-000000000001"')
    + 'const project = "00000000-0000-4000-8000-000000000002";\n',
    "export const other = true;",
    "",
])
async def test_sdk_refresh_rejects_unknown_analytics_identity_before_writing(source):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.max_managed_generation import refresh_integration_sdk

    handle = SimpleNamespace(snapshot_files=AsyncMock(return_value={ANALYTICS_PATH: source}),
                             stage_patch=AsyncMock())
    with pytest.raises(ApiError, match="MAX analytics project binding"):
        await refresh_integration_sdk(handle)
    handle.stage_patch.assert_not_awaited()


async def test_refreshed_analytics_typechecks_the_real_managed_business_call(tmp_path):
    from yleum_api.services.max_managed_generation import refresh_integration_sdk

    modules = WEB_NODE_MODULES
    compiler = modules / "typescript/lib/tsc.js"
    if not compiler.is_file():
        pytest.skip("TypeScript compiler is not installed")
    business = _template_file("src/lib/omnia/business-server.ts")
    call = re.search(
        r'const \{ forwardMaxAnalytics \} = await import\("@/lib/omnia/analytics"\);'
        r"\s*await forwardMaxAnalytics\([\s\S]*?\n      \);",
        business,
    )
    assert call is not None, "managed business must import and call analytics"
    (tmp_path / "probe.ts").write_text(
        'declare const resource: "orders" | "leads" | "bookings";\n'
        "declare const actor: string;\n"
        "declare const result: {record: {id: string}};\n"
        "async function probe() {\n" + call[0] + "\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(json.dumps({"compilerOptions": {
        "strict": True, "target": "ES2022", "module": "ESNext",
        "moduleResolution": "Bundler", "noEmit": True, "skipLibCheck": True,
        "baseUrl": str(tmp_path), "paths": {"@/*": ["src/*"]},
        "typeRoots": [str(modules / "@types")], "types": ["node"],
    }}), encoding="utf-8")
    tree = {ANALYTICS_PATH: legacy_analytics(json.dumps(str(uuid4())))}

    def typecheck():
        for path in (ANALYTICS_PATH, ACTIVITY_PATH):
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tree.get(path, _template_file(path)), encoding="utf-8")
        return run(["node", str(compiler), "--project", str(tmp_path)],
                   capture_output=True, text=True, check=False, timeout=30)

    before = typecheck()
    assert before.returncode != 0 and "TS2554" in before.stdout

    async def stage(files, deletes):
        assert deletes == ()
        tree.update(files)

    await refresh_integration_sdk(SimpleNamespace(
        snapshot_files=AsyncMock(return_value=dict(tree)), stage_patch=stage,
    ))
    after = typecheck()
    assert after.returncode == 0, after.stdout + after.stderr


@pytest.mark.parametrize("fail_delivery", [False, True])
async def test_generation_delivers_sdk_before_reading_agent_seed(monkeypatch, fail_delivery):
    tree = {
        SDK_PATH: "old SDK",
        LEGACY_CLIENT_PATH: "old legacy SDK",
        PROVIDER_PATH: "old session bootstrap",
        SUPPORT_PATH: "old support without contact",
        "src/app/page.tsx": "existing product",
        "src/app/api/omnia/events/route.ts": "existing product DB handler",
        ".omnia/cell.json": '{"project":"existing"}',
    }
    assert TELEMETRY_PATH not in tree
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
        assert tree[TELEMETRY_PATH] == _template_file(TELEMETRY_PATH)
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
        assert TELEMETRY_PATH not in tree
        return
    for _ in range(2):
        await runtime._build_agent_seed_parts(handle, refresh_managed_sdk=True)
    canonical = {
        path: _template_file(path)
        for path in (
            *MAX_MANAGEMENT_RUNTIME_FILES, "next.config.ts",
            SDK_PATH, LEGACY_CLIENT_PATH, ACTIVITY_PATH, ANALYTICS_PATH, TELEMETRY_PATH,
            CATALOG_SERVER_PATH, CATALOG_ACTIONS_PATH, CATALOG_CLIENT_PATH,
            PROVIDER_PATH, RENEWAL_PATH, FOOTER_PATH, SUPPORT_PATH
        )
        if path != "drizzle/0003_owner_management.sql"
    }
    assert writes == [(canonical, ())]
    activity_endpoint = re.search(
        r'export async function trackMaxActivity\([\s\S]*?await deliverActivity\("([^"]+)"',
        tree[LEGACY_CLIENT_PATH],
    )
    assert activity_endpoint is not None
    assert f"src/app{activity_endpoint[1]}/route.ts" == TELEMETRY_PATH
    assert tree[TELEMETRY_PATH] == _template_file(TELEMETRY_PATH)
    assert tree["src/app/page.tsx"] == "existing product"
    assert tree["src/app/api/omnia/events/route.ts"] == "existing product DB handler"
    assert tree[".omnia/cell.json"] == '{"project":"existing"}'


async def test_generation_retires_encrypted_crud_files_from_existing_projects():
    from yleum_api.services.max_managed_generation import refresh_integration_sdk
    from yleum_api.services.max_project_kit import MAX_RETIRED_MANAGED_FILES

    canonical = {
        path: _template_file(path)
        for path in (
            *MAX_MANAGEMENT_RUNTIME_FILES, "next.config.ts",
            SDK_PATH, LEGACY_CLIENT_PATH, ACTIVITY_PATH, ANALYTICS_PATH, TELEMETRY_PATH,
            CATALOG_SERVER_PATH, CATALOG_ACTIONS_PATH, CATALOG_CLIENT_PATH,
            PROVIDER_PATH, RENEWAL_PATH, FOOTER_PATH, SUPPORT_PATH
        )
        if path != "drizzle/0003_owner_management.sql"
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
            SDK_PATH, LEGACY_CLIENT_PATH, ACTIVITY_PATH, ANALYTICS_PATH, TELEMETRY_PATH,
            CATALOG_SERVER_PATH, CATALOG_ACTIONS_PATH, CATALOG_CLIENT_PATH,
            PROVIDER_PATH, RENEWAL_PATH, FOOTER_PATH, SUPPORT_PATH, "next.config.ts",
            *(path for path in MAX_MANAGEMENT_RUNTIME_FILES
              if path != "drizzle/0003_owner_management.sql"),
        )
    }
    assert "drizzle/0003_owner_management.sql" not in canonical
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
