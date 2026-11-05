"""New management is explicit; legacy config/data never silently gains privileges."""

import json
from pathlib import Path
from subprocess import run
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.max_content_delivery import public_business_config
from yleum_api.services.max_management_contract import build_management_contract
from yleum_api.services.max_management_generation import (
    linked_checkout_connected,
    management_implemented_capabilities,
    management_source_gap,
)
from yleum_api.services.max_project_kit import (
    MAX_SECURITY_LOCKED_FILES,
    render_max_managed_files,
)


def payload(**extra):
    return MaxProjectConfigPayload.model_validate(
        {
            "app_name": "Мастерская",
            "app_type": "booking",
            "summary": "Запрос на запись к мастеру",
            **extra,
        }
    )


def test_legacy_config_has_no_implicit_business_management():
    config = payload()
    assert config.management_modules is None
    assert public_business_config(config).get("management_modules") is None


@pytest.mark.parametrize(
    "modules",
    [
        ["orders"],
        ["bookings"],
        ["catalog", "orders", "orders"],
        ["private_diaries"],
        [True],
        "orders",
    ],
)
def test_invalid_management_selection_is_rejected(modules):
    with pytest.raises(ValidationError):
        payload(management_modules=modules)


def test_management_selection_is_private_and_catalog_patch_preserves_it():
    config = payload(management_modules=["bookings", "catalog", "leads"])
    assert [str(item.value) for item in config.management_modules] == [
        "catalog",
        "leads",
        "bookings",
    ]
    config.content = []
    saved = MaxProjectConfigPayload.model_validate(config.model_dump(mode="json"))
    assert saved.management_modules == config.management_modules
    assert "management_modules" not in public_business_config(config)
    files = render_max_managed_files(config, uuid4())
    public_source = files["src/lib/omnia/max-config.ts"]
    assert '"management_modules"' not in public_source
    manifest = json.loads(files[".omnia/management.json"])
    assert manifest["modules"] == ["catalog", "leads", "bookings"]
    assert [r["id"] for r in manifest["resources"]] == ["leads", "bookings"]


def test_new_runtime_manifest_and_sdk_are_platform_owned():
    files = render_max_managed_files(payload(management_modules=["catalog", "orders"]), uuid4())
    expected = {
        ".omnia/management.json",
        "src/lib/omnia/management-config.ts",
        "src/lib/omnia/management-auth.ts",
        "src/lib/omnia/management-store.ts",
        "src/lib/omnia/management-resources.ts",
        "src/lib/omnia/business-client.ts",
        "src/lib/omnia/business-server.ts",
        "src/app/api/omnia/owner/route.ts",
        "src/app/api/omnia/business/[resource]/route.ts",
        "drizzle/0003_owner_management.sql",
        "src/lib/omnia/app-profile-client.ts",
        "src/lib/omnia/app-profile-actions.ts",
    }
    assert expected <= files.keys()
    assert expected <= MAX_SECURITY_LOCKED_FILES
    assert "useYleumAppProfile" in files["src/lib/omnia/app-profile-client.ts"]


def test_legacy_managed_kit_has_no_order_capability():
    files = render_max_managed_files(payload(), uuid4())
    assert ".omnia/management.json" not in files
    assert "drizzle/0003_owner_management.sql" not in files


CONNECTION_MODULES = (
    (), ("leads",), ("catalog",), ("catalog", "leads"), ("catalog", "orders"),
    ("catalog", "bookings"), ("catalog", "orders", "leads"),
    ("catalog", "orders", "bookings"), ("catalog", "leads", "bookings"),
    ("catalog", "orders", "leads", "bookings"),
)
CREATE_METHOD = {
    "orders": "createManagedOrder", "leads": "createManagedLead",
    "bookings": "createManagedBooking",
}


CHECKOUT_PAGE = """
import { useEffect } from 'react';
import { createManagedOrder as saveOrder } from '@/lib/omnia/business-client';
import {
    createYleumOrderCheckout as startCheckout,
    getYleumOrderCheckout as readCheckout,
    getReturnedYleumCheckoutOrderId as returnedOrder,
    createOmniaPayment as legacyPayment,
} from '@/lib/omnia/integration-client';

export default function Page() {
    useEffect(() => {
        const orderId = returnedOrder();
        if (orderId) void readCheckout(orderId);
    }, []);
    async function placeOrder() {
        const order = await saveOrder({ customer_name: 'Тест' });
        await startCheckout(order.id);
    }
    return <button onClick={placeOrder}>Заказать</button>;
}
"""


def test_linked_checkout_uses_canonical_order_from_live_submit_and_loads_status():
    assert linked_checkout_connected({"src/app/page.tsx": CHECKOUT_PAGE})


def test_linked_checkout_accepts_live_checkout_with_local_error_handling():
    page = CHECKOUT_PAGE.replace(
        "await startCheckout(order.id);",
        "try { await startCheckout(order.id); } catch (error) { console.error(error); }",
    )
    assert linked_checkout_connected({"src/app/page.tsx": page})


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("await startCheckout(order.id);", "// await startCheckout(order.id);"),
        ("await startCheckout(order.id);", "'startCheckout(order.id)';"),
        ("await startCheckout(order.id);", "if (false) await startCheckout(order.id);"),
        (
            "await startCheckout(order.id);",
            "async function unused() { await startCheckout(order.id); }",
        ),
        ("await startCheckout(order.id);", "await startCheckout('fake-order-id');"),
        ("await startCheckout(order.id);", "await startCheckout(order.reference);"),
        ("if (orderId) void readCheckout(orderId);", "const unused = () => readCheckout(orderId);"),
        ("if (orderId) void readCheckout(orderId);", "if (false) void readCheckout(orderId);"),
        ("if (orderId) void readCheckout(orderId);", "void readCheckout('fake-order-id');"),
        ("onClick={placeOrder}", "onClick={() => {}}"),
        ("onClick={placeOrder}", "disabled onClick={placeOrder}"),
        ("return <button", "return null; return <button"),
        ("@/lib/omnia/integration-client", "@/lib/my-checkout"),
        (
            "await startCheckout(order.id);",
            "await startCheckout(order.id); await legacyPayment({});",
        ),
        (
            "await startCheckout(order.id);",
            "try { await startCheckout(order.id); } catch (error) { await legacyPayment({}); }",
        ),
        (
            "await startCheckout(order.id);",
            "try { await startCheckout(order.id); } finally { await legacyPayment({}); }",
        ),
        (
            "await startCheckout(order.id);",
            "try { return; } finally {} await startCheckout(order.id);",
        ),
        (
            "if (orderId) void readCheckout(orderId);",
            "if (orderId) void readCheckout(orderId); else void legacyPayment({});",
        ),
        (
            "export default function Page()",
            "void legacyPayment({}); export default function Page()",
        ),
    ],
)
def test_linked_checkout_rejects_unreachable_wrong_or_legacy_calls(before, after):
    assert not linked_checkout_connected({"src/app/page.tsx": CHECKOUT_PAGE.replace(before, after)})


def test_linked_checkout_rejects_legacy_payment_from_relative_namespace_import():
    page = CHECKOUT_PAGE.replace(
        "export default function Page()",
        "import * as sdk from '../lib/omnia/integration-client'; export default function Page()",
    ).replace(
        "await startCheckout(order.id);",
        "await startCheckout(order.id); await sdk.createYleumPayment({});",
    )
    assert not linked_checkout_connected({"src/app/page.tsx": page})


def test_linked_checkout_accepts_imported_event_helper_and_ignores_dead_legacy_code():
    event = """
import { createManagedOrder } from '@/lib/omnia/business-client';
import { createYleumOrderCheckout, createYleumPayment } from '@/lib/omnia/integration-client';
export async function placeOrder() {
    const { id } = await createManagedOrder({ customer_name: 'Тест' });
    return createYleumOrderCheckout(id);
}
function oldCheckout() { return createYleumPayment({}); }
"""
    start = CHECKOUT_PAGE.index("    async function placeOrder")
    end = CHECKOUT_PAGE.index("    return <button", start)
    page = (
        "import { placeOrder } from '@/lib/checkout';\n"
        + CHECKOUT_PAGE[:start]
        + CHECKOUT_PAGE[end:]
    )
    assert linked_checkout_connected({"src/app/page.tsx": page, "src/lib/checkout.ts": event})


def connection_example(modules):
    """Synthetic typed connection shapes, not a complete app or business inputs."""
    declarations = ["const {profile} = useYleumAppProfile();"]
    rendered = ["<h1>{profile?.app_name}</h1>"]
    inputs = []
    if "catalog" in modules:
        inputs.append("onChoose: (item: YleumMaxContentItem, option?: string) => void")
        declarations += [
            "const catalog = useYleumCatalog();",
            "const selection = useYleumCatalogSelection(catalog.items);",
            "const onChoose = inputs.onChoose;",
            "const ready = catalog.status === 'ready' && catalog.configVersion !== null;",
        ]
        rendered += [
            '<p role="status">{catalog.status === "loading" ? "Loading catalog" : ""}</p>',
            '<p role="alert">{catalog.error}</p>',
            """{catalog.items.map(item => item.active &&
              (selection.category === null || item.category === selection.category) ?
              <article key={item.id}><h2>{item.title}</h2><span>{item.price}</span>
                <button disabled={!ready || item.availability !== "in_stock" ||
                  (item.options.length > 0 && !selection.getOption(item))}
                  onClick={() => onChoose(item, selection.getOption(item))}>Choose</button>
              </article> : null)}""",
        ]
    for resource, method in CREATE_METHOD.items():
        if resource not in modules:
            continue
        inputs.append(f"{resource}: Parameters<typeof {method}>[0]")
        declarations += [
            f"const [{resource}, set_{resource}] = useState<ManagedRecord[]>([]);",
            f"""useEffect(() => {{ async function load() {{
              const response = await getManagedRecords('{resource}', {{limit: 50}});
              set_{resource}(response.records);
            }} void load(); }}, []);""",
            f"""async function submit_{resource}(event: FormEvent<HTMLFormElement>) {{
              event.preventDefault(); await {method}({{...inputs.{resource},
                operationKey: inputs.{resource}.operationKey}});
            }}""",
        ]
        rendered += [
            f'<form onSubmit={{submit_{resource}}}><button>Send {resource}</button></form>',
            f"{{{resource}.map(record => <p key={{record.id}}>"
            "{record.customer_name} {record.status}</p>)}",
        ]
    source = """
import {useEffect, useState, type FormEvent} from 'react';
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog, useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
import {createManagedOrder, createManagedLead, createManagedBooking,
  getManagedRecords} from '@/lib/omnia/business-client';
import type {ManagedRecord} from '@/lib/omnia/management-resources';
import type {YleumMaxContentItem} from '@/lib/omnia/max-config';
"""
    return (
        source + "\nexport default function Connections(inputs: {" + ";".join(inputs) + "}) {\n"
        + "\n".join(declarations) + "\nreturn <main>" + "\n".join(rendered) + "</main>;\n}\n"
    )


def connection_files(source, modules):
    return {
        ".omnia/management.json": build_management_contract(modules).model_dump_json(),
        "src/app/page.tsx": source,
    }


CATALOG_CATEGORY_PAGE = """
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog, useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
declare function opaque(value: unknown): any;
declare function compareCategory(candidate: unknown, item: unknown): boolean;
declare const opaquePredicate: (candidate: unknown) => boolean;
declare const opaqueThis: unknown;
const staticItem = {category: 'fixed'};
export default function Page() {
  const profile = useYleumAppProfile();
  const {items} = useYleumCatalog();
  const selection = useYleumCatalogSelection(items);
  return <main>
    {items.map((item, index) => item.active && item.category &&
      items.findIndex((candidate) => candidate.category === item.category) === index ?
      <button key={item.category} onClick={() => selection.setCategory(item.category)}>
        {item.category}
      </button> : null)}
    {items.map(item => <article key={item.id}><h2>{item.title}</h2><p>{item.price}</p></article>)}
    <h1>{profile.profile?.app_name}</h1>
  </main>;
}
"""


def test_read_only_category_find_index_keeps_later_live_catalog_rendering():
    files = connection_files(CATALOG_CATEGORY_PAGE, ("catalog",))
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset({"catalog"})


def test_passive_catalog_selection_destructuring_keeps_locked_category_field():
    source = CATALOG_CATEGORY_PAGE.replace(
        "const selection = useYleumCatalogSelection(items);",
        "const {category, setCategory, getOption, selectOption} = useYleumCatalogSelection(items);",
    ).replace("selection.setCategory", "setCategory")
    assert management_source_gap(connection_files(source, ("catalog",))) is None


def test_find_index_preserves_proven_collection_alias_and_local_predicate():
    source = CATALOG_CATEGORY_PAGE.replace(
        "  return <main>", "  const alias = items;\n  return <main>",
    ).replace(
        "items.map((item, index) => item.active && item.category &&\n"
        "      items.findIndex((candidate) => candidate.category === item.category) === index ?",
        "items.map((item, index) => {\n"
        "      const sameCategory = (candidate: typeof item) => "
        "candidate.category === item.category;\n"
        "      return item.active && item.category && alias.findIndex(sameCategory) === index ?",
    ).replace("</button> : null)}", "</button> : null; })}")
    assert source != CATALOG_CATEGORY_PAGE
    assert management_source_gap(connection_files(source, ("catalog",))) is None


def test_find_index_numeric_result_is_not_rendered_catalog_evidence():
    source = CATALOG_CATEGORY_PAGE.replace(
        "<button key={item.category} onClick={() => selection.setCategory(item.category)}>\n"
        "        {item.category}\n      </button>",
        "<span>{items.findIndex(candidate => candidate.category === item.category)}</span>",
    ).replace(
        "{items.map(item => <article key={item.id}><h2>{item.title}</h2>"
        "<p>{item.price}</p></article>)}",
        "",
    )
    assert source != CATALOG_CATEGORY_PAGE
    gap = management_source_gap(connection_files(source, ("catalog",)))
    assert gap is not None and gap.startswith("Connect useYleumCatalog items")


@pytest.mark.parametrize("predicate", [
    "(candidate) => { items.splice(0); return candidate.category === item.category; }",
    "(candidate) => { candidate.category = 'fake'; return true; }",
    "(candidate) => { const alias = items; alias.length = 0; return true; }",
    "(candidate) => { const alias = candidate; delete alias.category; return true; }",
    "(candidate) => { opaque(items); return candidate.category === item.category; }",
    "(candidate) => compareCategory(candidate, item)",
    "opaquePredicate",
    "async (candidate) => candidate.category === item.category",
    "(candidate = opaque(items)) => candidate.category === item.category",
    "(...candidates) => candidates[0].category === item.category",
    "(candidate, index, rows) => candidate.category === item.category",
    "(candidate) => candidate.category === opaque(item.category)",
    "(candidate) => candidate.category === (item.category = 'fake')",
    "(candidate) => candidate.category === staticItem.category",
    "(candidate) => candidate.category === 'fixed'",
    "(candidate) => candidate.title === item.title",
])
def test_find_index_keeps_unproven_or_mutating_callbacks_fail_closed(predicate):
    source = CATALOG_CATEGORY_PAGE.replace(
        "(candidate) => candidate.category === item.category", predicate,
    )
    assert source != CATALOG_CATEGORY_PAGE
    gap = management_source_gap(connection_files(source, ("catalog",)))
    assert gap is not None and gap.startswith("Connect useYleumCatalog items")


@pytest.mark.parametrize("mutation", [
    "const alias = items; alias.splice(0);",
    "const alias = items; ((alias as any)).length = 0;",
    "const alias = items; opaque(alias);",
    "(items as any).findIndex = opaquePredicate;",
    "delete (items as any).findIndex;",
])
def test_find_index_cannot_recreate_trust_after_aliased_catalog_mutation(mutation):
    source = CATALOG_CATEGORY_PAGE.replace("  return <main>", "  " + mutation + "\n  return <main>")
    assert management_source_gap(connection_files(source, ("catalog",)))


@pytest.mark.parametrize("operation", [
    "filter((candidate) => candidate.category === item.category)",
    "find((candidate) => candidate.category === item.category)",
    "findLastIndex((candidate) => candidate.category === item.category)",
    "findIndex((candidate) => candidate.category === item.category, opaqueThis)",
])
def test_find_index_does_not_admit_other_collection_methods_or_overloads(operation):
    source = CATALOG_CATEGORY_PAGE.replace(
        "findIndex((candidate) => candidate.category === item.category)", operation,
    )
    assert management_source_gap(connection_files(source, ("catalog",)))


def test_find_index_does_not_turn_static_rows_into_sdk_catalog():
    source = CATALOG_CATEGORY_PAGE.replace(
        "const {items} = useYleumCatalog();",
        "const items = [{id: 'fixed', title: 'Fixed', category: 'fake'}];",
    )
    assert management_source_gap(connection_files(source, ("catalog",)))


def test_find_index_retains_ast_budget_fail_closed():
    source = CATALOG_CATEGORY_PAGE.replace(
        "  return <main>",
        "  " + "".join(f"const unused{index} = 0;" for index in range(4500)) + "\n  return <main>",
    )
    assert management_source_gap(connection_files(source, ("catalog",))) == (
        "Managed source connection analysis exceeded its bounded AST budget."
    )


LEAD_STATUS_PAGE = """
import {useEffect, useState, type FormEvent} from 'react';
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {createManagedLead, getManagedRecords} from '@/lib/omnia/business-client';
export default function Page() {
  const {profile} = useYleumAppProfile();
  const [status, setStatus] = useState('idle');
  const [leads, setLeads] = useState<any[]>([]);
  async function refresh() { setLeads((await getManagedRecords('leads')).records); }
  useEffect(() => { void refresh(); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (status === 'pending') return;
    await createManagedLead({operationKey: 'fixture-operation', customerName: 'Fixture'});
    setStatus('ok');
    await refresh();
  }
  return <main><h1>{profile?.app_name}</h1>
    <form onSubmit={submit}><button type="submit">Send</button></form>
    {leads.map(record => <p key={record.id}>{record.customer_name} {record.status}</p>)}
  </main>;
}
"""


@pytest.mark.parametrize("guard", ["status === 'pending'", "'pending' === status"])
def test_status_comparison_does_not_hide_live_form_creation(guard):
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", guard)
    files = connection_files(source, ("leads",))
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset({"history"})


@pytest.mark.parametrize("guard", [
    "true", "true === true", "false !== true", "false !== 0", "true !== 1",
    "'1' !== 1", "1 === 1.0", "null === null", "null !== false", "0 === -0",
    "!'nonempty' === false", "true || opaque(leads)", "'nonempty' ?? false",
    "'same' == 'same'", "1 == 1.0", "false == false", "null == null",
])
def test_known_true_comparison_guards_keep_unreachable_create_rejected(guard):
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", guard)
    assert management_source_gap(connection_files(source, ("leads",)))


@pytest.mark.parametrize("guard", [
    "false", "false === true", "false === 0", "true === 1", "'1' === 1",
    "1 !== 1.0", "null !== null", "null === false", "''", "0", "-0",
    "false && opaque(leads)", "'' || false", "null ?? false",
    "unknownValue === unknownValue", "unknownValue === null", "NaN === NaN",
])
def test_false_or_unknown_comparison_guards_keep_potential_creation(guard):
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", guard)
    assert management_source_gap(connection_files(source, ("leads",))) is None


@pytest.mark.parametrize("disconnect", ["create", "history"])
def test_status_comparison_still_requires_actual_create_and_history(disconnect):
    source = LEAD_STATUS_PAGE.replace(
        "await createManagedLead(" if disconnect == "create"
        else "setLeads((await getManagedRecords('leads')).records)",
        "await Promise.resolve(" if disconnect == "create" else "setLeads([])",
    )
    assert management_source_gap(connection_files(source, ("leads",)))


@pytest.mark.parametrize("field", ["profile", "catalog", "history"])
@pytest.mark.parametrize("operator", ["===", "!==", "==", "!=", "<", ">", "<=", ">="])
@pytest.mark.parametrize("reverse", [False, True])
def test_comparison_boolean_is_not_rendered_managed_data(field, operator, reverse):
    source = connection_example(("catalog", "leads"))

    def comparison(value):
        return f"'fixed' {operator} {value}" if reverse else f"{value} {operator} 'fixed'"

    if field == "profile":
        source = source.replace("{profile?.app_name}", "{" + comparison("profile?.app_name") + "}")
    elif field == "catalog":
        source = source.replace(
            "<h2>{item.title}</h2><span>{item.price}</span>",
            "<h2>{" + comparison("item.title") + "}</h2>",
        )
    else:
        source = source.replace(
            "{record.customer_name} {record.status}",
            "{" + comparison("record.customer_name") + "}",
        )
    assert source != connection_example(("catalog", "leads"))
    assert management_source_gap(connection_files(source, ("catalog", "leads")))


def test_comparison_cannot_forward_sdk_field_into_visible_input_value():
    source = LEAD_STATUS_PAGE.replace(
        "<h1>{profile?.app_name}</h1>", '<input value={"fixed" === profile?.app_name} />',
    )
    gap = management_source_gap(connection_files(source, ("leads",)))
    assert gap is not None and gap.startswith("Connect useYleumAppProfile")


@pytest.mark.parametrize("condition", [
    "opaque(leads) === true",
    "(async () => { const alias = leads; alias.splice(0); })() === true",
])
def test_comparison_still_visits_opaque_or_async_mutation_before_guard(condition):
    # The normal post-submit refresh legitimately restores live records. Omit
    # that reload here so the earlier opaque mutation must remain observable.
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", condition).replace(
        "    await refresh();", "",
    )
    assert management_source_gap(connection_files(source, ("leads",)))


@pytest.mark.parametrize("condition", [
    "((arg = leads.splice(0)) => false)() === true",
    "(function(arg = leads.splice(0)) { return false; })() === true",
    "(async (arg = leads.splice(0)) => false)() === true",
    "(({value = leads.splice(0)} = {}) => false)() === true",
    "(([value = leads.splice(0)] = []) => false)() === true",
    "(({[leads.splice(0).length]: value} = {}) => false)() === true",
    "(({[opaque(leads)]: value}) => false)({}) === true",
    "(({value}) => false)({get value() { leads.splice(0); return 1; }}) === true",
    "((arg) => { const {value = leads.splice(0)} = {}; return false; })() === true",
    "(() => { const [value = leads.splice(0)] = []; return false; })() === true",
    "(() => { const {[opaque(leads)]: value} = {}; return false; })() === true",
    "(() => { const rows = leads; function helper(arg = rows.splice(0)) { return false; } "
    "const alias = helper; return alias(); })() === true",
])
def test_comparison_does_not_hide_parameter_or_binding_initializer_effects(condition):
    from yleum_api.services.max_management_generation import _Connections

    source = LEAD_STATUS_PAGE.replace("status === 'pending'", condition).replace(
        "    await refresh();", "",
    )
    files = connection_files(source, ("leads",))
    assert not _Connections(files).programs["src/app/page.tsx"].has_error
    assert management_source_gap(files)


@pytest.mark.parametrize("probe", [
    "((arg = leads.splice(0)) => false)()",
    "(function(arg = leads.splice(0)) { return false; })()",
    "(async (arg = leads.splice(0)) => false)()",
    "(({value = leads.splice(0)} = {}) => false)()",
    "(({[opaque(leads)]: value}) => false)({})",
    "(({value}) => false)({get value() { leads.splice(0); return 1; }})",
    "((arg) => { const {value} = arg; return false; })"
    "({get value() { leads.splice(0); return 1; }})",
    "(() => { const {[opaque(leads)]: value} = {}; return false; })()",
    "((arg = true) => arg)()",
])
def test_hoisted_probe_cannot_hide_default_or_binding_effects_from_later_comparison(probe):
    from yleum_api.services.max_management_generation import _Connections

    source = LEAD_STATUS_PAGE.replace(
        "    if (status === 'pending') return;",
        "    const probe = " + probe + ";\n    if (probe === true) return;",
    ).replace("    await refresh();", "")
    files = connection_files(source, ("leads",))
    assert not _Connections(files).programs["src/app/page.tsx"].has_error
    assert management_source_gap(files)


@pytest.mark.parametrize("condition", [
    "((arg: unknown) => false)(status) === true",
    "(async (arg: unknown) => false)(status) === true",
    "(function(arg: unknown) { return false; })(status) === true",
    "(({value}: {value: boolean}) => value)({value: false}) === true",
    "(([value]: boolean[]) => value)([false]) === true",
    "(() => { const {flag} = {flag: false}; return flag; })() === true",
    "(() => { const [flag] = [false]; return flag; })() === true",
])
def test_comparison_keeps_plain_async_or_typed_parameter_calls_supported(condition):
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", condition)
    assert management_source_gap(connection_files(source, ("leads",))) is None


@pytest.mark.parametrize("statements", [
    "const source = {get value() { leads.splice(0); return 1; }}; const copy = {...source};",
    "const source = {value: 0, get value() { leads.splice(0); return 1; }}; "
    "const copy = {...source};",
    "const copy = {[leads.splice(0).length]: 1};",
    "const copy = {[leads.splice(0).length]() { return 1; }};",
    "class X { static value = leads.splice(0); }",
    "class X { static { leads.splice(0); } }",
    "const X = class { static value = leads.splice(0); };",
    "enum X { value = leads.splice(0).length }",
    "const source = unknownSource(); const copy = {...source};",
    "const source = {value: 1}; opaque(source); const copy = {...source};",
    "const label = `${leads.splice(0)}`;",
    "const value = (leads.splice(0), false);",
])
@pytest.mark.parametrize("hoist", [False, True])
def test_comparison_cannot_drop_unproven_object_or_class_construction_effects(statements, hoist):
    from yleum_api.services.max_management_generation import _Connections

    probe = "(() => { " + statements + " return false; })()"
    guard = ("const probe = " + probe + "; if (probe === true) return;" if hoist
             else "if (" + probe + " === true) return;")
    source = LEAD_STATUS_PAGE.replace("if (status === 'pending') return;", guard).replace(
        "    await refresh();", "",
    )
    files = connection_files(source, ("leads",))
    assert not _Connections(files).programs["src/app/page.tsx"].has_error
    assert management_source_gap(files)


@pytest.mark.parametrize("statements", [
    "const copy = {value: false};",
    "const value = false; const copy = {value};",
    "const source = {value: false}; const copy = {...source};",
    "const source = {}; const copy = {...source};",
    "const copy = {...leads};",
    "type Shape = {value: boolean}; interface OtherShape { value: boolean; }",
    "const label = `Status: ${status}`;",
])
def test_passive_object_spreads_and_type_only_declarations_remain_supported(statements):
    source = LEAD_STATUS_PAGE.replace(
        "status === 'pending'", "(() => { " + statements + " return false; })() === true",
    )
    assert management_source_gap(connection_files(source, ("leads",))) is None


def test_opaque_callback_receiver_cannot_keep_later_input_spread_passive():
    source = connection_example(("catalog", "leads")).replace(
        "const onChoose = inputs.onChoose;", "",
    ).replace("onChoose(item,", "inputs.onChoose(item,")
    gap = management_source_gap(connection_files(source, ("catalog", "leads")))
    assert gap is not None and "cannot prove call or binding effects" in gap


@pytest.mark.parametrize("key", ["__proto__", '"__proto__"'])
def test_unproven_prototype_initializer_cannot_forge_passive_data(key):
    source = LEAD_STATUS_PAGE.replace(
        "status === 'pending'",
        "(() => { const copy = {" + key + ": unknownSource()}; "
        "const {value} = copy; return false; })() === true",
    )
    assert management_source_gap(connection_files(source, ("leads",)))


@pytest.mark.parametrize("probe", [
    "(({missing: {value}}) => false)({})",
    "(() => { const source = {}; const {missing: {value}} = source; return false; })()",
])
def test_spread_passivity_does_not_prove_missing_destructured_properties(probe):
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", probe + " === true")
    assert management_source_gap(connection_files(source, ("leads",)))


@pytest.mark.parametrize("condition", [
    "0 == '0'", "0 != '0'", "1 + 1 === 2", "'x' < 'y'", r"'\u0078' === 'x'",
])
def test_known_unsupported_comparison_control_fails_closed(condition):
    source = LEAD_STATUS_PAGE.replace("status === 'pending'", condition)
    gap = management_source_gap(connection_files(source, ("leads",)))
    assert gap == "Managed source connection analysis cannot prove unsupported constant control."


@pytest.mark.parametrize("expression", [
    "false && <h1>{profile?.app_name}</h1>",
    "true || <h1>{profile?.app_name}</h1>",
    "(true === false) && <h1>{profile?.app_name}</h1>",
    "('same' == 'same') || <h1>{profile?.app_name}</h1>",
    "'fixed' ?? <h1>{profile?.app_name}</h1>",
])
def test_comparison_short_circuit_does_not_render_unreachable_sdk_jsx(expression):
    source = LEAD_STATUS_PAGE.replace("<h1>{profile?.app_name}</h1>", "{" + expression + "}")
    gap = management_source_gap(connection_files(source, ("leads",)))
    assert gap is not None and gap.startswith("Connect useYleumAppProfile")


@pytest.mark.parametrize("reverse", [False, True])
def test_comparing_collection_map_to_something_is_not_history_rendering(reverse):
    original = "leads.map(record => <p key={record.id}>{record.customer_name} {record.status}</p>)"
    expression = f"something === ({original})" if reverse else f"({original}) === something"
    source = LEAD_STATUS_PAGE.replace("{" + original + "}", "{" + expression + "}")
    assert source != LEAD_STATUS_PAGE
    assert management_source_gap(connection_files(source, ("leads",)))


def test_comparison_map_operand_keeps_mutation_effects_before_later_history():
    source = LEAD_STATUS_PAGE.replace(
        "    {leads.map(record =>",
        "    {(leads.map(record => { leads.length = 0; return record; })) === something}\n"
        "    {leads.map(record =>",
    )
    assert source != LEAD_STATUS_PAGE
    assert management_source_gap(connection_files(source, ("leads",)))


def test_comparison_unmounted_form_does_not_activate_its_create_handler():
    form = '<form onSubmit={submit}><button type="submit">Send</button></form>'
    source = LEAD_STATUS_PAGE.replace(
        form, "{(" + form + ") === something}",
    ).replace("status === 'pending'", "'pending' === status")
    assert source != LEAD_STATUS_PAGE
    assert management_source_gap(connection_files(source, ("leads",)))


def test_comparison_unmounted_jsx_still_evaluates_child_expression_effects():
    source = LEAD_STATUS_PAGE.replace(
        "    {leads.map(record =>",
        "    {(<div>{opaque(leads)}</div>) === something}\n    {leads.map(record =>",
    )
    assert management_source_gap(connection_files(source, ("leads",)))


def test_comparison_unmounted_component_cannot_load_history_through_effect_hook():
    source = LEAD_STATUS_PAGE.replace(
        "  useEffect(() => { void refresh(); }, []);",
        "  function HiddenHistory() { useEffect(() => { void refresh(); }, []); return null; }",
    ).replace("    await refresh();", "").replace(
        "    {leads.map(record =>",
        "    {(<HiddenHistory />) === something}\n    {leads.map(record =>",
    )
    assert management_source_gap(connection_files(source, ("leads",)))


@pytest.mark.parametrize("modules", CONNECTION_MODULES)
def test_supported_direct_jsx_connections_cover_every_valid_module_combination(modules):
    files = connection_files(connection_example(modules), modules)
    expected = set()
    if "catalog" in modules:
        expected.add("catalog")
    if set(modules) & CREATE_METHOD.keys():
        expected.add("history")
    if "bookings" in modules:
        expected.add("booking")
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset(expected)


@pytest.mark.parametrize("before,after", [
    ("{profile?.app_name}", "Static app"),
    ("<h2>{item.title}</h2><span>{item.price}</span>", "<h2>Static item</h2>"),
    ("useYleumCatalogSelection(catalog.items)", "useYleumCatalogSelection([])"),
    ("await createManagedOrder(", "await Promise.resolve("),
    ("set_orders(response.records)", "set_orders([])"),
])
def test_disconnected_connections_keep_original_reason_and_explain_supported_shape(before, after):
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(before, after)
    gap = management_source_gap(connection_files(source, modules))
    assert gap is not None and gap.startswith("Connect ")
    assert "cannot prove" in gap
    assert "direct JSX" in gap and "useMemo" in gap
    assert not management_implemented_capabilities(connection_files(source, modules))


@pytest.mark.parametrize("resource,method", CREATE_METHOD.items())
@pytest.mark.parametrize("disconnect", ["create", "history"])
def test_each_business_module_rejects_disconnected_form_or_history(resource, method, disconnect):
    modules = ("catalog", resource)
    source = connection_example(modules)
    if disconnect == "create":
        source = source.replace(f"await {method}(", "await Promise.resolve(")
    else:
        source = source.replace(f"set_{resource}(response.records)", f"set_{resource}([])")
    gap = management_source_gap(connection_files(source, modules))
    assert gap is not None and gap.startswith(f"Connect {method}")
    assert "cannot prove" in gap


def unsupported_memo_example():
    return (
        connection_example(("catalog",))
        .replace("useEffect, useState", "useEffect, useMemo, useState")
        .replace("const ready =", "const shown = useMemo(() => catalog.items, [catalog.items]);\n"
                 "const ready =")
        .replace("catalog.items.map", "shown.map")
    )


def test_valid_but_unsupported_memo_keeps_the_fail_closed_subset():
    gap = management_source_gap(connection_files(unsupported_memo_example(), ("catalog",)))
    assert gap is not None and gap.startswith("Connect useYleumCatalog")
    assert "cannot prove" in gap and "useMemo" in gap
    assert management_source_gap({"src/app/page.tsx": "export default () => <div/>"}) is None
    invalid = {"src/app/page.tsx": "", ".omnia/management.json": "invalid"}
    assert management_source_gap(invalid).startswith("Invalid .omnia/management.json:")


def test_connection_examples_typecheck_against_real_sdk(tmp_path):
    apps = Path(__file__).resolve().parents[2]
    modules = apps / "web/node_modules"
    template = apps / "orchestrator/templates/max-miniapp-nextjs"
    compiler = template / "node_modules/typescript/lib/tsc.js"
    if not compiler.is_file():
        compiler = modules / "typescript/lib/tsc.js"
    if not compiler.is_file() or not (template / "node_modules/@types/pg/index.d.ts").is_file():
        pytest.skip("Real TypeScript/managed-template dependencies are not installed")
    for source in (template / "src").rglob("*"):
        if source.is_file() and source.suffix in {".ts", ".tsx"}:
            target = tmp_path / source.relative_to(template)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    for relative, content in render_max_managed_files(payload(), uuid4()).items():
        if relative.endswith((".ts", ".tsx")):
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    for index, selected in enumerate(CONNECTION_MODULES):
        (tmp_path / f"connection-{index}.tsx").write_text(
            connection_example(selected), encoding="utf-8",
        )
    (tmp_path / "connection-unsupported.tsx").write_text(
        unsupported_memo_example(), encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {
            "strict": True, "target": "ES2022", "module": "ESNext", "jsx": "react-jsx",
            "moduleResolution": "Bundler", "noEmit": True, "skipLibCheck": True,
            "baseUrl": str(tmp_path), "paths": {
                "@/*": ["src/*"],
                "react": [str(template / "node_modules/@types/react/index.d.ts")],
                "react/*": [str(template / "node_modules/@types/react/*")],
                "pg": [str(template / "node_modules/@types/pg/index.d.ts")],
                "*": [str(template / "node_modules/*"), str(modules / "*")],
            },
            "typeRoots": [str(template / "node_modules/@types"), str(modules / "@types")],
            "types": ["node", "react"],
        },
        "include": ["connection-*.tsx"],
    }), encoding="utf-8")
    result = run(["node", str(compiler), "--project", str(tmp_path)],
                 capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
