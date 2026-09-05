from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from decimal import Decimal
from pathlib import Path
from shutil import which
from subprocess import run
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.max_project_config import MaxProjectConfig
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.usage import Usage
from omnia_api.models.user import User
from omnia_api.routers import max_studio
from omnia_api.schemas.max_studio import (
    MaxContentItem,
    MaxProjectConfigPayload,
    MaxUrlAttachedPayload,
)
from omnia_api.services import max_project_kit as max_project_kit_svc
from omnia_api.services.max_project_kit import (
    MAX_MANAGED_KIT_VERSION,
    MAX_MODEL_LOCKED_FILES,
    MAX_SECURITY_LOCKED_FILES,
    _template_candidates,
    render_max_managed_files,
    render_max_starter_files,
)


def _config() -> MaxProjectConfigPayload:
    return MaxProjectConfigPayload(
        app_name='Кофе "Рядом"',
        app_type="loyalty",
        summary="Баллы, награды и предзаказ",
        primary_action="Заказать кофе",
        features=["Каталог", "Баллы", "Каталог"],
        content=[
            MaxContentItem(
                id="flat-white",
                title="Флэт уайт",
                description="Двойной эспрессо",
                price="290 ₽",
                action_label="Заказать",
            )
        ],
        operator={"legal_name": "ООО Кофе", "inn": "1234567890"},
        support={"email": "help@example.ru"},
        legal={"has_sales": True, "terms_accepted": True},
    )


def test_max_config_normalises_features() -> None:
    assert _config().features == ["Каталог", "Баллы"]


def test_managed_kit_contains_config_and_required_legal_routes() -> None:
    project_id = uuid4()
    files = render_max_managed_files(_config(), project_id)

    assert set(files) == {
        "postcss.config.mjs",
        "src/app/layout.tsx",
        "src/app/api/omnia/health/route.ts",
        "src/components/MaxAppProvider.tsx",
        "src/components/OmniaCompliance.tsx",
        "src/lib/db/index.ts",
        "src/lib/max/bot-api.ts",
        "src/lib/max/bridge.ts",
        "src/lib/max/validate-init-data.ts",
        "src/app/api/max/session/route.ts",
        "src/app/api/max/webhook/route.ts",
        "src/lib/max/session.ts",
        "src/app/api/omnia/preview-session/route.ts",
        "src/app/api/omnia/actions/route.ts",
        "src/app/api/omnia/consents/route.ts",
        "src/app/api/omnia/events/route.ts",
        "src/lib/omnia/max-config.ts",
        "src/lib/omnia/client.ts",
        "src/app/api/omnia/config/route.ts",
        "src/lib/omnia/integration-client.ts",
        "src/app/api/omnia/integrations/[...path]/route.ts",
        "src/app/legal/privacy/page.tsx",
        "src/app/legal/terms/page.tsx",
        "src/app/support/page.tsx",
    }
    config = files["src/lib/omnia/max-config.ts"]
    assert '"app_name": "Кофе \\"Рядом\\""' in config
    assert '"price": "290 ₽"' in config
    assert "export type OmniaMaxConfig" in config
    assert "omniaMaxConfig: OmniaMaxConfig" in config
    assert "max_url_attached" not in config
    assert str(project_id) in files["src/app/api/omnia/integrations/[...path]/route.ts"]
    preview_route = files["src/app/api/omnia/preview-session/route.ts"]
    assert 'process.env.NODE_ENV !== "development"' in preview_route
    assert f'const MANAGED_PROJECT_ID: string = "{project_id}"' in preview_route
    assert 'MANAGED_PROJECT_ID === "__OMNIA_PROJECT_ID__"' in preview_route
    assert "bootstrapMessage(projectId, expires)" in preview_route
    assert "bootstrapMessage(process.env.OMNIA_PROJECT_ID, expires)" not in preview_route
    assert "partitioned: true" in preview_route
    assert "PREVIEW_SESSION_MAX_AGE_SECONDS" in preview_route
    assert 'headers: { Location: "/" }' in preview_route
    assert 'NextResponse.redirect(new URL("/", request.url))' not in preview_route
    assert "options: { maxAge?: number } = {}" in files["src/lib/max/session.ts"]
    session = files["src/lib/max/session.ts"]
    assert 'const MAX_INIT_DATA_HEADER = "x-omnia-max-init-data"' in session
    assert "validateMaxInitData(initData, token)" in session
    assert "if (cookieUser) return cookieUser" in session
    provider = files["src/components/MaxAppProvider.tsx"]
    assert 'state.mode === "loading" || state.mode === "error"' in provider
    assert "<AuthScreen" in provider
    assert 'const MAX_INIT_DATA_HEADER = "X-Omnia-MAX-Init-Data"' in provider
    assert "installAuthenticatedFetch(webApp.initData)" in provider
    assert "requestUrl.origin !== window.location.origin" in provider
    assert '!requestUrl.pathname.startsWith("/api/")' in provider
    assert 'from "@/components/OmniaCompliance"' in provider
    assert "src/components/OmniaCompliance.tsx" in files
    assert 'from "@/lib/omnia/max-config"' in files["src/components/OmniaCompliance.tsx"]
    validator = files["src/lib/max/validate-init-data.ts"]
    assert 'typeof value.id === "string"' in validator
    assert "timingSafeEqual" in validator
    session_route = files["src/app/api/max/session/route.ts"]
    assert 'console.warn("[max-auth] rejected launch data"' in session_route
    assert "length: initData.length" in session_route
    assert 'sameSite: "lax"' in session_route
    assert 'sameSite: "none"' not in session_route


def test_managed_config_sync_never_overwrites_project_dependencies_or_schema() -> None:
    files = render_max_managed_files(_config(), uuid4())

    assert "package.json" not in files
    assert "pnpm-lock.yaml" not in files
    assert "src/lib/db/schema.ts" not in files


def test_managed_kit_refuses_a_missing_platform_component(monkeypatch) -> None:
    original = max_project_kit_svc._template_file

    def provider_with_missing_component(relative_path: str) -> str:
        if relative_path == "src/components/MaxAppProvider.tsx":
            return 'import { OmniaMissing } from "@/components/OmniaMissing";'
        return original(relative_path)

    monkeypatch.setattr(max_project_kit_svc, "_template_file", provider_with_missing_component)

    with pytest.raises(RuntimeError, match=r"src/components/OmniaMissing\.tsx"):
        render_max_managed_files(_config())


def test_template_lookup_does_not_depend_on_repository_depth() -> None:
    candidates = _template_candidates(
        "missing.ts",
        Path("/app/src/omnia_api/services/max_project_kit.py"),
    )

    assert len(candidates) >= 2
    assert (
        candidates[0].as_posix().endswith("/orchestrator/templates/max-miniapp-nextjs/missing.ts")
    )


def test_managed_kit_never_contains_model_or_generation_calls() -> None:
    combined = "\n".join(render_max_managed_files(_config()).values()).lower()

    assert "openai" not in combined
    assert "llmgw" not in combined
    assert "/chat/completions" not in combined
    assert "generate(" not in combined


def test_starter_kit_has_no_product_page_or_visual_template() -> None:
    files = render_max_starter_files(_config(), uuid4())

    assert "src/app/page.tsx" not in files
    assert "src/app/api/omnia/health/route.ts" in files
    assert "src/app/globals.css" in files
    assert "src/app/layout.tsx" in files
    assert "package.json" in files
    assert "pnpm-lock.yaml" in files
    assert "src/lib/db/schema.ts" in files
    css = files["src/app/globals.css"]
    assert '@import "tailwindcss"' in css
    assert "generation-canvas" not in css
    assert "canvas-" not in css
    assert "feature-grid" not in css
    assert "TODO" not in "\n".join(files.values())


@pytest.mark.parametrize(
    "app_type",
    ["loyalty", "catalog", "booking", "event", "education", "custom"],
)
def test_every_max_starter_overlay_keeps_portable_machine_tasks_executable(
    app_type: str, tmp_path: Path
) -> None:
    """The API-owned overlay is the last writer over the portable machine seed."""
    apps_root = Path(__file__).parents[2]
    orchestrator_source = apps_root / "orchestrator" / "src"
    template = apps_root / "orchestrator" / "templates" / "max-miniapp-nextjs"
    template_files = {
        path.relative_to(template).as_posix(): path.read_text(encoding="utf-8")
        for path in template.rglob("*")
        if path.is_file() and not {"node_modules", ".next", ".git"}.intersection(path.parts)
    }
    machine_defaults_path = (
        orchestrator_source / "omnia_orchestrator" / "services" / "machine_defaults.py"
    )
    spec = importlib.util.spec_from_file_location(
        "max_starter_machine_defaults", machine_defaults_path
    )
    assert spec is not None and spec.loader is not None
    machine_defaults = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(orchestrator_source))
    try:
        spec.loader.exec_module(machine_defaults)
    finally:
        sys.path.pop(0)
    assert Path(machine_defaults.__file__).resolve() == machine_defaults_path.resolve()

    final_files = {
        **machine_defaults.next_machine_seed(template_files),
        **render_max_starter_files(
            MaxProjectConfigPayload(
                app_name="Portable MAX",
                app_type=app_type,
                summary="Contract fixture",
            ),
            uuid4(), portable=True,
        ),
    }
    manifest = machine_defaults.MachineManifest.from_files(final_files)

    assert manifest is not None
    scripts = json.loads(final_files["package.json"])["scripts"]
    assert {"dev", "build", "start", "test", "typecheck"}.issubset(scripts)
    for task in manifest.tasks:
        assert task.argv[:1] == ["pnpm"]
        if task.argv[1] == "install":
            assert task.argv == ["pnpm", "install", "--frozen-lockfile"]
        else:
            assert task.argv[1] in scripts
    assert manifest.services[0].argv == ["pnpm", "start"]
    assert "start" in scripts
    assert "tests/starter.test.mjs" in final_files
    # All six MAX app types share this one physical starter. Execute the final
    # materialized test task once; the parametrized checks above still prove
    # that every variant resolves the same machine contract.
    if app_type != "custom":
        return
    for relative_path, content in final_files.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    test_task = next(task for task in manifest.tasks if task.argv[1] == "test")
    pnpm = which(test_task.argv[0])
    assert pnpm is not None
    result = run(
        [pnpm, *test_task.argv[1:]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "MAX starter package satisfies portable machine contract" in result.stdout


def test_managed_kit_exposes_secretless_google_ai_runtime_primitive() -> None:
    files = render_max_managed_files(_config(), uuid4())
    starter = render_max_starter_files(_config(), uuid4())
    client = files["src/lib/omnia/integration-client.ts"]
    proxy = files["src/app/api/omnia/integrations/[...path]/route.ts"]
    actions_route = files["src/app/api/omnia/actions/route.ts"]

    assert "requestOmniaAI" in client
    assert "input.message || input.prompt" in client
    assert "text: result.answer" in client
    assert "createMaxAction" in client
    assert "getMaxActions" in client
    assert "MaxActionHistoryPage" in client
    assert "nextCursor: string | null" in client
    assert 'params.set("limit"' in client
    assert 'params.set("cursor"' in client
    assert 'fetch(`/api/omnia/actions${query ? `?${query}` : ""}`' in client
    assert '"lucide-react": "^0.469.0"' in starter["package.json"]
    assert '"tailwindcss": "^4.0.0"' in starter["package.json"]
    assert '"catalog", "ai"' in proxy
    assert "/api/runtime/projects/${PROJECT_ID}/ai" in proxy
    assert "api_key" not in client.lower()
    assert "MAX_ACTION_LIMIT = 1000" in actions_route
    assert "MAX_ACTION_PAYLOAD_BYTES = 262_144" in actions_route
    assert "nextCursor" in actions_route
    assert "Invalid action cursor" in actions_route
    assert "TextEncoder" in actions_route


def test_max_lock_sets_expand_when_attested_shell_is_unavailable() -> None:
    assert "src/lib/db/schema.ts" not in MAX_SECURITY_LOCKED_FILES
    assert "package.json" not in MAX_SECURITY_LOCKED_FILES
    assert "pnpm-lock.yaml" not in MAX_SECURITY_LOCKED_FILES
    assert "postcss.config.mjs" in MAX_SECURITY_LOCKED_FILES
    assert "next.config.ts" in MAX_SECURITY_LOCKED_FILES
    assert "drizzle.config.ts" in MAX_SECURITY_LOCKED_FILES
    assert "Dockerfile.dev" in MAX_SECURITY_LOCKED_FILES
    assert "Dockerfile.prod" in MAX_SECURITY_LOCKED_FILES
    assert "scripts/apply-migrations.mjs" in MAX_SECURITY_LOCKED_FILES

    assert "src/lib/db/schema.ts" in MAX_MODEL_LOCKED_FILES
    assert "package.json" in MAX_MODEL_LOCKED_FILES
    assert "pnpm-lock.yaml" in MAX_MODEL_LOCKED_FILES
    assert "postcss.config.mjs" in MAX_MODEL_LOCKED_FILES


def test_max_readiness_ignores_empty_service_snapshot_prompts() -> None:
    source = inspect.getsource(max_studio.get_max_readiness)

    assert "func.length(func.trim(Snapshot.prompt_text)) > 0" in source


def test_max_readiness_requires_token_for_init_data_but_not_webhook() -> None:
    source = inspect.getsource(max_studio.get_max_readiness)

    assert 'id="bot"' in source
    assert 'id="webhook"' not in source
    assert 'id="max_url"' in source


async def test_config_save_is_versioned_and_idempotent(db_session, monkeypatch) -> None:
    user = User(email=f"max-{uuid4()}@example.ru")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="Кофе Рядом",
        slug=f"max-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    initial = Snapshot(
        project_id=project.id,
        commit_sha="1" * 40,
        prompt_text="Собери приложение",
    )
    db_session.add(initial)
    await db_session.flush()
    project.current_snapshot_id = initial.id
    await db_session.commit()

    calls: list[dict[str, str]] = []

    def fake_commit(project_id, files, message, parent_sha):
        calls.append(
            {
                "project_id": str(project_id),
                "message": message,
                "parent_sha": parent_sha,
                "config": files["src/lib/omnia/max-config.ts"],
            }
        )
        return "2" * 40

    async def stopped(_project_id):
        return {"state": "stopped"}

    async def not_deployed(_project_id):
        return {"phase": "queued"}

    proof_refreshes: list[str] = []

    async def refresh_proof(_session, project):
        proof_refreshes.append(str(project.current_snapshot_id))

    monkeypatch.setattr(max_studio.repo_svc, "commit_files", fake_commit)
    monkeypatch.setattr(max_studio.orchestrator_client, "get_status", stopped)
    monkeypatch.setattr(max_studio.orchestrator_client, "get_deploy", not_deployed)
    monkeypatch.setattr(max_studio, "_refresh_release_proof", refresh_proof)

    first = await max_studio.put_max_config(project.id, _config(), db_session, user)
    second = await max_studio.put_max_config(project.id, _config(), db_session, user)

    assert first.config_version == 1
    assert second.synced_snapshot_id == first.synced_snapshot_id
    assert len(calls) == 1
    assert calls[0]["parent_sha"] == "1" * 40
    saved = await db_session.get(MaxProjectConfig, project.id)
    assert saved is not None
    assert saved.config["app_name"] == 'Кофе "Рядом"'
    assert saved.managed_kit_version == MAX_MANAGED_KIT_VERSION
    assert project.current_snapshot_id == saved.synced_snapshot_id
    assert proof_refreshes == [
        str(first.synced_snapshot_id),
        str(first.synced_snapshot_id),
    ]

    # A project carrying an older managed kit is upgraded once even when its
    # business config and current snapshot are otherwise unchanged.
    saved.managed_kit_version = MAX_MANAGED_KIT_VERSION - 1
    await db_session.commit()
    upgraded = await max_studio.put_max_config(project.id, _config(), db_session, user)
    repeated_after_upgrade = await max_studio.put_max_config(
        project.id, _config(), db_session, user
    )
    refreshed = await db_session.get(MaxProjectConfig, project.id)

    assert len(calls) == 2
    assert refreshed is not None
    assert refreshed.managed_kit_version == MAX_MANAGED_KIT_VERSION
    assert repeated_after_upgrade.synced_snapshot_id == upgraded.synced_snapshot_id
    assert proof_refreshes[-2:] == [
        str(upgraded.synced_snapshot_id),
        str(upgraded.synced_snapshot_id),
    ]


async def test_url_confirmation_is_persisted_without_new_snapshot(db_session) -> None:
    user = User(email=f"max-url-{uuid4()}@example.ru")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="MAX URL",
        slug=f"max-url-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    initial = Snapshot(
        project_id=project.id,
        commit_sha="3" * 40,
        prompt_text="Собери приложение",
    )
    db_session.add(initial)
    await db_session.flush()
    project.current_snapshot_id = initial.id
    await db_session.commit()

    before = int(
        (
            await db_session.execute(
                select(func.count(Snapshot.id)).where(Snapshot.project_id == project.id)
            )
        ).scalar_one()
    )
    saved = await max_studio.patch_max_url_attached(
        project.id,
        MaxUrlAttachedPayload(attached=True),
        db_session,
        user,
    )
    repeated = await max_studio.patch_max_url_attached(
        project.id,
        MaxUrlAttachedPayload(attached=True),
        db_session,
        user,
    )
    after = int(
        (
            await db_session.execute(
                select(func.count(Snapshot.id)).where(Snapshot.project_id == project.id)
            )
        ).scalar_one()
    )

    assert saved.config.max_url_attached is True
    assert saved.config_version == 1
    assert repeated.config_version == 1
    assert saved.synced_snapshot_id is None
    assert project.current_snapshot_id == initial.id
    assert before == after == 1


async def test_max_usage_groups_actual_gateway_ledger_by_latest_run(db_session) -> None:
    user = User(email=f"max-usage-{uuid4()}@example.ru")
    db_session.add(user)
    await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="MAX Usage",
        slug=f"max-usage-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=user.id,
        idempotency_key=str(uuid4()),
        prompt_hash="a" * 64,
        status="running",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        [
            Usage(
                user_id=user.id,
                project_id=project.id,
                run_id=run.id,
                model_id="gemini-3.1-pro-preview-customtools",
                tokens_in=1_000,
                tokens_out=100,
                cost_rub=Decimal("2.5000"),
                stage="build_plan",
                cache_read_tokens=600,
                cache_write_tokens=200,
                retry_count=1,
            ),
            Usage(
                user_id=user.id,
                project_id=project.id,
                run_id=run.id,
                model_id="gemini-3.1-pro-preview-customtools",
                tokens_in=2_000,
                tokens_out=200,
                cost_rub=Decimal("5.2500"),
                stage="native_agent",
                cache_read_tokens=1_500,
                retry_count=2,
            ),
        ]
    )
    await db_session.commit()

    result = await max_studio.get_max_usage(project.id, db_session, user)

    assert result.run_id == run.id
    assert result.run_status == "running"
    assert result.run_cost_rub == result.total_cost_rub == 7.75
    stages = {stage.id: stage for stage in result.stages}
    assert stages["template"].cost_rub == 0
    assert stages["build_plan"].cache_read_tokens == 600
    assert stages["native_agent"].calls == 1
    assert stages["native_agent"].retries == 2

@pytest.mark.parametrize("portable", [False, True])
def test_starter_session_uses_only_its_runtime_auth_boundary(portable: bool) -> None:
    """Execute the shipped TypeScript helper without a signing key or browser cookie."""
    project_id = uuid4()
    files = render_max_starter_files(_config(), project_id, portable=portable)
    source = files["src/lib/max/session.ts"]
    script = r"""
const {stripTypeScriptTypes} = require('node:module');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let incoming = new Headers();
const exportsFor = {
 'node:crypto': {createHmac: crypto.createHmac, timingSafeEqual: crypto.timingSafeEqual},
 'next/headers': {headers: async () => incoming, cookies: async () => ({get: () => undefined})},
 '@/lib/max/validate-init-data': {validateMaxInitData: () => {
  throw Error('unexpected MAX token validation')
 }}
};
(async () => {
 const context = vm.createContext({Buffer, process: {env: {}}, console});
 const mod = new vm.SourceTextModule(stripTypeScriptTypes(input.source), {context});
 await mod.link(async name => {
  const values = exportsFor[name];
  assert.ok(values, name);
  return new vm.SyntheticModule(Object.keys(values), function() {
   for (const [key, value] of Object.entries(values)) this.setExport(key, value);
  }, {context});
 });
 await mod.evaluate();
 const trusted = {'x-omnia-user-id':'owner-123','x-omnia-project-id':input.project,
                  'x-omnia-session-epoch':'7'};
 incoming = new Headers(trusted);
 const user = await mod.namespace.getMaxUser();
 if (input.portable) assert.equal(user.id, 'owner-123');
 else assert.equal(user, null); // Legacy routes must never trust client-supplied identity.
 for (const changes of [
  {'x-omnia-user-id':''}, {'x-omnia-project-id':'another-project'},
  {'x-omnia-session-epoch':''}, {'x-omnia-session-epoch':'invalid'},
  {'x-omnia-session-epoch':'-1'}
 ]) {
  incoming = new Headers({...trusted, ...changes});
  assert.equal(await mod.namespace.getMaxUser(), null);
 }
 incoming = new Headers();
 assert.equal(await mod.namespace.getMaxUser(), null);
})().catch(error => {console.error(error); process.exitCode = 1});
"""
    result = run(
        ["node", "--experimental-vm-modules", "-e", script],
        input=json.dumps({"source": source, "project": str(project_id), "portable": portable}),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_portable_starter_requires_project_identity() -> None:
    with pytest.raises(ValueError, match="requires a project identity"):
        render_max_starter_files(_config(), portable=True)


def test_preseeded_portable_manifest_satisfies_real_completion_without_agent_rewrite():
    from omnia_api.services.max_generation_contract import max_source_completion_gap

    starter = render_max_starter_files(_config(), uuid4(), portable=True)
    manifest = json.dumps({
        "version": 1,
        "tasks": [{"name": "final-test", "role": "full_build", "argv": ["pnpm", "test"]}],
        "services": [{"name": "web"}],
        "routes": [{"path": "/", "service": "web", "port": 3000}],
    })
    old_page = "export default function Page(){return <main>Old user interface</main>}"
    workspace = {".omnia/cell.json": manifest, "src/app/page.tsx": old_page,
                 "src/app/api/old/route.ts": "export const GET = () => new Response('old')"}
    seed = max_project_kit_svc.include_portable_manifest(starter, workspace)
    assert "src/app/page.tsx" not in seed
    assert "src/app/api/old/route.ts" not in seed
    authored = {"src/app/page.tsx": (
        "export default function Page(){return <main>Authored warehouse product</main>}"
    )}
    assert max_source_completion_gap("Build warehouse", {**seed, **authored}, portable=True) is None
    for changed_manifest in ("", "{}", "broken"):
        assert max_source_completion_gap(
            "Build warehouse", {**seed, **authored, ".omnia/cell.json": changed_manifest},
            portable=True,
        ) is not None
    missing_seed = max_project_kit_svc.include_portable_manifest(starter, {})
    assert ".omnia/cell.json" not in missing_seed
    assert max_source_completion_gap(
        "Build warehouse", {**missing_seed, **authored}, portable=True,
    ) is not None
