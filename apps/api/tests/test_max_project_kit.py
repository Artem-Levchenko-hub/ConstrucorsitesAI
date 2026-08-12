from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from omnia_api.core.errors import ApiError
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
    MAX_MODEL_DIRECTIVE,
    MAX_PRODUCT_ENTRY_PATH,
    MAX_PRODUCT_PAGE_PATH,
    MAX_PRODUCT_RUNTIME_PATH,
    _template_candidates,
    max_history_product_files,
    max_legacy_server_file_deletions,
    max_legacy_snapshot_incompatibility,
    max_model_path_rejection,
    max_project_config_from_files,
    render_max_entry_migration_files,
    render_max_history_files,
    render_max_managed_files,
    render_max_restored_files,
)


@pytest.mark.parametrize(
    "path",
    [
        "./src/lib/omnia/integration-client.ts",
        "src/app/../lib/omnia/integration-client.ts",
        "src//lib/omnia/integration-client.ts",
        "src\\lib\\omnia\\integration-client.ts",
        "/src/app/page.tsx",
        ".",
    ],
)
def test_max_model_write_paths_must_be_canonical(path: str) -> None:
    assert max_model_path_rejection(path)


def test_max_model_write_path_accepts_normal_product_file() -> None:
    assert max_model_path_rejection(MAX_PRODUCT_ENTRY_PATH) is None


@pytest.mark.parametrize(
    "path",
    [
        "src/app/page.tsx",
        "src/app/api/debug/route.ts",
        "src/middleware.ts",
        "src/lib/db-copy.ts",
    ],
)
def test_max_model_write_path_blocks_server_execution(path: str) -> None:
    assert max_model_path_rejection(path)


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


def test_history_renderer_keeps_product_files_but_drops_managed_core() -> None:
    assert max_history_product_files(
        {
            "src/app/page.tsx": "export default function Page() {}",
            "src/app/layout.tsx": "old insecure layout",
            "src/lib/max/session.ts": "old live auth",
            "src/lib/omnia/legacy-control.ts": "old platform helper",
            "src/app/api/orders/route.ts": "old arbitrary api",
            "src/app/api/custom/score/route.ts": "isolated product api",
            "src/middleware.ts": "old middleware",
            "src/instrumentation.ts": "old instrumentation",
            "public/omnia-inspector.js": "old inspector",
            "docker-entrypoint.sh": "old entrypoint",
            "next.config.ts": "old config",
            "scripts/rewrite-runtime.sh": "old script",
            "../escape.ts": "nope",
        }
    ) == {MAX_PRODUCT_ENTRY_PATH: "export default function Page() {}"}

    runtime_files = render_max_history_files(
        {
            "src/app/page.tsx": "historical product",
            "src/lib/max/session.ts": "old live auth",
        },
        _config(),
        "00000000-0000-0000-0000-000000000001",
    )
    assert runtime_files[MAX_PRODUCT_PAGE_PATH] != "historical product"
    assert runtime_files[MAX_PRODUCT_ENTRY_PATH] == "historical product"
    assert "OmniaProductRuntime" in runtime_files[MAX_PRODUCT_PAGE_PATH]
    assert runtime_files["src/lib/max/session.ts"] != "old live auth"
    assert runtime_files["public/omnia-inspector.js"] != "old inspector"
    assert "Кофе" in runtime_files["src/lib/omnia/max-config.ts"]


def test_empty_initial_history_restores_neutral_generation_canvas() -> None:
    runtime_files = render_max_history_files({}, _config(), uuid4())
    config_only_files = render_max_history_files(
        {"src/lib/omnia/max-config.ts": "managed config only"},
        _config(),
        uuid4(),
    )

    assert 'data-max-product-canvas="empty"' in runtime_files[MAX_PRODUCT_ENTRY_PATH]
    assert '@import "tailwindcss"' in runtime_files["src/app/globals.css"]
    assert 'data-max-product-canvas="empty"' in config_only_files[MAX_PRODUCT_ENTRY_PATH]


def test_history_preserves_every_current_model_owned_product_artifact() -> None:
    snapshot = {
        MAX_PRODUCT_ENTRY_PATH: '"use client"; export default function ProductApp() {}',
        ".omnia/max-design-spec.json": '{"chosen_direction":"editorial"}',
        "public/product/worker.js": "self.onmessage = () => {};",
        "public/product/model.wasm": "binary-placeholder",
    }

    assert max_legacy_snapshot_incompatibility(snapshot) is None
    product = max_history_product_files(snapshot)
    assert product[".omnia/max-design-spec.json"] == snapshot[".omnia/max-design-spec.json"]
    assert product["public/product/worker.js"] == snapshot["public/product/worker.js"]
    assert product["public/product/model.wasm"] == snapshot["public/product/model.wasm"]


def test_history_refuses_legacy_public_executable_outside_product_root() -> None:
    snapshot = {"public/legacy-worker.js": "self.onmessage = () => {};"}

    assert max_legacy_snapshot_incompatibility(snapshot)
    with pytest.raises(ValueError, match="cannot be restored safely"):
        render_max_history_files(snapshot, _config(), uuid4())


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "src/lib/omnia/custom.ts",
        "src/lib/max/custom.ts",
        "src/lib/db/custom.ts",
    ],
)
def test_history_refuses_unknown_helpers_inside_platform_prefixes(unsafe_path: str) -> None:
    snapshot = {
        MAX_PRODUCT_ENTRY_PATH: (
            f'import {{ helper }} from "@/{unsafe_path.removeprefix("src/").removesuffix(".ts")}"; '
            "export default function ProductApp() { return <p>{helper}</p>; }"
        ),
        unsafe_path: "export const helper = 'legacy';",
    }

    assert max_legacy_snapshot_incompatibility(snapshot)
    with pytest.raises(ValueError, match="cannot be restored safely"):
        render_max_history_files(snapshot, _config(), uuid4())


def test_history_renderer_uses_config_committed_with_snapshot() -> None:
    historical = _config().model_copy(update={"app_name": "Исторический бренд"})
    source = render_max_managed_files(historical)["src/lib/omnia/max-config.ts"]

    parsed = max_project_config_from_files({"src/lib/omnia/max-config.ts": source})

    assert parsed is not None
    assert parsed.app_name == "Исторический бренд"
    assert max_project_config_from_files({"src/lib/omnia/max-config.ts": "invalid"}) is None


def test_max_restore_combines_historical_product_with_current_platform() -> None:
    restored = render_max_restored_files(
        {
            "src/app/page.tsx": "historical page",
            "docker-entrypoint.sh": "historical entrypoint",
        },
        {
            "src/app/page.tsx": "current page",
            "docker-entrypoint.sh": "current entrypoint",
            "next.config.ts": "current next config",
            "src/middleware.ts": "current untrusted middleware",
            "src/instrumentation.ts": "current untrusted instrumentation",
            "src/app/api/private/route.ts": "current untrusted api",
        },
        _config(),
        "00000000-0000-0000-0000-000000000001",
    )

    assert restored[MAX_PRODUCT_PAGE_PATH] != "historical page"
    assert restored[MAX_PRODUCT_ENTRY_PATH] == "historical page"
    assert restored["docker-entrypoint.sh"] != "current entrypoint"
    assert restored["next.config.ts"] != "current next config"
    assert restored["docker-entrypoint.sh"] == max_project_kit_svc._template_file(
        "docker-entrypoint.sh"
    )
    assert restored["next.config.ts"] == max_project_kit_svc._template_file("next.config.ts")
    assert "src/middleware.ts" not in restored
    assert "src/instrumentation.ts" not in restored
    assert "src/app/api/private/route.ts" not in restored


def test_managed_kit_contains_config_and_required_legal_routes() -> None:
    project_id = uuid4()
    files = render_max_managed_files(_config(), project_id)

    assert set(files) == {
        "package.json",
        "pnpm-lock.yaml",
        "next.config.ts",
        "postcss.config.mjs",
        "tsconfig.json",
        "public/omnia-inspector.js",
        MAX_PRODUCT_PAGE_PATH,
        "src/app/layout.tsx",
        "src/app/max-runtime.css",
        "src/components/MaxAppProvider.tsx",
        "src/components/OmniaCompliance.tsx",
        MAX_PRODUCT_RUNTIME_PATH,
        "src/lib/db/index.ts",
        "src/lib/db/schema.ts",
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
        "src/lib/omnia/max-ui-compat.ts",
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
    assert "<OmniaCompliance fallback" in provider
    assert 'className="omnia-max-runtime"' in provider
    assert "data-max-platform={appearance.platform}" in provider
    assert "next/dynamic" not in provider
    assert "legacyMaxUiEnabled" in provider
    assert "function LegacyMaxUiBoundary" in provider
    assert "if (!hydrated) return children" in provider
    assert "useEffect(() => setHydrated(true), [])" in provider
    assert 'import "@maxhub/max-ui/dist/styles.css"' in files["src/app/layout.tsx"]
    assert "export const legacyMaxUiEnabled = false;" in files["src/lib/omnia/max-ui-compat.ts"]
    # Dormant dependency stays pinned only so historical products that imported
    # it still compile; new runtime/prompt code never applies its visual system.
    assert '"@maxhub/max-ui": "0.2.0"' in files["package.json"]
    assert "src/components/OmniaCompliance.tsx" in files
    compliance = files["src/components/OmniaCompliance.tsx"]
    assert "data-omnia-native-legal-nav" in compliance
    assert "<details" in compliance
    assert "<footer" not in compliance
    assert "src/app/max-runtime.css" in files
    assert "display: contents" in files["src/app/max-runtime.css"]
    assert 'import "./max-runtime.css"' in files["src/app/layout.tsx"]
    assert 'data-omnia-product-runtime="true"' in files[MAX_PRODUCT_RUNTIME_PATH]
    assert 'style={{ display: "contents" }}' in files[MAX_PRODUCT_RUNTIME_PATH]
    product_runtime = files[MAX_PRODUCT_RUNTIME_PATH]
    assert 'from "next/dynamic"' not in product_runtime
    assert 'from "@/components/product/ProductApp"' not in product_runtime
    assert 'require("@/components/product/ProductApp")' in product_runtime
    assert "setProductApp(() => productModule.default)" in product_runtime
    assert "{ProductApp ? <ProductApp /> : null}" in product_runtime
    assert '"@/*": ["./src/*"]' in files["tsconfig.json"]
    validator = files["src/lib/max/validate-init-data.ts"]
    assert 'typeof value.id === "string"' in validator
    assert "timingSafeEqual" in validator
    session_route = files["src/app/api/max/session/route.ts"]
    assert 'console.warn("[max-auth] rejected launch data"' in session_route
    assert "length: initData.length" in session_route
    assert 'sameSite: "lax"' in session_route
    assert 'sameSite: "none"' not in session_route


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


def test_entry_migration_preserves_legacy_product_behind_locked_runtime() -> None:
    legacy = '"use client"; export default function Page() { return <main>Legacy</main>; }'

    files = render_max_entry_migration_files({MAX_PRODUCT_PAGE_PATH: legacy})

    assert files[MAX_PRODUCT_ENTRY_PATH] == legacy
    assert files[MAX_PRODUCT_PAGE_PATH] != legacy
    assert "OmniaProductRuntime" in files[MAX_PRODUCT_PAGE_PATH]
    assert 'require("@/components/product/ProductApp")' in files[MAX_PRODUCT_RUNTIME_PATH]
    assert "useEffect" in files[MAX_PRODUCT_RUNTIME_PATH]


def test_entry_migration_does_not_create_a_null_product() -> None:
    files = render_max_entry_migration_files({})

    assert MAX_PRODUCT_ENTRY_PATH not in files


def test_legacy_null_product_restores_neutral_canvas() -> None:
    legacy_null = """"use client";

// Safe fallback used only when a historical snapshot has no product entry.
export default function ProductApp() {
  return null;
}
"""

    product = max_history_product_files({MAX_PRODUCT_ENTRY_PATH: legacy_null})
    rendered = render_max_history_files(
        {MAX_PRODUCT_ENTRY_PATH: legacy_null},
        _config(),
        uuid4(),
    )

    assert MAX_PRODUCT_ENTRY_PATH not in product
    assert 'data-max-product-canvas="empty"' in rendered[MAX_PRODUCT_ENTRY_PATH]


def test_legacy_max_ui_provider_is_enabled_only_for_historical_imports() -> None:
    legacy = {
        MAX_PRODUCT_ENTRY_PATH: (
            'import { Button } from "@maxhub/max-ui"; '
            "export default function ProductApp() { return <Button>Go</Button>; }"
        )
    }
    custom = {
        MAX_PRODUCT_ENTRY_PATH: (
            "export default function ProductApp() { return <main>Custom</main>; }"
        )
    }

    legacy_files = render_max_history_files(legacy, _config(), uuid4())
    custom_files = render_max_history_files(custom, _config(), uuid4())
    synced_legacy = max_studio._max_config_sync_files(_config(), uuid4(), legacy)

    assert (
        "export const legacyMaxUiEnabled = true;" in legacy_files["src/lib/omnia/max-ui-compat.ts"]
    )
    assert (
        "export const legacyMaxUiEnabled = false;" in custom_files["src/lib/omnia/max-ui-compat.ts"]
    )
    assert (
        "export const legacyMaxUiEnabled = true;" in synced_legacy["src/lib/omnia/max-ui-compat.ts"]
    )


def test_legacy_server_cleanup_covers_all_next_execution_entrypoints() -> None:
    files = max_legacy_server_file_deletions(
        {
            "src/app/api/custom/route.ts": "route",
            "src/app/dashboard/page.tsx": "page",
            "app/api/leak/route.ts": "shadow route",
            "src/pages/api/legacy.ts": "handler",
            "src/instrumentation.ts": "register()",
            "src/proxy.ts": "proxy()",
            "next.config.js": "module.exports = {}",
            "src/app/api/health/route.ts": "platform health",
            "src/app/globals.css": "body {}",
        }
    )

    assert files["src/app/api/custom/route.ts"] == ""
    assert files["src/app/dashboard/page.tsx"] == ""
    assert files["app/api/leak/route.ts"] == ""
    assert files["src/pages/api/legacy.ts"] == ""
    assert files["src/instrumentation.ts"] == ""
    assert files["src/proxy.ts"] == ""
    assert files["next.config.js"] == ""
    assert "src/app/api/health/route.ts" not in files
    assert "src/app/globals.css" not in files


def test_entry_migration_refuses_incompatible_tree_before_writing() -> None:
    with pytest.raises(ValueError, match="cannot be migrated safely"):
        render_max_entry_migration_files(
            {
                MAX_PRODUCT_PAGE_PATH: (
                    'import Widget from "./Widget"; '
                    "export default function Page() { return <Widget />; }"
                ),
                "src/app/Widget.tsx": "export default function Widget() {}",
            }
        )


def test_config_sync_refuses_lossy_legacy_migration_before_commit() -> None:
    with pytest.raises(ApiError) as raised:
        max_studio._max_config_sync_files(
            _config(),
            uuid4(),
            {
                MAX_PRODUCT_PAGE_PATH: (
                    'import Dashboard from "./Dashboard"; '
                    "export default function Page() { return <Dashboard />; }"
                ),
                "src/app/dashboard/page.tsx": "export default function Dashboard() {}",
            },
        )

    assert raised.value.status_code == 409


@pytest.mark.parametrize(
    "snapshot",
    [
        {"src/app/dashboard/page.tsx": "export default function Dashboard() {}"},
        {"src/app/api/custom/route.ts": "export function GET() {}"},
        {"src/lib/helpers.ts": "export const value = 1"},
        {
            MAX_PRODUCT_PAGE_PATH: (
                'import Dashboard from "./Dashboard"; '
                "export default function Page() { return <Dashboard />; }"
            )
        },
    ],
)
def test_history_refuses_lossy_legacy_product_restore(snapshot: dict[str, str]) -> None:
    assert max_legacy_snapshot_incompatibility(snapshot)
    with pytest.raises(ValueError, match="cannot be restored safely"):
        render_max_history_files(snapshot, _config(), uuid4())


def test_history_refuses_legacy_product_server_action() -> None:
    snapshot = {
        "src/app/page.tsx": 'export { default } from "@/components/product/ProductApp";',
        "src/components/product/ProductApp.tsx": (
            '"use server";\nexport default async function ProductApp() { return null; }'
        ),
    }

    assert max_legacy_snapshot_incompatibility(snapshot)
    with pytest.raises(ValueError, match="cannot be restored safely"):
        render_max_history_files(snapshot, _config(), uuid4())


@pytest.mark.parametrize(
    "unsafe_path",
    ["app/api/leak/route.ts", "next.config.js", "postcss.config.js"],
)
def test_history_refuses_root_server_and_build_config_bypasses(unsafe_path: str) -> None:
    snapshot = {
        "src/app/page.tsx": "export default function Page() { return null; }",
        unsafe_path: "export default {};",
    }

    assert max_legacy_snapshot_incompatibility(snapshot)


def test_managed_kit_exposes_secretless_google_ai_runtime_primitive() -> None:
    project_id = uuid4()
    files = render_max_managed_files(_config(), project_id)
    client = files["src/lib/omnia/integration-client.ts"]
    proxy = files["src/app/api/omnia/integrations/[...path]/route.ts"]

    assert "requestOmniaAI" in client
    assert "input.message || input.prompt" in client
    assert "text: result.answer" in client
    assert "createMaxAction" in client
    assert "getMaxActions" in client
    assert 'credentials: "include"' in client
    assert 'if (!initData) throw new Error("Откройте приложение внутри MAX")' not in client
    assert '"lucide-react": "^0.469.0"' in files["package.json"]
    assert '"tailwindcss": "^4.0.0"' in files["package.json"]
    assert '"catalog", "ai"' in proxy
    assert "/api/runtime/projects/${PROJECT_ID}/ai" in proxy
    assert '["ai", "status", "catalog"].includes(operation)' in proxy
    assert "if (!initData && !previewAllowed)" in proxy
    assert f'const PROJECT_ID = "{project_id}";' in proxy
    assert "api_key" not in client.lower()


def test_model_directive_is_headless_and_matches_locked_max_runtime_api() -> None:
    assert "MAX HEADLESS PLATFORM ADAPTER" in MAX_MODEL_DIRECTIVE
    assert "first product write" in MAX_MODEL_DIRECTIVE
    assert "src/components/product/ProductApp.tsx" in MAX_MODEL_DIRECTIVE
    assert "@/components/MaxAppProvider" in MAX_MODEL_DIRECTIVE
    assert "firstName" in MAX_MODEL_DIRECTIVE
    assert "languageCode" in MAX_MODEL_DIRECTIVE
    assert "requestOmniaAI({ message, instructions, context })" in MAX_MODEL_DIRECTIVE
    assert "Demo/local data is allowed" in MAX_MODEL_DIRECTIVE
    assert "never import `@maxhub/max-ui`" in MAX_MODEL_DIRECTIVE
    assert "Do not expose credentials" in MAX_MODEL_DIRECTIVE
    assert "required legal footer/marker" in MAX_MODEL_DIRECTIVE
    assert 'data-omnia-native-legal-nav="true"' not in MAX_MODEL_DIRECTIVE


def test_max_readiness_ignores_empty_service_snapshot_prompts() -> None:
    source = inspect.getsource(max_studio.get_max_readiness)

    assert "func.length(func.trim(Snapshot.prompt_text)) > 0" in source


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

    def fake_read(_project_id, _commit_sha):
        return {
            MAX_PRODUCT_PAGE_PATH: (
                '"use client"; export default function Page() { return <main>Coffee</main>; }'
            )
        }

    monkeypatch.setattr(max_studio.repo_svc, "read_files", fake_read)
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
                model_id="claude-sonnet-5",
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
                model_id="claude-sonnet-5",
                tokens_in=2_000,
                tokens_out=200,
                cost_rub=Decimal("5.2500"),
                stage="native_agent",
                cache_read_tokens=1_500,
                retry_count=2,
            ),
            Usage(
                user_id=user.id,
                project_id=project.id,
                run_id=run.id,
                model_id="claude-sonnet-5",
                tokens_in=0,
                tokens_out=0,
                cost_rub=Decimal("80.0000"),
                stage="native_agent:reservation",
                provider_request_id="native-budget-reservation",
            ),
        ]
    )
    await db_session.commit()

    result = await max_studio.get_max_usage(project.id, db_session, user)

    assert result.run_id == run.id
    assert result.run_status == "running"
    assert result.run_cost_rub == result.total_cost_rub == 7.75
    assert result.pending_reservation_rub == 80
    assert result.run_pending_reservation_rub == 80
    assert result.pending_reservation_calls == 1
    stages = {stage.id: stage for stage in result.stages}
    assert stages["template"].cost_rub == 0
    assert stages["build_plan"].cache_read_tokens == 600
    assert stages["native_agent"].calls == 1
    assert stages["native_agent"].retries == 2
