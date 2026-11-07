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


CATALOG_ID_LOOKUP_PAGE = """
import {useState} from 'react';
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog, useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
declare function opaque(value: unknown): void;
export default function Page() {
  const profile = useYleumAppProfile();
  const catalog = useYleumCatalog();
  const selection = useYleumCatalogSelection(catalog.items);
  const [selectedId] = useState('first');
  const selected = catalog.items.find(item => item.id === selectedId);
  const option = selected ? selection.getOption({id: selected.id}) : undefined;
  return <main>
    <h1>{profile.profile?.app_name}</h1>
    <p>{selected?.title}</p>
    {selected && <span>{String(selected.price)} {option}</span>}
    {selected?.options.length ? selected.options.map(value => <span>{value}</span>) : null}
    {catalog.items.map(item => <article key={item.id}>{item.title}</article>)}
  </main>;
}
"""


def test_read_only_catalog_id_lookup_keeps_later_live_catalog_rendering():
    files = connection_files(CATALOG_ID_LOOKUP_PAGE, ("catalog",))
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset({"catalog"})


@pytest.mark.parametrize("suffix,inner_lookup", [
    (") ?? null", ""),
    (" && item.active)", ""),
    (" && item.active) ?? null", """
  function readSelectedTitle() {
    const selected = catalog.items.find(item => item.id === selectedId);
    return selected?.title;
  }
"""),
])
def test_read_only_active_catalog_lookup_with_null_fallback_keeps_live_rendering(
    suffix, inner_lookup,
):
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "item.id === selectedId);", f"item.id === selectedId{suffix};", 1,
    ).replace("  const option =", inner_lookup + "  const option =")
    files = connection_files(source, ("catalog",))
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset({"catalog"})


def test_nullable_catalog_lookup_keeps_selected_booking_form_reachable():
    source = connection_example(("catalog", "bookings")).replace(
        "const selection = useYleumCatalogSelection(catalog.items);",
        "const selection = useYleumCatalogSelection(catalog.items);\n"
        "const selectedId = 'class';\n"
        "const selected = catalog.items.find(item => item.id === selectedId "
        "&& item.active) ?? null;",
    ).replace(
        "<form onSubmit={submit_bookings}><button>Send bookings</button></form>",
        "{selected && <form onSubmit={submit_bookings}><button>Send bookings</button></form>}",
    )
    files = connection_files(source, ("catalog", "bookings"))
    assert management_source_gap(files) is None
    assert {"catalog", "booking"} <= management_implemented_capabilities(files)


def test_catalog_lookup_rejection_identifies_the_cause_across_unrelated_edits():
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "item.id === selectedId", "item.id === selectedId && opaque(item)", 1,
    )
    files = connection_files(source, ("catalog",))
    first = management_source_gap(files)
    assert first is not None and first.rejection_key() is not None
    assert "unproven_catalog_id_lookup" in first

    reformatted = management_source_gap({
        **files,
        "src/app/page.tsx": "\n" + source.replace("opaque(item)", "opaque( item )"),
        "src/app/globals.css": "main { color: red; }",
    })
    different = management_source_gap({
        **files,
        "src/app/page.tsx": source.replace("opaque(item)", "opaque(item.options)"),
    })
    assert reformatted is not None and reformatted.rejection_key() == first.rejection_key()
    assert different is not None and different.rejection_key() != first.rejection_key()


def test_catalog_lookup_rejection_locates_use_in_caller_of_imported_predicate():
    source = "import {same} from './same';\n" + CATALOG_ID_LOOKUP_PAGE.replace(
        "catalog.items.find(item => item.id === selectedId)", "catalog.items.find(same)",
    ).replace("  return <main>", "  opaque(selected);\n  return <main>")
    files = {
        **connection_files(source, ("catalog",)),
        "src/app/same.ts": "export const same = item => item.id === 'first';",
    }
    gap = management_source_gap(files)
    assert gap is not None and "unproven_catalog_lookup_use" in gap
    assert "Source location: src/app/page.tsx:" in gap


def test_catalog_lookup_rejection_distinguishes_repairs_to_imported_predicate():
    source = "import {same} from './same';\n" + CATALOG_ID_LOOKUP_PAGE.replace(
        "catalog.items.find(item => item.id === selectedId)", "catalog.items.find(same)",
    )
    files = connection_files(source, ("catalog",))
    first = management_source_gap({
        **files,
        "src/app/same.ts": "export const same = item => item.id === 'first' && !item.active;",
    })
    second = management_source_gap({
        **files,
        "src/app/same.ts": "export const same = item => item.id === 'first' && opaque(item);",
    })
    assert first is not None and "Source location: src/app/same.ts:" in first
    assert second is not None and second.rejection_key() != first.rejection_key()


@pytest.mark.parametrize("decoration", [
    "", "{separate.items.map(() => <i>Marker</i>)}",
    "{separate.items.map(item => <i key={item.id}>Marker</i>)}",
])
def test_catalog_lookup_rejection_does_not_key_unrelated_catalog_progress(decoration):
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "  const selected =", "  const separate = useYleumCatalog();\n"
        "  const unused = separate.items.find(item => item.id === selectedId && !item.active);\n"
        "  const selected =", 1,
    ).replace("  return <main>", f"  return <main>\n    {decoration}")
    assert management_source_gap(connection_files(source, ("catalog",))) is None
    no_fields = source.replace("key={item.id}>{item.title}", "key={item.id}>No fields")
    gap = management_source_gap(connection_files(no_fields, ("catalog",)))
    assert gap is not None and gap.rejection_key() is None


def test_catalog_id_lookup_alone_is_not_a_rendered_catalog():
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "{catalog.items.map(item => <article key={item.id}>{item.title}</article>)}", "",
    )
    gap = management_source_gap(connection_files(source, ("catalog",)))
    assert gap is not None and gap.startswith("Connect useYleumCatalog items")


@pytest.mark.parametrize("statement", [
    "selected.title = 'local';",
    "opaque(selected);",
    "const [saved] = useState(selected); saved.title = 'local';",
    "const [saved, setSaved] = useState(null); setSaved(selected); saved.title = 'local';",
    "const row = selected ?? {title: 'fallback'}; row.title = 'local';",
    "const row = selected || {title: 'fallback'}; row.title = 'local';",
    "const [saved] = useState(() => selected); saved.title = 'local';",
    "let row = null; row = selected; if (row) row.title = 'local';",
    "const row = {selected}; row.selected.title = 'local';",
    "const change = () => { selected.title = 'local'; }; setTimeout(change, 0);",
    "selected.options.push('local');",
])
def test_catalog_id_lookup_does_not_hide_mutation_through_the_returned_row(statement):
    source = CATALOG_ID_LOOKUP_PAGE.replace("  return <main>", f"  {statement}\n  return <main>")
    assert management_source_gap(connection_files(source, ("catalog",))) is not None


@pytest.mark.parametrize("predicate", [
    "async item => item.id === selectedId",
    "item => item.id === opaque(selectedId)",
    "item => (item.id = selectedId) === selectedId",
    "item => item.id === selectedId, {}",
    "(item = opaque(catalog.items)) => item.id === selectedId",
])
def test_catalog_id_lookup_keeps_unproven_predicates_fail_closed(predicate):
    source = CATALOG_ID_LOOKUP_PAGE.replace("item => item.id === selectedId", predicate)
    assert management_source_gap(connection_files(source, ("catalog",))) is not None


def test_catalog_id_lookup_requires_id_to_be_initialized_before_lookup():
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "  const [selectedId] = useState('first');\n", "",
    ).replace(
        "  const selected = catalog.items.find(item => item.id === selectedId);",
        "  const selected = catalog.items.find(item => item.id === selectedId);\n"
        "  const selectedId = 'first';",
    )
    assert management_source_gap(connection_files(source, ("catalog",))) is not None


def test_catalog_id_lookup_does_not_borrow_a_binding_from_the_callers_scope():
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "  const [selectedId] = useState('first');",
        "  const compare = item => item.id === selectedId;\n"
        "  if (true) {\n    const selectedId = 'inner';",
    ).replace("find(item => item.id === selectedId)", "find(compare)").replace(
        "  </main>;\n}", "  </main>;\n  }\n  const selectedId = 'outer';\n}",
    )
    assert management_source_gap(connection_files(source, ("catalog",))) is not None


def test_managed_rejection_key_distinguishes_components_and_ignores_layout():
    source = """
declare function loadHeading(): {title: string};
function Header() { const heading = {...loadHeading()}; return <h1>{heading.title}</h1>; }
function Footer() { const heading = {...loadHeading()}; return <p>{heading.title}</p>; }
export default function Page() { return <main><Header/><Footer/></main>; }
"""
    initial = management_source_gap(connection_files(source, ("catalog",)))
    formatted = management_source_gap(connection_files(
        "\n" + source.replace("heading = ", "heading  = "), ("catalog",),
    ))
    first_fixed = management_source_gap(connection_files(source.replace(
        "const heading = {...loadHeading()};", "const heading = {title: 'fixed'};", 1,
    ), ("catalog",)))
    assert initial is not None and formatted is not None and first_fixed is not None
    assert initial.rejection_key() is not None
    assert initial.rejection_key() == formatted.rejection_key()
    assert initial.rejection_key() != first_fixed.rejection_key()


def test_managed_rejection_key_distinguishes_inline_event_handlers():
    source = """
declare function loadHeading(): {title: string};
export default function Page() {
  return <main>
    <button onClick={() => { const heading = {...loadHeading()}; }}>First</button>
    <button onClick={() => { const heading = {...loadHeading()}; }}>Second</button>
    <button onClick={() => { const heading = {...loadHeading()}; }}>Third</button>
  </main>;
}
"""
    keys = []
    for _ in range(3):
        gap = management_source_gap(connection_files(source, ("catalog",)))
        assert gap is not None and "unproven_object_spread" in gap
        key = gap.rejection_key()
        assert key is not None
        keys.append(key)
        source = source.replace("{...loadHeading()}", "{title: 'fixed'}", 1)
    assert len(set(keys)) == 3


def test_catalog_id_lookup_rejects_deferred_mutation_after_catalog_render():
    source = CATALOG_ID_LOOKUP_PAGE.replace(
        "  </main>;", "    <button onClick={() => { selected.title = 'local'; }}>Edit</button>\n"
        "  </main>;",
    )
    assert management_source_gap(connection_files(source, ("catalog",))) is not None


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


@pytest.mark.parametrize("statistics", [
    "const total = orders.reduce((sum, row) => sum + (row.total_kopecks ?? 0), 0);",
    "const count = orders.filter(row => row.status === 'new').length;",
    "const statuses = ['new', 'completed'] as const; "
    "const counts = statuses.map(status => ({status, count: "
    "orders.filter(row => row.status === status).length}));",
    "const matches = (row: ManagedRecord) => row.status === 'new'; "
    "const count = orders.filter(matches).length;",
])
def test_read_only_statistics_preserve_original_live_collection_connections(statistics):
    modules = ("catalog", "orders", "leads")
    source = connection_example(modules).replace("return <main>", statistics + "\nreturn <main>")
    files = connection_files(source, modules)
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset({"catalog", "history"})


@pytest.mark.parametrize("statistics", [
    "orders.filter(row => { orders.splice(0); return true; })",
    "orders.filter(row => opaque(row))",
    "orders.filter(async row => row.status === 'new')",
    "orders.filter((row = opaque(orders)) => row.status === 'new')",
    "orders.filter((...rows) => rows[0].status === 'new')",
    "orders.filter((row, index, rows) => rows.splice(0))",
    "orders.filter(row => row.status === unknownObject.status)",
    "orders.reduce((sum, row) => { opaque(orders); return sum; }, 0)",
    "orders.reduce((sum, row) => (orders.length = 0, sum), 0)",
    "orders.reduce((sum, row) => sum + row.total_kopecks, unknownInitial)",
    "orders.reduce((sum, row) => sum + row.total_kopecks)",
    "['new'].map(status => ({get count() { orders.splice(0); return 1; }}))",
    "['new'].map(status => ({[opaque(orders)]: status}))",
])
def test_statistics_do_not_silently_skip_opaque_or_mutating_callback_effects(statistics):
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(
        "return <main>", "const statistic = " + statistics + ";\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, modules))


@pytest.mark.parametrize("expression", [
    "orders.filter(row => row.status === 'new')",
    "orders.reduce((sum, row) => sum + row.total_kopecks, 0)",
])
def test_read_only_statistics_do_not_become_rendered_record_identity(expression):
    modules = ("catalog", "orders")
    source = connection_example(modules)
    original = (
        "{orders.map(record => <p key={record.id}>"
        "{record.customer_name} {record.status}</p>)}"
    )
    assert original in source
    source = source.replace("return <main>", "const statistic = " + expression + ";\nreturn <main>")
    source = source.replace(original, "<span>{statistic}</span>")
    gap = management_source_gap(connection_files(source, modules))
    assert gap is not None and gap.startswith("Connect createManagedOrder")


def test_pure_statistics_cannot_restore_a_previously_escaped_collection():
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(
        "return <main>",
        "const alias = orders; opaque(alias); "
        "const count = orders.filter(row => row.status === 'new').length;\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, modules))


def test_active_category_deduplication_preserves_the_live_catalog():
    source = CATALOG_CATEGORY_PAGE.replace(
        "candidate.category === item.category",
        "candidate.active && candidate.category === item.category",
    )
    files = connection_files(source, ("catalog",))
    assert management_source_gap(files) is None
    assert management_implemented_capabilities(files) == frozenset({"catalog"})


@pytest.mark.parametrize("predicate", [
    "candidate.active && candidate.category === staticItem.category",
    "opaque(candidate) && candidate.category === item.category",
    "candidate.active && (candidate.category = item.category)",
    "candidate.active && candidate.title === item.title",
])
def test_active_category_lookup_still_rejects_unproven_shapes(predicate):
    source = CATALOG_CATEGORY_PAGE.replace("candidate.category === item.category", predicate)
    gap = management_source_gap(connection_files(source, ("catalog",)))
    assert gap is not None and gap.startswith("Connect useYleumCatalog items")


@pytest.mark.parametrize("comments", [
    "{/* SDK values remain live; opaque(orders) here is comment text only. */}",
    "{\n// eslint-disable-next-line example\nprofile?.app_name}",
])
def test_jsx_comments_are_not_unproven_executable_effects(comments):
    source = LEAD_STATUS_PAGE.replace(
        "<h1>{profile?.app_name}</h1>", "<h1>{profile?.app_name}" + comments + "</h1>",
    )
    assert management_source_gap(connection_files(source, ("leads",))) is None


def test_unsupported_effect_diagnostic_names_actual_ast_location_without_source_values():
    source = LEAD_STATUS_PAGE.replace(
        "  return <main>",
        "  const label = (() => { switch (status) { case 'DO-NOT-ECHO-THIS-VALUE': "
        "return false; default: return true; } })();\n  return <main>",
    )
    gap = management_source_gap(connection_files(source, ("leads",)))
    assert gap is not None and "Source location: src/app/page.tsx:" in gap
    assert "AST switch_statement; reason unsupported_statement" in gap
    assert "DO-NOT-ECHO-THIS-VALUE" not in gap
    assert "instead of default, rest" not in gap


@pytest.mark.parametrize("initial", ["[]", "[{}]"])
def test_passive_local_array_loop_can_construct_local_request_rows(initial):
    modules = ("catalog", "orders", "leads")
    source = connection_example(modules).replace(
        "return <main>",
        "const rows = []; for (const line of " + initial + ") { "
        "const row = {catalogId: line.id, quantity: line.quantity}; "
        "if (line.option) { row.option = line.option; } rows.push(row); }\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, modules)) is None


@pytest.mark.parametrize("statement", [
    "orders.length = 0;",
    "const alias = orders; alias.splice(0);",
    "if (line.flag) { const n = 1; } else { orders.length = 0; }",
    "if (line.flag) { const n = 1; } else { opaque(orders); }",
    "if (line.flag) { const n = 1; } else { set_orders([]); }",
    "set_orders([]);",
    "set_orders((previous) => { opaque(orders); return previous; });",
    "const probe = (value = orders.splice(0)) => false; probe();",
    "const copy = {[opaque(orders)]: 1};",
    "const copy = {get value() { orders.splice(0); return 1; }};",
    "createManagedOrder({operationKey: 'not-a-witness'});",
    "function helper(value) { if (value.flag) {} else { orders.length = 0; } } helper(line);",
    "const helper = value => { if (value.flag) {} else { opaque(orders); } }; helper(line);",
    "([1].map(value => { if (value.flag) {} else { orders.length = 0; } })) === ignored;",
    "var shown = [];",
    "break;", "continue;", "return;", "throw new Error('unsupported');",
])
@pytest.mark.parametrize("initial", ["[]", "[{}]"])
def test_passive_loop_keeps_captured_effects_and_unproven_control_fail_closed(statement, initial):
    from yleum_api.services.max_management_generation import _Connections

    modules = ("catalog", "orders")
    source = connection_example(modules).replace(
        "return <main>",
        "for (const line of " + initial + ") { " + statement + " }\nreturn <main>",
    )
    files = connection_files(source, modules)
    assert not _Connections(files).programs["src/app/page.tsx"].has_error
    assert management_source_gap(files)


@pytest.mark.parametrize("loop", [
    "for (const line of orders) {}",
    "for (const line of [orders]) {}",
    "for (const line of [{saved: orders}]) {}",
    "for (const line of unknownSource()) {}",
    "for (const {id} of [{}]) {}",
    "for (var line of []) {}",
    "for (const line in []) {}",
])
def test_loop_admission_requires_a_local_passive_array_and_plain_scoped_binding(loop):
    source = connection_example(("catalog", "orders")).replace(
        "return <main>", loop + "\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, ("catalog", "orders")))


def test_loop_let_shadow_does_not_invalidate_outer_sdk_collection():
    source = connection_example(("catalog", "orders")).replace(
        "return <main>",
        "for (const orders of []) { const row = {id: orders.id}; }\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, ("catalog", "orders"))) is None


@pytest.mark.parametrize("operation", [
    "empty.filter(value => opaque(orders))",
    "empty.filter((value = opaque(orders)) => true)",
    "empty.reduce((sum, value) => opaque(orders), 0)",
    "empty.reduce((sum, ...values) => values, 0)",
    "['new'].map(status => ({status, count: empty.filter(value => opaque(orders)).length}))",
    "empty.map(value => opaque(orders))",
    "empty.map(value => set_orders([]))",
    "empty.map(value => { const object = {[opaque(orders)]: 1}; return object; })",
    "empty.map(value => { setLocal((previous = opaque(orders)) => previous); })",
])
@pytest.mark.parametrize("initializer", [
    "const empty = []; const [local, setLocal] = useState([]);",
    "const [empty, setLocal] = useState([]);",
])
def test_empty_local_collections_do_not_skip_captured_sdk_effects(operation, initializer):
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(
        "return <main>", initializer + " const computed = " + operation + ";\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, modules))


@pytest.mark.parametrize("expression", [
    "orders.filter(row => row.status === 'new')",
    "orders.reduce((previous, row) => row, 0)",
    "[1].reduce((previous, row) => orders, 0)",
])
def test_statistic_results_retain_sdk_alias_invalidation_without_render_witnesses(expression):
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(
        "return <main>", "const derived = " + expression + "; opaque(derived);\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, modules))


def test_nested_empty_pure_statistics_remain_supported():
    modules = ("catalog", "orders")
    source = connection_example(modules).replace(
        "return <main>",
        "const counts = [1].map(key => ({key, count: "
        "[].filter(row => row.status === 'new').length}));\nreturn <main>",
    )
    assert management_source_gap(connection_files(source, modules)) is None


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
