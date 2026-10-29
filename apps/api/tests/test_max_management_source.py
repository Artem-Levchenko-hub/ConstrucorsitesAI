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
