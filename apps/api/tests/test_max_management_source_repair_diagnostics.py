"""Repair feedback locates the actual refusal without changing source admission."""

import re

import pytest

from yleum_api.services import max_management_generation as management
from yleum_api.services.max_management_contract import build_management_contract

BASE = """
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog,useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
export default function Page(){
  const {profile}=useYleumAppProfile();const catalog=useYleumCatalog();
  useYleumCatalogSelection(catalog.items);
  const cart:Record<string,number>={sku:2};
  EXTRA
  return <main><h1>{profile?.app_name}</h1>
    {catalog.items.map(item=><p key={item.id}>{item.title}</p>)}
  </main>;
}
"""


def source_gap(extra: str, *, prefix: str = "", **files: str):
    source = prefix + BASE.replace("EXTRA", extra)
    return source, management.management_source_advisory({
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        "src/app/page.tsx": source,
        **files,
    })


def location(gap):
    match = re.search(r"Source location: ([^:]+):(\d+):(\d+); AST (\w+); reason (\w+)", gap)
    assert match
    return match.group(1), int(match.group(2)), int(match.group(3)), match.group(4), match.group(5)


@pytest.mark.parametrize("operation", [
    "Object.entries(cart)", "Object.values(cart)", "Object.fromEntries([])",
])
def test_first_native_call_is_located_before_the_later_loop(operation):
    source, gap = source_gap(
        f"const rows={operation};let count=0;"
        "for(const item of catalog.items){count+=1;}",
    )
    assert gap
    offset = source.index(operation)
    row = source[:offset].count("\n") + 1
    column = offset - source.rfind("\n", 0, offset)
    assert location(gap) == (
        "src/app/page.tsx", row, column, "call_expression", "unproven_native_intrinsic_effect",
    )


@pytest.mark.parametrize("statement,call", [
    ("const O=Object;const rows=O.entries(cart);", "O.entries(cart)"),
    ("opaque(Object);", "opaque(Object)"),
    ("opaque(Array);", "opaque(Array)"),
    ("function take(value){opaque(value);}take(Object);", "opaque(value)"),
])
def test_unknown_call_native_escape_keeps_its_actual_invocation_site(statement, call):
    source, gap = source_gap(statement)
    assert gap
    offset = source.index(call)
    assert location(gap)[1:3] == (
        source[:offset].count("\n") + 1, offset - source.rfind("\n", 0, offset),
    )
    assert location(gap)[4] == "unproven_native_intrinsic_effect"


def test_native_rejection_identity_ignores_line_shift_and_irrelevant_later_loop_edits():
    first = "const rows=Object.entries(cart);"
    _, original = source_gap(first + "let n=0;for(const item of catalog.items){n+=1;}")
    _, shifted = source_gap(
        first + "let total=0;for(const row of catalog.items){total+=2;}",
        prefix="// unrelated comment\n\n",
    )
    assert original and shifted
    assert location(shifted)[1] == location(original)[1] + 2
    assert original.rejection_key() == shifted.rejection_key()


@pytest.mark.parametrize("statement,reason", [
    ("Object.entries(cart);", "unproven_native_intrinsic_effect"),
    ("for(const row of catalog.items){if(!row.active)continue;}", "unsupported_loop_control"),
    ("for(const row of catalog.items){const title=row.title;}", "unproven_loop_iterable"),
])
def test_known_shape_feedback_keeps_identity_and_gives_a_bounded_supported_reference(
    statement, reason,
):
    _, gap = source_gap(statement)
    assert gap and reason in gap
    assert gap.obligation is None and gap.repair_path is None
    assert "Supported source repair:" in gap
    assert management._SUPPORTED_CART_REPAIR_EXAMPLE in gap
    assert "already proven local JSON" in gap and "existing validated cart schema" in gap
    assert "A DTO is not SDK render authority" in gap
    assert "Preserve the manifest-selected create handler" in gap
    assert "add model/repair budget" in gap
    base = str(gap).split("\nSupported source repair:", 1)[0]
    assert gap.rejection_key() == management.ManagedSourceGap(base).rejection_key()


def test_published_scalar_quantity_cart_reference_passes_the_unchanged_oracle():
    _, gap = source_gap(management._SUPPORTED_CART_REPAIR_EXAMPLE)
    assert gap is None


@pytest.mark.parametrize("move_hook", [False, True])
def test_unproven_generic_storage_writer_gets_a_data_preserving_repair(move_hook):
    react = 'import {useState,useEffect} from "react";\n'
    hook = """
function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);
  return [value, setValue] as const;
}
"""
    files = {"src/lib/use-stored.ts": react + "export " + hook} if move_hook else {}
    prefix = 'import {useStored} from "@/lib/use-stored";\n' if move_hook else react + hook
    _, gap = source_gap(
        'const [draft, setDraft] = useStored("fixture-cart.v1", unknownInitial());',
        prefix=prefix, **files,
    )
    assert gap
    expected_path = "src/lib/use-stored.ts" if move_hook else "src/app/page.tsx"
    path, _, _, _, reason = location(gap)
    assert (path, reason) == (expected_path, "unproven_json_stringify")
    base, _, guidance = gap.partition("\n")
    assert gap.rejection_key() == management.ManagedSourceGap(base).rejection_key()
    assert "explicit typed state" in guidance
    assert "existing keys, stored shapes and writes" in guidance
    assert "wait for hydration" in guidance
    assert "moving the generic helper" in guidance
    assert "fixture-cart.v1" not in gap and len(gap.encode("utf-8")) < 1200


@pytest.mark.parametrize("case", [
    "canonical", "local_spoof", "tampered_sdk", "unawaited", "raw_receipt",
    "alias_override", "shadowed_module", "type_only", "alternate_next_js",
    "alternate_next_mjs", "changed_next_config", "babel_sidecar", "babel_package", "browser_map",
    "receipt_ternary", "receipt_short_circuit", "receipt_mutation", "receipt_destructuring",
    "side_effect_import", "empty_import", "stylesheet_and_import_type",
    "absent_stylesheet", "stylesheet_script_shadow", "local_js_import", "local_alias_override",
    "runtime_reexport",
])
def test_checkout_receipt_stringify_requires_resolved_canonical_sdk(case):
    import json

    from yleum_api.services.max_project_kit import _template_file

    sdk_path = "src/lib/omnia/integration-client.ts"
    helper_path = "src/lib/meal/entitlement-client.ts"
    method = "getYleumOrderCheckoutReceipt"
    specifier = "@/lib/omnia/integration-client"
    files = {
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        sdk_path: _template_file(sdk_path),
        **{
            path: _template_file(path) for path in (
                "src/lib/omnia/catalog-client.ts", "src/lib/omnia/app-profile-client.ts",
                "src/lib/omnia/identity-scope.ts", "src/lib/max/bridge.ts",
            )
        },
        "tsconfig.json": _template_file("tsconfig.json"),
        "next.config.ts": _template_file("next.config.ts"),
        helper_path: """
export async function redeem(receipt: string) {
  const payload = {receipt};
  return fetch("/api/meal/entitlements", {method: "POST", body: JSON.stringify(payload)});
}
""",
    }
    if case == "local_spoof":
        specifier = "@/lib/fake-checkout"
        files["src/lib/fake-checkout.ts"] = (
            f"export async function {method}(id: string): Promise<string> {{ "
            "return opaqueReceipt(id); }"
        )
    elif case == "tampered_sdk":
        files[sdk_path] += "\n// changed SDK"
    elif case == "alias_override":
        config = json.loads(files["tsconfig.json"])
        config["compilerOptions"]["paths"][specifier] = ["./src/lib/fake-checkout"]
        files["tsconfig.json"] = json.dumps(config)
    elif case == "shadowed_module":
        files["src/lib/omnia/integration-client.js"] = "export const fake = true;"
    elif case in {"alternate_next_js", "alternate_next_mjs", "changed_next_config"}:
        suffix = "ts" if case == "changed_next_config" else case.rsplit("_", 1)[-1]
        files[f"next.config.{suffix}"] = (
            'export default {typescript: {tsconfigPath: "./checkout.tsconfig.json"}};'
        )
        config = json.loads(files["tsconfig.json"])
        config["compilerOptions"]["paths"][specifier] = ["./src/lib/fake-checkout"]
        files["checkout.tsconfig.json"] = json.dumps(config)
    elif case in {"babel_sidecar", "babel_package"}:
        babel = {"plugins": [["module-resolver", {
            "alias": {specifier: "./src/lib/fake-checkout"},
        }]]}
        if case == "babel_sidecar":
            files["src/app/.babelrc.json"] = json.dumps(babel)
        else:
            files["package.json"] = json.dumps({"babel": babel})
    elif case == "browser_map":
        files["package.json"] = json.dumps({
            "browser": {"./" + sdk_path: "./src/lib/fake-checkout.ts"},
        })
    imported = "type " + method if case == "type_only" else method
    expression = (
        f"{method}(order.id)" if case == "unawaited" else
        f"(await {method}(order.id)).receipt" if case == "raw_receipt" else
        f"await {method}(order.id)"
    )
    extra = ""
    if case == "receipt_ternary":
        expression = f"unknownCondition() ? {expression} : untrustedReceipt()"
    elif case == "receipt_short_circuit":
        expression = f"untrustedReceipt() ?? {expression}"
    elif case == "receipt_mutation":
        extra = "if (unknownCondition()) {} else receipt = untrustedReceipt();"
    elif case == "receipt_destructuring":
        extra = "[receipt] = [untrustedReceipt()];"
    setup_import = ""
    if case in {"side_effect_import", "empty_import"}:
        files["src/app/shim.ts"] = "window.open = opaqueOpen;"
        setup_import = (
            'import "./shim";\n' if case == "side_effect_import" else 'import {} from "./shim";\n'
        )
    elif case in {"stylesheet_and_import_type", "absent_stylesheet", "stylesheet_script_shadow"}:
        if case == "stylesheet_and_import_type":
            files["src/app/meal.css"] = "body { color: black; }"
        elif case == "stylesheet_script_shadow":
            files["src/app/meal.css.ts"] = "window.open = opaqueOpen;"
        setup_import = 'import "./meal.css";\nimport type {OnlyType} from "./types";\n'
    elif case == "local_js_import":
        files["src/app/shim/index.js"] = "export const marker = 1;"
        setup_import = 'import {marker} from "./shim";\n'
    elif case == "runtime_reexport":
        files["src/lib/meal/shim.ts"] = "export const marker = 1;"
        files[helper_path] = 'export {marker} from "./shim";\n' + files[helper_path]
    elif case == "local_alias_override":
        config = json.loads(files["tsconfig.json"])
        config["compilerOptions"]["paths"]["@/lib/meal/*"] = ["./src/lib/other/*"]
        files["tsconfig.json"] = json.dumps(config)
    files["src/app/page.tsx"] = (
        setup_import + f'import {{{imported}}} from "{specifier}";\n'
        'import {redeem} from "@/lib/meal/entitlement-client";\n'
        + BASE.replace(
            "EXTRA",
            "async function syncReceipt(order) {"
            f"let receipt = {expression}; {extra} return redeem(receipt);"
            "} syncReceipt(selectedOrder);",
        )
    )
    gap = management.management_source_advisory(files)
    if case in {"canonical", "stylesheet_and_import_type"}:
        assert gap is None
        connections = management._Connections(files, inspect_checkout=True)
        connections.analyze()
        assert not connections.unsupported_effects
        assert not connections.created and not connections.checkout_calls
        assert not management.linked_checkout_connected(files)
    else:
        assert gap is not None
        path, _, _, _, reason = location(gap)
        if case in {
            "type_only", "side_effect_import", "empty_import", "stylesheet_script_shadow",
        }:
            assert (path, reason) == ("src/app/page.tsx", "unproven_checkout_side_effect_import")
        elif case == "absent_stylesheet":
            assert (path, reason) == ("src/app/page.tsx", "missing_checkout_stylesheet")
            assert gap.obligation is None and "Restore the intended stylesheet" in gap
        elif case == "local_js_import":
            assert (path, reason) == ("src/app/page.tsx", "unproven_checkout_local_import")
        elif case == "receipt_destructuring":
            assert (path, reason) == ("src/app/page.tsx", "unproven_checkout_assignment_pattern")
        elif case == "runtime_reexport":
            assert (path, reason) == (helper_path, "unproven_checkout_runtime_reexport")
        else:
            assert (path, reason) == (helper_path, "unproven_json_stringify")


@pytest.mark.parametrize("case", [
    "canonical", "arbitrary_url", "raw_url_object", "shadowed_string", "shadowed_window",
    "lookalike_sdk", "opener", "unawaited_checkout", "unawaited_order", "arbitrary_storage",
    "unguarded_url", "aliased_open", "captured_popup", "untrusted_storage_key",
    "platform_storage_key", "mutated_storage", "tampered_business_sdk",
    "joined_url", "short_circuit_url", "joined_order_id", "joined_storage_key",
    "template_early_return", "function_template_early_return",
    "joined_checkout_fn", "hidden_mutation", "hidden_window_override",
    "unrelated_idempotency_storage_key", "shadowed_crypto", "nonadjacent_idempotency_write",
    "unsafe_uuid_helper", "opaque_negation", "hidden_catch_mutation",
    "destructured_url_override", "destructured_native_override", "reordered_url_collection",
    "namespace_import_override", "namespace_directory_override", "ambiguous_namespace",
    "bare_typescript_alias", "wildcard_typescript_alias", "package_imports_alias",
    "unresolved_package_imports_alias", "uri_import", "base_url_import", "local_package_exports",
    "ordinary_package_imports", "required_module", "aliased_require", "shorthand_require",
    "commonjs_loader", "dynamic_import", "file_dependency", "link_dependency",
    "workspace_dependency", "relative_dependency", "malformed_dependency",
    "local_pnpm_override", "sdk_identity_alias", "sdk_identity_shadow", "sdk_bridge_shadow",
    "changed_sdk_identity", "canonical_pnpm_lock", "linked_pnpm_lock", "flow_escaped_pnpm_lock",
    "transitive_pnpm_lock", "directory_pnpm_lock", "cyclic_pnpm_lock", "evolved_pnpm_lock",
    "deep_pnpm_lock",
])
def test_checkout_navigation_and_storage_require_the_owned_order_flow(case):
    import json

    from yleum_api.services.max_project_kit import _template_file

    business_path = "src/lib/omnia/business-client.ts"
    integration_path = "src/lib/omnia/integration-client.ts"
    sdk_files = {
        path: _template_file(path)
        for path in (
            business_path, integration_path, "tsconfig.json", "next.config.ts",
            "src/lib/omnia/catalog-client.ts", "src/lib/omnia/app-profile-client.ts",
            "src/lib/omnia/identity-scope.ts", "src/lib/max/bridge.ts",
        )
    }
    entry = """
import {useEffect} from "react";
import {createManagedOrder} from "@/lib/omnia/business-client";
import {
  createYleumOrderCheckout, getYleumOrderCheckout,
} from "@/lib/omnia/integration-client";
const operationKey = "meal:checkout-operation";
const pendingOrderKey = "meal:checkout-order";
function freshOperation() { return crypto.randomUUID(); }
"""
    body = """
  useEffect(() => {
    const order = returnedOrderId();
    if (order) getYleumOrderCheckout(order);
  }, []);
  async function checkoutOrder() {
    let operationId = window.localStorage.getItem(operationKey);
    if (!operationId) {
      operationId = freshOperation();
      window.localStorage.setItem(operationKey, operationId);
    }
    const order = await createManagedOrder({customer_name: "Fixture"});
    const orderId = String(
      ((order as unknown as {id: string}).id ?? (order as WrappedOrder).data?.id) ?? ""
    );
    window.localStorage.setItem(pendingOrderKey, orderId);
    const checkout = await createYleumOrderCheckout(orderId);
    const url = (checkout as {confirmation_url: string | null}).confirmation_url;
    if (url) window.open(url, "_blank", "noopener");
  }
"""
    replacements = {
        "arbitrary_url": (
            "(checkout as {confirmation_url: string | null}).confirmation_url",
            '"https://example.test/pay"',
        ),
        "raw_url_object": (
            "(checkout as {confirmation_url: string | null}).confirmation_url",
            "opaqueCheckout().confirmation_url",
        ),
        "shadowed_string": (
            "const orderId = String",
            'const String = (value) => "fake"; const orderId = String',
        ),
        "shadowed_window": (
            "    window.localStorage.setItem",
            """    const window = fakeWindow();
    window.localStorage.setItem""",
        ),
        "lookalike_sdk": ("@/lib/omnia/integration-client", "@/lib/fake-checkout"),
        "opener": ('"_blank", "noopener"', '"_blank", "popup"'),
        "unawaited_checkout": ("await createYleumOrderCheckout", "createYleumOrderCheckout"),
        "unawaited_order": ("await createManagedOrder", "createManagedOrder"),
        "arbitrary_storage": (
            "setItem(pendingOrderKey, orderId)", 'setItem(pendingOrderKey, "not-an-order-id")',
        ),
        "unguarded_url": ("if (url) window.open", "window.open"),
        "aliased_open": ("if (url) window.open", "const open = window.open; if (url) open"),
        "captured_popup": ("if (url) window.open", "if (url) return window.open"),
        "untrusted_storage_key": ('"meal:checkout-order"', "unknownStorageKey()"),
        "platform_storage_key": ('"meal:checkout-order"', '"yleum.managed-intent.v1:orders"'),
        "joined_storage_key": (
            '"meal:checkout-order"', 'unknownCondition() ? "meal:checkout-order" : opaqueKey()',
        ),
        "joined_url": (
            "const url = (checkout as {confirmation_url: string | null}).confirmation_url;",
            """const url = unknownCondition()
                ? checkout.confirmation_url : "https://example.test/pay";""",
        ),
        "template_early_return": (
            "if (url) window.open",
            """const label = unknownCondition() ? `Ready: ${url}` : 'Pending';
    if (label) return;
    if (url) window.open""",
        ),
        "function_template_early_return": (
            "if (url) window.open",
            """function label() { return `Ready: ${url}`; }
    if (label()) return;
    if (url) window.open""",
        ),
        "short_circuit_url": (
            "(checkout as {confirmation_url: string | null}).confirmation_url",
            "opaqueUrl() ?? checkout.confirmation_url",
        ),
        "joined_order_id": (
            '((order as unknown as {id: string}).id ?? (order as WrappedOrder).data?.id) ?? ""',
            'unknownCondition() ? order.id : "some-other-id"',
        ),
        "unrelated_idempotency_storage_key": (
            "setItem(operationKey, operationId)", 'setItem("meal:other-key", operationId)',
        ),
        "shadowed_crypto": (
            "function freshOperation()",
            "const crypto = fakeCrypto(); function freshOperation()",
        ),
        "nonadjacent_idempotency_write": (
            "operationId = freshOperation();",
            "operationId = freshOperation(); operationId = freshOperation();",
        ),
        "unsafe_uuid_helper": (
            "return crypto.randomUUID();",
            "if (!unknownCondition()) return opaque(); return crypto.randomUUID();",
        ),
        "joined_checkout_fn": (
            "const checkout = await createYleumOrderCheckout(orderId);",
            """const launch = unknownCondition() ? createYleumOrderCheckout : opaqueCheckout;
    const checkout = await launch(orderId);""",
        ),
        "hidden_mutation": (
            "const url = (checkout",
            """if (unknownCondition()) {} else checkout.confirmation_url = opaqueUrl();
    const url = (checkout""",
        ),
        "opaque_negation": (
            'if (url) window.open',
            """if (!(unknownCondition() ? true : false)) window.open = opaqueOpen;
    if (url) window.open""",
        ),
        "hidden_catch_mutation": (
            'if (url) window.open',
            """try { couldThrow(); } catch { window.open = opaqueOpen; }
    if (url) window.open""",
        ),
        "destructured_url_override": (
            "const url = (checkout as {confirmation_url: string | null}).confirmation_url;",
            """let url = checkout.confirmation_url;
    [url] = ["https://example.test/pay"];""",
        ),
        "destructured_native_override": (
            'if (url) window.open',
            """({open: window.open} = opaque);
    if (url) window.open""",
        ),
        "reordered_url_collection": (
            "const url = (checkout as {confirmation_url: string | null}).confirmation_url;",
            """const options = [checkout.confirmation_url, "https://example.test/pay"];
    options.sort((left, right) => 1); const [url] = options;""",
        ),
        "hidden_window_override": (
            'if (url) window.open',
            """if (unknownCondition()) {} else window.open = opaqueOpen;
    if (url) window.open""",
        ),
        "mutated_storage": (
            "    window.localStorage.setItem",
            """    window.localStorage.setItem = fakeStorageWrite;
    window.localStorage.setItem""",
        ),
    }
    if case == "tampered_business_sdk":
        sdk_files[business_path] += "\n// Source differs from the protected template."
    if case == "lookalike_sdk":
        sdk_files["src/lib/fake-checkout.ts"] = """
export async function createYleumOrderCheckout(id) {
  return {confirmation_url: "https://example.test/pay"};
}
export async function getYleumOrderCheckout(id) { return {}; }
"""
    if case in {"namespace_import_override", "namespace_directory_override", "ambiguous_namespace"}:
        source_path = (
            "src/app/shim/index.ts" if case == "namespace_directory_override"
            else "src/app/shim.ts"
        )
        sdk_files[source_path] = "window.open = opaqueOpen; export const marker = 1;"
        if case == "ambiguous_namespace":
            sdk_files["src/app/shim.js"] = "window.open = anotherOpen;"
        entry = 'import * as namespace from "./shim";\n' + entry
    runtime_specifier = {
        "bare_typescript_alias": "checkout-shim",
        "wildcard_typescript_alias": "@project/checkout-shim",
        "package_imports_alias": "#checkout-shim",
        "unresolved_package_imports_alias": "#checkout-shim",
        "uri_import": "https://example.test/shim.js",
        "base_url_import": "checkout-shim",
        "local_package_exports": "checkout-shim",
    }.get(case)
    if runtime_specifier is not None:
        entry = f'import {{marker}} from "{runtime_specifier}";\n' + entry
        shim = "window.open = opaqueOpen; export const marker = 1;"
        if case in {"bare_typescript_alias", "wildcard_typescript_alias", "base_url_import"}:
            config = json.loads(sdk_files["tsconfig.json"])
            options = config["compilerOptions"]
            if case == "base_url_import":
                options["baseUrl"] = "."
                sdk_files["checkout-shim.ts"] = shim
            else:
                alias = runtime_specifier if case == "bare_typescript_alias" else "@project/*"
                target = (
                    "./src/app/shim.ts" if case == "bare_typescript_alias"
                    else "./src/app/checkout-shim.ts"
                )
                options["paths"][alias] = [
                    target if case == "bare_typescript_alias" else "./src/app/*.ts",
                ]
                sdk_files[target.removeprefix("./")] = shim
            sdk_files["tsconfig.json"] = json.dumps(config)
        elif case in {"package_imports_alias", "local_package_exports"}:
            target = "./src/app/shim.ts"
            mapping = (
                {"imports": {runtime_specifier: target}}
                if case == "package_imports_alias"
                else {"name": runtime_specifier, "exports": {".": target}}
            )
            sdk_files["package.json"] = json.dumps(mapping)
            sdk_files[target.removeprefix("./")] = shim
    if case == "ordinary_package_imports":
        entry = (
            'import {useRouter} from "next/navigation";\n'
            'import {Button} from "@maxhub/max-ui";\n'
        ) + entry
        sdk_files["package.json"] = _template_file("package.json")
    loader = {
        "required_module": 'const namespace = require("./shim");',
        "aliased_require": 'const load = require; const namespace = load("./shim");',
        "shorthand_require": (
            'const loader = {require}; const namespace = loader.require("./shim");'
        ),
        "commonjs_loader": 'const namespace = module.require("./shim");',
        "dynamic_import": 'const namespace = import("./shim");',
    }.get(case)
    if loader is not None:
        sdk_files["src/app/shim.ts"] = "window.open = opaqueOpen; export const marker = 1;"
        entry = loader + "\n" + entry
    dependency_specs = {
        "file_dependency": "file:./src/app/shim",
        "link_dependency": "link:./src/app/shim",
        "workspace_dependency": "workspace:*",
        "relative_dependency": "./src/app/shim",
        "malformed_dependency": None,
        "local_pnpm_override": "1.0.0",
    }
    if case in dependency_specs:
        manifest = {"dependencies": {"checkout-shim": dependency_specs[case]}}
        if case == "local_pnpm_override":
            manifest["pnpm"] = {"overrides": {"checkout-shim": "file:./src/app/shim"}}
        sdk_files["package.json"] = json.dumps(manifest)
        sdk_files["src/app/shim/package.json"] = '{"name":"checkout-shim","main":"index.js"}'
        sdk_files["src/app/shim/index.js"] = "window.open = opaqueOpen;"
        entry = 'import * as namespace from "checkout-shim";\n' + entry
    locks = {
        "linked_pnpm_lock": """
importers:
  .:
    dependencies:
      checkout-shim:
        specifier: ^1.0.0
        version: link:src/app/shim
""",
        "flow_escaped_pnpm_lock": r"""
importers: {.: {dependencies: {checkout-shim: {specifier: "^1.0.0",
                                               version: "li\x6ek:src/app/shim"}}}}
""",
        "transitive_pnpm_lock": """
importers:
  .:
    dependencies:
      checkout-shim:
        specifier: ^1.0.0
        version: 1.0.0
snapshots:
  checkout-shim@1.0.0:
    dependencies:
      indirect: file:src/app/shim
""",
        "deep_pnpm_lock": (
            "importers: {.: {}}\nsettings: " + "[" * 80 + "true" + "]" * 80 + "\n"
        ),
        "cyclic_pnpm_lock": """
settings: &cycle {self: *cycle}
importers: {.: {}}
""",
        "directory_pnpm_lock": """
importers:
  .:
    dependencies:
      checkout-shim:
        specifier: ^1.0.0
        version: 1.0.0
packages:
  checkout-shim@1.0.0:
    resolution: {type: directory, directory: src/app/shim}
""",
    }
    if case in {"canonical_pnpm_lock", "evolved_pnpm_lock"}:
        sdk_files["package.json"] = _template_file("package.json")
        lock = _template_file("pnpm-lock.yaml")
        if case == "evolved_pnpm_lock":
            lock = lock.replace("version: 2.1.1", "version: 2.1.2", 1).replace(
                "clsx@2.1.1", "clsx@2.1.2",
            )
        sdk_files["pnpm-lock.yaml"] = lock
    elif case in locks:
        sdk_files["package.json"] = json.dumps({
            "dependencies": {"checkout-shim": "^1.0.0"},
        })
        sdk_files["pnpm-lock.yaml"] = "lockfileVersion: '9.0'\n" + locks[case]
        sdk_files["src/app/shim/package.json"] = '{"name":"checkout-shim","main":"index.js"}'
        sdk_files["src/app/shim/index.js"] = "window.open = opaqueOpen; export const marker = 1;"
        entry = 'import * as namespace from "checkout-shim";\n' + entry
    if case == "sdk_identity_alias":
        config = json.loads(sdk_files["tsconfig.json"])
        config["compilerOptions"]["paths"]["@/lib/omnia/identity-scope"] = ["./src/app/shim.ts"]
        sdk_files["tsconfig.json"] = json.dumps(config)
        sdk_files["src/app/shim.ts"] = "window.open = opaqueOpen; export {};"
    elif case == "sdk_identity_shadow":
        sdk_files["src/lib/omnia/identity-scope.js"] = "window.open = opaqueOpen;"
    elif case == "sdk_bridge_shadow":
        sdk_files["src/lib/max/bridge.js"] = "window.open = opaqueOpen;"
    elif case == "changed_sdk_identity":
        sdk_files["src/lib/omnia/identity-scope.ts"] += "\n// Changed runtime source."
    code = entry + BASE.replace("EXTRA", body).replace(
        "<main><h1>", '<main><button onClick={checkoutOrder}>Pay</button><h1>',
    )
    if case in replacements:
        code = code.replace(*replacements[case])
    if loader is not None or case in dependency_specs or case in locks:
        code = code.replace("<main>", "<main><span>{namespace.marker}</span>", 1)
    files = {
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        "src/app/page.tsx": code,
        **sdk_files,
    }
    accepted = case in {
        "canonical", "ordinary_package_imports", "canonical_pnpm_lock", "evolved_pnpm_lock",
    }
    gap = management.management_source_advisory(files)
    assert (gap is None, management.linked_checkout_connected(files)) == (accepted, accepted)
    if case in {"package_imports_alias", "unresolved_package_imports_alias", "uri_import"}:
        assert location(gap)[4] == "unproven_checkout_package_import"
    if loader is not None:
        assert location(gap)[4] == "unproven_checkout_runtime_module_load"


@pytest.mark.parametrize(("folder", "editable"), [
    ("lib", True),
    ("lib/internal", True),
    ("lib/db", False),
    ("lib/max", False),
    ("lib/auth", False),
    ("components", False),
])
def test_only_editable_product_libraries_get_typed_stringify_repair(folder, editable):
    path = f"src/{folder}/send-fixture.ts"
    helper = """
export function sendFixture(value: unknown) {
  return fetch("/api/fixture", {method: "POST", body: JSON.stringify(value)});
}
"""
    manifest = build_management_contract(["catalog"])
    files = {
        ".omnia/management.json": manifest.model_dump_json(),
        "src/app/page.tsx": f'import {{sendFixture}} from "@/{folder}/send-fixture";\n'
        + BASE.replace("EXTRA", "sendFixture(unknownPayload());"),
        path: helper,
    }
    gap = management.management_source_advisory(files)
    assert gap
    assert location(gap) == (path, 3, 55, "call_expression", "unproven_json_stringify")
    plain, _, guidance = gap.partition("\n")
    assert plain.startswith(management.PARKED_THROW_DIAGNOSTIC_PREFIX)
    assert "Build the JSON.stringify input from explicit typed state" in guidance
    assert "unknownPayload" not in gap and "/api/fixture" not in gap
    assert gap.rejection_key() == management.ManagedSourceGap(plain).rejection_key()
    assert management.management_advisory_capabilities(files) == frozenset()
    if editable:
        assert gap.obligation is not None and gap.obligation.value == "json_stringify"
        assert gap.repair_path == path
        assert gap._contract_hash == manifest.contract_hash
        assert gap.with_diagnostic("redacted").repair_path == path
        assert gap.with_diagnostic("redacted").rejection_key() == gap.rejection_key()
    else:
        assert gap.obligation is None and gap.repair_path is None
    assert management.ManagedSourceGap(str(gap)).repair_path is None

    # Real proof still requires a source correction for the exact rejected call.
    files[path] = helper.replace("JSON.stringify(value)", "JSON.stringify({quantity: 1})")
    assert management.management_source_advisory(files) is None
    assert "catalog" in management.management_advisory_capabilities(files)


def test_explicit_hydrated_local_json_writes_keep_managed_connections_proven():
    _, gap = source_gap(
        """
const [cartDraft, setCartDraft] = useState({quantity: 0});
const [favorite, setFavorite] = useState<string | null>(null);
const [savedIds, setSavedIds] = useState<string[]>([]);
const [hydrated, setHydrated] = useState(false);
useEffect(() => {
  try {
    const storage = window.localStorage;
    const savedCart = storage.getItem("fixture-cart.v1");
    const savedFavorite = storage.getItem("fixture-favorite.v1");
    const idsText = storage.getItem("fixture-ids.v1");
    if (savedCart) {
      try {
        const parsed = JSON.parse(savedCart);
        if (parsed !== null && typeof parsed.quantity === "number") {
          setCartDraft({quantity: parsed.quantity});
        }
      } catch {}
    }
    if (savedFavorite) {
      try {
        const parsed = JSON.parse(savedFavorite);
        if (typeof parsed === "string") setFavorite(parsed);
      } catch {}
    }
    if (idsText) {
      try {
        const parsed = JSON.parse(idsText);
        if (parsed instanceof Array && parsed.every(id => typeof id === "string")) {
          setSavedIds(JSON.parse(idsText));
        }
      } catch {}
    }
    setHydrated(true);
  } catch {}
}, []);
useEffect(() => {
  if (!hydrated) return;
  try {
    const storage = window.localStorage;
    storage.setItem("fixture-cart.v1", JSON.stringify(cartDraft));
    storage.setItem("fixture-favorite.v1", JSON.stringify(favorite));
    storage.setItem("fixture-ids.v1", JSON.stringify(savedIds));
  } catch {}
}, [cartDraft, favorite, savedIds, hydrated]);
""",
        prefix='import {useState,useEffect} from "react";\n',
    )
    assert gap is None


@pytest.mark.parametrize("statement", [
    "const cartRows=Object.keys(cart).map(id=>{catalog.items.length=0;return{id,qty:1};});",
    "const cartRows=Object.keys(cart).map(id=>{"
    "const selected=catalog.items.find(item=>item.id===id)??null;"
    "selected.title='fake';return{id,qty:1};});",
    "Reflect.defineProperty(Reflect.getPrototypeOf({}),'toJSON',"
    "{value:()=>{catalog.items.length=0;return {};}});JSON.stringify({});",
    "Object.prototype.toJSON=()=>({});JSON.stringify({});",
])
def test_repair_guidance_does_not_admit_mutations_or_reflection(statement):
    _, gap = source_gap(statement)
    assert gap is not None


def test_diagnostic_and_reference_never_copy_source_values():
    _, gap = source_gap("Object.entries({api_key:'synthetic-private-value'});")
    assert gap
    assert "synthetic-private-value" not in gap and "api_key" not in gap
    assert len(gap.encode()) < 2600


def test_guidance_does_not_grant_derived_dto_sdk_render_identity():
    source = BASE.replace("EXTRA", management._SUPPORTED_CART_REPAIR_EXAMPLE).replace(
        "catalog.items.map(item=><p key={item.id}>{item.title}</p>)",
        "cartRows.map(row=><p key={row.id}>{row.title}</p>)",
    )
    gap = management.management_source_advisory({
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        "src/app/page.tsx": source,
    })
    assert gap and gap.startswith("Connect useYleumCatalog")
