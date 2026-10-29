"""Bounded AST consumer admission, independent of runtime module probes."""

import json

import pytest

from yleum_api.services.max_management_contract import build_management_contract
from yleum_api.services.max_management_generation import (
    management_implemented_capabilities,
    management_probe_resources,
    management_source_gap,
)


def files(source, modules=("catalog", "orders"), **extra):
    return {
        ".omnia/management.json": build_management_contract(modules).model_dump_json(),
        "src/app/page.tsx": source,
        **extra,
    }


PAGE = """
import {useState, useEffect} from 'react';
import {useYleumAppProfile as useProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog as useCatalog} from '@/lib/omnia/catalog-client';
import {createManagedOrder as createOrder,
  getManagedRecords as readRecords} from '@/lib/omnia/business-client';
export default function Page() {
  const {profile} = useProfile();
  const {items} = useCatalog();
  const [records, setRecords] = useState<any[]>([]);
  useEffect(() => { async function load() {
    const response = await readRecords('orders'); setRecords(response.records);
  } void load(); }, []);
  async function submit(event: any) {
    event.preventDefault();
    const input: any = {operationKey: 'op', customerName: 'Customer',
      items: [{catalogId: 'a', quantity: 1}]};
    await createOrder(input);
  }
  return <main><h1>{profile?.app_name}</h1>
    {items.map(item => <article key={item.id}>{item.title}</article>)}
    {records.map(record => <p key={record.id}>{record.customer_name}</p>)}
    <form onSubmit={submit}><button>Send</button></form></main>;
}
"""


def test_live_aliases_effect_state_history_and_typed_submit_are_connected():
    source = files(PAGE)
    assert management_source_gap(source) is None
    assert management_probe_resources(source) == ("orders",)
    assert management_implemented_capabilities(source) == frozenset({"catalog", "history"})


def test_real_child_consumes_parent_handler_prop():
    source = PAGE.replace(
        "export default function Page()",
        "import Form from '@/components/Form';\nexport default function Page()",
    )
    source = source.replace(
        "<form onSubmit={submit}><button>Send</button></form>", "<Form onSave={submit} />"
    )
    child = (
        "export default function Form({onSave}: {onSave: any}) "
        "{return <button onClick={onSave}>Send</button>}"
    )
    assert management_source_gap(files(source, **{"src/components/Form.tsx": child})) is None
    child = "export default function Form({onSave}: {onSave: any}) {return <button>Send</button>}"
    assert management_source_gap(files(source, **{"src/components/Form.tsx": child}))


def test_invented_intrinsic_event_does_not_connect_write():
    source = files(PAGE.replace("onSubmit={submit}", "onNotARealEvent={submit}"))
    assert management_source_gap(source)
    assert not management_implemented_capabilities(source)


@pytest.mark.parametrize(
    "before,after",
    [
        ("<h1>{profile?.app_name}</h1>", "<h1 data-profile={profile?.app_name}>Static</h1>"),
        (
            "<article key={item.id}>{item.title}</article>",
            "<article key={item.id} data-title={item.title}>Static</article>",
        ),
        (
            "<p key={record.id}>{record.customer_name}</p>",
            "<p key={record.id} data-name={record.customer_name}>Static</p>",
        ),
    ],
)
def test_metadata_attributes_do_not_claim_visible_output(before, after):
    source = files(PAGE.replace(before, after))
    assert management_source_gap(source)
    assert not management_implemented_capabilities(source)


def test_visible_form_value_and_image_alt_are_supported_output():
    source = PAGE.replace("<h1>{profile?.app_name}</h1>", "<input value={profile?.app_name} />")
    source = source.replace(
        "<article key={item.id}>{item.title}</article>", "<img key={item.id} alt={item.title} />"
    )
    assert management_source_gap(files(source)) is None


@pytest.mark.parametrize(
    "before,after",
    [
        ("onSubmit={submit}", "onSubmit={() => {}}"),
        ("onSubmit={submit}", "disabled onSubmit={submit}"),
        ("onSubmit={submit}", "hidden onSubmit={submit}"),
        ("void load();", ""),
        ("setRecords(response.records);", ""),
        ("readRecords('orders')", "readRecords('leads')"),
        ("{records.map(record => <p key={record.id}>{record.customer_name}</p>)}", ""),
        ("{items.map(item => <article key={item.id}>{item.title}</article>)}", ""),
        ("{profile?.app_name}", "Static name"),
        ("await createOrder(input);", "if (false) { await createOrder(input); }"),
        ("await createOrder(input);", "if (!true) { await createOrder(input); }"),
        (
            "<form onSubmit={submit}><button>Send</button></form>",
            "{false && <button onClick={submit}>Send</button>}",
        ),
        ("await createOrder(input);", "await createOrder({});"),
        ("await createOrder(input);", "return; await createOrder(input);"),
        (
            "{items.map(item => <article key={item.id}>{item.title}</article>)}",
            "{items.map(item => <article key={item.id}>Static</article>)}",
        ),
        (
            "const {profile} = useProfile();",
            'const useProfile = () => ({profile: {app_name: "forged"}}); '
            "const {profile} = useProfile();",
        ),
        (
            "async function submit(event: any)",
            "async function submit(event: any, createOrder: any)",
        ),
    ],
)
def test_disconnected_or_forged_consumers_cannot_pass(before, after):
    source = files(PAGE.replace(before, after))
    assert management_source_gap(source)
    assert management_implemented_capabilities(source) != frozenset({"catalog", "history"})


def test_strings_comments_and_managed_files_are_not_product_consumers():
    source = files("export default function Page(){return <h1>Static</h1>}")
    source["src/lib/omnia/fake.tsx"] = PAGE
    source["src/components/Dead.tsx"] = PAGE
    source["src/app/page.tsx"] += "\n// " + PAGE.replace("\n", " ")
    assert management_source_gap(source)
    assert not management_implemented_capabilities(source)


@pytest.mark.parametrize(
    "resource,method,capability",
    [
        ("leads", "createManagedLead", "history"),
        ("bookings", "createManagedBooking", "booking"),
    ],
)
def test_other_business_resources_use_actual_sdk_names(resource, method, capability):
    source = PAGE.replace("createManagedOrder", method).replace("'orders'", repr(resource))
    source = files(source, ("catalog", resource) if resource == "bookings" else (resource,))
    assert management_source_gap(source) is None
    assert management_probe_resources(source) == (resource,)
    assert capability in management_implemented_capabilities(source)


def test_empty_new_selection_still_requires_rendered_profile():
    assert management_source_gap(files(PAGE, ())) is None
    assert management_source_gap(files("export default function Page(){return <div/>}", ()))


def test_actual_profile_support_field_is_a_settings_consumer():
    assert (
        management_source_gap(
            files(PAGE.replace("profile?.app_name", "profile?.support.email"), ())
        )
        is None
    )


def test_create_call_outside_customer_event_cannot_claim_a_form():
    source = PAGE.replace("await createOrder(input);", "")
    source = source.replace(
        "const {profile} = useProfile();",
        "const {profile} = useProfile(); createOrder({customerName: 'probe'});",
    )
    assert management_source_gap(files(source))


def test_shadowed_local_sdk_and_unused_import_do_not_grant_creation():
    source = PAGE.replace(
        "async function submit(event: any)",
        "function createOrder(input: any) {} async function submit(event: any)",
    )
    assert management_source_gap(files(source))


def test_arrow_root_and_promise_destructuring_state_connection():
    source = PAGE.replace("export default function Page() {", "const Page = () => {")
    source += "\nexport default Page;"
    source = source.replace(
        "const response = await readRecords('orders'); setRecords(response.records);",
        "await readRecords('orders').then(({records}) => setRecords(records));",
    )
    assert management_source_gap(files(source)) is None


def test_legacy_missing_manifest_is_unchanged():
    source = {"src/app/page.tsx": PAGE}
    assert management_source_gap(source) is None
    assert management_probe_resources(source) == ()
    assert not management_implemented_capabilities(source)


@pytest.mark.parametrize("mutation", ["hash", "field", "json"])
def test_invalid_manifest_fails_closed(mutation):
    source = files(PAGE)
    contract = json.loads(source[".omnia/management.json"])
    if mutation == "hash":
        contract["contract_hash"] = "0" * 64
    elif mutation == "field":
        contract["resources"][0]["table"] = "secret"
    source[".omnia/management.json"] = "broken" if mutation == "json" else json.dumps(contract)
    assert management_source_gap(source)
    assert not management_implemented_capabilities(source)
    with pytest.raises(ValueError):
        management_probe_resources(source)


# Minimal compiler-shaped version of the private candidate's history pipeline.
# The private QA page itself is not committed as a product fixture.
AGGREGATE_LOAD = """
const parts = await Promise.all(
  (['orders', 'leads', 'bookings'] as const).map(async resource => {
    const r = await readRecords(resource, {limit: 50});
    return r.records.map(record => ({resource: resource as string, record}));
  }),
);
setRecords(parts.flat().sort((a, b) => b.record.created_at.localeCompare(a.record.created_at)));
"""
AGGREGATE_PAGE = (
    PAGE.replace(
        "getManagedRecords as readRecords",
        "createManagedLead, createManagedBooking, getManagedRecords as readRecords",
    )
    .replace(
        "const response = await readRecords('orders'); setRecords(response.records);",
        AGGREGATE_LOAD,
    )
    .replace(
        "await createOrder(input);",
        "await createOrder(input); await createManagedLead(input); "
        "await createManagedBooking(input);",
    )
    .replace(
        "records.map(record => <p key={record.id}>{record.customer_name}</p>)",
        "records.map(({resource, record}) => <p key={record.id}>{record.status}</p>)",
    )
)


def aggregate_files(source):
    return files(source, ("catalog", "orders", "leads", "bookings"))


def test_literal_resource_tuple_async_projection_flat_sort_state_and_visible_history():
    source = aggregate_files(AGGREGATE_PAGE)
    assert management_source_gap(source) is None
    assert management_implemented_capabilities(source) == frozenset(
        {"catalog", "history", "booking"}
    )


@pytest.mark.parametrize(
    "load",
    [
        "const r = await readRecords('orders'); setRecords(r.records.map(record => record));",
        "await Promise.all([readRecords('orders')]).then(([r]) => setRecords(r.records));",
    ],
)
def test_finite_identity_projection_and_promise_tuple_destructuring(load):
    source = PAGE.replace(
        "const response = await readRecords('orders'); setRecords(response.records);",
        load,
    )
    assert management_source_gap(files(source)) is None


@pytest.mark.parametrize(
    "before,after",
    [
        ("['orders', 'leads', 'bookings']", "[]"),
        ("['orders', 'leads', 'bookings']", "sharedResources"),
        ("['orders', 'leads', 'bookings']", "['leads']"),
        ("['orders', 'leads', 'bookings']", "Array.from(sharedResources)"),
        ("readRecords(resource, {limit: 50})", "readRecords(dynamicResource, {limit: 50})"),
        ("r.records.map(record => ({resource: resource as string, record}))", "[]"),
        (
            "r.records.map(record => ({resource: resource as string, record}))",
            "[{resource, record: {status: 'fixed'}}]",
        ),
        ("({resource: resource as string, record})", "({resource, record: {status: 'fixed'}})"),
        ("({resource: resource as string, record})", "({resource, record: opaque(record)})"),
        ("({resource: resource as string, record})", "({resource, record: null})"),
        (
            "({resource: resource as string, record})",
            "({resource, record: record.status === record.status})",
        ),
        ("({resource: resource as string, record})", "({resource, record, ...fakeRecords})"),
        (
            "({resource: resource as string, record})",
            "(false ? {resource, record} : {resource, record: null})",
        ),
        (
            "parts.flat().sort((a, b) => b.record.created_at.localeCompare(a.record.created_at))",
            "parts.flat().filter(() => false)",
        ),
        (
            "parts.flat().sort((a, b) => b.record.created_at.localeCompare(a.record.created_at))",
            "parts.flat().sort(opaque)",
        ),
        (
            "parts.flat().sort((a, b) => b.record.created_at.localeCompare(a.record.created_at))",
            "parts.flat().sort((a, b) => { parts.length = 0; return 0; })",
        ),
        ("setRecords(parts.flat()", "parts.length = 0; setRecords(parts.flat()"),
        ("setRecords(parts.flat()", "parts.splice(0); setRecords(parts.flat()"),
        ("setRecords(parts.flat()", "opaque(parts); setRecords(parts.flat()"),
        (
            "const parts = await Promise.all(",
            "const Promise = {all: (parts: any) => []}; const parts = await Promise.all(",
        ),
        (
            "const parts = await Promise.all(",
            "Promise.all = opaque; const parts = await Promise.all(",
        ),
        (
            "const parts = await Promise.all(",
            "const readRecords = async (_resource: any) => ({records: []}); "
            "const parts = await Promise.all(",
        ),
        ("{record.status}", "Fixed history"),
        ("{record.status}", "{record.status ? 'fixed' : 'fixed'}"),
        ("<p key={record.id}>{record.status}</p>", "<p key={record.id} hidden>{record.status}</p>"),
        ("void load();", ""),
        ("@/lib/omnia/business-client", "@/lib/unknown-business-client"),
    ],
)
def test_aggregate_history_requires_visible_untransformed_trusted_records(before, after):
    # Reject typo/no-op mutations so every negative exercises a distinct source.
    assert before in AGGREGATE_PAGE and before != after
    assert management_source_gap(aggregate_files(AGGREGATE_PAGE.replace(before, after)))


def test_resource_tuple_cardinality_is_bounded():
    oversized = "[" + ",".join(["'orders'"] * 17) + "]"
    source = AGGREGATE_PAGE.replace("['orders', 'leads', 'bookings']", oversized)
    assert management_source_gap(files(source))


@pytest.mark.parametrize(
    "mutation",
    [
        "opaque(Promise);",
        "Object.assign(Promise, {all: opaque});",
        "const P = Promise; opaque(P);",
        "const P = Promise; P.all = opaque;",
    ],
)
def test_opaque_global_promise_mutation_cannot_preserve_trust(mutation):
    source = AGGREGATE_PAGE.replace(
        "const parts = await Promise.all(", mutation + "const parts = await Promise.all("
    )
    assert management_source_gap(aggregate_files(source))


def test_mutating_response_records_cannot_recreate_history_by_rereading_property():
    source = PAGE.replace(
        "setRecords(response.records);", "response.records.splice(0); setRecords(response.records);"
    )
    assert management_source_gap(files(source))


def test_late_shadowed_promise_is_not_the_global_builtin():
    source = AGGREGATE_PAGE.replace(
        "setRecords(parts.flat()", "const Promise = {all: opaque}; setRecords(parts.flat()"
    )
    assert management_source_gap(aggregate_files(source))


def test_empty_state_replacement_erases_previous_collection_evidence():
    source = AGGREGATE_PAGE.replace(
        "const parts = await Promise.all(", "setRecords([]); const parts = await Promise.all("
    )
    source = source.replace(
        "setRecords(parts.flat().sort((a, b) => "
        "b.record.created_at.localeCompare(a.record.created_at)));",
        "setRecords(parts.flat()); setRecords([]);",
    )
    assert management_source_gap(aggregate_files(source))


def test_supported_collection_size_boundary():
    at_limit = "[" + ",".join(["'orders'"] * 16) + "]"
    assert (
        management_source_gap(
            files(AGGREGATE_PAGE.replace("['orders', 'leads', 'bookings']", at_limit))
        )
        is None
    )


def test_collection_callback_steps_exhaustion_fails_closed():
    from yleum_api.services.max_management_generation import _Connections

    prelude = (
        "const keys = ["
        + ",".join(["'orders'"] * 16)
        + "]; keys.map(a => keys.map(b => keys.map(c => c)));"
    )
    source = PAGE.replace("return <main>", prelude + "return <main>")
    connections = _Connections(files(source))
    connections.analyze()
    assert connections.steps > 4000
    assert "budget" in management_source_gap(files(source))


def test_sparse_tuple_cannot_shift_record_response_into_an_empty_position():
    source = PAGE.replace(
        "const response = await readRecords('orders'); setRecords(response.records);",
        "await Promise.all([,readRecords('orders')]).then(([r]) => setRecords(r.records));",
    )
    assert management_source_gap(files(source))


@pytest.mark.parametrize(
    "before,after",
    [
        ("const parts = await Promise.all(", "const parts = Promise.all("),
        ("const r = await readRecords", "const r = readRecords"),
        (
            "record => ({resource: resource as string, record})",
            "async record => ({resource: resource as string, record})",
        ),
        (
            "record => ({resource: resource as string, record})",
            "record => { try { return {resource, record}; } "
            "finally { return {resource, record: null}; } }",
        ),
    ],
)
def test_history_rejects_unresolved_promises_and_overridden_returns(before, after):
    assert before in AGGREGATE_PAGE
    assert management_source_gap(aggregate_files(AGGREGATE_PAGE.replace(before, after)))


@pytest.mark.parametrize(
    "invocation",
    [
        "new Mutator(Promise);",
        "new Mutator(...[Promise]);",
        "const args = [Promise] as const; new Mutator(...args);",
        "new Mutator(...[...[Promise]]);",
        "opaque(...[Promise]);",
    ],
)
def test_constructor_and_spread_arguments_cannot_preserve_builtin_authority(invocation):
    mutator = (
        "class Mutator { constructor(api: typeof Promise) { "
        "api.all = (() => Promise.resolve([])) as unknown as typeof Promise.all; } }"
    )
    source = AGGREGATE_PAGE.replace(
        "export default function Page", mutator + "export default function Page"
    )
    source = source.replace(
        "const parts = await Promise.all(", invocation + "const parts = await Promise.all("
    )
    assert management_source_gap(aggregate_files(source))


@pytest.mark.parametrize("arguments", ["parts", "...[parts]", "...[[...parts]]"])
def test_constructor_arguments_cannot_preserve_mutated_record_collection(arguments):
    source = AGGREGATE_PAGE.replace(
        "export default function Page",
        "class Mutator { constructor(data: any[][]) { "
        "for (const rows of data) rows.splice(0); data.splice(0); } } "
        "export default function Page",
    )
    source = source.replace(
        "setRecords(parts.flat()", "new Mutator(" + arguments + "); setRecords(parts.flat()"
    )
    assert management_source_gap(aggregate_files(source))


def test_constructor_without_shared_provenance_does_not_reject_valid_history():
    source = AGGREGATE_PAGE.replace(
        "const parts = await Promise.all(",
        "new Date('2026-10-29'); const parts = await Promise.all(",
    )
    assert management_source_gap(aggregate_files(source)) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "delete parts[0]; delete parts[1]; delete parts[2];",
        "(parts as any).length = 0;",
        "parts[0].splice(0); parts[1].splice(0); parts[2].splice(0);",
        "const alias = parts; delete ((alias as any))[0];",
        "const alias = parts; ((alias as any)).length -= 3;",
        "const alias = parts; --((alias as any)).length;",
        "const alias = parts; (alias as any)[dynamicIndex]['splice'](0);",
        "const alias = parts[0]; alias.splice(0);",
        "const alias = (parts as any)[dynamicIndex]; opaque(alias);",
    ],
)
def test_collection_mutation_roots_survive_wrappers_indices_and_aliases(mutation):
    source = AGGREGATE_PAGE.replace("setRecords(parts.flat()", mutation + "setRecords(parts.flat()")
    assert management_source_gap(aggregate_files(source))


@pytest.mark.parametrize(
    "mutation",
    [
        "(Promise as any).all = () => Promise.resolve([]);",
        "delete (Promise as unknown as {all?: unknown}).all;",
        "const api = Promise; delete ((api as any)).all;",
        "const api = Promise; ((api as any))['all'] = opaque;",
    ],
)
def test_builtin_mutation_roots_survive_wrappers_and_aliases(mutation):
    source = AGGREGATE_PAGE.replace(
        "const parts = await Promise.all(", mutation + "const parts = await Promise.all("
    )
    assert management_source_gap(aggregate_files(source))


def test_mutating_unrelated_wrapped_value_preserves_real_collection():
    source = AGGREGATE_PAGE.replace(
        "setRecords(parts.flat()",
        "const unrelated = {all: 1}; delete (unrelated as any).all; setRecords(parts.flat()",
    )
    assert management_source_gap(aggregate_files(source)) is None


def test_indexed_collection_alias_has_no_readable_record_provenance():
    source = AGGREGATE_PAGE.replace(
        "setRecords(parts.flat().sort((a, b) => "
        "b.record.created_at.localeCompare(a.record.created_at)));",
        "setRecords(parts[dynamicIndex]);",
    )
    assert management_source_gap(aggregate_files(source))


@pytest.mark.parametrize("hook,field", [("useCatalog", "items"), ("useProfile", "profile")])
def test_trusted_hook_refresh_preserves_connected_public_data(hook, field):
    source = PAGE.replace(
        "const {" + field + "} = " + hook + "();",
        "const result = " + hook + "(); result.refresh(); const {" + field + "} = result;",
    )
    assert management_source_gap(files(source)) is None
    source = source.replace(
        "result.refresh();", "(result as any).refresh = opaque; result.refresh();"
    )
    assert management_source_gap(files(source))
