"""Render the Omnia-managed MAX application kit without calling a model."""

# ruff: noqa: E501

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from uuid import UUID

from omnia_api.schemas.max_studio import (
    MaxLegal,
    MaxOperator,
    MaxProjectConfigPayload,
    MaxSupport,
)
from omnia_api.services.secret_safety import max_model_write_rejection

# Increment whenever the managed file set changes in a way that existing MAX
# projects must receive. It deliberately does not follow the public config
# schema version: this is a deployment revision of platform-owned source files.
MAX_MANAGED_KIT_VERSION = 31
_MANAGED_COMPONENT_IMPORT_RE = re.compile(r"""from\s+["']@/components/(Omnia[A-Za-z0-9_/-]+)["']""")

MAX_PRODUCT_ENTRY_PATH = "src/components/product/ProductApp.tsx"
MAX_PRODUCT_PAGE_PATH = "src/app/page.tsx"
MAX_PRODUCT_RUNTIME_PATH = "src/components/OmniaProductRuntime.tsx"

_EMPTY_PRODUCT_ENTRY = """"use client";

// Safe fallback used only when a historical snapshot has no product entry.
export default function ProductApp() {
  return null;
}
"""


def _template_candidates(
    relative_path: str,
    source_file: Path | None = None,
) -> tuple[Path, ...]:
    source = (source_file or Path(__file__)).resolve()
    return (
        Path("/orchestrator/templates/max-miniapp-nextjs") / relative_path,
        *(
            parent / "apps" / "orchestrator" / "templates" / "max-miniapp-nextjs" / relative_path
            for parent in source.parents
        ),
    )


def _template_file(relative_path: str) -> str:
    """Read one platform-owned MAX template file from dev or the API mount."""
    for candidate in _template_candidates(relative_path):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise RuntimeError(f"MAX managed template file is unavailable: {relative_path}")


def _json(config: MaxProjectConfigPayload) -> str:
    return json.dumps(
        config.model_dump(mode="json", exclude={"max_url_attached"}),
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
    )


_CONFIG_TYPES = """export type OmniaMaxContentItem = {
  id: string;
  title: string;
  description: string;
  price: string;
  action_label: string;
  active: boolean;
};

export type OmniaMaxConfig = {
  app_name: string;
  app_type: "loyalty" | "catalog" | "booking" | "event" | "education" | "custom";
  summary: string;
  audience: string;
  primary_action: string;
  features: string[];
  style: "brand" | "clean" | "bright";
  brand_colors: string;
  content: OmniaMaxContentItem[];
  operator: { legal_name: string; inn: string; ogrn: string; address: string };
  support: { email: string | null; phone: string; response_time: string };
  legal: {
    age_rating: "0+" | "6+" | "12+" | "16+" | "18+";
    has_sales: boolean;
    has_user_content: boolean;
    marketing_notifications: boolean;
    personal_data_consent: boolean;
    terms_accepted: boolean;
  };
};
"""


def _validate_managed_component_graph(files: dict[str, str]) -> None:
    """Refuse an incomplete platform kit before it reaches a project snapshot.

    Omnia-prefixed components are owned by the platform, so every such import
    must travel in the same atomic managed-file set. This keeps a future kit
    edit from turning a valid user app into a runtime `Module not found` error.
    """
    required = {
        f"src/components/{component}.tsx"
        for content in files.values()
        for component in _MANAGED_COMPONENT_IMPORT_RE.findall(content)
    }
    missing = sorted(required.difference(files))
    if missing:
        raise RuntimeError(f"MAX managed kit is missing imported files: {', '.join(missing)}")


def render_max_managed_files(
    config: MaxProjectConfigPayload,
    project_id: UUID | str | None = None,
    *,
    legacy_max_ui: bool = False,
) -> dict[str, str]:
    """Files safe to apply to both a starter and an already-generated app."""
    data = _json(config)
    project_literal = json.dumps(str(project_id) if project_id else "")
    preview_session_route = _template_file("src/app/api/omnia/preview-session/route.ts").replace(
        '"__OMNIA_PROJECT_ID__"', project_literal, 1
    )
    integration_proxy_route = _template_file(
        "src/app/api/omnia/integrations/[...path]/route.ts"
    ).replace(
        'const PROJECT_ID = process.env.OMNIA_PROJECT_ID || "";',
        f"const PROJECT_ID = {project_literal};",
        1,
    )
    max_ui_compat = _template_file("src/lib/omnia/max-ui-compat.ts").replace(
        "export const legacyMaxUiEnabled = false;",
        f"export const legacyMaxUiEnabled = {str(legacy_max_ui).lower()};",
        1,
    )
    files = {
        "package.json": _template_file("package.json"),
        "pnpm-lock.yaml": _template_file("pnpm-lock.yaml"),
        "postcss.config.mjs": _template_file("postcss.config.mjs"),
        # MAX source uses the @/* alias throughout the protected runtime.  The
        # base container may predate the MAX template, so its generic tsconfig
        # cannot be trusted to carry that alias into a freshly overlaid starter.
        "tsconfig.json": _template_file("tsconfig.json"),
        "public/omnia-inspector.js": _template_file("public/omnia-inspector.js"),
        MAX_PRODUCT_PAGE_PATH: _template_file(MAX_PRODUCT_PAGE_PATH),
        "src/app/layout.tsx": _template_file("src/app/layout.tsx"),
        "src/app/max-runtime.css": _template_file("src/app/max-runtime.css"),
        "src/components/MaxAppProvider.tsx": _template_file("src/components/MaxAppProvider.tsx"),
        "src/components/OmniaCompliance.tsx": _template_file("src/components/OmniaCompliance.tsx"),
        MAX_PRODUCT_RUNTIME_PATH: _template_file(MAX_PRODUCT_RUNTIME_PATH),
        "src/lib/db/index.ts": _template_file("src/lib/db/index.ts"),
        "src/lib/db/schema.ts": _template_file("src/lib/db/schema.ts"),
        "src/lib/max/bot-api.ts": _template_file("src/lib/max/bot-api.ts"),
        "src/lib/max/bridge.ts": _template_file("src/lib/max/bridge.ts"),
        "src/lib/max/validate-init-data.ts": _template_file("src/lib/max/validate-init-data.ts"),
        "src/app/api/max/session/route.ts": _template_file("src/app/api/max/session/route.ts"),
        "src/app/api/max/webhook/route.ts": _template_file("src/app/api/max/webhook/route.ts"),
        "src/lib/max/session.ts": _template_file("src/lib/max/session.ts"),
        "src/app/api/omnia/preview-session/route.ts": preview_session_route,
        "src/app/api/omnia/actions/route.ts": _template_file("src/app/api/omnia/actions/route.ts"),
        "src/app/api/omnia/consents/route.ts": _template_file(
            "src/app/api/omnia/consents/route.ts"
        ),
        "src/app/api/omnia/events/route.ts": _template_file("src/app/api/omnia/events/route.ts"),
        "src/lib/omnia/client.ts": _template_file("src/lib/omnia/client.ts"),
        "src/lib/omnia/max-ui-compat.ts": max_ui_compat,
        "src/lib/omnia/max-config.ts": (
            "/* Generated by MAX Studio. Edit the business profile in Omnia, not this file. */\n"
            f"{_CONFIG_TYPES}\n"
            f"export const omniaMaxConfig: OmniaMaxConfig = {data};\n"
        ),
        "src/app/api/omnia/config/route.ts": """import { NextResponse } from "next/server";

import { omniaMaxConfig } from "@/lib/omnia/max-config";

export const dynamic = "force-static";

export function GET() {
  return NextResponse.json(omniaMaxConfig, {
    headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=300" },
  });
}
""",
        "src/lib/omnia/integration-client.ts": """\"use client\";

import { getMaxWebApp } from "@/lib/max/bridge";

async function invoke<T>(
  path: "status" | "payments" | "payment-status" | "leads" | "catalog" | "ai",
  payload: Record<string, unknown> = {},
): Promise<T> {
  const initData = getMaxWebApp()?.initData;
  const response = await fetch(`/api/omnia/integrations/${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData, payload }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body?.error?.message || "Интеграция временно недоступна");
  }
  return body as T;
}

export type OmniaIntegrationStatus = {
  providers: string[];
  capabilities: string[];
  analytics_counter_id: string | null;
};

export function getOmniaIntegrations(): Promise<OmniaIntegrationStatus> {
  return invoke("status");
}

export function createOmniaPayment(input: {
  amount: number;
  description: string;
  return_url: string;
  idempotency_key?: string;
  metadata?: Record<string, string>;
  receipt?: Record<string, unknown>;
}): Promise<{ id: string; status: string; confirmation_url: string | null }> {
  return invoke("payments", {
    ...input,
    idempotency_key: input.idempotency_key || crypto.randomUUID(),
  });
}

export function getOmniaPayment(paymentId: string): Promise<{
  id: string;
  status: string;
  confirmation_url: string | null;
}> {
  return invoke("payment-status", { payment_id: paymentId });
}

export function createOmniaLead(input: {
  name: string;
  phone?: string;
  email?: string;
  comment?: string;
  source?: string;
}): Promise<{ provider: string; id: string }> {
  return invoke("leads", input);
}

export function getOmniaCatalog(): Promise<{
  provider: string;
  items: Array<{
    id: string;
    name: string;
    description: string;
    price: number | null;
    currency: string;
    available: boolean;
    image_url: string | null;
  }>;
}> {
  return invoke("catalog");
}

export async function createMaxAction(
  actionType: string,
  payload: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const response = await fetch("/api/omnia/actions", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actionType, payload }),
  });
  if (!response.ok) throw new Error("Action save failed");
  return response.json() as Promise<Record<string, unknown>>;
}

export async function getMaxActions(): Promise<{
  actions: Array<Record<string, unknown>>;
}> {
  const response = await fetch("/api/omnia/actions", { credentials: "include" });
  if (!response.ok) throw new Error("История действий временно недоступна");
  return response.json() as Promise<{ actions: Array<Record<string, unknown>> }>;
}

export const getActionHistory = getMaxActions;

type OmniaAIInput = {
  message?: string;
  prompt?: string;
  instructions?: string;
  context?: Record<string, unknown>;
};

export async function requestOmniaAI(
  input: OmniaAIInput,
): Promise<{ answer: string; text: string; model: string }> {
  const message = input.message || input.prompt;
  if (!message?.trim()) throw new Error("Введите сообщение для ИИ-тренера");
  const result = await invoke<{ answer: string; model: string }>("ai", {
    message,
    instructions: input.instructions,
    context: input.context,
  });
  return { ...result, text: result.answer };
}

export async function trackOmniaGoal(
  goal: string,
  parameters: Record<string, unknown> = {},
): Promise<void> {
  const status = await getOmniaIntegrations();
  const counterId = status.analytics_counter_id;
  if (!counterId || typeof window === "undefined") return;
  const target = window as typeof window & { ym?: (...args: unknown[]) => void };
  if (!target.ym) {
    target.ym = (...args: unknown[]) => {
      (target.ym as unknown as { a?: unknown[] }).a =
        (target.ym as unknown as { a?: unknown[] }).a || [];
      (target.ym as unknown as { a: unknown[] }).a.push(args);
    };
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://mc.yandex.ru/metrika/tag.js";
    document.head.appendChild(script);
    target.ym(Number(counterId), "init", {
      clickmap: true,
      trackLinks: true,
      accurateTrackBounce: true,
    });
  }
  target.ym(Number(counterId), "reachGoal", goal, parameters);
}
""",
        "src/app/api/omnia/integrations/[...path]/route.ts": integration_proxy_route,
        "src/app/legal/privacy/page.tsx": """import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Политика конфиденциальности — ${app.app_name}` };

export default function PrivacyPage() {
  const operator = app.operator.legal_name || app.app_name;
  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Политика конфиденциальности</h1>
      <p><strong>Оператор:</strong> {operator}</p>
      {app.operator.inn && <p><strong>ИНН:</strong> {app.operator.inn}</p>}
      {app.operator.address && <p><strong>Адрес:</strong> {app.operator.address}</p>}
      <h2>Какие данные обрабатываются</h2>
      <p>
        Приложение получает от MAX идентификатор пользователя и доступные данные профиля,
        необходимые для входа и работы функций приложения. Действия, заказы, записи и
        обращения сохраняются только в объёме, необходимом для оказания услуги.
      </p>
      <h2>Цели и срок обработки</h2>
      <p>
        Данные используются для исполнения запросов пользователя, поддержки, безопасности
        и улучшения сервиса. Они хранятся не дольше, чем требуют эти цели и закон.
      </p>
      <h2>Права пользователя</h2>
      <p>
        Пользователь может запросить сведения, исправление или удаление данных через
        страницу поддержки. Согласие на необязательные уведомления можно отозвать.
      </p>
      <p>Возрастная маркировка: {app.legal.age_rating}.</p>
      <p>Дата актуализации: {new Date().toLocaleDateString("ru-RU")}.</p>
    </main>
  );
}
""",
        "src/app/legal/terms/page.tsx": """import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Условия использования — ${app.app_name}` };

export default function TermsPage() {
  const operator = app.operator.legal_name || app.app_name;
  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Условия использования</h1>
      <p>
        Эти условия регулируют использование мини-приложения «{app.app_name}».
        Владелец сервиса: {operator}.
      </p>
      <h2>Возможности сервиса</h2>
      <p>{app.summary}</p>
      <h2>Правила</h2>
      <p>
        Нельзя нарушать закон, права других лиц, пытаться получить чужие данные,
        вмешиваться в работу сервиса или использовать его для спама и обмана.
      </p>
      {app.legal.has_sales && (
        <>
          <h2>Заказы и оплата</h2>
          <p>
            Итоговая цена, состав заказа, способ оплаты, отмены и возврата показываются
            до подтверждения. Платёж обрабатывает указанный при оформлении провайдер.
          </p>
        </>
      )}
      {app.legal.has_user_content && (
        <>
          <h2>Пользовательский контент</h2>
          <p>
            Запрещён незаконный и оскорбительный контент. Владелец вправе ограничить
            доступ и удалить нарушение; жалобу можно отправить через поддержку.
          </p>
        </>
      )}
      <p>Возрастная маркировка: {app.legal.age_rating}.</p>
    </main>
  );
}
""",
        "src/app/support/page.tsx": """import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Поддержка — ${app.app_name}` };

export default function SupportPage() {
  return (
    <main style={{ maxWidth: 680, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Поддержка</h1>
      <p>Опишите проблему, ожидаемый результат и время, когда она возникла.</p>
      {app.support.email && <p><strong>Email:</strong> <a href={`mailto:${app.support.email}`}>{app.support.email}</a></p>}
      {app.support.phone && <p><strong>Телефон:</strong> <a href={`tel:${app.support.phone}`}>{app.support.phone}</a></p>}
      <p>{app.support.response_time}</p>
      <nav style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 28 }}>
        <a href="/legal/privacy">Конфиденциальность</a>
        <a href="/legal/terms">Условия использования</a>
      </nav>
    </main>
  );
}
""",
    }
    _validate_managed_component_graph(files)
    return files


MAX_MODEL_LOCKED_FILES = frozenset(
    {
        "docker-entrypoint.sh",
        "Dockerfile.dev",
        "Dockerfile.prod",
        "drizzle.config.ts",
        "next-env.d.ts",
        "next.config.ts",
        "package.json",
        "pnpm-lock.yaml",
        "postcss.config.mjs",
        "scripts/apply-migrations.mjs",
        "drizzle/0000_max_core.sql",
        "drizzle/0001_business_core.sql",
        "public/omnia-brief-narration.js",
        "public/omnia-inspector.js",
        "public/omnia-remix-cta.js",
        "src/app/api/health/route.ts",
        MAX_PRODUCT_PAGE_PATH,
        "src/components/MaxAppProvider.tsx",
        "src/components/OmniaCompliance.tsx",
        MAX_PRODUCT_RUNTIME_PATH,
        "src/app/layout.tsx",
        "src/app/max-runtime.css",
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
)

_MAX_HISTORY_PRODUCT_ROOTS = (
    ".omnia/",
    "src/app/",
    "src/components/",
    "src/data/",
    "src/hooks/",
    "src/lib/",
    "src/store/",
    "src/styles/",
    "src/types/",
    "public/",
)
_MAX_HISTORY_PLATFORM_PREFIXES = (
    "src/app/api/max/",
    "src/app/api/omnia/",
    "src/lib/db/",
    "src/lib/max/",
    "src/lib/omnia/",
)
_LEGACY_RELATIVE_IMPORT_RE = re.compile(
    r"(?:from\s+|import\s*(?:\(\s*)?|require\s*\(\s*)[\"']\.\.?/"
)


def max_legacy_server_file_deletions(files: dict[str, str]) -> dict[str, str]:
    """Delete executable legacy Next routes outside the audited platform kit.

    Empty content is the shared repo/orchestrator delete-intent.  This is used
    during both config sync and pre-generation migration so an old arbitrary
    API/Page route cannot survive beside the new browser-only product entry.
    """

    deletions: dict[str, str] = {}
    for raw_path in files:
        path = posixpath.normpath(raw_path.replace("\\", "/"))
        if path != raw_path or path.startswith(("/", "../")):
            continue
        unmanaged_app = path.startswith("app/") or (
            path.startswith("src/app/")
            and path != "src/app/globals.css"
            and path not in MAX_MODEL_LOCKED_FILES
        )
        legacy_pages_router = path.startswith(("src/pages/", "pages/"))
        executable_build_config = path in {
            ".babelrc",
            ".babelrc.js",
            ".babelrc.cjs",
            ".babelrc.mjs",
            "babel.config.js",
            "babel.config.cjs",
            "babel.config.mjs",
            "babel.config.ts",
            "next.config.js",
            "next.config.mjs",
            "postcss.config.js",
            "postcss.config.cjs",
            "postcss.config.ts",
            "tailwind.config.js",
            "tailwind.config.cjs",
            "tailwind.config.mjs",
            "tailwind.config.ts",
        }
        legacy_server_entry = path in {
            "middleware.ts",
            "middleware.js",
            "src/middleware.ts",
            "src/middleware.js",
            "instrumentation.ts",
            "instrumentation.js",
            "src/instrumentation.ts",
            "src/instrumentation.js",
            "instrumentation-client.ts",
            "instrumentation-client.js",
            "src/instrumentation-client.ts",
            "src/instrumentation-client.js",
            "proxy.ts",
            "proxy.js",
            "src/proxy.ts",
            "src/proxy.js",
        }
        if unmanaged_app or legacy_pages_router or executable_build_config or legacy_server_entry:
            deletions[path] = ""
    return deletions


def max_legacy_snapshot_incompatibility(files: dict[str, str]) -> str | None:
    """Explain why a historical product cannot be restored losslessly.

    Older agents could spread product functionality across server-capable App
    Router routes and arbitrary ``src/lib`` modules.  Silently pruning those
    files produces a green-looking but incomplete rollback, so history must
    refuse that version until a real dependency migration exists.
    """

    unsafe_routes = sorted(max_legacy_server_file_deletions(files))
    if unsafe_routes:
        return f"legacy server route is not browser-isolatable: {unsafe_routes[0]}"
    unsafe_libs = sorted(
        path
        for path in files
        if path.startswith("src/lib/")
        and not path.startswith("src/lib/product/")
        and path not in MAX_MODEL_LOCKED_FILES
    )
    if unsafe_libs:
        return f"legacy product helper is outside src/lib/product: {unsafe_libs[0]}"
    unsafe_public_runtime = sorted(
        path
        for path in files
        if path.startswith("public/")
        and path.endswith((".js", ".mjs", ".cjs", ".wasm"))
        and not path.startswith("public/product/")
        and path not in MAX_MODEL_LOCKED_FILES
    )
    if unsafe_public_runtime:
        return f"legacy public executable is outside public/product: {unsafe_public_runtime[0]}"
    for path, content in sorted(files.items()):
        is_product_file = path == "src/app/globals.css" or path.startswith(
            (
                "src/components/",
                "src/data/",
                "src/hooks/",
                "src/lib/product/",
                "src/store/",
                "src/styles/",
                "src/types/",
                "public/product/",
            )
        )
        if not is_product_file or path in MAX_MODEL_LOCKED_FILES:
            continue
        if max_model_write_rejection(path, content):
            return f"legacy product file violates the browser boundary: {path}"
    legacy_page = files.get(MAX_PRODUCT_PAGE_PATH)
    if legacy_page and max_model_write_rejection(MAX_PRODUCT_ENTRY_PATH, legacy_page):
        return "legacy root page violates the browser boundary"
    if (
        MAX_PRODUCT_ENTRY_PATH not in files
        and legacy_page
        and _LEGACY_RELATIVE_IMPORT_RE.search(legacy_page)
    ):
        return "legacy root page uses relative imports that would break after isolation"
    return None


def max_model_path_rejection(path: str) -> str | None:
    """Reject non-canonical model write paths before applying MAX policy.

    The orchestrator safely normalizes archive paths, but policy checks happen in
    apps/api first. Without this guard, ``./src/...`` or ``src/x/../...`` could
    normalize onto a Studio-owned file after bypassing an exact-path lock.
    """

    raw = str(path or "")
    normalized = posixpath.normpath(raw)
    if (
        not raw
        or raw != raw.strip()
        or raw.startswith(("/", "~"))
        or "\\" in raw
        or "\x00" in raw
        or normalized in {"", "."}
        or normalized != raw
    ):
        return (
            "MAX source paths must be canonical project-relative POSIX paths "
            "without '.', '..', duplicate separators or backslashes."
        )
    allowed = (
        raw == "src/app/globals.css"
        or raw == ".omnia/max-design-spec.json"
        or raw.startswith("src/components/")
        or raw.startswith("src/hooks/")
        or raw.startswith("src/data/")
        or raw.startswith("src/store/")
        or raw.startswith("src/styles/")
        or raw.startswith("src/types/")
        or raw.startswith("src/lib/product/")
        or raw.startswith("public/product/")
    )
    if not allowed:
        return (
            "MAX product code is browser-isolated. Write ProductApp and client-only "
            "components/styles under the allowed product directories; server routes, "
            "middleware, app routes and runtime files are platform-owned."
        )
    return None


# Root/build files are not valid product customisation points.  A restored
# commit receives this audited subset from the canonical server template;
# trusting current repo bytes (or their complement) would silently classify a
# user-modified executable such as ``src/middleware.ts`` as platform core.
_MAX_RESTORE_TEMPLATE_PLATFORM_FILES = frozenset(
    {
        "docker-entrypoint.sh",
        "Dockerfile.dev",
        "Dockerfile.prod",
        "drizzle.config.ts",
        "next-env.d.ts",
        "next.config.ts",
        "tsconfig.json",
        "drizzle/0000_max_core.sql",
        "drizzle/0001_business_core.sql",
        "scripts/apply-migrations.mjs",
        "public/omnia-brief-narration.js",
        "public/omnia-remix-cta.js",
        "src/app/api/health/route.ts",
    }
)
_MAX_CONFIG_MARKER = "export const omniaMaxConfig: OmniaMaxConfig = "


def max_history_product_files(files: dict[str, str]) -> dict[str, str]:
    """Return only snapshot-owned product files for a history renderer.

    Historical product UI is intentionally combined with the current,
    security-patched MAX runtime baked into the renderer image.  Old commits
    must therefore never overwrite platform-owned auth, bridge, persistence or
    dependency files.  ``write_files`` performs its own traversal checks; this
    normalization makes the ownership boundary explicit before data crosses
    the orchestrator API.
    """
    product: dict[str, str] = {}
    legacy_page = files.get(MAX_PRODUCT_PAGE_PATH)
    for raw_path, content in files.items():
        path = posixpath.normpath(raw_path.replace("\\", "/"))
        if path.startswith("/") or path == ".." or path.startswith("../"):
            continue
        if not path.startswith(_MAX_HISTORY_PRODUCT_ROOTS):
            continue
        if path in MAX_MODEL_LOCKED_FILES or path.startswith(_MAX_HISTORY_PLATFORM_PREFIXES):
            continue
        if path.startswith(".omnia/") and path != ".omnia/max-design-spec.json":
            continue
        # Historical model-owned App Router files are server-capable code. Keep
        # only globals.css and migrate the legacy root product into ProductApp;
        # every executable product module is then reached through ssr:false.
        if path.startswith("src/app/") and path != "src/app/globals.css":
            continue
        if path.startswith("src/lib/") and not path.startswith("src/lib/product/"):
            continue
        if (
            path.startswith("public/")
            and path.endswith((".js", ".mjs", ".cjs", ".wasm"))
            and not path.startswith("public/product/")
        ):
            continue
        # Kit v20 briefly wrote this exact null component into config-only
        # snapshots. It marks an absent product, not generated product code.
        if path == MAX_PRODUCT_ENTRY_PATH and content.strip() == _EMPTY_PRODUCT_ENTRY.strip():
            continue
        product[path] = content
    if (
        MAX_PRODUCT_ENTRY_PATH not in product
        and legacy_page
        and legacy_page.strip() != _EMPTY_PRODUCT_ENTRY.strip()
    ):
        # Kit v14 and older let the model own the root page.  History and restore
        # migrate that product byte-for-byte behind the current browser-only
        # runtime instead of executing the historical module on the server.
        product[MAX_PRODUCT_ENTRY_PATH] = legacy_page
    return product


def max_snapshot_uses_legacy_max_ui(files: dict[str, str]) -> bool:
    """Detect historical product code that still needs the old visual provider."""

    return any("@maxhub/max-ui" in content for content in max_history_product_files(files).values())


def render_max_entry_migration_files(snapshot_files: dict[str, str]) -> dict[str, str]:
    """Return the trusted entry boundary plus the current product component.

    Config/kit sync is a partial commit, so it must explicitly carry a legacy
    page into the new ProductApp path before replacing the root page.
    """

    incompatibility = max_legacy_snapshot_incompatibility(snapshot_files)
    if incompatibility:
        raise ValueError(f"MAX entry cannot be migrated safely: {incompatibility}")
    product = max_history_product_files(snapshot_files)
    migrated = {
        **max_legacy_server_file_deletions(snapshot_files),
        "next.config.ts": _template_file("next.config.ts"),
        MAX_PRODUCT_PAGE_PATH: _template_file(MAX_PRODUCT_PAGE_PATH),
        MAX_PRODUCT_RUNTIME_PATH: _template_file(MAX_PRODUCT_RUNTIME_PATH),
    }
    if MAX_PRODUCT_ENTRY_PATH in product:
        migrated[MAX_PRODUCT_ENTRY_PATH] = product[MAX_PRODUCT_ENTRY_PATH]
    return migrated


def max_project_config_from_files(
    files: dict[str, str],
) -> MaxProjectConfigPayload | None:
    """Read the immutable business profile committed with a MAX snapshot.

    The config module is generated from JSON, so parsing it avoids executing
    repository code and makes historical preview/restore match the selected
    commit instead of today's database row.
    """
    source = files.get("src/lib/omnia/max-config.ts")
    if not source or _MAX_CONFIG_MARKER not in source:
        return None
    payload = source.split(_MAX_CONFIG_MARKER, 1)[1].strip()
    if not payload.endswith(";"):
        return None
    try:
        raw = json.loads(payload[:-1])
        return MaxProjectConfigPayload.model_validate(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def default_max_project_config(app_name: str) -> MaxProjectConfigPayload:
    """Canonical model-free fallback for MAX projects without a saved profile."""
    return MaxProjectConfigPayload(
        app_name=app_name,
        app_type="custom",
        summary="Мини-приложение для пользователей MAX",
        operator=MaxOperator(),
        support=MaxSupport(),
        legal=MaxLegal(),
    )


def render_max_history_files(
    snapshot_files: dict[str, str],
    config: MaxProjectConfigPayload,
    project_id: UUID | str,
) -> dict[str, str]:
    """Combine historical product UI with today's trusted MAX runtime core."""
    incompatibility = max_legacy_snapshot_incompatibility(snapshot_files)
    if incompatibility:
        raise ValueError(f"MAX snapshot cannot be restored safely: {incompatibility}")
    product = max_history_product_files(snapshot_files)
    default_entry = (
        _template_file(MAX_PRODUCT_ENTRY_PATH)
        if MAX_PRODUCT_ENTRY_PATH not in product
        else product[MAX_PRODUCT_ENTRY_PATH]
    )
    return {
        **render_max_managed_files(
            config,
            project_id,
            legacy_max_ui=max_snapshot_uses_legacy_max_ui(product),
        ),
        # New MAX projects intentionally start with an empty Git snapshot while
        # the maintained starter lives in the runtime image. Reconciliation of
        # that empty snapshot must restore the same usable starter, not delete
        # its model-owned entry and CSS after a failed/cancelled first build.
        "src/app/globals.css": _template_file("src/app/globals.css"),
        MAX_PRODUCT_ENTRY_PATH: default_entry,
        **product,
    }


def render_max_restored_files(
    snapshot_files: dict[str, str],
    _current_files: dict[str, str],
    config: MaxProjectConfigPayload,
    project_id: UUID | str,
) -> dict[str, str]:
    """Build a rollback tree with historical product and today's trusted core."""
    trusted_platform = {path: _template_file(path) for path in _MAX_RESTORE_TEMPLATE_PLATFORM_FILES}
    return {
        **trusted_platform,
        **render_max_history_files(snapshot_files, config, project_id),
    }


MAX_MODEL_DIRECTIVE = """
MAX HEADLESS PLATFORM ADAPTER
The maintained runtime already provides MAX Bridge, verified initData, the MAX
profile, bot webhook, legal/support routes and managed integrations. It is not a
visual or product template. The model owns ProductApp and all visible UI.

For a fresh build, create a complete usable
`src/components/product/ProductApp.tsx` and organise supporting product files in
the order that best fits the implementation. The finished vertical slice must
contain the requested screens, navigation and states.

Platform boundaries only:
- Do not edit locked runtime files, root layout/page, package/build config or create
  app/API routes. Never import `@/lib/db`, `drizzle-orm` or server-only modules.
- For a fresh build, never import `@maxhub/max-ui`; that dependency exists only to
  render historical snapshots. Use ordinary React, Tailwind or product CSS.
- Do not expose credentials, copy a pasted key into source, or add email/password auth.
- `useMaxApp` comes from `@/components/MaxAppProvider` and returns
  `{ mode, user, error }`; nullable `user` has `id`, `firstName`, `lastName`,
  `username`, `languageCode`, `photoUrl`.
- `createMaxAction`, `getMaxActions` and `requestOmniaAI` come from
  `@/lib/omnia/integration-client`. The AI call is
  `requestOmniaAI({ message, instructions, context })`.
- Static product reference content (catalogs, services, plans, exercises) is
  allowed when the brief needs it. Never invent user identity, history, metrics,
  orders, completed activity or successful integrations. Restore managed user
  activity with `getMaxActions` after reload and await every `createMaxAction`.

Product design contract:
- Derive the visual system from the product's audience, task and content. Do not
  default to a dark purple AI dashboard, glass/bento cards, decorative gradients,
  huge radii, repeated badges or generic "AI"/"premium" labels.
- Never use emoji as interface icons. Use one coherent SVG/icon set, restrained
  typography and a small intentional color/radius/spacing system.
- Every chart, metric, card and status must communicate useful real or clearly
  labelled demo data. Do not render empty decorative charts or duplicate the same
  card pattern across every screen.
- Build and verify every requested navigation destination and primary action.
  Tabs must remain responsive through repeated ordinary clicks with editor mode off.
- Expose inert production-test hooks on real controls/views:
  `data-omnia-screen-nav`, `data-omnia-screen`, `data-omnia-primary-action`,
  `data-omnia-persisted-action` and planner-supplied `data-omnia-capability` ids.
  They do not replace semantic buttons/links, labels or actual behavior.

Do not recreate MAX-owned chrome. Keep reachable links to the managed support,
privacy and terms routes. Prove build, signed runtime interactions, reload
persistence, accessibility and visual quality before completion.
""".strip()
