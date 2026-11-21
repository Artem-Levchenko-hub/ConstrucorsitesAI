"""Render the Yleum-managed MAX application kit without calling a model."""

# ruff: noqa: E501

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from yleum_api.core.config import get_settings
from yleum_api.core.errors import ApiError
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.max_content_delivery import public_business_config
from yleum_api.services.max_legacy_order_management import (
    LEGACY_ORDER_MANAGED_PATHS,
    render_legacy_order_management,
)
from yleum_api.services.max_management_contract import build_management_contract

# Increment whenever the managed file set changes in a way that existing MAX
# projects must receive. It deliberately does not follow the public config
# schema version: this is a deployment revision of platform-owned source files.
MAX_MANAGED_KIT_VERSION = 36
# v36 adds the verification-only signed order receipt to managed source.
# v35 adds exact staff reads and order-linked checkout to managed source.
# v34 sanitizes catalog selection, adopts legacy orders and recovers pending intents.
# v33 adds explicit owner management and typed business resources in product DB.
# v32 adds the owner catalog's shared server/client configuration file contract.
# v30: keep legal footer links and keyboard focus clear of fixed bottom navigation.
# v29 adds authenticated exact lead receipt lookup for safe legacy adoption.
# Kit v18 shipped encrypted owner-scoped CRUD. v19 retires exactly those
# platform-owned paths. v20 materializes trusted gateway subjects in the
# isolated product DB before business tables can enforce max_users FKs. v21
# adds owner-scoped item CRUD used by fail-closed activation health checks.
# v22 stops reading and storing the MAX visitor profile (sessions carry the MAX
# user id only) and drops requisites from the app's documents: the operator is a
# display name, the policy may be the owner's own link (152-ФЗ ст. 5).
# v24 materializes a portable legacy identity only when its physical parent exists;
# independently managed project schemas need no local MAX core tables.
MAX_RETIRED_MANAGED_FILES = frozenset(
    {
        "src/app/api/omnia/data/[...path]/route.ts",
        "src/lib/secure-data/crypto.ts",
        "src/lib/secure-data/store.ts",
        "src/lib/secure-data/validation.ts",
        "src/lib/secure-data/http.ts",
        "src/lib/secure-data/runtime.ts",
        "src/lib/omnia/data-client.ts",
        "drizzle/0003_secure_records.sql",
        # Переименован в YleumCompliance.tsx 25.09.2026: у приложений, собранных
        # раньше, старый файл надо удалить, иначе останется мёртвый дубль.
        "src/components/OmniaCompliance.tsx",
    }
)
# Принимаем оба префикса: управляемые файлы уже переименованы в Yleum*, а kit
# может встретиться с приложением, собранным до переименования.
_MANAGED_COMPONENT_IMPORT_RE = re.compile(
    r"""from\s+["']@/components/((?:Yleum|Omnia)[A-Za-z0-9_/-]+)["']"""
)

MAX_MANAGEMENT_RUNTIME_FILES = (
    "src/lib/omnia/management-config.ts", "src/lib/omnia/management-auth.ts",
    "src/lib/omnia/management-store.ts", "src/lib/omnia/management-resources.ts",
    "src/lib/omnia/business-client.ts", "src/lib/omnia/business-server.ts",
    "src/lib/omnia/identity-scope.ts",
    "src/app/api/omnia/owner/route.ts", "src/app/api/omnia/business/[resource]/route.ts",
    "src/lib/omnia/app-profile-client.ts", "src/lib/omnia/app-profile-actions.ts",
    "drizzle/0003_owner_management.sql",
)


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


def _checkout_verifier(project_id: UUID | str | None) -> str:
    from yleum_api.services.generation.meal_acceptance import fixture_public_key_pem
    from yleum_api.services.max_checkout_receipt import checkout_receipt_public_key_pem

    real, fixture = "", ""
    try:
        real = checkout_receipt_public_key_pem()
        if project_id is not None:
            fixture = fixture_public_key_pem(project_id, get_settings().jwt_secret.get_secret_value())
    except (ApiError, ValueError):
        # Unconfigured signing must not prevent existing applications building.
        pass
    return _template_file("src/lib/omnia/checkout-receipt.ts").replace(
        '"__YLEUM_CHECKOUT_RECEIPT_PUBLIC_KEY__"', json.dumps(real),
    ).replace('"__YLEUM_MEAL_FIXTURE_PUBLIC_KEY__"', json.dumps(fixture))


_MAX_SAST_TEMPLATE_TESTS = (
    "tests/catalog-config.test.mjs",
    "tests/database-compatibility.test.mjs",
)


def max_sast_source_view(files: Mapping[str, str], *, is_max: bool) -> dict[str, str]:
    """Exclude only byte-exact trusted MAX test harnesses from the SAST view.

    These platform tests intentionally transpile local TypeScript and exercise
    parameterized SQL. Their paths are not security locks: every changed copy
    still needs the ordinary scan. Neither candidate nor baseline content can
    establish trust, and unavailable template bytes never grant an exemption.
    """
    view = dict(files)
    if not is_max:
        return view
    for path in _MAX_SAST_TEMPLATE_TESTS:
        if path not in view:
            continue
        for candidate in _template_candidates(path):
            try:
                trusted = candidate.read_bytes().decode("utf-8")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError):
                break
            # Preserve newlines and all other UTF-8 content exactly; do not use
            # read_text's universal-newline conversion for a trust comparison.
            if view[path] == trusted:
                del view[path]
            break
    return view


def _json(config: MaxProjectConfigPayload) -> str:
    return json.dumps(
        public_business_config(config),
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
    )


_CONFIG_TYPES = """export type YleumMaxContentItem = {
  id: string;
  title: string;
  /** Раздел каталога, заданный владельцем («Женское», «Стрижки»); "" — без раздела. */
  category: string;
  description: string;
  price: string;
  /** Наличие позиции: в наличии, под заказ, нет в наличии. */
  availability: "in_stock" | "on_request" | "out_of_stock";
  /** Варианты выбора: размеры, объёмы, длительности; пустой массив — выбора нет. */
  options: string[];
  /** Публичный https-адрес фотографии или "" — тогда показывайте запасной блок. */
  image_url: string;
  action_label: string;
  active: boolean;
};

export type YleumMaxConfig = {
  app_name: string;
  app_type: "loyalty" | "catalog" | "booking" | "event" | "education" | "custom";
  summary: string;
  audience: string;
  primary_action: string;
  features: string[];
  style: "brand" | "clean" | "bright";
  brand_colors: string;
  content: YleumMaxContentItem[];
  operator: { legal_name: string };
  support: { email: string | null; response_time: string };
  legal: {
    age_rating: "0+" | "6+" | "12+" | "16+" | "18+";
    has_sales: boolean;
    has_user_content: boolean;
    marketing_notifications: boolean;
    personal_data_consent: boolean;
    terms_accepted: boolean;
    policy_url: string;
  };
};
"""


def _validate_managed_component_graph(files: dict[str, str]) -> None:
    """Refuse an incomplete platform kit before it reaches a project snapshot.

    Yleum-prefixed components are owned by the platform, so every such import
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
    config: MaxProjectConfigPayload, project_id: UUID | str | None = None
) -> dict[str, str]:
    """Files safe to apply to both a starter and an already-generated app."""
    data = _json(config)
    project_literal = json.dumps(str(project_id) if project_id else "")
    preview_session_route = _template_file("src/app/api/omnia/preview-session/route.ts").replace(
        '"__OMNIA_PROJECT_ID__"', project_literal, 1
    )
    files = {
        **{path: _template_file(path) for path in MAX_MANAGEMENT_RUNTIME_FILES},
        "next.config.ts": _template_file("next.config.ts"),
        ".omnia/management.json": json.dumps(
            build_management_contract(config.management_modules or []).model_dump(mode="json"),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ) + "\n",
        "postcss.config.mjs": _template_file("postcss.config.mjs"),
        "src/app/layout.tsx": _template_file("src/app/layout.tsx"),
        "src/app/api/omnia/health/route.ts": _template_file("src/app/api/omnia/health/route.ts"),
        "src/components/MaxAppProvider.tsx": _template_file("src/components/MaxAppProvider.tsx"),
        "src/components/YleumCompliance.tsx": _template_file("src/components/YleumCompliance.tsx"),
        "src/lib/db/index.ts": _template_file("src/lib/db/index.ts"),
        "src/lib/max/bot-api.ts": _template_file("src/lib/max/bot-api.ts"),
        "src/lib/max/bridge.ts": _template_file("src/lib/max/bridge.ts"),
        "src/lib/max/owner-preview-renewal.ts": _template_file("src/lib/max/owner-preview-renewal.ts"),
        "src/lib/max/validate-init-data.ts": _template_file("src/lib/max/validate-init-data.ts"),
        "src/app/api/max/session/route.ts": _template_file("src/app/api/max/session/route.ts"),
        "src/app/api/max/webhook/route.ts": _template_file("src/app/api/max/webhook/route.ts"),
        "src/lib/max/session.ts": _template_file("src/lib/max/session.ts"),
        "src/app/api/omnia/preview-session/route.ts": preview_session_route,
        "src/app/api/omnia/actions/route.ts": _template_file("src/app/api/omnia/actions/route.ts"),
        "src/app/api/omnia/actions/[id]/route.ts": _template_file(
            "src/app/api/omnia/actions/[id]/route.ts"
        ),
        "src/app/api/omnia/consents/route.ts": _template_file(
            "src/app/api/omnia/consents/route.ts"
        ),
        "src/app/api/omnia/events/route.ts": _template_file("src/app/api/omnia/events/route.ts"),
        "src/app/api/omnia/telemetry/route.ts": _template_file("src/app/api/omnia/telemetry/route.ts"),
        "src/lib/omnia/activity.ts": _template_file("src/lib/omnia/activity.ts"),
        "src/lib/omnia/catalog-server.ts": _template_file("src/lib/omnia/catalog-server.ts"),
        "src/lib/omnia/catalog-actions.ts": _template_file("src/lib/omnia/catalog-actions.ts"),
        "src/lib/omnia/catalog-client.ts": _template_file("src/lib/omnia/catalog-client.ts"),
        "src/lib/omnia/consent.ts": _template_file("src/lib/omnia/consent.ts"),
        "src/lib/omnia/analytics.ts": _template_file("src/lib/omnia/analytics.ts").replace(
            'process.env.OMNIA_PROJECT_ID || process.env.YLEUM_OWNER_PROJECT_ID || ""',
            project_literal, 1
        ),
        "src/lib/omnia/client.ts": _template_file("src/lib/omnia/client.ts"),
        "src/lib/omnia/max-config.ts": (
            "/* Generated by Yleum. Edit the business profile in Yleum, not this file. */\n"
            f"{_CONFIG_TYPES}\n"
            f"export const omniaMaxConfig: YleumMaxConfig = {data};\n"
        ),
        "src/app/api/omnia/config/route.ts": """import { NextResponse } from "next/server";

import { omniaMaxConfig } from "@/lib/omnia/max-config";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(omniaMaxConfig, {
    headers: { "Cache-Control": "no-store" },
  });
}
""",
        "src/lib/omnia/integration-client.ts": _template_file(
            "src/lib/omnia/integration-client.ts"
        ),
        "src/lib/omnia/checkout-receipt.ts": _checkout_verifier(project_id),
        "src/app/api/omnia/integrations/[...path]/route.ts": _template_file(
            "src/app/api/omnia/integrations/[...path]/route.ts"
        ).replace('process.env.OMNIA_PROJECT_ID || ""', project_literal, 1),
        "src/app/legal/privacy/page.tsx": """import { omniaMaxConfig as app } from "@/lib/omnia/max-config";

export const metadata = { title: `Политика конфиденциальности — ${app.app_name}` };

export default function PrivacyPage() {
  const operator = app.operator.legal_name || app.app_name;
  if (app.legal.policy_url) {
    return (
      <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
        <h1>Политика конфиденциальности</h1>
        <p>
          Обработка данных в мини-приложении «{app.app_name}» регулируется политикой владельца:{" "}
          <a href={app.legal.policy_url} rel="noopener noreferrer">{app.legal.policy_url}</a>.
        </p>
        <p>Возрастная маркировка: {app.legal.age_rating}.</p>
      </main>
    );
  }
  return (
    <main style={{ maxWidth: 760, margin: "0 auto", padding: "32px 20px 64px", lineHeight: 1.65 }}>
      <h1>Политика конфиденциальности</h1>
      <p><strong>Оператор:</strong> {operator}</p>
      {app.support.email && <p><strong>Контакт по вопросам данных:</strong> {app.support.email}</p>}
      <h2>Какие данные обрабатываются</h2>
      <p>
        Приложение получает от MAX только идентификатор пользователя — он нужен, чтобы
        различать пользователей и хранить их действия, заказы, записи и обращения. Имя,
        фамилия, имя пользователя, язык и фотография из MAX не запрашиваются и не сохраняются.
        Другие сведения приложение запрашивает у пользователя явно и только для конкретной функции.
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
        "src/app/support/page.tsx": _template_file("src/app/support/page.tsx"),
    }
    if config.management_modules is None:
        files.pop(".omnia/management.json", None)
    if not build_management_contract(config.management_modules or []).resources:
        files.pop("drizzle/0003_owner_management.sql", None)
    if config.legacy_orders_table is not None:
        if project_id is None:
            raise ValueError("legacy orders adoption requires a project UUID")
        files.update(render_legacy_order_management(project_id, config.legacy_orders_table))
    _validate_managed_component_graph(files)
    return files


def render_max_managed_kit_update(
    config: MaxProjectConfigPayload,
    project_id: UUID | str | None = None,
) -> dict[str, str]:
    """Managed files plus deletions (empty content) of retired managed paths."""
    return {
        **{path: "" for path in sorted(MAX_RETIRED_MANAGED_FILES)},
        **render_max_managed_files(config, project_id),
    }


MAX_SECURITY_LOCKED_FILES = frozenset(
    {
        *MAX_MANAGEMENT_RUNTIME_FILES,
        *LEGACY_ORDER_MANAGED_PATHS,
        ".omnia/management.json",
        "src/lib/omnia/catalog-server.ts",
        "src/lib/omnia/catalog-actions.ts",
        "src/lib/omnia/catalog-client.ts",
        "src/components/MaxAppProvider.tsx",
        "src/components/YleumCompliance.tsx",
        "src/app/layout.tsx",
        "src/app/api/omnia/health/route.ts",
        "next.config.ts",
        "drizzle.config.ts",
        "postcss.config.mjs",
        "docker-entrypoint.sh",
        "Dockerfile.dev",
        "Dockerfile.prod",
        "scripts/apply-migrations.mjs",
        "src/lib/db/index.ts",
        "src/lib/max/bot-api.ts",
        "src/lib/max/bridge.ts",
        "src/lib/max/owner-preview-renewal.ts",
        "src/lib/max/validate-init-data.ts",
        "src/app/api/max/session/route.ts",
        "src/app/api/max/webhook/route.ts",
        "src/lib/max/session.ts",
        "src/app/api/omnia/preview-session/route.ts",
        "src/app/api/omnia/actions/route.ts",
        "src/app/api/omnia/actions/[id]/route.ts",
        "src/app/api/omnia/consents/route.ts",
        "src/app/api/omnia/events/route.ts",
        "src/app/api/omnia/telemetry/route.ts",
        "src/lib/omnia/max-config.ts",
        "src/lib/omnia/client.ts",
        "src/lib/omnia/analytics.ts",
        "src/lib/omnia/activity.ts",
        "src/lib/omnia/consent.ts",
        "src/app/api/omnia/config/route.ts",
        "src/lib/omnia/integration-client.ts",
        "src/lib/omnia/checkout-receipt.ts",
        "src/app/api/omnia/integrations/[...path]/route.ts",
        "src/app/legal/privacy/page.tsx",
        "src/app/legal/terms/page.tsx",
        "src/app/support/page.tsx",
    }
)

# Legacy fallback used when the disposable sandbox is disabled or cannot be
# attested.  A ready project-owner sandbox narrows this to the security core
# above, so the agent can choose dependencies and design real relational data.
MAX_MODEL_LOCKED_FILES = frozenset(
    {
        *MAX_SECURITY_LOCKED_FILES,
        "package.json",
        "pnpm-lock.yaml",
        "postcss.config.mjs",
        "src/lib/db/schema.ts",
    }
)

MAX_UI_GUIDANCE = """
MAX UI 0.2.0: Typography is a namespace, not a JSX component.
Never render <Typography>. Inspect the installed public exports/types for the
concrete text components and their props. Do not assume Typography.Caption exists
or guess level values; verify exported components and prop unions in the installed
declarations before using them. Typography.Title has no level prop: removing an
unsupported level is required, not choosing another number. Its optional variant
is 'large-strong' | 'medium-strong' | 'small' | 'small-strong' | 'custom'.
For a semantic heading use <Typography.Title variant="small" asChild><h2>Title</h2></Typography.Title>.
Keep the installed MAX UI version; do not add a replacement UI library or bypass
TypeScript errors.
Product typography comes from the project's stylesheet; the MAX shell inherits
its font family. Preserve explicit owner fonts and intentional heading/control
roles. Check the actual inherited font and component styles rather than applying
a blanket font or color override to every descendant.
For authored component CSS, use CSS Modules or product-scoped class names.
Do not redefine framework utility names such as .ring for a product illustration:
Tailwind's .ring also paints a box-shadow on the element's rectangular box.
Use framework utilities intentionally and keep custom selectors scoped to their
own component. Do not remove keyboard focus styles or all shadows to mask a clash.
For a first visit, distinguish absent personal data from measured zero progress.
Show what is needed and the requested next action instead of empty charts,
invented history or a completed-looking summary. Keep requested empty screens
usable, and distinguish a successful empty read from loading or a failed read.
Static reference content is separate from the current user's records. Honor an
explicitly blank, static or API-only scope without adding a dashboard or onboarding.
""".strip()

MAX_MANAGED_SDK_GUIDANCE = """
MAX MANAGED SDK CONTRACT
The owner edits business data in Yleum's Catalog and project settings.
For a managed UI app, show the public application name in a visible heading
from `useYleumAppProfile()` in `@/lib/omnia/app-profile-client`, using
profile.app_name. Do this when you first implement the entry page.
This requirement applies even if the personal Profile screen was left off.
Add a personal profile or user history screen only when the brief or the managed
module requires it. Do not use app metadata to invent a user feature.
The hook returns {profile,configVersion,status,error,refresh}. Public fields
are app_name, summary and support.email/response_time. A neutral platform summary
such as "Мини-приложение для пользователей MAX" is metadata, not a mandatory
customer-facing tagline. Preserve explicit owner wording and a meaningful saved
summary. If useful public benefit copy is needed, derive it from the brief rather
than copying a platform placeholder; do not rewrite the owner's stored config.
Keep required legal/platform attribution. Handle loading and error
without a bundled app-name fallback. Use app_name, never name. Catalog items use
item.title. Read the SDK declarations for precise types before calling any method.
Read the locked management manifest before implementing business flows. Use
createManagedOrder/createManagedLead/
createManagedBooking and getManagedRecords from `@/lib/omnia/business-client`
for the selected Yleum-managed orders, leads and bookings. For separate
provider-owned flows use the connected service's documented contract and receipt.
Submit each business intent once through its flow's write method. Do not replace
a selected managed module with a custom SQL table or generic action.
Read business-client.ts and business-server.ts for the exact typed signatures.
Each create takes operationKey (one UUID per deliberate intent), customerName
(explicit form input, never a fabricated MAX profile), optional contacts/comment.
Orders also take configVersion and items [{catalogId,quantity,unitPriceKopecks,
option?}]. Convert only explicit numeric RUB catalog prices to integer kopecks.
For pickup orders the optional fulfillment is {type:"pickup",delayMinutes:0..1440}.
The server computes fulfillment.scheduled_for after checking an existing receipt;
render that saved timestamp and delay_minutes. Never submit a client-computed
scheduled_for or lose the fulfillment/comment when reading historical orders.
On ManagedBusinessError.code=quote_changed, show error.quote current prices and
require a separate explicit confirmation; never automatically submit again.
Keep the same operationKey and body after ambiguous failures, including a reload.
On mount call loadPendingManagedIntent(resource) from business-client for each
selected writable module, using the literal "orders", "leads" or "bookings".
Wait for each resource's verified-session lookup before enabling its form.
Keep submission disabled on lookup failure and show a way to retry the lookup.
If an intent exists, show an explicit recovery action and pass its frozen input
and original operationKey to the same createManaged* method. Check this branch
before new-form or cart validation so an order can recover with an empty cart
or a changed/hidden catalog. A lookup does not submit anything.
Do not discard an unresolved intent after an authentication error or replace it
with a new UUID. createManaged* persists the frozen intent before a browser POST.
Resolve a pending_intent before starting another submission.
If intent_storage_unavailable blocks an attempt, say that attempt was not sent.
History reads
getManagedRecords(resource,{limit,cursor}) and renders result.records; show only
the current customer's records and the server-returned status.
Order creation, payment and fulfillment are separate states. A successful order
write confirms the saved order/request, not a paid or fulfilled order. A payment
creation response, confirmation_url, return URL or browser redirect is not payment
confirmation. Use the selected checkout SDK's authoritative status/receipt read
after returning and on reload; only a verified succeeded payment may be shown as
paid. Keep pending, canceled, failed and needs_review states honest and identify
test payments as test payments. If payment is unavailable or not connected, say
so and keep the saved order's actual state. Never create another order or replay
a business write to check payment, and never bypass the pending-intent contract.
SDK CONNECTION CONTRACT (projects with .omnia/management.json):
Use the current exported SDK types and actual method signatures. Each selected
resource connects its createManaged* method and getManagedRecords history to the
requested screens. getManagedRecords(resource,{limit,cursor}) returns
{records,next_cursor}; render the server-returned records and status, not a copied
constant. Use Parameters<typeof createManagedOrder>[0] and the equivalent types
for leads and bookings; preserve all optional form/frozen-intent fields and key.
Visible submit controls must reach their handler once, reflect lookup/in-flight/
recovery state and prevent duplicate submissions. Implement every selected
resource's requested create/history flow; do not invent additional modules.
Keep new add-to-cart/order submissions disabled until catalog status is ready,
an in-stock numeric price exists and any required option was deliberately selected.
Non-numeric prices keep an enquiry path, not a guessed checkout total. Loading and
errors must remain visible even with an empty cart; never silently ignore an
apparently enabled first tap. Keep cart/history navigation and recovery of an
existing frozen intent available independently of today's catalog or nonempty cart.
React component boundaries, shared views, selectors and layout are implementation
choices: connect the live data to the product's planned screens without imposing
one JSX tree, form arrangement or visual layout. With no selected modules the
live public application profile still supplies the product heading. Without that
manifest preserve existing legacy contracts/data; never silently enable modules.
Bookings take serviceId and requestedFor (timezone-bearing ISO); they are requests
until the owner confirms them. Do not invent free slots, capacity or payment.
Private/personal features outside these selected modules keep their existing
user-scoped storage and must never become an owner-wide business registry.
Keep existing browser storage keys, formats and writes for app-owned JSON such
as cart quantities and favorite IDs. Read and validate the saved values before
writing, wait for hydration, and serialize the validated typed local values.
The business SDK alone handles storage for pending managed intents.
Read `src/lib/omnia/max-config.ts` to understand the saved brief. Product catalog
screens MUST use `useYleumCatalog()` from `@/lib/omnia/catalog-client`, which
refreshes on mount/focus from the same mounted public business file as the server.
It returns {items,configVersion,status,error,refresh}; items are YleumMaxContentItem[].
Pass catalog.items to useYleumCatalogSelection from the same module.
Use selection.category/setCategory for category filters (null means All),
selection.selectOption(id,value) for option inputs, and selection.getOption(item)
when adding a CURRENT item to the cart. The hook discards removed options and
categories synchronously; never keep a competing raw option/category state or
invent a first option. With nonempty options require a deliberate valid choice.
When accepting a changed server quote replace each line snapshot entirely,
including an absent option; do not spread an old option into the new quote.
Never render a live catalog from bundled config, constants or the legacy core
catalog endpoint; a missing/unavailable file must show an honest error.
Render only active content items, keep their stable ids and handle loading,
empty and failed reads honestly. A content item carries
`category`, `image_url`, `price`, `availability` and `options`: group the catalog
by the non-empty categories, show the photo when `image_url` is set and a neutral
placeholder when it is not, label availability honestly (`out_of_stock` must not
look orderable), and let the user pick one of `options` before the main action
when the array is non-empty. Never invent a photo, a price or a size the owner
did not enter. Do not copy this mutable data
into constants or invent catalog entries. Keep runtime business configuration
separate from user-owned actions. Saved feature/style/policy choices are a brief
to implement, not proof that a payment, consent or marketing flow exists.
Product server actions MUST call `getYleumServerAppConfig()` from
`@/lib/omnia/catalog-server` and resolve active IDs, availability, options and
current prices before persisting an order; never trust a submitted price/title.
Submit the viewed configVersion; if it changed, refresh and ask the user to
confirm the current total before accepting the order.
Save title/price snapshots in the order, preserving existing IDs and old history.
Prices are free text: “По запросу” and other non-numeric prices require an enquiry,
never a silently parsed checkout total. Missing config fails closed, with no
static fallback. Preserve `"app_config_protocol": 1` in `.omnia/cell.json`.
Catalog saves in Yleum need neither generation nor source republish. The public
app must not expose owner administration or the complete operator configuration.
""".strip()

MAX_MODEL_DIRECTIVE = (
    """
MAX PLATFORM CORE CONTRACT
The existing MAX files are a secure runtime substrate, not a product UI template.
Preserve the MAX bridge, authenticated session, legal/support routes, managed AI
and integration clients, webhook security and generated business config. Do not
rewrite platform-owned files.
""".strip()
    + "\n\n"
    + MAX_UI_GUIDANCE
    + "\n\n"
    + MAX_MANAGED_SDK_GUIDANCE
    + "\n\n"
    + """
On a FULL BUILD, there is deliberately no product home page or visual template.
For requested UI create src/app/page.tsx, product styles, domain screens and
working behavior. An explicit API-only brief needs its product APIs and a
technical root route. Use `createMaxAction` and `getMaxActions` for generic
persisted user activity, with optional pagination via `{ limit, cursor }`.
Use the Yleum business SDK or the connected provider method for the business
flow it owns. Read history from the same system and submit each intent once.
Do not impose arbitrary limits on the number of business listings or records a
user can create. Use pagination for reading large lists; a page size is not a
creation quota. Add a business quota only when the owner explicitly requests it.
Never fabricate the current user's history, profile, progress, workouts, meals
or metrics with demo/mock/test constants.
Show an honest empty state when no user records exist. Keep static reference
catalogs separate from user activity. Add product API routes outside the reserved
`/api/max/*` and `/api/omnia/*` namespaces. In this legacy MAX stack, persist
user data through the managed SDK. Keep direct database imports in platform-owned
code. Authorize every product-owned API request with the trusted MAX identity.
For new/updated product flows, import `trackMaxActivity` from `@/lib/omnia/client`.
Its only event codes are `screen_view`, `item_view`, `button_click`, `cart_add`,
`checkout_start`, `form_submit`, `order_created`, `booking_created`, `lead_created`.
Emit the matching code only after the named UI transition or business write
succeeds. The typed helper sends no arbitrary properties and works with custom
schemas through the authenticated `/api/omnia/telemetry` route. Allocate one UUID
per logical event and pass `{ eventId }`; reuse it for telemetry retries, never
repeat a business write because telemetry failed. Catch delivery errors separately
from business success. Do not duplicate a receipt already forwarded by
`createMaxAction`: exact canonical action types automatically supply their detail;
legacy arbitrary names remain generic and must not be guessed from free text.
`trackMaxEvent` remains the local-properties API for compatible stock schemas.
Do not send names, contacts, payloads or MAX profiles to platform analytics.
Never call a public helper for `app_open`; only verified login records opens.
Updating the managed kit alone does not instrument existing custom product flows.
When the brief requests AI, use the exact typed call
`const { answer } = await requestYleumAI({ message, instructions, context })`
from `@/lib/omnia/integration-client`; the managed Google model runs server-side.
Never embed a provider key in source or expose one to the browser. If a user pastes
a credential into chat, do not write it or create an .env file: Yleum handles
credentials only through the encrypted Studio Integration Hub.

If the project sandbox shell is available, `bash` runs only inside the isolated
project container without network: use it for offline generators, tests,
migrations and data transforms, not as host/root access. Follow the appended
MAX DATA EVOLUTION POLICY for persistent data. Add dependencies by
editing `package.json`; Yleum installs them separately with lifecycle scripts
disabled. Managed MAX files stay locked even when shell is available. If shell
is unavailable, fall back to read_file/edit_file/write_file. On a later surgical
edit, preserve working behaviour and change only the relevant product files.
""".strip()
)


def enable_catalog_runtime_manifest(content: str) -> str:
    """Opt a valid portable MAX source into public config without altering services.

    Broken/incomplete drafts remain for the normal manifest validator/agent to
    repair. Unknown newer protocols are never silently downgraded.
    """
    try:
        manifest = json.loads(content)
    except (TypeError, ValueError):
        return content
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("version")) is not int
        or manifest.get("version") != 1
        or not isinstance(manifest.get("services"), list)
        or not isinstance(manifest.get("routes"), list)
    ):
        return content
    protocol = manifest.get("app_config_protocol")
    if type(protocol) is int and protocol == 1:
        return content
    if protocol is not None:
        raise ValueError("Unsupported application configuration protocol")
    return json.dumps({**manifest, "app_config_protocol": 1}, ensure_ascii=False, indent=2) + "\n"


def include_portable_manifest(
    starter_files: Mapping[str, str],
    workspace_files: Mapping[str, str],
    *,
    restoring_core: bool = False,
) -> dict[str, str]:
    """Pair the freshly rendered core with the workspace's portable settings."""
    files = dict(starter_files)
    path = ".omnia/cell.json"
    if path in workspace_files:
        manifest = enable_catalog_runtime_manifest(workspace_files[path])
        if restoring_core:
            manifest = _core_test_manifest(manifest, files.get("package.json", ""))
        files[path] = manifest
    return files


def _core_test_manifest(manifest_source: str, package_source: str) -> str:
    # A resumed first build may still carry the product's final-test script.
    # Its tests and dependencies are absent from the rendered core. Verify this
    # tree using its actual test script; never rewrite an accepted product.
    try:
        manifest = json.loads(manifest_source)
        package = json.loads(package_source)
    except (ValueError, TypeError):
        return manifest_source
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not (
        isinstance(manifest, dict) and manifest.get("app_config_protocol") == 1
        and isinstance(manifest.get("tasks"), list)
        and isinstance(scripts, dict) and scripts.get("test")
    ):
        return manifest_source
    if "test:full" in scripts:
        return manifest_source
    for task in manifest["tasks"]:
        if (
            isinstance(task, dict) and task.get("role") == "full_build"
            and task.get("name") == "final-test" and task.get("cwd", ".") == "."
            and task.get("argv") in (["pnpm", "test:full"], ["pnpm", "run", "test:full"])
        ):
            task["argv"] = ["pnpm", "test"]
            return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    return manifest_source


_PORTABLE_MAX_IDENTITY_CATALOG_SQL = (
    "SELECT pg_catalog.to_regclass('public.max_users') IS NOT NULL AS materialize"
)
_PORTABLE_MAX_IDENTITY_INSERT_SQL = (
    "INSERT INTO public.max_users (max_user_id, first_name) VALUES ($1, '') "
    "ON CONFLICT (max_user_id) DO NOTHING"
)


def render_portable_max_session(project_id: UUID | str) -> str:
    """Adapt product auth to the secretless, gateway-authenticated runtime.

    The trusted core owns MAX identity. Older product schemas may reference a
    local parent; preserve that behavior only when the physical relation exists.
    """
    source = _template_file("src/lib/max/session.ts")
    source = source.replace(
        'import { cookies, headers } from "next/headers";\n',
        'import { cookies, headers } from "next/headers";\n\n'
        'import { pool } from "@/lib/db";\n',
        1,
    )
    start = source.index("export async function getMaxUser()")
    end = source.index("export async function requireMaxUser()", start)
    helper = """export async function getMaxUser(): Promise<MaxSessionUser | null> {
  // Only the portable product server uses this adapter. Its gateway strips
  // incoming identity headers and injects the authenticated session identity.
  const incoming = await headers();
  const id = incoming.get("x-omnia-user-id");
  const projectId = incoming.get("x-omnia-project-id");
  const epoch = incoming.get("x-omnia-session-epoch");
  if (!id?.trim() || projectId !== __PROJECT_ID__ || !epoch || !/^[0-9]+$/.test(epoch)) {
    return null;
  }
  // A schema.ts export does not prove that a legacy parent exists. Do not cache:
  // project migrations can change the physical schema while this process is alive.
  const result = await pool.query(__IDENTITY_CATALOG_SQL__);
  const materialize = result.rows[0]?.materialize;
  if (materialize === true) {
    await pool.query(__IDENTITY_INSERT_SQL__, [id]);
  } else if (materialize !== false) {
    throw new Error("MAX identity catalog result is invalid");
  }
  return { id };
}

""".replace("__PROJECT_ID__", json.dumps(str(project_id))).replace(
        "__IDENTITY_CATALOG_SQL__", json.dumps(_PORTABLE_MAX_IDENTITY_CATALOG_SQL)
    ).replace("__IDENTITY_INSERT_SQL__", json.dumps(_PORTABLE_MAX_IDENTITY_INSERT_SQL))
    return source[:start] + helper + source[end:]


def render_portable_max_managed_files(
    config: MaxProjectConfigPayload,
    project_id: UUID | str,
) -> dict[str, str]:
    """Current managed kit for a secretless portable product runtime."""
    files = render_max_managed_files(config, project_id)
    files["src/lib/max/session.ts"] = render_portable_max_session(project_id)
    return files


def render_max_starter_files(
    config: MaxProjectConfigPayload,
    project_id: UUID | str | None = None,
    *,
    portable: bool = False,
) -> dict[str, str]:
    """Buildable MAX platform core with no generated product UI.

    Security/session/legal primitives stay deterministic.  Product design and
    feature architecture do not: the Google agent must create the home page and
    product styling from scratch instead of inheriting a deceptively finished UI.
    """
    files = {
        # Seed-only project-owned files. Config saves and managed-kit upgrades
        # must never overwrite dependencies or a relational schema the agent
        # has evolved after the first build.
        "package.json": _template_file("package.json"),
        "pnpm-lock.yaml": _template_file("pnpm-lock.yaml"),
        "tests/starter.test.mjs": _template_file("tests/starter.test.mjs"),
        "src/lib/db/schema.ts": _template_file("src/lib/db/schema.ts"),
        # The migrations that create the platform tables. They already exist in
        # the materialized workspace, but until they were seeded here the file
        # set the platform reasons about did not contain them at all — so losing
        # them could not be noticed until a signed request hit a table that had
        # never been created (LIVE-04).
        "drizzle/0000_max_core.sql": _template_file("drizzle/0000_max_core.sql"),
        "drizzle/0001_business_core.sql": _template_file("drizzle/0001_business_core.sql"),
        **render_max_managed_files(config, project_id),
        "src/app/globals.css": _template_file("src/app/globals.css"),
    }
    if portable:
        if project_id is None:
            raise ValueError("portable MAX starter requires a project identity")
        # Match the portable orchestrator seed; migrations run only through the
        # controller-owned canonical SQL runner, never legacy schema push.
        package = json.loads(files["package.json"])
        package["scripts"].pop("db:push", None)
        package["scripts"].pop("db:generate", None)
        files["package.json"] = json.dumps(package, indent=2) + "\n"
        files.update(render_portable_max_managed_files(config, project_id))
        files["src/lib/db/schema.ts"] = (
            "// PORTABLE PRODUCT DATABASE: the legacy core exports below are type-compatible\n"
            "// definitions, not physical product tables. Core 0000/0001 SQL is skipped.\n"
            "// Add project-owned tables and canonical SQL. Store getMaxUser().id as text\n"
            "// without a max_users FK unless your own migration creates a local parent.\n"
            + files["src/lib/db/schema.ts"]
        )
    return files
